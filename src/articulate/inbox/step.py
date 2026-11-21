"""The `inbox.notes` step (registry order 120, jobs watch + nightly).

One fleeting `Inbox/` note per uncommanded, non-class, non-Private Pocket recording or class tail, on local
`qwen3:8b` (#42, #31, #8; SPEC-requirements 1.10–1.17). Runs after `pocket.poll` and before `tasks`, so a task
can link the note through `vault_notes.source_item_id`.
"""
import hashlib
import importlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from articulate.core.db import Database
from articulate.core.ids import new_ulid
from articulate.core.jobs import StepSummary
from articulate.core.models import Held, ModelRequest, Part
from articulate.core.pipeline import Privacy, RunContext
from articulate.core.vault import NoteRef
from articulate.inbox.quotes import find_quote
from articulate.inbox.transcript import Transcript, format_ts, load_archived

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
STEP = "personal_text"  # models.toml chain: local qwen3:8b only (§3.2)
MIN_WORDS = 15
MAX_QUOTES = 3
MAX_POINTS = 5
MAX_FAILURES = 3
KINDS = ["idea", "meeting", "journal", "content", "other"]
FLAG_CHOICES = [("retry", "retry"), ("drop", "drop")]
_TITLE_BAD = str.maketrans({c: " " for c in '/\\:*?"<>|#^[]'})

MeetingMatcher = Callable[[datetime, datetime, RunContext], Any]

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["skip", "tasks_only", "note"]},
        "kind": {"type": "string", "enum": KINDS},
        "reason": _STR,
    },
    "required": ["decision", "kind", "reason"],
}
NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _STR, "paraphrase": _STR, "quotes": _STRS, "decisions": _STRS, "open_questions": _STRS,
    },
    "required": ["title", "paraphrase", "quotes", "decisions", "open_questions"],
}
MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _STR,
        "paraphrase": _STR,
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": _STR, "quote": {"type": "integer"}},
                "required": ["text", "quote"],
            },
        },
        "decisions": _STRS,
        "open_questions": _STRS,
    },
    "required": ["title", "paraphrase", "points", "decisions", "open_questions"],
}


# --- prompts -------------------------------------------------------------------------------------------------


def _prompt(name: str) -> tuple[str, str]:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode()).hexdigest()[:8]


# --- what pocket hands over ----------------------------------------------------------------------------------


@dataclass
class RecordingView:
    """The fields inbox reads from a pocket `recording` or `span` Item (see the question on #56)."""

    item_id: str
    recording_id: str
    start_sec: float | None = None  # span only: window on the recording's timeline
    end_sec: float | None = None
    title: str | None = None

    @property
    def is_span(self) -> bool:
        return self.start_sec is not None

    @classmethod
    def from_row(cls, row: Any) -> "RecordingView":
        payload = json.loads(row["payload_json"] or "{}")
        if row["kind"] == "span":
            return cls(
                item_id=row["item_id"],
                recording_id=str(payload["recording_id"]),
                start_sec=float(payload["start_sec"]),
                end_sec=float(payload["end_sec"]) if payload.get("end_sec") is not None else None,
                title=payload.get("title"),
            )
        return cls(item_id=row["item_id"], recording_id=row["native_id"], title=payload.get("title"))


def _default_meeting_matcher(start: datetime, end: datetime, ctx: RunContext) -> Any:
    """The calendar slice's non-class event match, if it ships one; otherwise no meetings."""
    try:
        calendar = importlib.import_module("articulate.calendar")
    except ImportError:
        return None
    match = getattr(calendar, "match_meeting", None)
    if match is None:
        return None
    try:
        return match(start, end, ctx)
    except Exception as e:  # noqa: BLE001  # a calendar outage must not stop notes
        logger.warning("Meeting match failed: %s", type(e).__name__)
        return None


@dataclass
class Meeting:
    title: str
    start: datetime
    end: datetime
    attendees: list[str] = field(default_factory=list)

    @classmethod
    def coerce(cls, raw: Any) -> "Meeting | None":
        if raw is None:
            return None

        def get(key: str) -> Any:
            return raw.get(key) if isinstance(raw, dict) else getattr(raw, key, None)

        def when(v: Any) -> datetime:
            return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))

        return cls(
            title=str(get("title")),
            start=when(get("start")),
            end=when(get("end")),
            attendees=[str(a) for a in (get("attendees") or [])],
        )


# --- the step ------------------------------------------------------------------------------------------------


class QuoteCheckFailed(Exception):
    pass


@dataclass
class _Done:
    status: str  # done | held | pending
    outcome: str | None = None
    hold_reason: str | None = None
    articulate_id: str | None = None
    kind: str | None = None
    model: str | None = None
    detail: str = ""


