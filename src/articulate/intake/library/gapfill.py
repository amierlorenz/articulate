"""Gap-fill concept generation and holding from unmatched guide items (§2.31, Issue #10)."""
import json
import logging
from dataclasses import dataclass
from typing import Any

from articulate.core.ids import slugify
from articulate.core.pipeline import RunContext

logger = logging.getLogger(__name__)


@dataclass
class GapFillResult:
    concept_id: str
    title: str
    status: str
    hold_reason: str
    has_source: bool
    details_json: str


def process_gap_fill(
    guide_item: dict[str, Any],
    course_key: str,
    unit_id: str | None,
    ctx: RunContext,
) -> GapFillResult:
    """
    Process an unmatched study guide item into a held gap-fill concept (§2.31).
    A printed answer serves as a cited source; if no source is found, adds a weekly flag.
    """
    quote = guide_item.get("quote", "").strip()
    ans_quote = (guide_item.get("answer_quote") or "").strip()
    file_id = guide_item.get("file_id") or "guide"
    page = guide_item.get("page")
    ans_page = guide_item.get("answer_page") or page
    kind = guide_item.get("kind") or "definition"

    # Generate a slug from the first few words of the quote
    raw_title = quote.split(".")[0].split("?")[0].strip()
    if len(raw_title) > 60:
        raw_title = raw_title[:60].rsplit(" ", 1)[0]
    if not raw_title:
        raw_title = f"Gap-fill item {guide_item.get('position', 0)}"

    slug = slugify(raw_title)
    pos = guide_item.get("position", 0)
    concept_id = f"{course_key}.gapfill.{slug[:24]}-{pos}"

    has_source = bool(ans_quote)
    if has_source:
        claim = {
            "text": ans_quote,
            "provenance": "cited",
            "citations": [
                {
                    "sourceFileId": file_id,
                    "page": ans_page,
                    "quote": ans_quote,
                    "status": "verified" if guide_item.get("answer_verified") else "unverified",
                }
            ],
        }
    else:
        claim = {
            "text": quote,
            "provenance": "gapFill",
            "citations": [],
        }

    details = {
        "explanationCitations": claim["citations"] if has_source else [],
        "aliases": [],
        "useWhen": [claim] if kind == "method" else [],
        "conditions": [],
        "workedExamples": [],
    }
    details_json = json.dumps(details)

    reasons = [
        {
            "signal": "study_guide",
            "quote": quote,
            "sourceFileId": file_id,
            "page": page,
        }
    ]
    reasons_json = json.dumps(reasons)
    now_str = ctx.now.isoformat()

    # Insert or update held concept in database
    with ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO concepts (
                concept_id, course_key, unit_id, kind, title, aliases_json,
                details_json, tier, tier_reasons_json, on_guide, status,
                hold_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '[]', ?, 'standard', ?, 1, 'held', 'gap_fill', ?, ?);
            """,
            (
                concept_id,
                course_key,
                unit_id,
                kind,
                raw_title,
                details_json,
                reasons_json,
                now_str,
                now_str,
            ),
        )
        conn.commit()

    # If no source was found, add a weekly flag on Intake flags.md
    if ctx.flags is not None:
        if not has_source:
            ctx.flags.add(
                kind="intake.gap_fill",
                subject=f"Gap-fill '{raw_title}' has no source passage",
                choices=[("keep", "keep"), ("drop", "drop")],
                item_id=concept_id,
            )
        else:
            ctx.flags.add(
                kind="intake.gap_fill",
                subject=f"Gap-fill candidate: '{raw_title}' (from printed answer)",
                choices=[("keep", "keep"), ("drop", "drop")],
                item_id=concept_id,
            )

    return GapFillResult(
        concept_id=concept_id,
        title=raw_title,
        status="held",
        hold_reason="gap_fill",
        has_source=has_source,
        details_json=details_json,
    )
