"""Mechanical quote check (SPEC-requirements 1.12, #30).

A quote passes when every piece outside `[bracketed fixes]` and `…` cuts appears in the transcript, in order,
after whitespace normalisation; only a piece's first letter may differ in case. Typographic quotes and apostrophes count as their straight forms.
"""
import re

_TYPO = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "…": "…"})
_SPLIT = re.compile(r"\[[^\]]*\]|…|\.\.\.")
_EDGE = " \t\n,.;:!?\"'-—–"
_MIN_WORDS = 2


def _normalise(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace; return the normalised text and each char's index in the original."""
    out: list[str] = []
    index: list[int] = []
    prev_space = True
    for i, ch in enumerate(text.translate(_TYPO)):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            index.append(i)
            prev_space = True
        else:
            out.append(ch)
            index.append(i)
            prev_space = False
    return "".join(out), index


def quote_pieces(quote: str) -> list[str]:
    """The verbatim pieces of a quote: what's left between bracketed fixes and `…` cuts."""
    q, _ = _normalise(quote.strip())
    pieces = []
    for raw in _SPLIT.split(q):
        piece = raw.strip(_EDGE)
        if piece:
            pieces.append(piece)
    return pieces


def find_quote(quote: str, text: str) -> int | None:
    """Character index in `text` where the quote's first piece starts, or None when it fails the check."""
    pieces = quote_pieces(quote)
    if sum(len(p.split()) for p in pieces) < _MIN_WORDS:
        return None
    norm, index = _normalise(text)
    cursor = 0
    first: int | None = None
    for piece in pieces:
        # a cut can turn a mid-sentence word into a sentence start, so the first letter may differ in case
        hits = [p for p in (norm.find(piece, cursor), norm.find(piece[0].swapcase() + piece[1:], cursor)) if p >= 0]
        if not hits:
            return None
        pos = min(hits)
        if first is None:
            first = pos
        cursor = pos + len(piece)
    assert first is not None
    return index[first]
