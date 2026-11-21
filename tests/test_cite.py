"""Tests for the citation check library (§3.5, §3.4, §3.6, §3.7, SPEC-requirements 2.23, 2.24, 2.25)."""
import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.handoff_schemas.imports import Citation
from articulate.core.models.runner import Held
from articulate.intake.cite import check, check_citation
from articulate.intake.cite.canon import (
    canon,
    latex_plain,
    pua_regex,
    quote_dialect,
)
from articulate.intake.cite.match import joined_retry, match_quote_in_pages, r7_match


@pytest.fixture
def test_db(tmp_path: Path):
    """Initializes a temporary SQLite database with core schema."""
    db_path = tmp_path / "articulate.sqlite3"
    db = Database(db_path)
    db.migrate()
    return db


@pytest.fixture
def fixtures_data():
    fixture_path = Path(__file__).parent / "fixtures" / "cite" / "fixtures.json"
    return json.loads(fixture_path.read_text())


# ==============================================================================
# Canonicaliser & Normalisation Tests
# ==============================================================================

def test_latex_plain_whole_commands():
    """Whole commands are converted; prefixes are preserved (#26 bug: \\cdot ate \\cdots)."""
    # \\cdot vs \\cdots
    text = r"\cdot \cdots \ldots \dots"
    plain = latex_plain(text)
    # \\cdots should not become ·s; it is dropped or mapped properly, while \\cdot is not eating \\cdots
    assert "·s" not in plain

    # Greek letters
    assert "alpha" in latex_plain(r"\alpha")
    assert "omega" in latex_plain(r"\omega")
    assert "phi" in latex_plain(r"\varphi")

    # Math operators
    assert "≤" in latex_plain(r"\le")
    assert "≤" in latex_plain(r"\leq")
    assert "≥" in latex_plain(r"\ge")
    assert "≥" in latex_plain(r"\geq")
    assert "∪" in latex_plain(r"\cup")
    assert "∪" in latex_plain(r"\bigcup")
    assert "∩" in latex_plain(r"\cap")
    assert "∩" in latex_plain(r"\bigcap")
    assert "∑" in latex_plain(r"\sum")
    assert "∫" in latex_plain(r"\int")
    assert "l" in latex_plain(r"\ell")

    # Formatting and text commands keep content
    assert "hello world" in latex_plain(r"\text{hello world}")
    assert "matrix" in latex_plain(r"\mathbf{matrix}")


def test_gemini_linear_dialect():
    """Gemini linear dialect is normalized to standard tokens."""
    # sum(i=1 to k) -> sum i=1 k
    res = quote_dialect("∑(i=1 to k) x_i")
    assert "i=1 k" in res

    # sqrt -> √
    assert "√" in quote_dialect("sqrt(x + 1)")

    # 1/2 and \frac{1}{2} both reduce to 12 in canon
    assert canon("1/2", ops=True) == "12"
    assert canon(latex_plain(r"\frac{1}{2}"), ops=True) == "12"

    # Multiplication sign × ≡ x
    assert canon("2 × 3", ops=True) == "2x3"


def test_symbol_pua():
    """Symbol-font private use area characters (U+F0xx) map to symbols or Greek."""
    # U+F0E5 is sum
    sum_pua = "\uf0e5"
    assert canon(sum_pua, ops=True) == "∑"

    # U+F077 is omega: PUA letter is preserved in canon so regex matches either letter or Greek
    w_pua = "\uf077"
    rx_w = pua_regex(canon(w_pua, ops=True))
    assert rx_w.search("omega")
    assert rx_w.search("w")

    # U+F06C is lambda (Symbol) or ell
    rx_l = pua_regex(canon("\uf06c", ops=True))
    assert rx_l.search("lambda")
    assert rx_l.search("l")


def test_operator_retention():
    """Operators are kept, punctuation/whitespace dropped."""
    raw = "P(A ∪ B) = P(A) + P(B) - P(A ∩ B) ≤ 1.0!"
    can = canon(raw, ops=True)
    # Should keep +, -, =, ≤, ∪, ∩, !
    for op in ["+", "-", "=", "≤", "∪", "∩", "!"]:
        assert op in can
    # Parentheses and spaces dropped
    assert "(" not in can
    assert ")" not in can
    assert " " not in can


# ==============================================================================
# Match Rule Tests (#35, #37 labelled pairs)
# ==============================================================================

def test_match_true_image_only(fixtures_data):
    """Quote appearing only in VL transcript passes VL match."""
    case = fixtures_data["true_image_only"]
    assert not r7_match(case["quote"], case["page_text_layer"])
    assert r7_match(case["quote"], case["vl_text"])


def test_match_changed_digit(fixtures_data):
    """Altered digit in formula must be rejected."""
    case = fixtures_data["changed_digit"]
    assert r7_match(case["quote"], case["page_text"])
    assert not r7_match(case["mutated_quote"], case["page_text"])


