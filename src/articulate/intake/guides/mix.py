"""Question mix calculations for study guides and exams (§3.5, SPEC 2.21, 2.27, Issue #32, Issue #62).

Recognition kinds are MC, true/false and matching.
topic and context don't count toward the '>= 1/2 free recall' mix.
Lettered parts are separate items after one context item for the shared stem; the mix counts parts.
Unverified items count toward the question mix only.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from articulate.intake.guides.schema import ITEM_KINDS, NOT_COUNTED, RECOGNITION


@dataclass
class QuestionMix:
    """Summary of question formats and recall ratio."""

    total_items: int = 0
    counted_items: int = 0
    recognition_items: int = 0
    recall_items: int = 0
    counts_by_kind: dict[str, int] = field(default_factory=dict)
    recall_ratio: float = 0.0
    is_at_least_half_recall: bool = False
    unverified_count: int = 0
    unverified_ratio: float = 0.0


def count_mix(items: list[dict[str, Any]]) -> QuestionMix:
    """
    Compute question mix statistics across items (§3.5, SPEC 2.21, 2.27).

    - topic and context items are excluded from counted items.
    - Recognition kinds: multipleChoice, trueFalse, matching.
    - Recall items = counted_items - recognition_items.
    - Unverified items count toward the mix.
    """
    total = len(items)
    kind_counts: dict[str, int] = {k: 0 for k in ITEM_KINDS}
    c = Counter(it.get("kind", "") for it in items)
    for k, v in c.items():
        if k in kind_counts:
            kind_counts[k] = v
        else:
            kind_counts[k] = v

    counted = [it for it in items if it.get("kind") not in NOT_COUNTED]
    counted_len = len(counted)

    rec_len = sum(1 for it in counted if it.get("kind") in RECOGNITION)
    recall_len = counted_len - rec_len

    recall_ratio = (recall_len / counted_len) if counted_len > 0 else 0.0
    is_at_least_half = recall_ratio >= 0.5 or (counted_len == 0)

    unv_count = sum(1 for it in items if not it.get("verified"))
    unv_ratio = (unv_count / total) if total > 0 else 0.0

    return QuestionMix(
        total_items=total,
        counted_items=counted_len,
        recognition_items=rec_len,
        recall_items=recall_len,
        counts_by_kind=kind_counts,
        recall_ratio=recall_ratio,
        is_at_least_half_recall=is_at_least_half,
        unverified_count=unv_count,
        unverified_ratio=unv_ratio,
    )