def _ensure_services(ctx: RunContext) -> None:
    if ctx.vault is None:
        from articulate.core.paths import vault_path
        from articulate.core.vault import Vault

        ctx.vault = Vault(vault_path(ctx.config), ctx.db)
    if ctx.runlog is None:
        from articulate.core.runlog import RunLog

        ctx.runlog = RunLog(ctx.vault)
    if ctx.flags is None:
        from articulate.core.flags import Flags

        ctx.flags = Flags(ctx.db, vault=ctx.vault)
    if ctx.models is None:
        from articulate.core.models import DefaultModelRunner

        ctx.models = DefaultModelRunner(ctx.db, ctx.config, job=ctx.job, run_id=ctx.run_id)


def step(ctx: RunContext) -> StepSummary:
    return run_inbox(ctx)


def run_inbox(
    ctx: RunContext,
    *,
    meeting_matcher: MeetingMatcher | None = None,
    tz: tzinfo | None = None,
) -> StepSummary:
    _ensure_services(ctx)
    tz = tz or datetime.now().astimezone().tzinfo
    matcher = meeting_matcher or _default_meeting_matcher
    summary = StepSummary()

    try:
        ctx.vault.scan()  # deleted notes become tombstones before anything is (re)written
    except Exception as e:  # noqa: BLE001
        logger.warning("Vault scan failed: %s", type(e).__name__)

    _apply_resolved_flags(ctx)

    for row in _candidates(ctx.db):
        view = RecordingView.from_row(row)
        try:
            result = _process(ctx, view, Privacy(row["privacy"]), matcher, tz)
        except Exception as e:  # noqa: BLE001  # one bad recording must not stop the others
            logger.error("inbox failed on %s: %s", view.item_id, type(e).__name__)
            result = _failure(ctx, view, f"{type(e).__name__}: {e}")
        if result.status == "pending":
            continue
        if result.status == "done":
            summary.done += 1
        elif result.status == "held":
            summary.held += 1
        else:
            summary.failed += 1
    summary.detail = f"inbox: {summary.done} done, {summary.held} held, {summary.failed} failed"
    return summary


def _candidates(db: Database) -> list[Any]:
    with db.connect() as conn:
        return conn.execute(
            """
            SELECT i.item_id, i.kind, i.native_id, i.privacy, i.payload_json FROM items i
            LEFT JOIN inbox_items x ON x.item_id = i.item_id
            WHERE i.source = 'pocket' AND i.kind IN ('recording', 'span') AND i.privacy = 'personal'
              AND i.status NOT IN ('superseded', 'skipped')
              AND (x.item_id IS NULL OR x.status IN ('pending', 'failed')
                   OR (x.status = 'held' AND x.flag_id IS NULL))
            ORDER BY i.created_at, i.item_id;
            """
        ).fetchall()


def _apply_resolved_flags(ctx: RunContext) -> None:
    """`core.flags` runs before this step registers anything, so read our own resolved flags here."""
    now = ctx.now.isoformat()
    with ctx.db.connect() as conn:
        rows = conn.execute(
            """
            SELECT x.item_id, f.chosen FROM inbox_items x JOIN flags f ON f.flag_id = x.flag_id
            WHERE x.status = 'held' AND f.status = 'resolved';
            """
        ).fetchall()
        for row in rows:
            if row["chosen"] == "drop":
                conn.execute(
                    "UPDATE inbox_items SET status='dropped', flag_id=NULL, updated_at=? WHERE item_id=?;",
                    (now, row["item_id"]),
                )
            else:
                conn.execute(
                    """
                    UPDATE inbox_items SET status='pending', flag_id=NULL, hold_reason=NULL, attempts=0,
                           updated_at=? WHERE item_id=?;
                    """,
                    (now, row["item_id"]),
                )
        conn.commit()


def _ledger(ctx: RunContext, item_id: str, **cols: Any) -> None:
    now = ctx.now.isoformat()
    cols["updated_at"] = now
    with ctx.db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO inbox_items (item_id, status, created_at, updated_at) VALUES (?, 'pending', ?, ?);",
            (item_id, now, now),
        )
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(f"UPDATE inbox_items SET {sets} WHERE item_id = ?;", (*cols.values(), item_id))
        conn.commit()


