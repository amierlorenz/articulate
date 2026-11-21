"""Concept tier assignment with quoted reasons (§2.30, Issue #10)."""
from enum import StrEnum
from typing import Any


class Tier(StrEnum):
    CORE = "core"
    STANDARD = "standard"
    BACKGROUND = "background"
    OUT = "out"


class TierSignal(StrEnum):
    EXAM_HINT = "exam_hint"
    DISMISSAL = "dismissal"
    ADMIN_OR_AGENDA = "admin_or_agenda"
    DEFAULT_STANDARD = "default_standard"
    STUDY_GUIDE = "study_guide"
    NOT_ON_GUIDE = "not_on_guide"
    DISMISSED_NOT_ON_GUIDE = "dismissed_not_on_guide"
    PAST_EXAM = "past_exam"
    NO_EXAM_UNIT = "no_exam_unit"
    CUMULATIVE_FINAL_EMPHASIS = "cumulative_final_emphasis"


TIER_RANKS = {
    Tier.OUT: 0,
    Tier.BACKGROUND: 1,
    Tier.STANDARD: 2,
    Tier.CORE: 3,
}

RANK_TO_TIER = {
    0: Tier.OUT,
    1: Tier.BACKGROUND,
    2: Tier.STANDARD,
    3: Tier.CORE,
}


def _step_tier_up(tier: Tier) -> Tier:
    """Move tier up by one step (out -> background -> standard -> core)."""
    curr = TIER_RANKS[tier]
    nxt = min(curr + 1, 3)
    return RANK_TO_TIER[nxt]


def calculate_concept_tier(
    concept_title: str,
    unit_id: str | None = None,
    *,
    exam_hint: dict[str, Any] | None = None,
    dismissal: dict[str, Any] | None = None,
    is_admin_or_agenda: bool = False,
    matched_guide_items: list[dict[str, Any]] | None = None,
    guide_exists_for_unit: bool = False,
    matched_exam_questions: list[dict[str, Any]] | None = None,
    covered_by_exams: bool = True,
    emphasized_in_multiple_units_or_midterm: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Calculate the concept's tier and return (tier_str, reasons_list) (§2.30, #10).
    Reasons follow [{signal, quote, sourceFileId, page}].
    """
    reasons: list[dict[str, Any]] = []

    # 1. Base slide/lecture signals
    if not covered_by_exams:
        tier = Tier.OUT
        reasons.append({
            "signal": TierSignal.NO_EXAM_UNIT.value,
            "quote": None,
            "sourceFileId": None,
            "page": None,
        })
        return tier.value, reasons

    if is_admin_or_agenda:
        tier = Tier.OUT
        reasons.append({
            "signal": TierSignal.ADMIN_OR_AGENDA.value,
            "quote": None,
            "sourceFileId": None,
            "page": None,
        })
    elif exam_hint and exam_hint.get("quote"):
        tier = Tier.CORE
        reasons.append({
            "signal": TierSignal.EXAM_HINT.value,
            "quote": exam_hint.get("quote"),
            "sourceFileId": exam_hint.get("sourceFileId"),
            "page": exam_hint.get("page"),
        })
    elif dismissal and dismissal.get("quote"):
        tier = Tier.BACKGROUND
        reasons.append({
            "signal": TierSignal.DISMISSAL.value,
            "quote": dismissal.get("quote"),
            "sourceFileId": dismissal.get("sourceFileId"),
            "page": dismissal.get("page"),
        })
    else:
        tier = Tier.STANDARD
        reasons.append({
            "signal": TierSignal.DEFAULT_STANDARD.value,
            "quote": None,
            "sourceFileId": None,
            "page": None,
        })

    # 2. Study guide effects
    was_dismissed = (dismissal is not None and bool(dismissal.get("quote")))

    if guide_exists_for_unit:
        if matched_guide_items:
            # Matched guide item -> at least standard and on_guide
            if TIER_RANKS[tier] < TIER_RANKS[Tier.STANDARD]:
                tier = Tier.STANDARD
            for gi in matched_guide_items:
                reasons.append({
                    "signal": TierSignal.STUDY_GUIDE.value,
                    "quote": gi.get("quote"),
                    "sourceFileId": gi.get("sourceFileId"),
                    "page": gi.get("page"),
                })
        else:
            # Not on guide in a unit that has a guide
            if was_dismissed:
                # Dismissed and not on guide -> out
                tier = Tier.OUT
                reasons.append({
                    "signal": TierSignal.DISMISSED_NOT_ON_GUIDE.value,
                    "quote": dismissal.get("quote") if dismissal else None,
                    "sourceFileId": dismissal.get("sourceFileId") if dismissal else None,
                    "page": dismissal.get("page") if dismissal else None,
                })
            else:
                # Normal concept not on guide -> drops to background
                if TIER_RANKS[tier] > TIER_RANKS[Tier.BACKGROUND]:
                    tier = Tier.BACKGROUND
                reasons.append({
                    "signal": TierSignal.NOT_ON_GUIDE.value,
                    "quote": None,
                    "sourceFileId": None,
                    "page": None,
                })

    # 3. Past-exam effects
    if matched_exam_questions:
        for eq in matched_exam_questions:
            tier = _step_tier_up(tier)
            reasons.append({
                "signal": TierSignal.PAST_EXAM.value,
                "quote": eq.get("quote"),
                "sourceFileId": eq.get("sourceFileId"),
                "page": eq.get("page"),
            })

    # 4. Cumulative final emphasis
    if emphasized_in_multiple_units_or_midterm:
        tier = _step_tier_up(tier)
        reasons.append({
            "signal": TierSignal.CUMULATIVE_FINAL_EMPHASIS.value,
            "quote": None,
            "sourceFileId": None,
            "page": None,
        })

    return tier.value, reasons
