"""Pipeline runner for study guide and past exam Extract stage (§3.5, SPEC 2.21, 2.22, 2.25, 2.27, Issue #62)."""
import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from articulate.core.jobs import StepSummary
from articulate.core.models.runner import Held, ModelRequest, Part
from articulate.core.pipeline import Item, Privacy, RunContext, StageResult
from articulate.intake.cite import check_guide_item
from articulate.intake.guides.checks import (
    check_control_chars_and_math,
    check_exam_hint,
    check_reading_order_and_parts,
    check_unit_hints,
    check_unverified_share,
)
from articulate.intake.guides.join import join_answer_key
from articulate.intake.guides.mix import count_mix
from articulate.intake.guides.schema import SCHEMA

logger = logging.getLogger(__name__)


def _load_prompt_template() -> str:
    """Load prompts/guide-exam.md."""
    here = Path(__file__).resolve()
    for parent in [here.parent, here.parents[1], here.parents[2], here.parents[3], here.parents[4]]:
        p = parent / "prompts" / "guide-exam.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    return "You read one course document that is a study guide, a past exam, a practice test or an answer key.\nReturn JSON matching the schema."


def _get_course_exams(course_key: str, ctx: RunContext) -> list[str]:
    """Retrieve allowed exam names for course from DB and config (§3.14)."""
    exams: list[str] = []
    if ctx.db:
        with ctx.db.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM exams WHERE course_key = ? ORDER BY date ASC, name ASC;",
                (course_key,),
            ).fetchall()
            exams.extend(r["name"] for r in rows if r["name"])

    if ctx.config and ctx.config.courses:
        c_conf = ctx.config.courses.get(course_key)
        if c_conf and c_conf.exams:
            for ex in c_conf.exams:
                if ex.name and ex.name not in exams:
                    exams.append(ex.name)
    return exams


def _get_course_units(course_key: str, ctx: RunContext) -> list[str]:
    """Retrieve allowed unit names for course from DB and config (§3.14)."""
    units: list[str] = []
    if ctx.db:
        with ctx.db.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM units WHERE course_key = ? ORDER BY position ASC, name ASC;",
                (course_key,),
            ).fetchall()
            units.extend(r["name"] for r in rows if r["name"])

    if ctx.config and ctx.config.courses:
        c_conf = ctx.config.courses.get(course_key)
        if c_conf and c_conf.exams:
            for ex in c_conf.exams:
                for u in ex.units:
                    if u and u not in units:
                        units.append(u)
    return units


def _sub_pdf_bytes(pdf_path: Path, page_indices: list[int]) -> bytes:
    """Extract a subset of pages into a new PDF bytes stream."""
    src = pdfium.PdfDocument(pdf_path)
    dst = pdfium.PdfDocument.new()
    dst.import_pages(src, page_indices)
    buf = io.BytesIO()
    dst.save(buf)
    return buf.getvalue()


