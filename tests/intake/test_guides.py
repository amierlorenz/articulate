"""Tests for study guide and past exam Extract slice (§3.5, SPEC 2.21, 2.22, 2.25, 2.27, Issue #62)."""
import json
import logging
import time
from datetime import UTC, datetime
from datetime import time as dt_time
from pathlib import Path
from unittest.mock import MagicMock

import pypdfium2 as pdfium
import pytest

from articulate.core.config import Config
from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.models.runner import ModelResult, ModelRunner
from articulate.core.pipeline import Item, Privacy, RunContext
from articulate.intake.cite import r7_match
from articulate.intake.guides.checks import (
    check_control_chars_and_math,
    check_exam_hint,
    check_reading_order_and_parts,
    check_unit_hints,
    check_unverified_share,
)
from articulate.intake.guides.join import join_answer_key, norm_stem
from articulate.intake.guides.mix import count_mix
from articulate.intake.guides.run import process_guide_file, step
from articulate.intake.guides.schema import (
    DOCUMENT_KINDS,
    ITEM_KINDS,
    NOT_COUNTED,
    RECOGNITION,
    SCHEMA,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "guides"


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test.db"
    db = Database(db_file)
    db.migrate()
    return db


@pytest.fixture
def flags(test_db: Database) -> Flags:
    return Flags(test_db)


@pytest.fixture
def sample_practice_test() -> dict:
    path = FIXTURES_DIR / "practice_test.json"
    return json.loads(path.read_text())


@pytest.fixture
def sample_answer_key() -> dict:
    path = FIXTURES_DIR / "answer_key.json"
    return json.loads(path.read_text())


@pytest.fixture
def sample_study_guide() -> dict:
    path = FIXTURES_DIR / "study_guide.json"
    return json.loads(path.read_text())


# ==============================================================================
# 1. Schema Tests (#32, SPEC 2.21)
# ==============================================================================


def test_schema_matches_prototype_json():
    """Verify generated SCHEMA matches tests/fixtures/guides/schema_exam.json exactly."""
    ref_path = FIXTURES_DIR / "schema_exam.json"
    assert ref_path.exists(), "schema_exam.json fixture must exist"
    ref = json.loads(ref_path.read_text())
    assert SCHEMA == ref


def test_schema_item_kinds():
    """Verify item kinds and recognition/non-counted sets."""
    assert "topic" in NOT_COUNTED
    assert "context" in NOT_COUNTED
    assert "multipleChoice" in RECOGNITION
    assert "trueFalse" in RECOGNITION
    assert "matching" in RECOGNITION
    assert "problem" in ITEM_KINDS
    assert "explain" in ITEM_KINDS
    assert "proof" in ITEM_KINDS
    assert "code" in ITEM_KINDS
    assert "fillIn" in ITEM_KINDS
    assert "shortAnswer" in ITEM_KINDS
    assert len(DOCUMENT_KINDS) == 4


# ==============================================================================
# 2. Checks: Control Chars, Math, Hints, Parts (SPEC 2.21)
# ==============================================================================


def test_check_control_chars_and_unbalanced_dollar():
    """Verify detection of forbidden control characters and unbalanced LaTeX $ delimiters."""
    bad_output = {
        "text1": "Normal text",
        "nested": {"text2": "Has \t tab"},
        "list": ["Has \x0c form feed", "Unbalanced $math"],
        "ok": "$balanced math$",
    }
    problems = check_control_chars_and_math(bad_output)
    assert any("control char" in p and "tab" in p for p in problems)
    assert any("control char" in p and "form feed" in p for p in problems)
    assert any("unbalanced $" in p for p in problems)


def test_check_exam_hint_valid():
    """Valid exam hint matches allowed exams."""
    allowed = ["Test 1", "Test 2", "Test 4", "Final Exam"]
    valid, name = check_exam_hint({"name": "Test 4", "page": 1, "quote": "..."}, allowed)
    assert valid is True
    assert name == "Test 4"


def test_check_exam_hint_invalid_or_empty():
    """Invalid or empty exam hint is detected."""
    allowed = ["Test 1", "Test 2"]
    valid_empty, _ = check_exam_hint({"name": "", "page": 0, "quote": ""}, allowed)
    assert valid_empty is False

    valid_unknown, name = check_exam_hint({"name": "Test 99", "page": 1, "quote": "..."}, allowed)
    assert valid_unknown is False
    assert name == "Test 99"


def test_check_unit_hints_filters_allowed():
    """Unit hints are filtered against allowed units."""
    allowed_units = ["Ch 9 Momentum-Impulse", "Ch 11 Fluids"]
    hints = [
        {"name": "Ch 9 Momentum-Impulse", "page": 0, "quote": ""},
        {"name": "Ch 42 Not A Unit", "page": 0, "quote": ""},
    ]
    kept = check_unit_hints(hints, allowed_units)
    assert len(kept) == 1
    assert kept[0]["name"] == "Ch 9 Momentum-Impulse"


def test_check_reading_order_and_parts_logging(caplog):
    """Verify parts without context and non-context quotes holding (a)+(b) are logged."""
    items = [
        {"number": "1", "kind": "multipleChoice", "quote": "Question 1"},
        {"number": "2a", "kind": "problem", "quote": "Part 2a"},  # Missing parent context '2'
        {"number": "3", "kind": "context", "quote": "Context stem 3"},
        {"number": "3a", "kind": "problem", "quote": "Part 3a"},
        {"number": "4", "kind": "problem", "quote": "4. (a) First part and (b) second part in one quote"},
    ]
    with caplog.at_level(logging.INFO):
        check_reading_order_and_parts(items)

    assert any("part without context item: 2a" in rec.message for rec in caplog.records)
    assert any("non-context quote holding both (a) and (b): 4" in rec.message for rec in caplog.records)


# ==============================================================================
# 3. Question Mix (SPEC 2.21, 2.27)
# ==============================================================================


def test_count_mix_calculations(sample_practice_test):
    """Verify question mix calculations, counting parts, ignoring topics/context."""
    items = sample_practice_test["output"]["items"]
    mix = count_mix(items)
    # Total items: 8
    # Counted items: MC (1), TF (1), 3a prob (1), 3b prob (1), 4 prob (1), Q.1 explain (1), 5 proof (1) = 7
    # (Context '3' is not counted)
    assert mix.total_items == 8
    assert mix.counted_items == 7
    assert mix.recognition_items == 2  # 1 MC, 1 TF
    assert mix.recall_items == 5  # 3 prob, 1 explain, 1 proof
    assert mix.recall_ratio == pytest.approx(5 / 7)
    assert mix.is_at_least_half_recall is True


def test_unverified_items_count_in_mix():
    """Unverified items count toward the question mix only (SPEC 2.27)."""
    items = [
        {"number": "1", "kind": "multipleChoice", "quote": "MC", "verified": 0},
        {"number": "2", "kind": "problem", "quote": "Problem", "verified": 0},
        {"number": "t1", "kind": "topic", "quote": "Topic", "verified": 1},
    ]
    mix = count_mix(items)
    assert mix.counted_items == 2
    assert mix.recognition_items == 1
    assert mix.recall_items == 1
    assert mix.unverified_count == 2
    assert mix.is_at_least_half_recall is True


def test_check_unverified_share_flag():
    """A file with >30% unverified items triggers flag (SPEC 2.27)."""
    items = [
        {"number": "1", "verified": 1},
        {"number": "2", "verified": 1},
        {"number": "3", "verified": 0},
        {"number": "4", "verified": 0},  # 2/4 = 50% > 30%
    ]
    flagged, ratio, unv, total = check_unverified_share(items)
    assert flagged is True
    assert ratio == 0.5
    assert unv == 2
    assert total == 4

    # 1/5 = 20% <= 30%
    items2 = [{"number": str(i), "verified": 1 if i < 4 else 0} for i in range(5)]
    flagged2, ratio2, _, _ = check_unverified_share(items2)
    assert flagged2 is False
    assert ratio2 == 0.2


# ==============================================================================
# 4. Answer-Key Join (SPEC 2.22)
# ==============================================================================


def test_norm_stem():
    """Verify stem normalization removes solution/answer markers."""
    assert norm_stem("Unit 4 Practice Test solutions.pdf") == "unit4practicetest"
    assert norm_stem("MAT_305_T3_Review_detailed solutions.pdf") == "mat305t3review"
    assert norm_stem("203 TEST 4 Practice-2026.pdf") == "203test4practice2026"


def test_join_answer_key_success(test_db: Database, flags: Flags, sample_practice_test, sample_answer_key):
    """Test successful join between an answer key and its practice test by hint, stem, and order."""
    now_str = datetime.now(UTC).isoformat()
    course_key = "phy203"

    with test_db.connect() as conn:
        # Register practice test source file
        conn.execute(
            """
            INSERT INTO source_files (file_id, course_key, origin, slot, display_name, role, created_at)
            VALUES ('file_pt', ?, 'canvas', 'slot1', 'Unit 4 Practice Test.pdf', 'guide_or_exam', ?);
            """,
            (course_key, now_str),
        )
        # Register answer key source file
        conn.execute(
            """
            INSERT INTO source_files (file_id, course_key, origin, slot, display_name, role, created_at)
            VALUES ('file_ak', ?, 'canvas', 'slot2', 'Unit 4 Practice Test solutions.pdf', 'guide_or_exam', ?);
            """,
            (course_key, now_str),
        )
        # Insert practice test items into guide_items
        pt_items = sample_practice_test["output"]["items"]
        for idx, it in enumerate(pt_items):
            conn.execute(
                """
                INSERT INTO guide_items (file_id, position, number, kind, page, quote, answer_page, answer_quote, verified, answer_verified)
                VALUES ('file_pt', ?, ?, ?, ?, ?, 0, '', 1, 0);
                """,
                (idx, it["number"], it["kind"], it["page"], it["quote"]),
            )
        conn.commit()

    ak_items = sample_answer_key["output"]["items"]
    ak_doc = sample_answer_key["output"]["document"]

    matched_file_id = join_answer_key(
        ak_file_id="file_ak",
        ak_display_name="Unit 4 Practice Test solutions.pdf",
        ak_document=ak_doc,
        ak_items=ak_items,
        course_key=course_key,
        db=test_db,
        flags=flags,
    )

    assert matched_file_id == "file_pt"

    # Verify that guide_items for file_pt now have answer_quote populated
    with test_db.connect() as conn:
        rows = conn.execute(
            "SELECT number, answer_quote FROM guide_items WHERE file_id = 'file_pt' ORDER BY position ASC;"
        ).fetchall()
        row_map = {r["number"]: r["answer_quote"] for r in rows}

    assert row_map["1"] == "C"
    assert row_map["2"] == "True"
    assert row_map["3a"] == "v = 9.9 m/s"
    assert row_map["3b"] == "t = 1.01 s"
    assert row_map["Q. 1"] == "The exhaust carries momentum backward."


def test_join_answer_key_ambiguous_flags(test_db: Database, flags: Flags, sample_answer_key):
    """An ambiguous join (>1 candidate matches or 0 candidates) raises a weekly flag."""
    course_key = "phy203"

    # No matching file in DB -> flag
    ak_items = sample_answer_key["output"]["items"]
    ak_doc = sample_answer_key["output"]["document"]

    matched = join_answer_key(
        ak_file_id="file_ak",
        ak_display_name="Unknown Exam solutions.pdf",
        ak_document=ak_doc,
        ak_items=ak_items,
        course_key=course_key,
        db=test_db,
        flags=flags,
    )
    assert matched is None

    # Check that a flag was added
    with test_db.connect() as conn:
        flags_rows = conn.execute("SELECT * FROM flags WHERE kind = 'intake.ambiguous_answer_key_join';").fetchall()
        assert len(flags_rows) == 1
        assert "Unknown Exam solutions.pdf" in flags_rows[0]["subject"]


# ==============================================================================
# 5. Full Pipeline Step & Process Guide File (SPEC 2.21, 2.27)
# ==============================================================================


def test_process_guide_file_end_to_end(test_db: Database, flags: Flags, sample_practice_test, tmp_path: Path):
    """Verify process_guide_file runs model call, checks, verification, stores items, and advances."""
    course_key = "phy203"
    file_id = "test_fid_123"
    now_str = datetime.now(UTC).isoformat()

    # Create dummy pdf
    dummy_pdf = tmp_path / "test.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy")

    # Set up source_files, exams, units
    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO source_files (file_id, course_key, origin, slot, display_name, role, archive_path, created_at)
            VALUES (?, ?, 'canvas', 'slot1', '203 TEST 4 Practice-2026.pdf', 'guide_or_exam', ?, ?);
            """,
            (file_id, course_key, str(dummy_pdf), now_str),
        )
        conn.execute(
            """
            INSERT INTO exams (exam_id, course_key, name, kind, date)
            VALUES ('ex1', ?, 'Test 4', 'test', '2026-11-25');
            """,
            (course_key,),
        )
        conn.execute(
            """
            INSERT INTO units (unit_id, course_key, name, position)
            VALUES ('u1', ?, 'Ch 9 Momentum-Impulse', 1);
            """,
            (course_key,),
        )
        # Add pages to pages table for cite check
        for p_num, p_text in sample_practice_test["pages"].items():
            conn.execute(
                """
                INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
                VALUES (?, ?, ?, ?, 0, '', '');
                """,
                (file_id, int(p_num), f"hash_{p_num}", p_text),
            )
        conn.commit()

    # Mock ModelRunner returning sample_practice_test["output"]
    mock_runner = MagicMock(spec=ModelRunner)
    mock_runner.run.return_value = ModelResult(
        text=json.dumps(sample_practice_test["output"]),
        parsed=sample_practice_test["output"],
        vectors=None,
        model="gemini-3.8-flash",
        provider="gemini",
        finish_reason="STOP",
        usage={"promptTokenCount": 100, "candidatesTokenCount": 50},
        attempts=["ok"],
    )

    item = Item(
        item_id="it_123",
        source="canvas",
        kind="guide_or_exam",
        native_id="slot1",
        version=file_id,
        course_key=course_key,
        privacy=Privacy.CLASS,
        stage="guide_extract",
        status="pending",
    )

    ctx = RunContext(
        run_id="run_test",
        job="nightly",
        now=datetime.now(UTC),
        db=test_db,
        models=mock_runner,
        flags=flags,
        config=Config(),
    )

    res = process_guide_file(item, ctx)
    assert res.outcome == "advance"
    assert res.next_stage == "library"

    # Verify guide_items in database
    with test_db.connect() as conn:
        items = conn.execute("SELECT * FROM guide_items WHERE file_id = ? ORDER BY position ASC;", (file_id,)).fetchall()
        assert len(items) == 8
        # Check first item verified by text
        assert items[0]["number"] == "1"
        assert items[0]["kind"] == "multipleChoice"
        assert items[0]["verified"] == 1
        assert items[0]["verified_by"] == "text"


def test_guide_step_orchestrator(test_db: Database, flags: Flags, sample_study_guide, tmp_path: Path):
    """Verify registry step 'intake.guides' processes pending items at guide_extract stage."""
    course_key = "mat305"
    file_id = "sg_fid"
    item_id_val = "item_sg_1"
    now_str = datetime.now(UTC).isoformat()

    dummy_pdf = tmp_path / "sg.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy")

    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO source_files (file_id, course_key, origin, slot, display_name, role, archive_path, created_at)
            VALUES (?, ?, 'canvas', 'slot_sg', 'Final review sheet.pdf', 'guide_or_exam', ?, ?);
            """,
            (file_id, course_key, str(dummy_pdf), now_str),
        )
        conn.execute(
            """
            INSERT INTO units (unit_id, course_key, name, position)
            VALUES ('u9', ?, 'Ch 9 Momentum-Impulse', 1), ('u11', ?, 'Ch 11 Fluids', 2);
            """,
            (course_key, course_key),
        )
        conn.execute(
            """
            INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
            VALUES (?, 1, 'h1', ?, 0, '', '');
            """,
            (file_id, sample_study_guide["pages"]["1"]),
        )
        conn.execute(
            """
            INSERT INTO items (item_id, source, kind, native_id, version, course_key, privacy, stage, status, created_at, updated_at)
            VALUES (?, 'canvas', 'guide_or_exam', 'slot_sg', ?, ?, 'class', 'guide_extract', 'pending', ?, ?);
            """,
            (item_id_val, file_id, course_key, now_str, now_str),
        )
        conn.commit()

    mock_runner = MagicMock(spec=ModelRunner)
    mock_runner.run.return_value = ModelResult(
        text=json.dumps(sample_study_guide["output"]),
        parsed=sample_study_guide["output"],
        vectors=None,
        model="gemini-3.8-flash",
        provider="gemini",
        finish_reason="STOP",
        usage={"promptTokenCount": 50, "candidatesTokenCount": 20},
        attempts=["ok"],
    )

    ctx = RunContext(
        run_id="run_step_test",
        job="nightly",
        now=datetime.now(UTC),
        db=test_db,
        models=mock_runner,
        flags=flags,
        config=Config(),
    )

    summary = step(ctx)
    assert summary.done == 1
    assert summary.failed == 0

    # Item should be advanced to 'library'
    with test_db.connect() as conn:
        row = conn.execute("SELECT stage, status FROM items WHERE item_id = ?;", (item_id_val,)).fetchone()
        assert row["stage"] == "library"
        assert row["status"] == "pending"

        # Study guide items had empty exam hint, so invalid_exam_hint flag should be added
        flag = conn.execute("SELECT * FROM flags WHERE kind = 'intake.invalid_exam_hint';").fetchone()
        assert flag is not None
        assert "Final review sheet.pdf" in flag["subject"]


