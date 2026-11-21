"""Mechanical quote check for Inbox notes (SPEC-requirements 1.12, #30)."""
from articulate.inbox.quotes import find_quote

TEXT = (
    "So I was thinking, um, about the garden.\n"
    "We should plant the tomatoes  along the south fence this spring,\n"
    "because the north side gets no sun after noon."
)


def test_exact_quote_found_at_its_position():
    pos = find_quote("plant the tomatoes along the south fence", TEXT)
    assert pos == TEXT.index("plant the tomatoes")


def test_whitespace_and_line_breaks_are_normalised():
    assert find_quote("this spring, because the north side", TEXT) is not None


def test_ellipsis_cuts_must_keep_order():
    assert find_quote("I was thinking, … about the garden", TEXT) is not None
    assert find_quote("I was thinking ... about the garden", TEXT) is not None
    assert find_quote("about the garden … I was thinking", TEXT) is None


def test_bracketed_fix_replaces_words():
    assert find_quote("We should plant the [tomatoes] along the south fence", TEXT) is not None
    assert find_quote("[We'll] plant the tomatoes along the south fence", TEXT) is not None


def test_typographic_apostrophes_match_straight_ones():
    text = "I don't think that's right."
    assert find_quote("I don’t think that’s right", text) is not None


def test_invented_words_fail():
    assert find_quote("plant the potatoes along the south fence", TEXT) is None


def test_quote_of_only_brackets_or_ellipses_fails():
    assert find_quote("[something] … [else]", TEXT) is None
    assert find_quote("", TEXT) is None


def test_surrounding_quote_marks_and_end_punctuation_are_ignored():
    assert find_quote("“plant the tomatoes along the south fence.”", TEXT) is not None


def test_first_letter_of_a_piece_may_change_case_but_nothing_else():
    text = "So you can scan the code at the door."
    assert find_quote("You can scan the code", text) is not None
    assert find_quote("so you can scan the code", text) is not None
    assert find_quote("You can Scan the code", text) is None