def _run_model_extract(
    pdf_path: Path,
    prompt: str,
    ctx: RunContext,
) -> dict[str, Any]:
    """Execute model extraction call, retrying once as two page-range calls if split is needed."""
    timeout = 1200
    if ctx.config and ctx.config.models and hasattr(ctx.config.models, "long_timeout_s"):
        timeout = ctx.config.models.long_timeout_s or 1200

    req = ModelRequest(
        step="guide_exam",
        material=Privacy.CLASS,
        parts=[
            Part(pdf=pdf_path),
            Part(text=prompt),
        ],
        json_schema=SCHEMA,
        timeout_s=timeout,
    )

    res = ctx.models.run(req)

    # Check if retry needed on MAX_TOKENS or schema rejection
    need_retry = res.finish_reason == "MAX_TOKENS" or (
        res.attempts and any("schema" in a.lower() for a in res.attempts)
    )

    if need_retry and pdf_path.exists():
        try:
            pdf_doc = pdfium.PdfDocument(pdf_path)
            n_pages = len(pdf_doc)
            if n_pages > 1:
                logger.warning("Retrying guide_exam as two page-range calls for %s (%d pages)", pdf_path, n_pages)
                cut = (n_pages + 1) // 2
                h1_pages = list(range(cut))
                h2_pages = list(range(cut, n_pages))

                h1_bytes = _sub_pdf_bytes(pdf_path, h1_pages)
                h2_bytes = _sub_pdf_bytes(pdf_path, h2_pages)

                # Call half 1
                req1 = ModelRequest(
                    step="guide_exam",
                    material=Privacy.CLASS,
                    parts=[
                        Part(pdf=pdf_path.with_name(f"{pdf_path.stem}_h1.pdf")),
                        Part(text=prompt),
                    ],
                    json_schema=SCHEMA,
                    timeout_s=timeout,
                )
                # Temporarily write half 1
                h1_file = pdf_path.with_name(f"{pdf_path.stem}_h1.pdf")
                h1_file.write_bytes(h1_bytes)
                try:
                    res1 = ctx.models.run(req1)
                finally:
                    h1_file.unlink(missing_ok=True)

                # Call half 2
                h2_file = pdf_path.with_name(f"{pdf_path.stem}_h2.pdf")
                h2_file.write_bytes(h2_bytes)
                req2 = ModelRequest(
                    step="guide_exam",
                    material=Privacy.CLASS,
                    parts=[
                        Part(pdf=h2_file),
                        Part(text=prompt),
                    ],
                    json_schema=SCHEMA,
                    timeout_s=timeout,
                )
                try:
                    res2 = ctx.models.run(req2)
                finally:
                    h2_file.unlink(missing_ok=True)

                out1 = res1.parsed or (json.loads(res1.text) if res1.text else {})
                out2 = res2.parsed or (json.loads(res2.text) if res2.text else {})

                # Remap page numbers in out2
                items2 = out2.get("items", [])
                for it in items2:
                    if it.get("page", 0) > 0:
                        it["page"] += cut
                    ans = it.get("answer")
                    if isinstance(ans, dict) and ans.get("page", 0) > 0:
                        ans["page"] += cut

                combined_items = out1.get("items", []) + items2
                doc = out1.get("document", {})
                return {"document": doc, "items": combined_items}
        except Exception as split_err:  # noqa: BLE001
            logger.warning("Split retry failed: %s; falling back to original output", split_err)

    if res.parsed:
        return res.parsed
    if res.text:
        return json.loads(res.text)
    return {}