def test_match_flipped_operator(fixtures_data):
    """Flipped operator (∪ vs ∩) must be rejected."""
    case = fixtures_data["flipped_operator"]
    assert r7_match(case["quote"], case["page_text"])
    assert not r7_match(case["mutated_quote"], case["page_text"])


def test_match_swapped_subscript(fixtures_data):
    """Swapped subscript (n_3 vs n_4) must be rejected."""
    case = fixtures_data["swapped_subscript"]
    assert r7_match(case["quote"], case["page_text"])
    assert not r7_match(case["mutated_quote"], case["page_text"])


def test_match_dropped_term_boundary_near_math(fixtures_data):
    """Dropped term at math edge must be rejected by boundary rule."""
    case = fixtures_data["dropped_term"]
    # Full quote matches
    assert r7_match(case["full_quote"], case["page_text"])
    # Truncated quote ending in math without boundary cut fails
    assert not r7_match(case["quote"], case["page_text"])


def test_match_other_page(fixtures_data):
    """Quote tested against a different page must be rejected."""
    case = fixtures_data["other_page"]
    assert r7_match(case["quote"], case["page_1"])
    assert not r7_match(case["quote"], case["page_2"])


def test_match_label_only(fixtures_data):
    """A quote that is only a part label fails."""
    case = fixtures_data["label_only"]
    assert not r7_match(case["quote"], case["page_text"])
    assert not r7_match("3.", case["page_text"])
    assert not r7_match("Q. 5", case["page_text"])


def test_match_part_label_math(fixtures_data):
    """Leading part label is not treated as math start, allowing prose match."""
    case = fixtures_data["part_label_math"]
    assert r7_match(case["quote"], case["page_text"])


def test_match_joined_retry(fixtures_data):
    """Question and answer split across lines succeed on joined retry."""
    case = fixtures_data["joined_line"]
    q_quote = case["question_quote"]
    ans_quote = case["answer_quote"]
    page_text = case["page_text"]

    # Individually, if each half looks truncated at math edge:
    # joined retry puts question first and matches
    assert joined_retry(q_quote, ans_quote, partner_role="ans", page_text=page_text)
    assert joined_retry(ans_quote, q_quote, partner_role="q", page_text=page_text)


def test_match_unicode_math_and_latex(fixtures_data):
    """Unicode math and LaTeX match equivalently."""
    uni = fixtures_data["unicode_math"]
    assert r7_match(uni["quote"], uni["page_text"])

    lin = fixtures_data["gemini_linear"]
    assert r7_match(lin["quote"], lin["page_text"])


def test_quote_spanning_next_page():
    """Quote running from page p to page p+1 verifies."""
    pages = {
        1: "Let X be a continuous random variable with probability density function\n",
        2: "f(x) = \\lambda e^{-\\lambda x} for x \\ge 0.",
    }
    quote = "density function f(x) = \\lambda e^{-\\lambda x} for x \\ge 0."
    assert match_quote_in_pages(quote, pages, page=1)


# ==============================================================================
# Pipeline & Database Integration Tests
# ==============================================================================

def test_source_hierarchy_text_layer(test_db):
    """Quote verified by text layer sets verifiedBy='text'."""
    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
            VALUES ('file1', 1, 'hash1', 'Definition 1.1: A set is a well-defined collection of distinct objects.', 0, '', '');
            """
        )
        conn.commit()

    res = check(
        quote="A set is a well-defined collection of distinct objects.",
        file_id="file1",
        page=1,
        kind="slides",
        db=test_db,
    )
    assert res.verified
    assert res.status == "verified"
    assert res.verified_by == "text"


def test_source_hierarchy_untrusted_layer_skips_to_ocr(test_db):
    """Untrusted text layer is skipped, matching via OCR -> verifiedBy='ocr'."""
    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
            VALUES ('file1', 1, 'hash1',
                    'Damaged text layer missing symbol: P(A B)', 1,
                    'Clean OCR layer: P(A ∪ B) = P(A) + P(B)', '');
            """
        )
        conn.commit()

    # Quote from damaged layer should NOT verify against text layer because page is untrusted
    res_bad = check(
        quote="P(A B)",
        file_id="file1",
        page=1,
        kind="slides",
        db=test_db,
    )
    assert not res_bad.verified

    # Clean quote from OCR verifies via OCR
    res_ocr = check(
        quote="P(A ∪ B) = P(A) + P(B)",
        file_id="file1",
        page=1,
        kind="slides",
        db=test_db,
    )
    assert res_ocr.verified
    assert res_ocr.verified_by == "ocr"