def _failure(ctx: RunContext, view: RecordingView, error: str) -> _Done:
    with ctx.db.connect() as conn:
        row = conn.execute("SELECT attempts FROM inbox_items WHERE item_id = ?;", (view.item_id,)).fetchone()
    attempts = (row["attempts"] if row else 0) + 1
    if attempts >= MAX_FAILURES:
        flag_id = ctx.flags.add(
            "inbox.failed",
            f"Inbox note for Pocket recording {view.recording_id} failed {attempts} times",
            FLAG_CHOICES,
            item_id=view.item_id,
        )
        _ledger(ctx, view.item_id, status="held", hold_reason="failed", flag_id=flag_id,
                attempts=attempts, last_error=error[:500])
        _runs_line(ctx, view, "held (failed)", None, None, None)
        return _Done("held", hold_reason="failed")
    _ledger(ctx, view.item_id, status="failed", attempts=attempts, last_error=error[:500])
    return _Done("failed")


def _tombstoned(ctx: RunContext, item_id: str) -> bool:
    with ctx.db.connect() as conn:
        return conn.execute("SELECT 1 FROM tombstones WHERE key = ?;", (item_id,)).fetchone() is not None


def _finish(ctx: RunContext, view: RecordingView, outcome: str, **extra: Any) -> _Done:
    model = extra.pop("_model", None)
    _ledger(ctx, view.item_id, status="done", outcome=outcome, hold_reason=None, flag_id=None, **extra)
    _runs_line(ctx, view, outcome, extra.get("kind"), extra.get("articulate_id"), model)
    return _Done("done", outcome=outcome)


def _process(
    ctx: RunContext,
    view: RecordingView,
    privacy: Privacy,
    matcher: MeetingMatcher,
    tz: tzinfo,
) -> _Done:
    if privacy != Privacy.PERSONAL:  # the query already filters; never read anything else
        return _Done("pending")
    if _tombstoned(ctx, view.item_id):
        _ledger(ctx, view.item_id, status="done", outcome="tombstoned")
        return _Done("done", outcome="tombstoned")

    full = load_archived(view.recording_id)
    if full is None:
        return _Done("pending")  # pocket hasn't archived it yet; try next pass
    t = full.window(view.start_sec, view.end_sec) if view.is_span else full
    title = view.title or full.title

    if not t.segments:
        return _finish(ctx, view, "skip_no_transcript")
    if t.word_count() < MIN_WORDS:
        return _finish(ctx, view, "skip_short")

    start = full.recorded_at() + timedelta(seconds=view.start_sec or 0.0)
    end = start + timedelta(seconds=t.span_seconds() or (full.duration or 0.0))
    meeting = Meeting.coerce(matcher(start, end, ctx))

    gate_prompt, gate_hash = _prompt("inbox-gate")
    note_prompt, note_hash = _prompt("inbox-note")
    versions = f"inbox-gate@{gate_hash} inbox-note@{note_hash}"
    chunks = t.chunks() if t.needs_chunking() else [t]

    try:
        decision, kind, model = _gate(ctx, view, gate_prompt, chunks)
        if decision != "note":
            outcome = "tasks_only" if decision == "tasks_only" else "skip_gate"
            return _finish(ctx, view, outcome, kind=kind, prompt_version=versions, _model=model)
        if meeting is not None:
            kind = "meeting"
        header = _header(title, start, tz, meeting)
        content, model = _write_content(ctx, view, note_prompt, header, chunks)
    except Held as h:
        _ledger(ctx, view.item_id, status="held", hold_reason=h.reason, prompt_version=versions)
        return _Done("held", hold_reason=h.reason)
    except QuoteCheckFailed as q:
        flag_id = ctx.flags.add(
            "inbox.quote_check",
            f"Inbox note for Pocket recording {view.recording_id} held: its quotes failed the check twice",
            FLAG_CHOICES,
            item_id=view.item_id,
        )
        _ledger(ctx, view.item_id, status="held", hold_reason="quote_check_failed", flag_id=flag_id,
                prompt_version=versions, last_error=str(q)[:500])
        _runs_line(ctx, view, "held (quote_check_failed)", None, None, None)
        return _Done("held", hold_reason="quote_check_failed")

    note_title = (meeting.title if meeting else content["title"]).translate(_TITLE_BAD)
    note_title = " ".join(note_title.split())[:120] or "Recording note"
    local_start = start.astimezone(tz)
    fm: dict[str, Any] = {
        "tags": ["fleeting", "agent"],
        "kind": kind,
        "pocket_id": view.recording_id,
        "created": ctx.now.astimezone(tz).date().isoformat(),
        "recorded": local_start.isoformat(timespec="minutes"),
    }
    if meeting:
        s, e = meeting.start.astimezone(tz), meeting.end.astimezone(tz)
        fm["event"] = f"{s:%Y-%m-%d %H:%M}–{e:%H:%M}"
    art_id = new_ulid()
    fm["articulate_id"] = art_id
    body = _render_body(content, meeting, title if view.is_span else None, view)

    ref = ctx.vault.create_note("Inbox", note_title, fm, body, kind="inbox", source_item_id=view.item_id)
    if ref is None:
        return _finish(ctx, view, "tombstoned", kind=kind, prompt_version=versions, _model=model)
    return _finish(ctx, view, "note", kind=kind, articulate_id=ref.articulate_id,
                   prompt_version=versions, _model=model)