def process_guide_file(item: Item, ctx: RunContext) -> StageResult:
    """
    Process one guide_or_exam item at stage 'guide_extract' (§3.5, SPEC 2.21, 2.22, 2.25, 2.27).

    - Model call on guide_exam chain (1200s timeout).
    - Checks: control chars, unbalanced $, exam/unit names, reading order/parts.
    - Citation checks through intake/cite.
    - Answer-key join if kind is answerKey.
    - Records items into guide_items table.
    - Advances item to 'library'.
    """
    course_key = item.course_key or ""
    file_id = item.version or item.native_id

    # 1. Fetch source file information from SQLite
    archive_path_str: str | None = None
    display_name = item.native_id

    if ctx.db:
        with ctx.db.connect() as conn:
            row = conn.execute(
                "SELECT display_name, archive_path, course_key FROM source_files WHERE file_id = ?;",
                (file_id,),
            ).fetchone()
            if row:
                display_name = row["display_name"] or display_name
                archive_path_str = row["archive_path"]
                course_key = row["course_key"] or course_key

    pdf_path = Path(archive_path_str) if archive_path_str else Path(f"{file_id}.pdf")

    # 2. Get allowed exam and unit lists
    allowed_exams = _get_course_exams(course_key, ctx)
    allowed_units = _get_course_units(course_key, ctx)

    # 3. Build prompt
    prompt_template = _load_prompt_template()
    lists_block = (
        "EXAM LIST:\n"
        + ("\n".join(f"- {e}" for e in allowed_exams) if allowed_exams else "(none)")
        + "\n\nUNIT LIST:\n"
        + ("\n".join(f"- {u}" for u in allowed_units) if allowed_units else "(none)")
    )
    full_prompt = f"{prompt_template.strip()}\n\n{lists_block}"

    # 4. Run model extraction
    try:
        output = _run_model_extract(pdf_path, full_prompt, ctx)
    except Held as held:
        logger.info("Model call held for item %s: %s", item.item_id, held.reason)
        return StageResult(outcome="hold", hold_reason=held.reason, detail=str(held))
    except Exception as e:
        logger.exception("Extraction model call failed for item %s", item.item_id)
        return StageResult(outcome="fail", detail=str(e))

    doc = output.get("document", {})
    raw_items = output.get("items", [])

    # 5. Deterministic checks
    problems = check_control_chars_and_math(output)
    if problems:
        logger.warning("Checks found formatting problems in %s: %s", display_name, problems)

    # Exam hint check
    exam_hint = doc.get("examHint", {})
    valid_exam, exam_name = check_exam_hint(exam_hint, allowed_exams)
    if not valid_exam:
        logger.info("Empty or invalid exam hint for %s (hint='%s') -> weekly flag", display_name, exam_name)
        if ctx.flags:
            reason = "empty" if not exam_name else f"invalid ('{exam_name}')"
            ctx.flags.add(
                "intake.invalid_exam_hint",
                f"File '{display_name}' has {reason} exam hint",
                choices=[("keep", "keep"), ("dismiss", "dismiss")],
                item_id=item.item_id,
            )
        # Inferred unit hints used only when exam is unknown (§3.5, SPEC 2.21)
        doc["unitHints"] = check_unit_hints(doc.get("unitHints", []), allowed_units)

    # Reading order and parts check
    check_reading_order_and_parts(raw_items)

    # 6. Verify quotes with intake/cite
    verified_items: list[dict[str, Any]] = []
    for raw_it in raw_items:
        it_dict = dict(raw_it)
        if ctx.db:
            check_guide_item(
                it_dict,
                file_id=file_id,
                db=ctx.db,
                models=ctx.models,
                flags=ctx.flags,
                file_path=pdf_path if pdf_path.exists() else None,
            )
        else:
            it_dict["verified"] = 1
            it_dict["verified_by"] = "text"
            it_dict["answer_verified"] = 1
        verified_items.append(it_dict)

    # 7. Check unverified share (>30% -> weekly flag)
    is_unv_flagged, unv_ratio, unv_count, total_count = check_unverified_share(verified_items)
    if is_unv_flagged:
        logger.warning(
            "File '%s' has %d/%d (%.0f%%) unverified items (>30%%) -> weekly flag",
            display_name,
            unv_count,
            total_count,
            unv_ratio * 100,
        )
        if ctx.flags:
            ctx.flags.add(
                "intake.guides_unverified",
                f"File '{display_name}' has {unv_count}/{total_count} ({unv_ratio:.0%}) unverified items (>30%)",
                choices=[("keep", "keep"), ("dismiss", "dismiss")],
                item_id=item.item_id,
            )

    # 8. Store items in guide_items table
    if ctx.db:
        with ctx.db.connect() as conn:
            for pos, it in enumerate(verified_items):
                num = str(it.get("number", ""))
                kind = str(it.get("kind", "topic"))
                page = int(it.get("page", 1))
                quote = str(it.get("quote", ""))

                ans = it.get("answer") if isinstance(it.get("answer"), dict) else {}
                ans_page = int(it.get("answer_page") or ans.get("page", 0))
                ans_quote = str(it.get("answer_quote") or ans.get("quote", ""))

                verified = int(it.get("verified", 0))
                verified_by = it.get("verified_by")
                ans_verified = int(it.get("answer_verified", 0))

                conn.execute(
                    """
                    INSERT OR REPLACE INTO guide_items (
                        file_id, position, number, kind, page, quote,
                        answer_page, answer_quote, verified, verified_by, answer_verified,
                        matched_concept_ids_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]');
                    """,
                    (
                        file_id,
                        pos,
                        num,
                        kind,
                        page,
                        quote,
                        ans_page,
                        ans_quote,
                        verified,
                        verified_by,
                        ans_verified,
                    ),
                )
            conn.commit()

    # 9. Answer-key join
    doc_kind = doc.get("kind", "")
    if doc_kind == "answerKey" and ctx.db:
        join_answer_key(
            ak_file_id=file_id,
            ak_display_name=display_name,
            ak_document=doc,
            ak_items=verified_items,
            course_key=course_key,
            db=ctx.db,
            flags=ctx.flags,
            models=ctx.models,
        )

    # 10. Question mix counts
    mix = count_mix(verified_items)
    logger.info(
        "Question mix for %s: %d items (%d counted, %d recognition, %d recall, %.0f%% recall)",
        display_name,
        mix.total_items,
        mix.counted_items,
        mix.recognition_items,
        mix.recall_items,
        mix.recall_ratio * 100,
    )

    return StageResult(
        outcome="advance",
        next_stage="library",
        detail=f"Extracted {len(verified_items)} guide items ({mix.counted_items} counted)",
    )


