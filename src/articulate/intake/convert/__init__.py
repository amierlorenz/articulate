"""Deck and document conversion, page text, OCR and damaged-layer signals (articulate#59, §3.5)."""

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from articulate.core.jobs import StepSummary
from articulate.core.paths import archive_pages_path, get_data_dir
from articulate.core.paths import work_dir as get_work_dir
from articulate.core.pipeline import Item, Privacy, RunContext, StageResult
from articulate.intake.convert.deckcraft import DeckCraftFallbackError, convert_pptx_deckcraft
from articulate.intake.convert.keynote import (
    KeynoteHoldError,
    convert_pptx_keynote,
    extract_pptx_notes_titles,
)
from articulate.intake.convert.pdftext import extract_pdf_pages
from articulate.intake.convert.wordcraft import convert_wordcraft

logger = logging.getLogger(__name__)


class ConvertHandler:
    """Pipeline Handler for stage 'convert' (§3.5)."""

    name = "intake.convert"
    kinds: ClassVar[set[str]] = {"slides", "guide_or_exam"}
    stages: ClassVar[list[str]] = ["convert"]

    def run(self, item: Item, stage: str, ctx: RunContext) -> StageResult:
        return convert_item(item, ctx)


def convert_item(item: Item, ctx: RunContext) -> StageResult:
    """
    Converts one item at stage 'convert':
    Turns picked file into PDF + per-page text, damaged-layer signals, and OCR where needed (§2.11–2.17).
    Advances slides -> extract, guide_or_exam -> guide_extract.
    """
    course_key = item.course_key or "default"
    file_id = item.version or item.native_id

    # 1. Locate source file
    archive_file: Path | None = None
    with ctx.db.connect() as conn:
        row = conn.execute(
            "SELECT archive_path FROM source_files WHERE file_id = ?;",
            (file_id,),
        ).fetchone()
        if row and row["archive_path"]:
            p = Path(row["archive_path"])
            if p.exists():
                archive_file = p

    if archive_file is None:
        # Search in archive folder
        arc_dir = get_data_dir() / "archive" / course_key
        if arc_dir.exists():
            for candidate in arc_dir.glob(f"{file_id}.*"):
                if candidate.suffix != ".pages.json":
                    archive_file = candidate
                    break

    if archive_file is None or not archive_file.exists():
        return StageResult(
            outcome="fail", detail=f"Source file not found in archive for file_id {file_id}"
        )

    ext = archive_file.suffix.lower()
    run_work = get_work_dir(ctx.run_id) / item.item_id
    run_work.mkdir(parents=True, exist_ok=True)
    converted_pdf = run_work / f"{file_id}.pdf"

    inspect_data: dict[str, Any] | None = None

    try:
        # 2. Conversion
        if ext == ".pptx":
            try:
                # Primary: DeckCraft (§2.11)
                inspect_data, _ = convert_pptx_deckcraft(archive_file, converted_pdf)
            except DeckCraftFallbackError as fe:
                logger.info("DeckCraft fallback triggered for %s (%s); trying Keynote", file_id, fe)
                # Fallback: Keynote (§2.12)
                try:
                    convert_pptx_keynote(archive_file, converted_pdf)
                    inspect_data = extract_pptx_notes_titles(archive_file)
                except KeynoteHoldError as khe:
                    return StageResult(outcome="hold", hold_reason=khe.hold_reason, detail=str(khe))
        elif ext in (".docx", ".doc", ".rtf"):
            # WordCraft (§2.14)
            convert_wordcraft(archive_file, converted_pdf)
        elif ext == ".pdf":
            # Already PDF (§2.14)
            converted_pdf = archive_file
        else:
            return StageResult(outcome="fail", detail=f"Unsupported file extension {ext}")

        # 3. Extract per-page text, signals, OCR (§2.14–2.16)
        pages_data = extract_pdf_pages(converted_pdf, inspect_data=inspect_data, work_dir=run_work)
        page_count = len(pages_data)

        # 4. Save to SQLite (§3.4)
        with ctx.db.connect() as conn:
            conn.execute(
                "UPDATE source_files SET page_count = ? WHERE file_id = ?;",
                (page_count, file_id),
            )
            for p in pages_data:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO pages (
                        file_id, page, page_hash, layer_text, layer_untrusted,
                        ocr_text, title, notes, hidden
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        file_id,
                        p["page"],
                        p["page_hash"],
                        p["text"],
                        p["layer_untrusted"],
                        p["ocr_text"],
                        p["title"],
                        p["notes"],
                        p["hidden"],
                    ),
                )
            conn.commit()

        # 5. Write archive .pages.json (§2.17)
        pages_json_path = archive_pages_path(course_key, file_id)
        pages_json_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_json = pages_json_path.with_suffix(".tmp")
        payload = {
            "file_id": file_id,
            "page_count": page_count,
            "pages": pages_data,
        }
        tmp_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_json.replace(pages_json_path)

        # 6. Advance stage (§3.5)
        next_stage = "extract" if item.kind == "slides" else "guide_extract"
        return StageResult(
            outcome="advance", next_stage=next_stage, detail=f"Converted {page_count} pages"
        )

    except Exception as e:
        logger.exception("Conversion failed for item %s", item.item_id)
        return StageResult(outcome="fail", detail=str(e))
    finally:
        # Work files deleted at run end (§2.17)
        if run_work.exists():
            shutil.rmtree(run_work, ignore_errors=True)


def convert_step(ctx: RunContext) -> StepSummary:
    """
    Registry step: intake.convert (order 220, jobs: nightly + pass2).
    Processes items at stage 'convert'.
    """
    summary = StepSummary()
    now_str = ctx.now.isoformat()

    with ctx.db.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM items
            WHERE stage = 'convert' AND status IN ('pending', 'running', 'held')
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
            privacy=Privacy(r["privacy"]),
            exam_key=r["exam_key"],
            priority=r["priority"] or 0.0,
        )

        # Mark running
        with ctx.db.connect() as conn:
            conn.execute(
                "UPDATE items SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE item_id = ?;",
                (now_str, item_id_val),
            )
            conn.execute(
                "INSERT INTO item_events (item_id, run_id, at, stage, event) VALUES (?, ?, ?, 'convert', 'started');",
                (item_id_val, ctx.run_id, now_str),
            )
            conn.commit()

        res = convert_item(item, ctx)
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
                    "INSERT INTO item_events (item_id, run_id, at, stage, event, detail) VALUES (?, ?, ?, 'convert', 'advance', ?);",
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
                    "INSERT INTO item_events (item_id, run_id, at, stage, event, detail) VALUES (?, ?, ?, 'convert', 'held', ?);",
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
                    "INSERT INTO item_events (item_id, run_id, at, stage, event, detail) VALUES (?, ?, ?, 'convert', 'failed', ?);",
                    (item_id_val, ctx.run_id, end_str, res.detail),
                )
                summary.failed += 1
            conn.commit()

    # Clean up top-level work dir if empty
    work_base = get_work_dir(ctx.run_id)
    if work_base.exists() and not any(work_base.iterdir()):
        try:
            work_base.rmdir()
        except OSError:
            pass

    return summary
