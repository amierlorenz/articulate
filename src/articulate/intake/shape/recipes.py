"""Prompt shaping recipes (§3.5, §3.7, SPEC-requirements 2.32, Issue #64).

Maps concept kind, contrast group presence, and tier to exact prompt kinds:
- definition / fact:
  - core (3): explain (shortAnswer), flashcard, multipleChoice
  - standard (2): explain, flashcard
  - background (1): flashcard
- method in a group:
  - core (3): methodPick, setUp, full solve (shortAnswer)
  - standard (2): setUp, full solve
  - background (1): full solve
- method alone:
  - core (3): setUp, full solve (shortAnswer), multipleChoice
  - standard (2): setUp, full solve
  - background (1): full solve
- out: 0 prompts
"""

FREE_RECALL_KINDS: set[str] = {"flashcard", "shortAnswer", "setUp"}


def is_free_recall(kind: str) -> bool:
    """Return True if prompt kind is free recall (flashcard, shortAnswer, setUp)."""
    return kind in FREE_RECALL_KINDS


def get_recipe(
    concept_kind: str,
    *,
    is_in_group: bool,
    tier: str,
) -> list[str]:
    """
    Return ordered list of prompt kinds for a concept based on its kind, group status, and tier.
    """
    if tier == "out":
        return []

    c_kind = concept_kind.lower()
    t = tier.lower()

    if c_kind in ("definition", "fact"):
        if t == "core":
            return ["shortAnswer", "flashcard", "multipleChoice"]
        if t == "standard":
            return ["shortAnswer", "flashcard"]
        if t == "background":
            return ["flashcard"]
        return []

    if c_kind == "method":
        if is_in_group:
            if t == "core":
                return ["methodPick", "setUp", "shortAnswer"]
            if t == "standard":
                return ["setUp", "shortAnswer"]
            if t == "background":
                return ["shortAnswer"]
            return []
        # method alone
        if t == "core":
            return ["setUp", "shortAnswer", "multipleChoice"]
        if t == "standard":
            return ["setUp", "shortAnswer"]
        if t == "background":
            return ["shortAnswer"]
        return []

    return []


def calculate_free_recall_ratio(prompt_kinds: list[str]) -> float:
    """Calculate the ratio of free recall prompts in a list of prompt kinds."""
    if not prompt_kinds:
        return 0.0
    free_recall_count = sum(1 for k in prompt_kinds if is_free_recall(k))
    return free_recall_count / len(prompt_kinds)
