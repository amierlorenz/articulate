"""Citation matching rules: R7 exact + boundary near math + label-aware + joined retry (§3.5, #35, #37)."""
from collections.abc import Mapping, Sequence

from articulate.intake.cite.canon import (
    LABEL,
    MATH_EDGE,
    chunks_canon,
    pua_regex,
    q_canon,
)


def r7_match(q: str, page_text: str) -> bool:
    """
    R7+label match rule (#35, #37):
    - Exact match after canonicalisation, operators kept (+ − = < > ≤ ≥ ≠ ∪ ∩ ∑ ∫ √ ! | ± ∈).
    - Part labels (e.g. (a), 3), 3., Q. 5) are not math: skipped when deciding if quote starts in math.
    - Quote that is only a label fails.
    - Boundary near math: if the first or last 12 chars hold math, match must begin/end at a boundary.
    """
    if not q or not page_text:
        return False

    # A quote that is only a label (e.g. "(c)", "3.") fails (#37)
    rest = LABEL.sub("", q, count=1)
    if not q_canon(rest, ops=True):
        return False

    n = q_canon(q, ops=True)
    if not n:
        return False

    vn, cuts = chunks_canon(page_text, ops=True)
    qs = q.strip().rstrip(".,;: ")

    # Short part: strip label so label's ')' is not counted as math at end
    need_end = bool(MATH_EDGE.search(LABEL.sub("", qs, count=1)[-12:]))

    # Label present: start is prose (rest.strip() != qs); otherwise check first 12 chars
    need_start = bool(MATH_EDGE.search(rest.strip()[:12])) and (rest.strip() == qs)

    rx = pua_regex(n)
    pos = 0
    while m := rx.search(vn, pos):
        if (not need_start or m.start() in cuts) and (not need_end or m.end() in cuts):
            return True
        pos = m.start() + 1

    return False


def match_quote_in_pages(q: str, page_texts: Mapping[int, str] | Sequence[str], page: int) -> bool:
    """
    Match quote against page text, allowing quote to run onto page + 1 (multi-page span, #32, #37).
    """
    if isinstance(page_texts, Mapping):
        p1_text = page_texts.get(page, "")
        p2_text = page_texts.get(page + 1, "")
    elif isinstance(page_texts, Sequence):
        # 1-indexed page conversion if 0-based list
        p1_text = page_texts[page - 1] if 0 <= page - 1 < len(page_texts) else ""
        p2_text = page_texts[page] if 0 <= page < len(page_texts) else ""
    else:
        p1_text = str(page_texts)
        p2_text = ""

    span = p1_text + "\n" + p2_text if p2_text else p1_text
    return r7_match(q, span)


def joined_retry(q: str, partner_q: str, partner_role: str, page_text: str) -> bool:
    """
    Joined retry of question and answer split across a printed line on the same page (#37).
    Question is always placed first.
    """
    if not q or not partner_q:
        return False

    if partner_role == "ans":
        combined = f"{q} {partner_q}"
    else:
        combined = f"{partner_q} {q}"

    return r7_match(combined, page_text)