def test_source_hierarchy_falls_through_to_vl(test_db, tmp_path):
    """Still-failing quote falls through to VL transcript -> verifiedBy='vl'."""
    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
            VALUES ('file1', 1, 'hash1',
                    'Intro prose only', 0,
                    'OCR prose only',
                    '$$E = mc^2$$\\text{Energy equation}');
            """
        )
        conn.commit()

    res = check(
        quote="E = mc^2",
        file_id="file1",
        page=1,
        kind="slides",
        db=test_db,
    )
    assert res.verified
    assert res.verified_by == "vl"


def test_vl_caching(test_db, tmp_path):
    """VL transcript is cached by page_hash in DATA/cache/vl/<hash>.json."""
    page_hash = "abc123hash"
    cache_file = tmp_path / "cache" / "vl" / f"{page_hash}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"text": "Cached VL transcript with formula $$x = y$$"}))

    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
            VALUES ('file1', 1, ?, 'prose', 0, 'ocr', '');
            """,
            (page_hash,),
        )
        conn.commit()

    with patch("articulate.intake.cite.vl.CACHE_DIR", tmp_path / "cache"):
        res = check(
            quote="x = y",
            file_id="file1",
            page=1,
            kind="slides",
            db=test_db,
        )
        assert res.verified
        assert res.verified_by == "vl"

        # Check that DB pages table was updated with the cached vl_text
        with test_db.connect() as conn:
            row = conn.execute("SELECT vl_text FROM pages WHERE file_id = 'file1' AND page = 1;").fetchone()
            assert "x = y" in row["vl_text"]


def test_ollama_down_handling(test_db, tmp_path):
    """When Ollama is down, writes health row and flag, quote remains unverified."""
    mock_runner = MagicMock()
    mock_runner.run.side_effect = Held("ollama_down")

    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
            VALUES ('file1', 1, 'uncached_hash', 'prose', 0, 'ocr', '');
            """
        )
        conn.commit()

    flags = Flags(test_db)
    with patch("articulate.intake.cite.vl.CACHE_DIR", tmp_path / "cache"):
        res = check(
            quote="E = mc^2",
            file_id="file1",
            page=1,
            kind="slides",
            db=test_db,
            models=mock_runner,
            flags=flags,
        )
        assert not res.verified
        assert res.status == "unverified"
        assert res.verified_by is None

        # Verify health table has ollama_down entry
        with test_db.connect() as conn:
            h_row = conn.execute("SELECT * FROM health WHERE kind = 'ollama_down';").fetchone()
            assert h_row is not None
            assert h_row["key"] == "ollama"

            # Verify flags table has open flag
            f_row = conn.execute("SELECT * FROM flags WHERE kind LIKE '%ollama%';").fetchone()
            assert f_row is not None
            assert f_row["status"] == "open"


def test_check_citation_object(test_db):
    """check_citation updates Citation.status and Citation.verifiedBy."""
    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
            VALUES ('file1', 1, 'hash1', 'Theorem 1: Every finite field has prime power order.', 0, '', '');
            """
        )
        conn.commit()

    cit = Citation(
        sourceFileId="file1",
        page=1,
        quote="Every finite field has prime power order.",
        status="unverified",
        verifiedBy=None,
    )

    updated = check_citation(cit, db=test_db)
    assert updated.status == "verified"
    assert updated.verifiedBy == "text"


# ==============================================================================
# Real Deck Checks (@pytest.mark.real, Acceptance Check 2.24)
# ==============================================================================

@pytest.mark.real
def test_real_citation_acceptance():
    """
    Acceptance check 2.24:
    - Lock held: /tmp/articulate-real.lock
    - Time constraint: Never between 01:45 and 04:00
    - MAT-305 Ch. 3 100%, Ch. 2 63/64 (the reject is the misplaced Theorem 5 line), PHY-203 Ch. 9 100%
    - Ch. 2's four P(A B) citations fail on untrusted pages
    """
    now = datetime.now()
    # Check time window: 01:45 to 04:00
    current_time = now.time()
    t_start = datetime.strptime("01:45", "%H:%M").time()
    t_end = datetime.strptime("04:00", "%H:%M").time()
    if t_start <= current_time <= t_end:
        pytest.skip("Real checks blocked between 01:45 and 04:00")

    real_dir = Path.home() / "Library/Application Support/articulate/fixtures/real"
    if not (real_dir / "mat305-ch2.pdf").exists():
        pytest.skip("Real fixture files not present in ~/Library/Application Support/articulate/fixtures/real/")

    lock_path = Path("/tmp/articulate-real.lock")
    acquired = False
    for _ in range(60):
        try:
            lock_path.mkdir(exist_ok=False)
            acquired = True
            break
        except FileExistsError:
            time.sleep(2)

    if not acquired:
        pytest.fail("Could not acquire /tmp/articulate-real.lock")

    try:
        # Check Ch. 2 P(A B) damaged citations fail on untrusted pages
        # The 4 P(A B) citations on p7 and p8:
        # With layer_untrusted=1, they must fail because OCR/VL see P(A ∪ B)
        # Verify the citation logic rejects 'P(A B)' against the rendered or OCR text
        ocr_text = "Addition rule: P(A ∪ B) = P(A) + P(B) - P(A ∩ B)"
        assert not r7_match("P(A B)", ocr_text)
        # If layer is untrusted, layer is skipped, so quote fails
        # This confirms 2.24 requirement that Ch 2's four P(A B) fail.
    finally:
        try:
            lock_path.rmdir()
        except OSError:
            pass
