"""Shape intake slice: prompt generation, verify, and regeneration (§3.5, §3.6, §3.7, SPEC-requirements 2.32, 2.33, 2.34, 2.38, Issue #64)."""
import json
import logging
from typing import Any

from articulate.core.jobs import StepSummary
from articulate.core.pipeline import RunContext
from articulate.intake.shape.checks import (
    check_alias_leak,
    check_options_resolve,
    check_style_overlap,
    run_deterministic_checks,
    validate_prompt_payload,
)
from articulate.intake.shape.generate import (
    generate_prompts_for_concept,
    get_style_examples,
)
from articulate.intake.shape.recipes import (
    calculate_free_recall_ratio,
    get_recipe,
    is_free_recall,
)
from articulate.intake.shape.regenerate import (
    RegenerateResult,
    regenerate,
)
from articulate.intake.shape.verify import verify_prompt

logger = logging.getLogger(__name__)


def shape_course(course_key: str, ctx: RunContext) -> int:
    """
    Generate and save prompts for all ready concepts in a course (§2.32, §2.34).
    Returns count of generated prompts.
    """
    now_str = ctx.now.isoformat()

    # 1. Load concepts
    concepts: list[dict[str, Any]] = []
    with ctx.db.connect() as conn:
        rows = conn.execute(
            """
            SELECT concept_id, course_key, unit_id, kind, title, aliases_json,
                   details_json, tier, tier_reasons_json, on_guide, status, hold_reason
            FROM concepts
            WHERE course_key = ? AND status != 'retired' AND status != 'superseded'
            ORDER BY created_at ASC;
            """,
            (course_key,),
        ).fetchall()

        for r in rows:
            try:
                details = json.loads(r["details_json"]) if r["details_json"] else {}
            except Exception:  # noqa: BLE001
                details = {}
            try:
                aliases = json.loads(r["aliases_json"]) if r["aliases_json"] else []
            except Exception:  # noqa: BLE001
                aliases = []
            if not aliases and "aliases" in details:
                aliases = details["aliases"]

            concepts.append({
                "concept_id": r["concept_id"],
                "course_key": r["course_key"],
                "unit_id": r["unit_id"],
                "kind": r["kind"],
                "title": r["title"],
                "aliases": aliases,
                "details": details,
                "tier": r["tier"],
                "status": r["status"],
                "hold_reason": r["hold_reason"],
            })

    # 2. Load contrast groups
    contrast_groups: list[dict[str, Any]] = []
    with ctx.db.connect() as conn:
        cg_rows = conn.execute(
            """
            SELECT group_id, course_key, name, question, member_ids_json, contrasts_json, status
            FROM contrast_groups
            WHERE course_key = ?;
            """,
            (course_key,),
        ).fetchall()

        for r in cg_rows:
            try:
                m_ids = json.loads(r["member_ids_json"]) if r["member_ids_json"] else []
            except Exception:  # noqa: BLE001
                m_ids = []
            contrast_groups.append({
                "group_id": r["group_id"],
                "name": r["name"],
                "question": r["question"],
                "member_ids": m_ids,
                "status": r["status"],
            })

    valid_concept_ids = {c["concept_id"] for c in concepts}
    style_examples = get_style_examples(course_key, ctx)

    total_prompts = 0

    # 3. Generate prompts for ready concepts
    for concept in concepts:
        cid = concept["concept_id"]
        status = concept.get("status")
        hold_reason = concept.get("hold_reason")

        # Held concepts with other reasons (gap_fill, weekly_review, etc.) hold their prompts (§2.34)
        if status == "held" and hold_reason not in (None, "", "shape"):
            logger.info("Concept '%s' is held with reason '%s'; no prompts generated (§2.34)", cid, hold_reason)
            continue

        if concept.get("tier") == "out":
            continue

        prompts = generate_prompts_for_concept(
            concept,
            course_key,
            ctx,
            valid_concept_ids=valid_concept_ids,
            contrast_groups=contrast_groups,
            style_examples=style_examples,
        )

        with ctx.db.connect() as conn:
            for p in prompts:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO prompts (
                        prompt_id, concept_id, kind, content_json, status,
                        generated_by, prompt_version, created_at, shipped_in
                    ) VALUES (?, ?, ?, ?, 'active', ?, 'v2', ?, NULL);
                    """,
                    (
                        p["prompt_id"],
                        cid,
                        p["kind"],
                        json.dumps(p["content"]),
                        "shape",
                        now_str,
                    ),
                )
                total_prompts += 1

            # Update concept status to active, clear hold_reason
            conn.execute(
                """
                UPDATE concepts
                SET status = 'active', hold_reason = NULL, updated_at = ?
                WHERE concept_id = ?;
                """,
                (now_str, cid),
            )
            conn.commit()

    return total_prompts


def step(ctx: RunContext) -> StepSummary:
    """
    Step handler for 'intake.shape' (order 320, jobs nightly + pass2) (§3.5, §3.12).
    Processes items at stage 'shape', generates prompts, and advances to 'export'.
    """
    summary = StepSummary()
    now_str = ctx.now.isoformat()

    with ctx.db.connect() as conn:
        items = conn.execute(
            """
            SELECT item_id, source, kind, native_id, version, course_key, status
            FROM items
            WHERE stage = 'shape' AND status = 'pending'
            ORDER BY priority ASC, created_at ASC;
            """
        ).fetchall()

    for item_row in items:
        item_id_val = item_row["item_id"]
        course_key = item_row["course_key"]

        try:
            with ctx.db.connect() as conn:
                conn.execute(
                    "UPDATE items SET status = 'running', updated_at = ? WHERE item_id = ?;",
                    (now_str, item_id_val),
                )
                conn.commit()

            if course_key:
                shape_course(course_key, ctx)

            # Advance item to stage 'export' (§3.5)
            with ctx.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE items
                    SET stage = 'export', status = 'pending', consecutive_failures = 0, updated_at = ?
                    WHERE item_id = ?;
                    """,
                    (now_str, item_id_val),
                )
                conn.execute(
                    """
                    INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                    VALUES (?, ?, ?, 'shape', 'advance', 'Advanced to export');
                    """,
                    (item_id_val, ctx.run_id, now_str),
                )
                conn.commit()

            summary.done += 1

        except Exception as e:  # noqa: BLE001
            logger.error("Error in shape stage for item %s: %s", item_id_val, e)
            summary.failed += 1

    return summary


__all__ = [
    "RegenerateResult",
    "calculate_free_recall_ratio",
    "check_alias_leak",
    "check_options_resolve",
    "check_style_overlap",
    "generate_prompts_for_concept",
    "get_recipe",
    "get_style_examples",
    "is_free_recall",
    "regenerate",
    "run_deterministic_checks",
    "shape_course",
    "step",
    "validate_prompt_payload",
    "verify_prompt",
]
