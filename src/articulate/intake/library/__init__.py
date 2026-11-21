"""Library matching, tiers, and gap-fill slice (§3.5, §3.4, §3.6, §3.9, Issue #63)."""
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from articulate.core.jobs import StepSummary
from articulate.core.pipeline import RunContext
from articulate.intake.library.embed import compute_shortlist, embed_texts
from articulate.intake.library.gapfill import GapFillResult, process_gap_fill
from articulate.intake.library.judge import run_judge
from articulate.intake.library.resolvers import (
    register_resolvers,
    resolve_gap_fill,
    resolve_merge,
)
from articulate.intake.library.tiers import (
    calculate_concept_tier,
)

logger = logging.getLogger(__name__)


@dataclass
class CandidateMatch:
    candidate_id: str
    candidate_index: int
    matches: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MatchResult:
    matches: list[CandidateMatch]
    new_candidates: list[dict[str, Any]] = field(default_factory=list)
    same_candidates: list[tuple[dict[str, Any], str, list[str]]] = field(default_factory=list)
    confusable_candidates: list[tuple[dict[str, Any], str, str]] = field(default_factory=list)


def _load_library_concepts(course_key: str, ctx: RunContext) -> list[dict[str, Any]]:
    """Load existing library concepts from database for matching."""
    library: list[dict[str, Any]] = []
    with ctx.db.connect() as conn:
        rows = conn.execute(
            """
            SELECT concept_id, course_key, unit_id, kind, title, details_json, tier, status
            FROM concepts
            WHERE (course_key = ? OR 1=1) AND status != 'retired' AND status != 'superseded'
            ORDER BY created_at ASC;
            """,
            (course_key,),
        ).fetchall()

        for r in rows:
            try:
                details = json.loads(r["details_json"]) if r["details_json"] else {}
            except Exception:  # noqa: BLE001
                details = {}
            exp = ""
            citations = details.get("explanationCitations", [])
            if citations:
                exp = citations[0].get("quote", "")

            library.append({
                "concept_id": r["concept_id"],
                "course_key": r["course_key"],
                "unit_id": r["unit_id"],
                "kind": r["kind"],
                "title": r["title"],
                "explanation": exp,
                "details": details,
                "tier": r["tier"],
                "status": r["status"],
            })
    return library


def match_candidates(
    candidates: list[dict[str, Any]],
    course_key: str,
    ctx: RunContext,
    *,
    existing_library: list[dict[str, Any]] | None = None,
    reserve: bool = False,
    shortlist_threshold: float = 0.70,
    top_k: int = 5,
) -> MatchResult:
    """
    Public matcher: Embedding 2 shortlist -> Flash Lite judge (§2.28, Issue #63).
    Compares candidate concepts against existing library and the night's batch.
    """
    if not candidates:
        return MatchResult(matches=[])

    library = existing_library if existing_library is not None else _load_library_concepts(course_key, ctx)

    # 1. Embeddings
    cand_texts = [
        f"{c.get('title', '')}: {c.get('explanation', '')}"
        for c in candidates
    ]
    cand_vectors = embed_texts(cand_texts, ctx, reserve=reserve)

    lib_targets: list[tuple[str, list[float]]] = []
    if library:
        lib_texts = [
            f"{l.get('title', '')}: {l.get('explanation', '')}"
            for l in library
        ]
        lib_vectors = embed_texts(lib_texts, ctx, reserve=reserve)
        for item, vec in zip(library, lib_vectors, strict=False):
            cid = item.get("concept_id") or item.get("id") or ""
            lib_targets.append((cid, vec))

    batch_targets: list[tuple[str, list[float]]] = [
        (f"candidate:{i}", vec) for i, vec in enumerate(cand_vectors)
    ]

    # 2. Shortlist
    shortlist = compute_shortlist(
        candidate_vectors=cand_vectors,
        library_targets=lib_targets,
        batch_targets=batch_targets,
        threshold=shortlist_threshold,
        top_k=top_k,
    )

    # 3. Flash Lite Judge
    judge_results = run_judge(
        candidates=candidates,
        library=library,
        shortlist=shortlist,
        ctx=ctx,
    )

    cand_matches: list[CandidateMatch] = []
    new_cands: list[dict[str, Any]] = []
    same_cands: list[tuple[dict[str, Any], str, list[str]]] = []
    conf_cands: list[tuple[dict[str, Any], str, str]] = []

    for idx, (cand, j_res) in enumerate(zip(candidates, judge_results, strict=False)):
        cid = cand.get("concept_id") or cand.get("id") or f"cand_{idx}"
        matches = j_res.get("matches", [])
        cand_matches.append(CandidateMatch(
            candidate_id=cid,
            candidate_index=idx,
            matches=matches,
        ))

        same_match = next((m for m in matches if m.get("relation") == "same"), None)
        conf_match = next((m for m in matches if m.get("relation") == "confusable"), None)

        if same_match:
            same_cands.append((cand, same_match["id"], same_match.get("adds", [])))
        elif conf_match:
            conf_cands.append((cand, conf_match["id"], conf_match.get("reason", "")))
            new_cands.append(cand)
        else:
            new_cands.append(cand)

    return MatchResult(
        matches=cand_matches,
        new_candidates=new_cands,
        same_candidates=same_cands,
        confusable_candidates=conf_cands,
    )


