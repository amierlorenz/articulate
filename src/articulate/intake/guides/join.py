"""Answer key join slice (§3.5, SPEC 2.22, Issue #32, Issue #62).

Separate answer-key files are extracted on their own (kind: answerKey).
Code joins them to their exam by exam hint, filename stem, and question order.
A failed or ambiguous join goes on the weekly flags list.
"""
import logging
import re
from typing import Any

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.models.runner import ModelRunner
from articulate.intake.cite import check

logger = logging.getLogger(__name__)

STOP_WORDS = {"solutions", "solution", "answers", "answer", "key", "ans", "detailed"}


def norm_stem(filename: str) -> str:
    """
    Normalize filename stem for matching:
    lowercase, strips extension and common answer-key markers (solutions, answers, key, etc.).
    """
    stem = re.sub(r"\.[^.]+$", "", filename.lower())
    # Remove words in STOP_WORDS
    tokens = re.findall(r"[a-z0-9]+", stem)
    filtered = [t for t in tokens if t not in STOP_WORDS]
    return "".join(filtered)


def norm_number(num: str) -> str:
    """Normalize item number / label for matching (e.g. '3(a)' -> '3a', 'Q. 1' -> 'q1')."""
    s = num.lower().strip()
    return re.sub(r"[\(\)\s\.,_-]", "", s)


def join_answer_key(
    ak_file_id: str,
    ak_display_name: str,
    ak_document: dict[str, Any],
    ak_items: list[dict[str, Any]],
    course_key: str,
    db: Database,
    flags: Flags | None = None,
    models: ModelRunner | None = None,
) -> str | None:
    """
    Join an answerKey file to its exam/practiceTest in the same course (§3.5, SPEC 2.22).

    1. Match exam by filename stem and exam hint.
    2. If 0 or >1 matches: add weekly flag 'intake.ambiguous_answer_key_join'.
    3. If 1 match: update target exam's guide_items with answer_page, answer_quote, answer_verified.
    Returns matched target file_id or None.
    """
    ak_stem = norm_stem(ak_display_name)
    ak_hint_name = (ak_document.get("examHint", {}).get("name") or "").strip().lower()

    with db.connect() as conn:
        candidates = conn.execute(
            """
            SELECT file_id, display_name FROM source_files
            WHERE course_key = ? AND file_id != ? AND role = 'guide_or_exam';
            """,
            (course_key, ak_file_id),
        ).fetchall()

    if not candidates:
        logger.info("No candidates found in course %s for answer key %s", course_key, ak_display_name)
        if flags:
            flags.add(
                "intake.ambiguous_answer_key_join",
                f"Answer key '{ak_display_name}' could not be matched to an exam in {course_key}",
                choices=[("skip", "skip"), ("retry", "retry")],
            )
        return None

    # Score candidates: stem match has highest precedence, then hint match
    stem_matches: list[dict[str, Any]] = []
    hint_matches: list[dict[str, Any]] = []

    for c in candidates:
        c_dict = dict(c)
        c_disp = c_dict["display_name"]
        c_stem = norm_stem(c_disp)

        if ak_stem and c_stem == ak_stem:
            stem_matches.append(c_dict)
        elif ak_hint_name and ak_hint_name in c_disp.lower():
            hint_matches.append(c_dict)

    chosen_candidates = stem_matches if stem_matches else hint_matches

    if len(chosen_candidates) == 0:
        logger.warning(
            "Answer key '%s' could not be matched to any exam in %s (0 matches)",
            ak_display_name,
            course_key,
        )
        if flags:
            flags.add(
                "intake.ambiguous_answer_key_join",
                f"Answer key '{ak_display_name}' could not be matched to an exam in {course_key}",
                choices=[("skip", "skip"), ("retry", "retry")],
            )
        return None

    if len(chosen_candidates) > 1:
        match_names = [c["display_name"] for c in chosen_candidates]
        logger.warning(
            "Answer key '%s' matched multiple candidates (%s)",
            ak_display_name,
            match_names,
        )
        if flags:
            flags.add(
                "intake.ambiguous_answer_key_join",
                f"Answer key '{ak_display_name}' matches multiple exams: {match_names}",
                choices=[("skip", "skip"), ("retry", "retry")],
            )
        return None

    target = chosen_candidates[0]
    target_file_id = target["file_id"]

    # 2. Match answer items to target exam's guide_items
    with db.connect() as conn:
        ex_items = conn.execute(
            """
            SELECT * FROM guide_items
            WHERE file_id = ?
            ORDER BY position ASC;
            """,
            (target_file_id,),
        ).fetchall()

    if not ex_items:
        logger.info("Target exam %s has no guide_items in DB", target_file_id)
        return target_file_id

    ex_list = [dict(r) for r in ex_items]
    num_to_ex: dict[str, list[dict[str, Any]]] = {}
    for r in ex_list:
        k = norm_number(r.get("number", ""))
        if k:
            num_to_ex.setdefault(k, []).append(r)

    non_context_ex = [r for r in ex_list if r.get("kind") != "context"]

    # Iterate through answer items and join
    for idx, ak_it in enumerate(ak_items):
        ak_num = str(ak_it.get("number", ""))
        ak_norm = norm_number(ak_num)

        matched_ex_row: dict[str, Any] | None = None
        if num_to_ex.get(ak_norm):
            matched_ex_row = num_to_ex[ak_norm].pop(0)
        elif idx < len(non_context_ex):
            matched_ex_row = non_context_ex[idx]

        if matched_ex_row is None:
            continue

        ans_dict = ak_it.get("answer") if isinstance(ak_it.get("answer"), dict) else {}
        ans_quote = ans_dict.get("quote") or ak_it.get("quote") or ""
        ans_page = ans_dict.get("page") or ak_it.get("page") or 1

        if not ans_quote.strip():
            continue

        # Verify answer quote against answer key document
        ans_verified = 0
        if db is not None:
            chk = check(
                quote=ans_quote,
                file_id=ak_file_id,
                page=ans_page,
                kind="guide_or_exam",
                db=db,
                models=models,
                flags=flags,
            )
            ans_verified = 1 if chk.verified else 0

        # Update target exam guide_items row
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE guide_items
                SET answer_page = ?, answer_quote = ?, answer_verified = ?
                WHERE file_id = ? AND position = ?;
                """,
                (ans_page, ans_quote, ans_verified, target_file_id, matched_ex_row["position"]),
            )
            conn.commit()

    logger.info("Successfully joined answer key %s to exam %s", ak_display_name, target["display_name"])
    return target_file_id
