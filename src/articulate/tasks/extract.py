"""Task extraction from non-class Pocket recordings on the local model (1.18–1.21, #20).

Reads `items` rows that `pocket` emitted and `inbox` finished (`status = 'done'`), so the Inbox note exists
before its tasks link to it (1.16). Each item is extracted once; the result lands in core's `tasks` ledger:
high → `pending` (sent this run), medium and unanswered requests → `flagged` (a `tasks.medium` line in
`Inbox/Intake flags.md`), low → `dropped` (kept for eval).

Ambient audio (a podcast, a tutorial, an interview playing nearby) is full of second-person advice that reads
like a to-do ("create a thumbnail with motion blur"). Beyond the prompt, `gate` holds the model to it (#112): a
commitment needs a first-person marker in its quote, an accepted request needs Amier's agreement, and an
unanswered request must be aimed at Amier. Class lectures go through `tasks.lecture`, where instructor directives
("read Chapter 4 before Monday") are the point and this gate does not apply.
"""
import gzip
import itertools
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from articulate.core.clock import EASTERN
from articulate.core.db import Database
from articulate.core.jobs import StepSummary
from articulate.core.models.runner import Held, ModelRequest, Part
from articulate.core.paths import get_data_dir
from articulate.core.pipeline import Privacy, RunContext
from articulate.core.taskqueue import TaskQueue, TaskRequest
from articulate.core.vault import NoteRef

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "task.md"
_PROMPT_TEXT = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_VERSION = re.search(r"<!--\s*version:\s*(\S+)\s*-->", _PROMPT_TEXT).group(1)  # type: ignore[union-attr]
PROMPT = re.sub(r"^<!--.*?-->\s*", "", _PROMPT_TEXT, count=1, flags=re.DOTALL)

FLAG_KIND = "tasks.medium"
FLAG_CHOICES = [("send", "send"), ("drop", "drop")]
DEDUPE_DAYS = 14
DEDUPE_COSINE = 0.9
CHUNK_CHARS = 8000
TITLE_MAX = 80

KINDS = ["commitment", "accepted_request", "unanswered_request", "delegated", "musing", "advice"]
NEVER_TASKS = {"delegated", "musing", "advice"}
CONFIDENCES = ["high", "medium", "low"]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "quote": {"type": "string"},
                    "kind": {"type": "string", "enum": KINDS},
                    "confidence": {"type": "string", "enum": CONFIDENCES},
                    "due_spoken": {"type": ["string", "null"]},
                },
                "required": ["title", "quote", "kind", "confidence", "due_spoken"],
            },
        }
    },
    "required": ["tasks"],
}