# --- model calls ---------------------------------------------------------------------------------------------


def _call(ctx: RunContext, view: RecordingView, text: str, schema: dict) -> tuple[Any, str]:
    req = ModelRequest(
        step=STEP,
        material=Privacy.PERSONAL,
        parts=[Part(text=text)],
        json_schema=schema,
        item_id=view.item_id,
    )
    res = ctx.models.run(req)
    parsed = res.parsed
    if parsed is None and res.text:
        try:
            parsed = json.loads(res.text)
        except json.JSONDecodeError:
            parsed = None
    return parsed, f"{res.provider}:{res.model}"


def _gate(
    ctx: RunContext, view: RecordingView, prompt: str, chunks: list[Transcript]
) -> tuple[str, str, str]:
    decisions: list[str] = []
    kinds: list[str] = []
    model = ""
    for i, chunk in enumerate(chunks):
        part = f"\n\nPart {i + 1} of {len(chunks)} of the recording.\n" if len(chunks) > 1 else "\n\n"
        parsed, model = _call(ctx, view, f"{prompt}{part}Transcript:\n{chunk.text()}", GATE_SCHEMA)
        parsed = parsed if isinstance(parsed, dict) else {}
        decisions.append(parsed.get("decision", "note"))  # unreadable gate → keep, don't drop content
        kinds.append(parsed.get("kind") if parsed.get("kind") in KINDS else "other")
    if "note" in decisions:
        decision = "note"
    elif "tasks_only" in decisions:
        decision = "tasks_only"
    else:
        decision = "skip"
    kind = max(KINDS, key=lambda k: (kinds.count(k), k != "other"))
    return decision, kind, model


def _header(title: str, start: datetime, tz: tzinfo, meeting: Meeting | None) -> str:
    lines = [f"Recording: {title}", f"Recorded: {start.astimezone(tz):%Y-%m-%d %H:%M}"]
    if meeting:
        lines.append(f"Calendar event: {meeting.title}")
        if meeting.attendees:
            lines.append(f"Attendees: {', '.join(meeting.attendees)}")
    return "\n".join(lines)


def _check_note(parsed: Any, chunk: Transcript) -> tuple[list[str], list[tuple[str, float]]]:
    if not isinstance(parsed, dict):
        return ["- the answer was not the JSON object asked for"], []
    errors = []
    for key in ("title", "paraphrase"):
        if not str(parsed.get(key) or "").strip():
            errors.append(f"- `{key}` is empty")
    quotes = [str(q).strip().strip('"“”') for q in (parsed.get("quotes") or []) if str(q).strip()]
    quotes = quotes[:MAX_QUOTES]  # code cuts extras rather than rejecting the note
    if not quotes:
        errors.append("- `quotes` is empty: give 1 to 3 verbatim quotes")
    text = chunk.text()
    found = []
    for q in quotes:
        pos = find_quote(q, text)
        if pos is None:
            errors.append(f'- quote not found verbatim in the transcript: "{q}"')
        else:
            found.append((q, chunk.time_at(pos)))
    return errors, found


def _with_retry(ctx: RunContext, view: RecordingView, text: str, schema: dict, check: Callable) -> tuple:
    """One retry with the errors appended; a second failure raises QuoteCheckFailed (#31 rule 12)."""
    parsed, model = _call(ctx, view, text, schema)
    errors, extra = check(parsed)
    if errors:
        retry = f"{text}\n\nYour previous answer was rejected:\n" + "\n".join(errors)
        parsed, model = _call(ctx, view, retry, schema)
        errors, extra = check(parsed)
        if errors:
            raise QuoteCheckFailed("; ".join(errors))
    return parsed, extra, model


