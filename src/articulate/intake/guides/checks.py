"""Deterministic checks on study guide and past exam Extract output (§3.5, SPEC 2.21, 2.27, Issue #32, #34, #37)."""
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

CONTROL_CHAR_RE = re.compile(r"[\t\x08\x0c]")
PART_RE = re.compile(r"^(\d+)[a-z]$", re.IGNORECASE)


def strings(x: Any):
    """Yield all string values nested inside dictionaries and lists."""
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def check_control_chars_and_math(data: Any) -> list[str]:
    """
    Check for control characters (tab, backspace, form feed) and unbalanced $ delimiters.
    Returns a list of problem descriptions.
    """
    problems: list[str] = []
    for s in strings(data):
        if CONTROL_CHAR_RE.search(s):
            problems.append(f"control char in: {s[:60]!r}")
        if s.count("$") % 2 != 0:
            problems.append(f"unbalanced $ in: {s[:60]!r}")
    return problems


def check_exam_hint(exam_hint: dict[str, Any], allowed_exams: list[str]) -> tuple[bool, str]:
    """
    Check exam hint name against the course's allowed exam list (§3.5, SPEC 2.21).
    Empty or invalid exam hint → weekly flag.
    Returns (is_valid, exam_name).
    """
    name = (exam_hint.get("name") or "").strip()
    if not name:
        return False, ""
    if name not in allowed_exams:
        return False, name
    return True, name


def check_unit_hints(unit_hints: list[dict[str, Any]], allowed_units: list[str]) -> list[dict[str, Any]]:
    """
    Filter unit hints against the course's allowed unit list (§3.5, SPEC 2.21).
    Inferred unit hints are used only when the exam is unknown.
    """
    kept: list[dict[str, Any]] = []
    for uh in unit_hints:
        u_name = (uh.get("name") or "").strip()
        if u_name in allowed_units:
            kept.append(uh)
    return kept


def check_reading_order_and_parts(items: list[dict[str, Any]]) -> list[str]:
    """
    Check items reading order and structure (§3.5, SPEC 2.21, Issue #32, #34):
    - Lettered parts after a context stem. A part without context item is logged.
    - A non-context quote holding both (a) and (b) is logged.
    Returns list of logged findings.
    """
    findings: list[str] = []
    seen_numbers: set[str] = set()

    for it in items:
        num = str(it.get("number", "")).strip()
        kind = it.get("kind", "")
        quote = it.get("quote", "")

        # Check part without context item
        m = PART_RE.match(num)
        if m:
            parent_num = m.group(1)
            if parent_num not in seen_numbers:
                msg = f"part without context item: {num}"
                logger.info(msg)
                findings.append(msg)

        # Check non-context quote holding both (a) and (b)
        if kind != "context" and "(a)" in quote and "(b)" in quote:
            msg = f"non-context quote holding both (a) and (b): {num}"
            logger.info(msg)
            findings.append(msg)

        if num:
            seen_numbers.add(num)

    return findings


def check_unverified_share(
    items: list[dict[str, Any]], threshold: float = 0.30
) -> tuple[bool, float, int, int]:
    """
    Check if more than 30% of a file's items are unverified (§3.5, SPEC 2.27).
    Returns (is_flagged, unverified_ratio, unverified_count, total_count).
    """
    total = len(items)
    if total == 0:
        return False, 0.0, 0, 0

    unverified = sum(1 for it in items if not it.get("verified"))
    ratio = unverified / total
    is_flagged = ratio > threshold
    return is_flagged, ratio, unverified, total