def step(ctx: RunContext) -> StepSummary:
    """
    Registry step: intake.guides (order 270, jobs: nightly, pass2).
    Processes items at stage 'guide_extract'.
    """
    summary = StepSummary()
    now_str = ctx.now.isoformat()

    if not ctx.db:
        return summary

    with ctx.db.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM items
            WHERE stage = 'guide_extract' AND status IN ('pending', 'running', 'held')
            ORDER BY priority ASC, created_at ASC;
            """
        ).fetchall()

    for r in rows:
        item_id_val = r["item_id"]
        item = Item(
            item_id=item_id_val,
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

        # Mark item running
        with ctx.db.connect() as conn:
            conn.execute(
                "UPDATE items SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE item_id = ?;",
                (now_str, item_id_val),
            )
            conn.execute(
                "INSERT INTO item_events (item_id, run_id, at, stage, event) VALUES (?, ?, ?, 'guide_extract', 'started');",
                (item_id_val, ctx.run_id, now_str),
            )
            conn.commit()

        res = process_guide_file(item, ctx)
        end_str = datetime.now(UTC).isoformat()

        with ctx.db.connect() as conn:
            if res.outcome == "advance":
                conn.execute(
                    """
                    UPDATE items
                    SET stage = ?, status = 'pending', consecutive_failures = 0, updated_at = ?
                    WHERE item_id = ?;
                    """,
                    (res.next_stage, end_str, item_id_val),
                )
                conn.execute(
                    "INSERT INTO item_events (item_id, run_id, at, stage, event, detail) VALUES (?, ?, ?, 'guide_extract', 'advance', ?);",
                    (item_id_val, ctx.run_id, end_str, res.detail),
                )
                summary.done += 1
            elif res.outcome == "hold":
                conn.execute(
                    """
                    UPDATE items
                    SET status = 'held', hold_reason = ?, hold_since = COALESCE(hold_since, ?), updated_at = ?
                    WHERE item_id = ?;
                    """,
                    (res.hold_reason, end_str, end_str, item_id_val),
                )
                conn.execute(
                    "INSERT INTO item_events (item_id, run_id, at, stage, event, detail) VALUES (?, ?, ?, 'guide_extract', 'held', ?);",
                    (item_id_val, ctx.run_id, end_str, f"Hold reason: {res.hold_reason}"),
                )
                summary.held += 1
            else:
                conn.execute(
                    """
                    UPDATE items
                    SET status = 'failed', consecutive_failures = consecutive_failures + 1,
                        last_error = ?, updated_at = ?
                    WHERE item_id = ?;
                    """,
                    (res.detail, end_str, item_id_val),
                )
                conn.execute(
                    "INSERT INTO item_events (item_id, run_id, at, stage, event, detail) VALUES (?, ?, ?, 'guide_extract', 'failed', ?);",
                    (item_id_val, ctx.run_id, end_str, res.detail),
                )
                summary.failed += 1
            conn.commit()

    return summary