def _write_content(
    ctx: RunContext, view: RecordingView, prompt: str, header: str, chunks: list[Transcript]
) -> tuple[dict, str]:
    notes = []
    model = ""
    for i, chunk in enumerate(chunks):
        where = f"\nThis transcript is chunk {i + 1} of {len(chunks)} of a longer recording." if len(chunks) > 1 else ""
        text = f"{prompt}\n\n{header}{where}\nMode: note\n\nTranscript:\n{chunk.text()}"
        parsed, found, model = _with_retry(ctx, view, text, NOTE_SCHEMA, lambda p, c=chunk: _check_note(p, c))
        notes.append({**parsed, "found": found})

    if len(notes) == 1:
        n = notes[0]
        return {
            "title": n["title"],
            "paraphrase": n["paraphrase"],
            "quotes": n["found"],
            "decisions": n.get("decisions") or [],
            "open_questions": n.get("open_questions") or [],
        }, model

    numbered: list[tuple[str, float]] = [q for n in notes for q in n["found"]]
    lines = []
    k = 0
    for i, n in enumerate(notes):
        lines.append(f"Chunk {i + 1}: {n['paraphrase']}")
        for q, _ in n["found"]:
            k += 1
            lines.append(f'  {k}. "{q}"')
        for d in n.get("decisions") or []:
            lines.append(f"  decision: {d}")
        for o in n.get("open_questions") or []:
            lines.append(f"  open question: {o}")
    text = f"{prompt}\n\n{header}\nMode: merge\n\nChunk notes:\n" + "\n".join(lines)

    def check_merge(parsed: Any) -> tuple[list[str], list[tuple[str, str, float]]]:
        if not isinstance(parsed, dict):
            return ["- the answer was not the JSON object asked for"], []
        points = []
        for p in parsed.get("points") or []:
            idx = p.get("quote") if isinstance(p, dict) else None
            if isinstance(idx, int) and 1 <= idx <= len(numbered) and str(p.get("text") or "").strip():
                q, secs = numbered[idx - 1]
                points.append((str(p["text"]).strip(), q, secs))
        errors = [] if points else ["- `points` is empty or no point names a listed quote number"]
        if not str(parsed.get("title") or "").strip():
            errors.append("- `title` is empty")
        return errors, points[:MAX_POINTS]

    parsed, points, model = _with_retry(ctx, view, text, MERGE_SCHEMA, check_merge)
    return {
        "title": parsed["title"],
        "paraphrase": parsed.get("paraphrase") or "",
        "points": points,
        "decisions": parsed.get("decisions") or [],
        "open_questions": parsed.get("open_questions") or [],
    }, model


# --- rendering and logging -----------------------------------------------------------------------------------


def _render_body(content: dict, meeting: Meeting | None, cut_from: str | None, view: RecordingView) -> str:
    out = [content["paraphrase"].strip(), ""]
    if "points" in content:
        out += [f'- {text} — "{q}" ≈{format_ts(secs)}' for text, q, secs in content["points"]]
    else:
        out += [f'- "{q}" ≈{format_ts(secs)}' for q, secs in content["quotes"]]
    if meeting:
        if meeting.attendees:
            out += ["", f"Attendees: {', '.join(meeting.attendees)}"]
        if content["decisions"]:
            out += ["", "## Decisions"] + [f"- {d}" for d in content["decisions"]]
        if content["open_questions"]:
            out += ["", "## Open questions"] + [f"- {q}" for q in content["open_questions"]]
    if cut_from is not None:
        span = f"≈{format_ts(view.start_sec or 0)}"
        if view.end_sec is not None:
            span += f"–{format_ts(view.end_sec)}"
        out += ["", f"Cut from {cut_from} ({span}), the part after class."]
    return "\n".join(out).rstrip() + "\n"


def _runs_line(
    ctx: RunContext,
    view: RecordingView,
    outcome: str,
    kind: str | None,
    articulate_id: str | None,
    model: str | None,
) -> None:
    """One Runs.md line per recording (#42 rule 8). Ids only: no titles or transcript text."""
    if ctx.runlog is None:
        return
    rec = view.recording_id + (f"@{int(view.start_sec or 0)}s" if view.is_span else "")
    parts = [f"- {ctx.now.astimezone():%Y-%m-%d %H:%M} inbox pocket {rec}: {outcome}"]
    if kind:
        parts.append(f"kind {kind}")
    if articulate_id:
        parts.append(f"note {articulate_id}")
    if model:
        parts.append(model)
    parts.append(f"run {ctx.run_id}")
    ctx.runlog.line(" · ".join(parts))


def note_for_item(db: Database, item_id: str) -> NoteRef | None:
    """The Inbox note written for an item, for `tasks` to link (`obsidian_uri` needs only the relative path)."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT articulate_id, path FROM vault_notes WHERE source_item_id = ? AND kind = 'inbox';",
            (item_id,),
        ).fetchone()
    if row is None:
        return None
    rel = row["path"]
    return NoteRef(articulate_id=row["articulate_id"], path=Path(rel), relative_path=rel, title=Path(rel).stem)
