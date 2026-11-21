"""Extract runner: deck extraction, item handler, and job step (§3.5, SPEC 2.18–2.20, #61)."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from articulate.core.jobs import StepSummary
from articulate.core.models.runner import Held, ModelRequest, ModelRunner, Part
from articulate.core.pipeline import Item, Privacy, RunContext, StageResult
from articulate.intake import cite
from articulate.intake.extract import checks, split
from articulate.intake.extract.schema import SCHEMA

logger = logging.getLogger(__name__)


class ExtractFailed(Exception):
    """Raised when extraction fails fatally."""


@dataclass
class ExtractResult:
    calls: int
    ranges: list[tuple[int, int]]
    output: dict[str, Any]
    stats: dict[str, int]
    logged: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)


def load_prompt() -> str:
    """Load the slide extract prompt from prompts/extract.md."""
    candidates = [
        Path(__file__).resolve().parents[4] / "prompts" / "extract.md",
        Path("prompts/extract.md").resolve(),
    ]
    for p in candidates:
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("prompts/extract.md not found")


def extract_deck(
    deck_path: Path,
    models: ModelRunner,
    work: Path,
    material: Privacy = Privacy.CLASS,
    page_subset: list[int] | None = None,
    context_titles: list[str] | None = None,
) -> ExtractResult:
    """
    Extract concepts and contrast groups from a slide deck PDF.
    Splits long decks (>40 pages) into sub-ranges, retries MAX_TOKENS / schema 400 with re-split,
    checks output, remaps citations, and merges same-title concepts.
    """
    deck_path = Path(deck_path).resolve()
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(str(deck_path))
    total_pages = len(pdf)

    if page_subset:
        ranges = [(min(page_subset), max(page_subset))]
    else:
        ranges = split.plan_ranges(total_pages)

    prompt_base = load_prompt()
    if context_titles:
        prompt_base += "\n\nContext concepts already known: " + ", ".join(context_titles)

    queue: list[tuple[tuple[int, int], int]] = [(r, 0) for r in ranges]
    completed_ranges: list[tuple[int, int]] = []
    remapped_parts: list[dict[str, Any]] = []
    logged: list[str] = []
    models_used: list[str] = []
    call_count = 0

    while queue:
        (first, last), depth = queue.pop(0)

        if first == 1 and last == total_pages and not page_subset:
            call_pdf = deck_path
            note = ""
        else:
            call_pdf = work / f"part_{first}_{last}.pdf"
            split.sub_pdf(deck_path, first, last, call_pdf)
            note = split.range_note(first, last, total_pages)

        full_prompt = prompt_base + note
        req = ModelRequest(
            step="extract",
            material=material,
            parts=[Part(pdf=call_pdf), Part(text=full_prompt)],
            json_schema=SCHEMA,
        )

        call_count += 1
        res = models.run(req)

        finish = res.finish_reason or ""
        if finish in ("MAX_TOKENS", "schema_rejected", "http_400", "max_tokens", "LENGTH"):
            if depth >= 1:
                raise ExtractFailed(f"Range {first}–{last} failed with {finish} after re-split")
            h1, h2 = split.halve((first, last))
            logged.append(
                f"Range {first}–{last} {finish}; re-split into {h1[0]}–{h1[1]} and {h2[0]}–{h2[1]}"
            )
            queue.insert(0, (h2, depth + 1))
            queue.insert(0, (h1, depth + 1))
            continue

        if res.model:
            models_used.append(res.model)

        raw_out = res.parsed if res.parsed is not None else json.loads(res.text)
        checked_out, report = checks.check_output(raw_out)
        logged.extend(report.logged)

        remapped_out = split.remap(checked_out, first_page=first)
        remapped_parts.append(remapped_out)
        completed_ranges.append((first, last))

    merged_out, stats = split.merge(remapped_parts)
    return ExtractResult(
        calls=call_count,
        ranges=completed_ranges,
        output=merged_out,
        stats=stats,
        logged=logged,
        models=models_used,
    )


def extract_item(item: Item, ctx: RunContext) -> StageResult:
    """
    Process an item at the 'extract' stage:
    1. Resolve PDF path from source_files
    2. Run extract_deck
    3. Run cite.check on all citations
    4. Write held concepts and contrast_groups to DB for library
    5. Advance to 'library'
    """
    file_id = item.version
    with ctx.db.connect() as conn:
        row = conn.execute(
            "SELECT file_id, course_key, archive_path FROM source_files WHERE file_id = ? OR slot = ?",
            (file_id, item.native_id),
        ).fetchone()

    if not row or not row["archive_path"]:
        return StageResult(
            outcome="fail", detail=f"Source file {file_id} not found in source_files"
        )

    deck_path = Path(row["archive_path"])
    if not deck_path.exists() or deck_path.suffix.lower() != ".pdf":
        return StageResult(
            outcome="fail", detail=f"PDF file does not exist or is not a PDF: {deck_path}"
        )

    from articulate.core.paths import work_dir

    work_path = work_dir(ctx.run_id) / f"extract_{item.item_id.replace(':', '_')}"

    try:
        res = extract_deck(deck_path, models=ctx.models, work=work_path, material=item.privacy)
    except Held as e:
        return StageResult(outcome="hold", hold_reason=e.reason, detail=str(e))
    except Exception as e:
        logger.exception("extract_deck failed for item %s", item.item_id)
        return StageResult(outcome="fail", detail=f"Extract failed: {e}")

    course_key = item.course_key or row["course_key"] or ""
    source_file_id = f"f.{file_id[:16]}" if not file_id.startswith("f.") else file_id

    def _check_citation(ct: dict[str, Any]) -> dict[str, Any]:
        quote = ct.get("quote", "")
        page = ct.get("page", 0)
        c_res = cite.check(
            quote=quote,
            file_id=row["file_id"],
            page=page,
            kind="slides",
            db=ctx.db,
            models=ctx.models,
            flags=ctx.flags,
        )
        return {
            "sourceFileId": source_file_id,
            "page": page,
            "startSec": None,
            "endSec": None,
            "quote": quote,
            "status": c_res.status,
            "verifiedBy": c_res.verified_by,
        }

    # Clean up previous held concepts / groups from this file on rerun
    with ctx.db.connect() as conn:
        existing_held = conn.execute(
            "SELECT concept_id, details_json FROM concepts WHERE course_key = ? AND status = 'held'",
            (course_key,),
        ).fetchall()
        cids_to_clean = [
            r["concept_id"] for r in existing_held if source_file_id in (r["details_json"] or "")
        ]
        if cids_to_clean:
            ph = ",".join("?" for _ in cids_to_clean)
            conn.execute(f"DELETE FROM concepts WHERE concept_id IN ({ph})", cids_to_clean)

            # Clean up held contrast groups that referenced deleted concepts
            g_rows = conn.execute(
                "SELECT group_id, member_ids_json FROM contrast_groups WHERE course_key = ? AND status = 'held'",
                (course_key,),
            ).fetchall()
            gids_to_clean = [
                gr["group_id"]
                for gr in g_rows
                if any(cid in json.loads(gr["member_ids_json"] or "[]") for cid in cids_to_clean)
            ]
            if gids_to_clean:
                gph = ",".join("?" for _ in gids_to_clean)
                conn.execute(
                    f"DELETE FROM contrast_groups WHERE group_id IN ({gph})", gids_to_clean
                )
        conn.commit()

    # Get existing concept IDs in DB
    with ctx.db.connect() as conn:
        existing_cids = {
            r[0]
            for r in conn.execute(
                "SELECT concept_id FROM concepts WHERE course_key = ?", (course_key,)
            ).fetchall()
        }

    from articulate.core.ids import slugify

    key_to_concept_id: dict[str, str] = {}
    now_str = ctx.now.isoformat()

    with ctx.db.connect() as conn:
        for c in res.output.get("concepts", []):
            base_slug = slugify(c["title"])
            cid = f"{course_key}.{base_slug}"
            if cid in existing_cids:
                idx = 2
                while f"{cid}-{idx}" in existing_cids:
                    idx += 1
                cid = f"{cid}-{idx}"
            existing_cids.add(cid)
            key_to_concept_id[c["key"]] = cid

            exp = c.get("explanation") or {}
            exp_cites = [_check_citation(ct) for ct in exp.get("citations", [])]

            details: dict[str, Any] = {
                "explanation": exp.get("text", ""),
                "explanationCitations": exp_cites,
                "aliases": c.get("aliases", []),
                "useWhen": [],
                "conditions": [],
                "workedExamples": [],
                "slideHints": c.get("slideHints", []),
            }

            for f_name in ("useWhen", "conditions", "workedExamples"):
                claims_list = []
                for cl in c.get(f_name, []):
                    cl_cites = [_check_citation(ct) for ct in cl.get("citations", [])]
                    claims_list.append(
                        {
                            "text": cl.get("text", ""),
                            "provenance": "gapFill" if cl.get("gapFill") else "cited",
                            "citations": cl_cites,
                        }
                    )
                details[f_name] = claims_list

            conn.execute(
                """
                INSERT INTO concepts (
                    concept_id, course_key, kind, title, aliases_json, details_json,
                    confidence, confidence_note, status, hold_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'held', 'library', ?, ?)
                """,
                (
                    cid,
                    course_key,
                    c.get("kind", "definition"),
                    c.get("title", ""),
                    json.dumps(c.get("aliases", [])),
                    json.dumps(details),
                    c.get("confidence", "high"),
                    c.get("confidenceNote", ""),
                    now_str,
                    now_str,
                ),
            )

        for g in res.output.get("contrastGroups", []):
            g_slug = slugify(g["name"])
            group_id = f"{course_key}.g.{g_slug}"
            members = [
                key_to_concept_id[mk] for mk in g.get("memberKeys", []) if mk in key_to_concept_id
            ]
            if len(members) < 2:
                continue

            contrasts = []
            for ct in g.get("contrasts", []):
                a_id = key_to_concept_id.get(ct.get("a", ""))
                b_id = key_to_concept_id.get(ct.get("b", ""))
                if a_id and b_id:
                    df = ct.get("decidingFeature") or {}
                    df_cites = [_check_citation(ct_cite) for ct_cite in df.get("citations", [])]
                    contrasts.append(
                        {
                            "conceptA": a_id,
                            "conceptB": b_id,
                            "decidingFeature": {
                                "text": df.get("text", ""),
                                "provenance": "gapFill" if df.get("gapFill") else "cited",
                                "citations": df_cites,
                            },
                        }
                    )

            conn.execute(
                """
                INSERT INTO contrast_groups (
                    group_id, course_key, name, question, member_ids_json, contrasts_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'held')
                ON CONFLICT(group_id) DO UPDATE SET
                    member_ids_json = excluded.member_ids_json,
                    contrasts_json = excluded.contrasts_json,
                    status = excluded.status;
                """,
                (
                    group_id,
                    course_key,
                    g.get("name", ""),
                    g.get("question", ""),
                    json.dumps(members),
                    json.dumps(contrasts),
                ),
            )

        conn.commit()

    return StageResult(
        outcome="advance",
        next_stage="library",
        detail=f"Extracted {len(res.output.get('concepts', []))} concepts and {len(res.output.get('contrastGroups', []))} groups",
    )


def step(ctx: RunContext) -> StepSummary:
    """
    Step intake.extract (jobs: nightly, pass2, order=260).
    Processes items at stage 'extract' and status 'pending' / 'running'.
    """
    summary = StepSummary()
    now_str = ctx.now.isoformat()

    with ctx.db.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM items
            WHERE stage = 'extract' AND status IN ('pending', 'running')
            ORDER BY priority ASC, created_at ASC;
            """
        ).fetchall()

    if not rows:
        return summary

    for r in rows:
        item = Item(
            item_id=r["item_id"],
            source=r["source"],
            kind=r["kind"],
            native_id=r["native_id"],
            version=r["version"] or "",
            course_key=r["course_key"],
            privacy=Privacy(r["privacy"]) if r["privacy"] else Privacy.CLASS,
            exam_key=r["exam_key"],
            priority=r["priority"] or 0.0,
            payload=json.loads(r["payload_json"]) if r["payload_json"] else {},
        )

        with ctx.db.connect() as conn:
            conn.execute(
                "UPDATE items SET status = 'running', updated_at = ? WHERE item_id = ?",
                (now_str, item.item_id),
            )
            conn.execute(
                """
                INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                VALUES (?, ?, ?, 'extract', 'started', 'Starting extraction')
                """,
                (item.item_id, ctx.run_id, now_str),
            )
            conn.commit()

        res = extract_item(item, ctx)

        with ctx.db.connect() as conn:
            if res.outcome == "advance":
                conn.execute(
                    """
                    UPDATE items
                    SET stage = ?, status = 'pending', consecutive_failures = 0, updated_at = ?
                    WHERE item_id = ?
                    """,
                    (res.next_stage, now_str, item.item_id),
                )
                conn.execute(
                    """
                    INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                    VALUES (?, ?, ?, 'extract', 'advance', ?)
                    """,
                    (item.item_id, ctx.run_id, now_str, res.detail or "Advanced to library"),
                )
                summary.done += 1
            elif res.outcome == "hold":
                conn.execute(
                    """
                    UPDATE items
                    SET status = 'held', hold_reason = ?, updated_at = ?
                    WHERE item_id = ?
                    """,
                    (res.hold_reason, now_str, item.item_id),
                )
                conn.execute(
                    """
                    INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                    VALUES (?, ?, ?, 'extract', 'hold', ?)
                    """,
                    (item.item_id, ctx.run_id, now_str, res.detail or f"Held: {res.hold_reason}"),
                )
                summary.held += 1
            elif res.outcome == "fail":
                conn.execute(
                    """
                    UPDATE items
                    SET consecutive_failures = consecutive_failures + 1, updated_at = ?
                    WHERE item_id = ?
                    """,
                    (now_str, item.item_id),
                )
                conn.execute(
                    """
                    INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                    VALUES (?, ?, ?, 'extract', 'fail', ?)
                    """,
                    (item.item_id, ctx.run_id, now_str, res.detail or "Extraction failed"),
                )
                summary.failed += 1
            elif res.outcome == "skip":
                conn.execute(
                    "UPDATE items SET status = 'skipped', updated_at = ? WHERE item_id = ?",
                    (now_str, item.item_id),
                )
            conn.commit()

    return summary
