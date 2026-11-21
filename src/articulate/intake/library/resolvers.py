"""Resolvers for intake flags: keep / merge / drop (§2.29, §3.9)."""
import json
import logging

from articulate.core.flags import Flags
from articulate.core.pipeline import RunContext

logger = logging.getLogger(__name__)


def resolve_merge(flag_id: str, choice: str, ctx: RunContext) -> None:
    """
    Resolver for 'intake.merge' flags (keep / merge / drop).
    Ticking a box applies once to the candidate concept (§2.29).
    """
    now_str = ctx.now.isoformat()
    with ctx.db.connect() as conn:
        row = conn.execute("SELECT * FROM flags WHERE flag_id = ?;", (flag_id,)).fetchone()
        if not row:
            return
        candidate_id = row["item_id"]

        cand_row = conn.execute(
            "SELECT * FROM concepts WHERE concept_id = ?;", (candidate_id,)
        ).fetchone()
        if not cand_row:
            return

        choice_norm = choice.strip().lower()

        if choice_norm == "keep":
            # Keep as separate concept: unhold from review and mark ready for shape
            conn.execute(
                """
                UPDATE concepts
                SET status = 'held', hold_reason = 'shape', updated_at = ?
                WHERE concept_id = ?;
                """,
                (now_str, candidate_id),
            )
            conn.commit()
            logger.info("Kept concept %s as separate concept", candidate_id)

        elif choice_norm == "merge":
            # Merge into library target concept
            # Find the target from subject or from judge notes
            # Look for existing concept with similar title in course
            target_row = conn.execute(
                """
                SELECT * FROM concepts
                WHERE course_key = ? AND concept_id != ? AND status != 'retired' AND status != 'superseded'
                ORDER BY created_at ASC LIMIT 1;
                """,
                (cand_row["course_key"], candidate_id),
            ).fetchone()

            if target_row:
                target_id = target_row["concept_id"]
                # Enrich target details with candidate details
                try:
                    cand_details = json.loads(cand_row["details_json"]) if cand_row["details_json"] else {}
                except Exception:  # noqa: BLE001
                    cand_details = {}

                try:
                    target_details = json.loads(target_row["details_json"]) if target_row["details_json"] else {}
                except Exception:  # noqa: BLE001
                    target_details = {}

                # Merge aliases
                c_aliases = set(cand_details.get("aliases", []))
                t_aliases = set(target_details.get("aliases", []))
                merged_aliases = list(t_aliases.union(c_aliases))
                target_details["aliases"] = merged_aliases

                # Merge citations
                c_citations = cand_details.get("explanationCitations", [])
                t_citations = target_details.get("explanationCitations", [])
                target_details["explanationCitations"] = t_citations + [
                    c for c in c_citations if c not in t_citations
                ]

                # Merge useWhen
                c_use_when = cand_details.get("useWhen", [])
                t_use_when = target_details.get("useWhen", [])
                target_details["useWhen"] = t_use_when + [
                    u for u in c_use_when if u not in t_use_when
                ]

                conn.execute(
                    """
                    UPDATE concepts
                    SET details_json = ?, updated_at = ?
                    WHERE concept_id = ?;
                    """,
                    (json.dumps(target_details), now_str, target_id),
                )

            # Retire / supersede candidate concept
            conn.execute(
                """
                UPDATE concepts
                SET status = 'superseded', hold_reason = 'merged', updated_at = ?
                WHERE concept_id = ?;
                """,
                (now_str, candidate_id),
            )
            conn.commit()
            logger.info("Merged candidate %s into library target", candidate_id)

        elif choice_norm == "drop":
            conn.execute(
                """
                UPDATE concepts
                SET status = 'retired', hold_reason = 'dropped', updated_at = ?
                WHERE concept_id = ?;
                """,
                (now_str, candidate_id),
            )
            conn.commit()
            logger.info("Dropped candidate %s", candidate_id)


def resolve_gap_fill(flag_id: str, choice: str, ctx: RunContext) -> None:
    """Resolver for 'intake.gap_fill' flags (keep / drop) (§2.31)."""
    now_str = ctx.now.isoformat()
    with ctx.db.connect() as conn:
        row = conn.execute("SELECT * FROM flags WHERE flag_id = ?;", (flag_id,)).fetchone()
        if not row:
            return
        concept_id = row["item_id"]

        choice_norm = choice.strip().lower()
        if choice_norm == "keep":
            conn.execute(
                """
                UPDATE concepts
                SET status = 'held', hold_reason = 'shape', updated_at = ?
                WHERE concept_id = ?;
                """,
                (now_str, concept_id),
            )
            conn.commit()
            logger.info("Approved gap-fill concept %s", concept_id)
        elif choice_norm == "drop":
            conn.execute(
                """
                UPDATE concepts
                SET status = 'retired', hold_reason = 'dropped', updated_at = ?
                WHERE concept_id = ?;
                """,
                (now_str, concept_id),
            )
            conn.commit()
            logger.info("Dropped gap-fill concept %s", concept_id)


def register_resolvers(flags: Flags) -> None:
    """Register intake resolvers on the Flags manager (§3.9)."""
    flags.register("intake.merge", resolve_merge)
    flags.register("intake.gap_fill", resolve_gap_fill)