# --- Due dates: only when spoken, resolved against the recording date ---------------------------

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"]
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(d[:3] for d in _WEEKDAYS) + r")[a-z]*\b")
_MONTH_DAY_RE = re.compile(r"\b(" + "|".join(m[:3] for m in _MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b")
_SLASH_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)")
_AT_HOUR_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\b")


def _spoken_date(text: str, rec: date) -> date | None:
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", text):
        return rec
    if re.search(r"\btomorrow\b", text):
        return rec + timedelta(days=1)
    if m := _MONTH_DAY_RE.search(text):
        month = [x[:3] for x in _MONTHS].index(m.group(1)) + 1
        return _roll_forward(rec, month, int(m.group(2)))
    if m := _SLASH_RE.search(text):
        return _roll_forward(rec, int(m.group(1)), int(m.group(2)))
    if m := _WEEKDAY_RE.search(text):
        ahead = ([d[:3] for d in _WEEKDAYS].index(m.group(1)) - rec.weekday()) % 7 or 7
        return rec + timedelta(days=ahead)
    return None


def _roll_forward(rec: date, month: int, day: int) -> date | None:
    try:
        d = date(rec.year, month, day)
    except ValueError:
        return None
    return d if d >= rec else date(rec.year + 1, month, day)


def _spoken_time(text: str) -> tuple[int, int] | None:
    if re.search(r"\bnoon\b", text):
        return (12, 0)
    if re.search(r"\bmidnight\b", text):
        return (23, 59)
    if m := _TIME_RE.search(text):
        hour, minute = int(m.group(1)) % 12, int(m.group(2) or 0)
        return (hour + (12 if m.group(3).startswith("p") else 0), minute)
    if m := _AT_HOUR_RE.search(text):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if 1 <= hour <= 7:  # "at 3" in a to-do means the afternoon
            hour += 12
        return (hour, minute) if hour < 24 and minute < 60 else None
    return None


def resolve_due(spoken: str | None, recorded_at: datetime) -> tuple[str | None, bool]:
    """Turn the spoken due words into (ISO dueAt in local time, hasDueTime); (None, False) when vague.

    "Thursday" is the next Thursday after the recording; a month-day already past rolls to next year.
    """
    if not spoken or not spoken.strip():
        return (None, False)
    text = spoken.lower()
    rec = recorded_at.astimezone(EASTERN)
    when = _spoken_date(text, rec.date())
    hm = _spoken_time(text)
    if when is None:
        if hm is None:
            return (None, False)
        when = rec.date()
    hour, minute = hm or (0, 0)
    due = datetime(when.year, when.month, when.day, hour, minute, tzinfo=EASTERN)
    return (due.isoformat(), hm is not None)


# --- Transcript access ----------------------------------------------------------------------------

@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Recording:
    recording_id: str
    title: str
    recorded_at: datetime
    segments: list[Segment]


def _find_transcript(recording_id: str) -> Path | None:
    root = get_data_dir() / "transcripts"
    hits = sorted(root.glob(f"*/*/{recording_id}.json.gz")) if root.exists() else []
    return hits[0] if hits else None


def load_recording(item: dict[str, Any], payload: dict[str, Any]) -> Recording | None:
    """Read the archived transcript (TRANSCRIPTS/YYYY/MM/<id>.json.gz) for an item, cut to its window."""
    recording_id = str(payload.get("recording_id") or item["native_id"].split(":")[0])
    path = _find_transcript(recording_id)
    if path is None:
        return None
    raw = json.loads(gzip.decompress(path.read_bytes()))
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    segs = [
        Segment(float(s.get("start") or 0), float(s.get("end") or 0), str(s.get("text") or ""))
        for s in (data.get("transcript") or {}).get("segments") or []
        if str(s.get("text") or "").strip()
    ]
    lo, hi = payload.get("start_sec"), payload.get("end_sec")
    if lo is not None or hi is not None:
        lo_f = float(lo or 0)
        hi_f = float(hi) if hi is not None else math.inf
        segs = [s for s in segs if lo_f <= s.start < hi_f]

    recorded_at: datetime
    if payload.get("recorded_at"):
        recorded_at = datetime.fromisoformat(str(payload["recorded_at"]))
    else:
        rec_val = data.get("recording_at")
        if isinstance(rec_val, (int, float)):
            recorded_at = datetime.fromtimestamp(float(rec_val), tz=EASTERN)
        elif isinstance(rec_val, str):
            try:
                recorded_at = datetime.fromtimestamp(float(rec_val), tz=EASTERN)
            except ValueError:
                recorded_at = datetime.fromisoformat(rec_val)
        else:
            recorded_at = datetime.now(tz=EASTERN)
    title = str(payload.get("title") or data.get("title") or "Recording")
    return Recording(recording_id, title, recorded_at, segs)


def _stamp(sec: float) -> str:
    s = int(sec)
    return f"{s // 60}:{s % 60:02d}"


def transcript_chunks(segments: list[Segment]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for seg in segments:
        line = f"[{_stamp(seg.start)}] {seg.text.strip()}"
        if current and size + len(line) > CHUNK_CHARS:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _norm(text: str) -> str:
    text = text.lower().translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'}))
    text = re.sub(r"^\[\d+:\d{2}(?::\d{2})?\]\s*", "", text.strip())
    return re.sub(r"\s+", " ", text).strip(" .,!?;:\"'")


def find_quote(quote: str, segments: list[Segment]) -> Segment | None:
    """The segment the quote comes from (or the first of two it spans); None when it isn't there."""
    q = _norm(quote)
    if len(q) < 3:
        return None
    for seg in segments:
        if q in _norm(seg.text):
            return seg
    for seg, nxt in itertools.pairwise(segments):
        if q in _norm(seg.text + " " + nxt.text):
            return seg
    return None


# --- Gate: explicit first-person commitments only (#112) ---------------------------------------------

_COMMIT_RE = re.compile(
    r"\b(i'll|i will|i'm going to|i am going to|i'm gonna|i need to|i have to|i've got to|i got to|i gotta"
    r"|i must|let me|remind me|note to self|action item|add (a )?task|to-?do)\b"
)
_ACCEPT_RE = re.compile(
    r"\b(will do|i can|i could|i'm on it|no problem|sounds good|got it)\b"
    r"|(?:^|[.,!?;:]\s*|\band\s+)(yes|yeah|yep|sure|ok|okay)\b"  # a bare yes, not "make sure you"
)
_AIMED_AT_AMIER_RE = re.compile(r"\b(i|i'm|i've|i'd|me|my|amier)\b")


def gate(kind: str, quote: str, following: str = "") -> str | None:
    """Why a candidate fails the commitment gate (a `task_drops` reason), or None when it passes.

    `following` is the next transcript line, where an agreement to a request often lands.
    """
    q = _norm(quote)
    if kind == "commitment" and not _COMMIT_RE.search(q):
        return "no_commitment_marker"
    said = (q, _norm(following))
    if kind == "accepted_request" and not any(_COMMIT_RE.search(t) or _ACCEPT_RE.search(t) for t in said):
        return "not_accepted"
    if kind == "unanswered_request" and not _AIMED_AT_AMIER_RE.search(q):
        return "second_person_advice"
    return None


def _following(seg: Segment, segments: list[Segment]) -> str:
    idx = next((i for i, s in enumerate(segments) if s is seg), -1)
    return segments[idx + 1].text if 0 <= idx < len(segments) - 1 else ""


# --- Ledger helpers -------------------------------------------------------------------------------

def cap_title(title: str) -> str:
    title = " ".join(title.split())
    if len(title) <= TITLE_MAX:
        return title
    cut = title[: TITLE_MAX - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-") + "…"


def store_embedding(db: Database, external_id: str, vector: list[float]) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO task_embeddings (external_id, vector_json) VALUES (?, ?);",
            (external_id, json.dumps(vector)),
        )
        conn.commit()


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _recent_open_vectors(db: Database, now: datetime) -> list[tuple[str, list[float]]]:
    cutoff = now - timedelta(days=DEDUPE_DAYS)
    out: list[tuple[str, list[float]]] = []
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT t.external_id, t.created_at, t.sent_at, e.vector_json
               FROM tasks t JOIN task_embeddings e USING (external_id)
               WHERE t.state IN ('pending', 'fallback', 'sent', 'open', 'flagged');"""
        ).fetchall()
    for r in rows:
        when = datetime.fromisoformat(r["sent_at"] or r["created_at"])
        if when >= cutoff:
            out.append((r["external_id"], json.loads(r["vector_json"])))
    return out


def _note_link(ctx: RunContext, item_id: str) -> str | None:
    if ctx.vault is None:
        return None
    with ctx.db.connect() as conn:
        row = conn.execute(
            """SELECT articulate_id, path FROM vault_notes
               WHERE source_item_id = ? AND kind = 'inbox' ORDER BY created_at LIMIT 1;""",
            (item_id,),
        ).fetchone()
    if row is None:
        return None
    rel = row["path"]
    ref = NoteRef(row["articulate_id"], ctx.vault.vault_dir / rel, rel, Path(rel).stem)
    return ctx.vault.obsidian_uri(ref)


def _set_state(db: Database, external_id: str, state: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE tasks SET state = ? WHERE external_id = ?;", (state, external_id))
        conn.commit()


def _log_drop(db: Database, item_id: str, cand: dict[str, Any], reason: str, now: datetime) -> None:
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO task_drops (item_id, title, quote, kind, confidence, reason, prompt_version,
                                       created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
            (item_id, str(cand.get("title") or ""), cand.get("quote"), cand.get("kind"),
             cand.get("confidence"), reason, PROMPT_VERSION, now.isoformat()),
        )
        conn.commit()


# --- Extraction -----------------------------------------------------------------------------------

def _eligible_items(db: Database) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT i.* FROM items i
               JOIN inbox_items x ON x.item_id = i.item_id
               LEFT JOIN task_extractions e ON e.item_id = i.item_id
               WHERE i.source = 'pocket' AND i.kind IN ('recording', 'span')
                 AND i.privacy = 'personal' AND x.status = 'done' AND e.item_id IS NULL
               ORDER BY i.created_at, i.item_id;"""
        ).fetchall()
    return [dict(r) for r in rows]


def _candidates(ctx: RunContext, item_id: str, rec: Recording) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for chunk in transcript_chunks(rec.segments):
        res = ctx.models.run(ModelRequest(
            step="personal_text", material=Privacy.PERSONAL, item_id=item_id, json_schema=SCHEMA,
            parts=[Part(text=PROMPT), Part(text=f"Recording: {rec.title}\n\n{chunk}")],
        ))
        parsed = res.parsed if res.parsed is not None else json.loads(res.text or "{}")
        out.extend(c for c in (parsed or {}).get("tasks") or [] if isinstance(c, dict))
    return out


def _extract_item(ctx: RunContext, item: dict[str, Any], summary: StepSummary) -> None:
    item_id = item["item_id"]
    payload = json.loads(item["payload_json"] or "{}")
    if payload.get("merged"):
        return  # merged copies are skipped (#2); the original carries the tasks
    rec = load_recording(item, payload)
    if rec is None:
        logger.info("tasks: no archived transcript yet for %s", item_id)
        return

    # Every model call happens before anything is written, so a hold leaves no partial state.
    try:
        cands = _candidates(ctx, item_id, rec) if rec.segments else []
        keep: list[tuple[dict[str, Any], Segment]] = []
        for c in cands:
            kind, conf = c.get("kind"), c.get("confidence")
            seg = find_quote(str(c.get("quote") or ""), rec.segments)
            if not str(c.get("title") or "").strip() or kind not in KINDS or conf not in CONFIDENCES:
                _log_drop(ctx.db, item_id, c, "invalid", ctx.now)
            elif kind in NEVER_TASKS:
                _log_drop(ctx.db, item_id, c, kind, ctx.now)
            elif seg is None:
                _log_drop(ctx.db, item_id, c, "quote_not_found", ctx.now)
            elif reason := gate(kind, str(c["quote"]), _following(seg, rec.segments)):
                _log_drop(ctx.db, item_id, c, reason, ctx.now)
            else:
                keep.append((c, seg))
        to_embed = [cap_title(c["title"]) for c, _ in keep if c["confidence"] != "low"]
        vectors: list[list[float]] = []
        if to_embed:
            emb = ctx.models.run(ModelRequest(
                step="embed_personal", material=Privacy.PERSONAL, item_id=item_id, embed=True,
                parts=[Part(text=t) for t in to_embed],
            ))
            vectors = list(emb.vectors or [])
    except Held as h:
        logger.info("tasks: extraction for %s held (%s)", item_id, h.reason)
        summary.held += 1
        return

    queue = ctx.tasks or TaskQueue(ctx.db)
    link = _note_link(ctx, item_id)
    rec_date = rec.recorded_at.astimezone(EASTERN).date().isoformat()
    recent = _recent_open_vectors(ctx.db, ctx.now)
    embedded = [i for i, (c, _) in enumerate(keep) if c["confidence"] != "low"]
    vec_for = dict(zip(embedded, vectors, strict=False))

    for idx, (c, seg) in enumerate(keep):
        start = int(seg.start)
        ext = f"task:{rec.recording_id}:{start}"
        title = cap_title(c["title"])
        quote = " ".join(str(c["quote"]).split())
        spoken = c.get("due_spoken")
        if spoken and _norm(spoken) not in _norm(quote):
            spoken = None  # due only when said out loud in that line
        due_at, has_time = resolve_due(spoken, rec.recorded_at)
        unanswered = c["kind"] == "unanswered_request"
        band = "medium" if unanswered and c["confidence"] == "high" else c["confidence"]
        if not queue.enqueue(TaskRequest(
            external_id=ext, title=title, due_at=due_at, has_due_time=has_time,
            notes=f"“{quote}”\n{rec.title} · {rec_date} · @{start // 60:02d}:{start % 60:02d}",
            link=link, origin_item_id=item_id, band=band, prompt_version=PROMPT_VERSION,
        )):
            continue  # same segment already gave a task
        if band == "low":
            _set_state(ctx.db, ext, "dropped")
            continue

        vec = vec_for.get(idx)
        dup = None
        if vec:
            dup = next((other for other, v in recent if _cosine(vec, v) >= DEDUPE_COSINE), None)
            store_embedding(ctx.db, ext, vec)
        if dup:
            _set_state(ctx.db, ext, "dropped")
            if ctx.runlog:
                ts = ctx.now.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M")
                ctx.runlog.line(f"- {ts} tasks: dropped near-duplicate {ext} of {dup}")
            continue
        if vec:
            recent.append((ext, vec))

        if band == "medium":
            _set_state(ctx.db, ext, "flagged")
            if ctx.flags is not None:
                prefix = "Unanswered request: " if unanswered else "Task? "
                ctx.flags.add(FLAG_KIND, f"{prefix}{title} — “{quote}” ({rec.title}, {rec_date})",
                              FLAG_CHOICES, item_id=ext)
        else:
            summary.done += 1

    with ctx.db.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO task_extractions
               (item_id, recording_id, prompt_version, extracted_at, candidates) VALUES (?, ?, ?, ?, ?);""",
            (item_id, rec.recording_id, PROMPT_VERSION, ctx.now.isoformat(), len(cands)),
        )
        conn.commit()


def extract_pending(ctx: RunContext) -> StepSummary:
    """Extract tasks from every finished, non-Private, non-class recording not read yet."""
    summary = StepSummary()
    for item in _eligible_items(ctx.db):
        _extract_item(ctx, item, summary)
        if summary.held:
            break  # the local model is unavailable; the rest wait for the next run
    return summary


# --- Weekly flag choices (tasks.medium) -----------------------------------------------------------

def resolve_flag(flag_id: str, chosen: str, ctx: RunContext) -> None:
    """Resolver registered for `tasks.medium`: ticking send queues the task, drop drops it."""
    with ctx.db.connect() as conn:
        row = conn.execute("SELECT item_id FROM flags WHERE flag_id = ?;", (flag_id,)).fetchone()
        if row is None or not row["item_id"]:
            return
        state = "pending" if chosen == "send" else "dropped"
        conn.execute("UPDATE tasks SET state = ? WHERE external_id = ? AND state = 'flagged';",
                     (state, row["item_id"]))
        conn.commit()


def apply_flag_choices(ctx: RunContext) -> int:
    """Apply resolved `tasks.medium` flags whose tasks are still flagged.

    core.flags (order 20) may resolve a tick before this slice's resolver is registered, so the
    choice recorded on the flag row is applied here too.
    """
    with ctx.db.connect() as conn:
        rows = conn.execute(
            """SELECT f.flag_id, f.chosen FROM flags f JOIN tasks t ON t.external_id = f.item_id
               WHERE f.kind = ? AND f.status = 'resolved' AND t.state = 'flagged';""",
            (FLAG_KIND,),
        ).fetchall()
    for r in rows:
        resolve_flag(r["flag_id"], r["chosen"] or "", ctx)
    return len(rows)