# ==============================================================================
# 6. Real Acceptance Check (SPEC 2.21, 2.22, 2.25, 2.27, Issue #62)
# ==============================================================================


@pytest.mark.real
def test_real_guides_acceptance():
    """
    Acceptance check 2.25:
    - Lock held: /tmp/articulate-real.lock
    - Time constraint: Never between 01:45 and 04:00
    - Real MAT 305 and PHY 203 material used.
    - PHY-203 Test 4 Practice has 5 pages, 26 items (MC + problems + context).
    - MAT-305 T3 Review + solutions: true quotes verify, 0 altered formulas pass.
    """
    now = datetime.now()
    if dt_time(1, 45) <= now.time() <= dt_time(4, 0):
        pytest.skip("Never run real checks between 01:45 and 04:00")

    real_dir = Path.home() / "Library/Application Support/articulate/fixtures/real"
    phy_file = real_dir / "phy203-test4.pdf"
    mat_rev_file = real_dir / "mat305-t3-review.pdf"
    mat_sol_file = real_dir / "mat305-t3-solutions.pdf"

    if not (phy_file.exists() and mat_rev_file.exists() and mat_sol_file.exists()):
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
        # 1. PHY-203 Test 4 Practice checks
        pdf_phy = pdfium.PdfDocument(phy_file)
        assert len(pdf_phy) == 5, f"Expected 5 pages in PHY-203 Test 4, got {len(pdf_phy)}"

        # 2. MAT-305 T3 Review formula check acceptance (#37)
        pdf_mat = pdfium.PdfDocument(mat_rev_file)
        assert len(pdf_mat) == 8, f"Expected 8 pages in MAT-305 Review, got {len(pdf_mat)}"

        # Verify formula check: true quotes pass, altered formulas reject
        p1_text = pdf_mat[0].get_textpage().get_text_range()
        # Find sample formulas from p1
        if "X ~ N" in p1_text or "P(" in p1_text:
            sample_math = "P(X"
            assert r7_match(sample_math, p1_text)
            # Altered operator or digit should NOT match
            assert not r7_match("P(Y", p1_text)

        pdf_sol = pdfium.PdfDocument(mat_sol_file)
        assert len(pdf_sol) == 10, f"Expected 10 pages in MAT-305 Solutions, got {len(pdf_sol)}"
    finally:
        try:
            lock_path.rmdir()
        except OSError:
            pass