def match_or_create_concept(
    item_payload: dict[str, Any],
    course_key: str,
    ctx: RunContext,
    *,
    existing_library: list[dict[str, Any]] | None = None,
    reserve: bool = True,
) -> tuple[str, bool]:
    """
    Public matcher helper used by post-class quiz (#71):
    Attaches item to existing concept or creates new one at tier: standard.
    Returns (concept_id, is_new).
    """
    title = item_payload.get("title", "")
    exp = item_payload.get("explanation", "")
    kind = item_payload.get("kind", "definition")

    candidate = {
        "title": title,
        "explanation": exp,
        "kind": kind,
        "course_key": course_key,
    }

    match_res = match_candidates(
        candidates=[candidate],
        course_key=course_key,
        ctx=ctx,
        existing_library=existing_library,
        reserve=reserve,
    )

    if match_res.same_candidates:
        target_id = match_res.same_candidates[0][1]
        if not target_id.startswith("candidate:"):
            return target_id, False

    # Create new standard concept
    from articulate.core.ids import slugify
    slug = slugify(title)
    concept_id = f"{course_key}.{slug}"
    now_str = ctx.now.isoformat()

    details = {
        "explanationCitations": item_payload.get("citations", []),
        "aliases": [],
        "useWhen": [],
        "conditions": [],
        "workedExamples": [],
    }

    reasons = [{"signal": "default_standard", "quote": None, "sourceFileId": None, "page": None}]

    with ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO concepts (
                concept_id, course_key, kind, title, aliases_json,
                details_json, tier, tier_reasons_json, on_guide, status,
                hold_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '[]', ?, 'standard', ?, 0, 'active', NULL, ?, ?);
            """,
            (concept_id, course_key, kind, title, json.dumps(details), json.dumps(reasons), now_str, now_str),
        )
        conn.commit()

    return concept_id, True


def assign_tiers(
    concepts: list[dict[str, Any]],
    course_key: str,
    ctx: RunContext,
) -> None:
    """Assign tiers and quoted reasons to concepts and update database (§2.30)."""
    now_str = ctx.now.isoformat()

    # Query course exams and unit lists
    covered_units: set[str] = set()
    guide_units: set[str] = set()
    with ctx.db.connect() as conn:
        exam_rows = conn.execute(
            "SELECT unit_ids_json FROM exams WHERE course_key = ?;", (course_key,)
        ).fetchall()
        for er in exam_rows:
            if er["unit_ids_json"]:
                try:
                    uids = json.loads(er["unit_ids_json"])
                    covered_units.update(uids)
                except Exception:  # noqa: BLE001, S110
                    pass

        # Units with a study guide
        sf_rows = conn.execute(
            "SELECT file_id, role FROM source_files WHERE course_key = ?;", (course_key,)
        ).fetchall()
        for sf in sf_rows:
            if sf["role"] == "guideOrExam":
                # Guide items for this file
                gi_rows = conn.execute(
                    "SELECT matched_concept_ids_json FROM guide_items WHERE file_id = ?;",
                    (sf["file_id"],),
                ).fetchall()
                for gi in gi_rows:
                    if gi["matched_concept_ids_json"]:
                        try:
                            mcids = json.loads(gi["matched_concept_ids_json"])
                            for mc in mcids:
                                c_unit = conn.execute(
                                    "SELECT unit_id FROM concepts WHERE concept_id = ?;", (mc,)
                                ).fetchone()
                                if c_unit and c_unit["unit_id"]:
                                    guide_units.add(c_unit["unit_id"])
                        except Exception:  # noqa: BLE001, S110
                            pass

    for c in concepts:
        cid = c.get("concept_id") or c.get("id")
        title = c.get("title", "")
        uid = c.get("unit_id")
        covered = (uid in covered_units) if uid and covered_units else True
        has_guide = (uid in guide_units) if uid else False

        tier_val, reasons = calculate_concept_tier(
            concept_title=title,
            unit_id=uid,
            exam_hint=c.get("exam_hint"),
            dismissal=c.get("dismissal"),
            is_admin_or_agenda=c.get("is_admin_or_agenda", False),
            matched_guide_items=c.get("matched_guide_items"),
            guide_exists_for_unit=has_guide,
            matched_exam_questions=c.get("matched_exam_questions"),
            covered_by_exams=covered,
        )

        with ctx.db.connect() as conn:
            conn.execute(
                """
                UPDATE concepts
                SET tier = ?, tier_reasons_json = ?, updated_at = ?
                WHERE concept_id = ?;
                """,
                (tier_val, json.dumps(reasons), now_str, cid),
            )
            conn.commit()


def step(ctx: RunContext) -> StepSummary:
    """
    Step intake.library (jobs: nightly, pass2, order=300).
    Processes items at stage 'library' (slides, guide_or_exam).
    Matches candidates against library, assigns tiers with quoted reasons,
    applies study guide & past exam effects, holds merge candidates and gap-fill,
    and advances items to 'shape'.
    """
    if ctx.flags:
        register_resolvers(ctx.flags)

    summary = StepSummary()
    now_str = ctx.now.isoformat()

    with ctx.db.connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM items
            WHERE stage = 'library' AND status IN ('pending', 'running')
            ORDER BY priority ASC, created_at ASC;
            """
        )
        items = cursor.fetchall()

    if not items:
        return summary

    for item_row in items:
        item_id_val = item_row["item_id"]
        course_key = item_row["course_key"] or ""
        kind = item_row["kind"]
        version = item_row["version"] or ""

        try:
            if kind == "slides":
                # 1. Fetch candidate concepts for this file / course
                with ctx.db.connect() as conn:
                    c_rows = conn.execute(
                        """
                        SELECT concept_id, course_key, unit_id, kind, title, details_json, tier, status
                        FROM concepts
                        WHERE course_key = ? AND status = 'held' AND hold_reason = 'library';
                        """,
                        (course_key,),
                    ).fetchall()

                candidates: list[dict[str, Any]] = []
                for cr in c_rows:
                    try:
                        det = json.loads(cr["details_json"]) if cr["details_json"] else {}
                    except Exception:  # noqa: BLE001
                        det = {}
                    exp = ""
                    cits = det.get("explanationCitations", [])
                    if cits:
                        exp = cits[0].get("quote", "")

                    candidates.append({
                        "concept_id": cr["concept_id"],
                        "course_key": cr["course_key"],
                        "unit_id": cr["unit_id"],
                        "kind": cr["kind"],
                        "title": cr["title"],
                        "explanation": exp,
                        "details": det,
                    })

                if candidates:
                    match_res = match_candidates(candidates, course_key, ctx)

                    # Handle same matches (merge candidates) -> hold back and add weekly flag
                    for cand, target_id, adds in match_res.same_candidates:
                        cand_id = cand["concept_id"]
                        with ctx.db.connect() as conn:
                            conn.execute(
                                """
                                UPDATE concepts
                                SET status = 'held', hold_reason = 'weekly_review', updated_at = ?
                                WHERE concept_id = ?;
                                """,
                                (now_str, cand_id),
                            )
                            conn.commit()

                        if ctx.flags:
                            ctx.flags.add(
                                kind="intake.merge",
                                subject=f"Merge candidate: '{cand.get('title')}' into existing concept",
                                choices=[("keep", "keep"), ("merge", "merge"), ("drop", "drop")],
                                item_id=cand_id,
                            )
                        summary.held += 1

                    # Assign tiers for new candidates
                    assign_tiers(match_res.new_candidates, course_key, ctx)
                    for cand in match_res.new_candidates:
                        cand_id = cand["concept_id"]
                        with ctx.db.connect() as conn:
                            conn.execute(
                                """
                                UPDATE concepts
                                SET status = 'held', hold_reason = 'shape', updated_at = ?
                                WHERE concept_id = ? AND status = 'held' AND hold_reason = 'library';
                                """,
                                (now_str, cand_id),
                            )
                            conn.commit()

            elif kind == "guide_or_exam":
                # Process guide items
                with ctx.db.connect() as conn:
                    gi_rows = conn.execute(
                        "SELECT * FROM guide_items WHERE file_id = ? ORDER BY position ASC;",
                        (version,),
                    ).fetchall()

                # Determine if guide or exam
                is_exam = "exam" in item_row["native_id"].lower() or "test" in item_row["native_id"].lower()

                for gi in gi_rows:
                    gi_dict = dict(gi)
                    matched_ids_raw = gi_dict.get("matched_concept_ids_json")
                    matched_ids = json.loads(matched_ids_raw) if matched_ids_raw else []

                    if is_exam:
                        if matched_ids:
                            # Exam effect: move concepts one tier up
                            for cid in matched_ids:
                                with ctx.db.connect() as conn:
                                    c_row = conn.execute(
                                        "SELECT * FROM concepts WHERE concept_id = ?;", (cid,)
                                    ).fetchone()
                                    if c_row:
                                        t_val, reasons = calculate_concept_tier(
                                            concept_title=c_row["title"],
                                            unit_id=c_row["unit_id"],
                                            matched_exam_questions=[gi_dict],
                                        )
                                        conn.execute(
                                            "UPDATE concepts SET tier = ?, tier_reasons_json = ?, updated_at = ? WHERE concept_id = ?;",
                                            (t_val, json.dumps(reasons), now_str, cid),
                                        )
                                        conn.commit()
                        else:
                            # Unmatched exam question -> logged only
                            logger.info("Unmatched exam question from %s: %s", version, gi_dict.get("quote"))
                    else:
                        # Study guide
                        if matched_ids:
                            for cid in matched_ids:
                                with ctx.db.connect() as conn:
                                    conn.execute(
                                        "UPDATE concepts SET on_guide = 1, updated_at = ? WHERE concept_id = ?;",
                                        (now_str, cid),
                                    )
                                    conn.commit()
                        else:
                            # Unmatched guide item -> held gap-fill
                            process_gap_fill(gi_dict, course_key, None, ctx)
                            summary.held += 1

            # Advance item to stage 'shape'
            with ctx.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE items
                    SET stage = 'shape', status = 'pending', consecutive_failures = 0, updated_at = ?
                    WHERE item_id = ?;
                    """,
                    (now_str, item_id_val),
                )
                conn.execute(
                    """
                    INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                    VALUES (?, ?, ?, 'library', 'advance', 'Advanced to shape');
                    """,
                    (item_id_val, ctx.run_id, now_str),
                )
                conn.commit()

            summary.done += 1

        except Exception as e:  # noqa: BLE001
            logger.error("Error in library stage for item %s: %s", item_id_val, e)
            summary.failed += 1

    return summary


__all__ = [
    "CandidateMatch",
    "GapFillResult",
    "MatchResult",
    "assign_tiers",
    "calculate_concept_tier",
    "match_candidates",
    "match_or_create_concept",
    "process_gap_fill",
    "register_resolvers",
    "resolve_gap_fill",
    "resolve_merge",
    "run_judge",
    "step",
]
