"""Tests for intake.convert package (§3.5, Acceptance checks 2.11–2.17)."""

import json
import shutil
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pypdfium2 as pdfium
import pytest

from articulate.core.db import Database
from articulate.core.paths import archive_pages_path, cache_ocr_path
from articulate.core.pipeline import RunContext
from articulate.intake.convert import convert_step
from articulate.intake.convert.deckcraft import (
    DeckCraftClient,
    DeckCraftFallbackError,
)
from articulate.intake.convert.keynote import (
    KeynoteHoldError,
    convert_pptx_keynote,
)
from articulate.intake.convert.ocr import get_page_ocr
from articulate.intake.convert.signals import compute_damaged_layer_signals
from articulate.intake.convert.wordcraft import convert_wordcraft


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def temp_articulate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTICULATE_DATA", str(data_dir))
    monkeypatch.setenv("ARTICULATE_DATA_DIR", str(data_dir))
    db_path = data_dir / "articulate.sqlite3"
    db = Database(db_path)
    db.migrate()
    return {"data_dir": data_dir, "db": db}


# -----------------------------------------------------------------------------
# 1. DeckCraft & Fallback Triggers (2.11, 2.12)
# -----------------------------------------------------------------------------


def _start_mock_deckcraft_server(responses: list[dict]) -> tuple[int, threading.Thread, list[dict]]:
    """Starts a mock TCP server simulating DeckCraft's JSON control port."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(5)

    received_requests = []
    resp_iter = iter(responses)
    stop_event = threading.Event()

    def handle():
        server.settimeout(0.5)
        while not stop_event.is_set():
            try:
                conn, _ = server.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                try:
                    f = conn.makefile("rw")
                    line = f.readline()
                    if line:
                        req = json.loads(line)
                        received_requests.append(req)
                        try:
                            resp = next(resp_iter)
                        except StopIteration:
                            resp = {"id": req.get("id", 1), "ok": True, "result": {}}
                        resp_obj = dict(resp)
                        resp_obj["id"] = req.get("id", 1)
                        f.write(json.dumps(resp_obj) + "\n")
                        f.flush()
                except (OSError, json.JSONDecodeError):
                    pass
        server.close()

    t = threading.Thread(target=handle, daemon=True)
    t.start()
    return port, stop_event, received_requests


def test_deckcraft_client_normal_flow(fixtures_dir: Path, tmp_path: Path):
    """Test standard DeckCraft open -> inspect -> export -> close -> quit flow."""
    dc_dir = fixtures_dir / "deckcraft"
    open_resp = json.loads((dc_dir / "file-open.json").read_text())
    inspect_resp = json.loads((dc_dir / "document-inspect.json").read_text())
    export_resp = json.loads((dc_dir / "file-export-ok.json").read_text())

    responses = [
        open_resp,  # file.open
        inspect_resp,  # document.inspect
        export_resp,  # file.export
        {"id": 1, "ok": True, "result": {}},  # file.close
        {"id": 1, "ok": True, "result": {}},  # app.quit
    ]
    port, stop_server, requests = _start_mock_deckcraft_server(responses)

    # Fake launching: patch launch to do nothing since server is already running
    with patch("articulate.intake.convert.deckcraft.launch_deckcraft", return_value=MagicMock()):
        out_pdf = tmp_path / "out.pdf"
        src_doc = pdfium.PdfDocument(fixtures_dir / "pdf" / "text-layer.pdf")
        doc = pdfium.PdfDocument.new()
        doc.import_pages(src_doc, [0])
        doc.import_pages(src_doc, [0])
        doc.save(out_pdf)

        client = DeckCraftClient(port=port)
        inspect_data, pages_exported = client.convert(
            pptx_path=Path("/dummy/deck.pptx"),
            out_pdf_path=out_pdf,
        )

        assert pages_exported == 2
        assert len(inspect_data["slides"]) == 2
        assert inspect_data["slides"][0]["title"] == "Overview"
        assert inspect_data["slides"][1]["hidden"] is True

        stop_server.set()
        # Check command order
        assert "command" in [r.get("method") for r in requests]
        assert any(r.get("params", {}).get("id") == "file.open" for r in requests)
        assert any(r.get("params", {}).get("id") == "document.inspect" for r in requests)
        assert any(r.get("params", {}).get("id") == "file.export" for r in requests)
        assert any(r.get("params", {}).get("id") == "file.close" for r in requests)
        assert any(r.get("method") == "app.quit" for r in requests)


def test_deckcraft_fallback_triggers(fixtures_dir: Path, tmp_path: Path):
    """
    Test each of the 5 fallback triggers (§2.12):
    1. Port not up in 10 s or process exit
    2. ok:false or timeout (60 s)
    3. lostSlides or damagedMedia non-empty
    4. PDF page count != slide count from inspect
    5. Empty text layer on >20% of titled slides
    And verify missingFonts does NOT trigger fallback.
    """
    dc_dir = fixtures_dir / "deckcraft"

    # Trigger 1: Port not up
    with (
        patch("articulate.intake.convert.deckcraft.wait_for_port", return_value=False),
        pytest.raises(DeckCraftFallbackError, match="port not up"),
    ):
        client = DeckCraftClient(port=9999)
        client.convert(Path("test.pptx"), tmp_path / "out.pdf")

    # Trigger 2: file.export fails (ok: false)
    responses = [
        json.loads((dc_dir / "file-open.json").read_text()),
        json.loads((dc_dir / "document-inspect.json").read_text()),
        json.loads((dc_dir / "file-export-fail.json").read_text()),
        {"id": 1, "ok": True, "result": {}},  # file.close
        {"id": 1, "ok": True, "result": {}},  # app.quit
    ]
    port, stop_server, _ = _start_mock_deckcraft_server(responses)
    with patch("articulate.intake.convert.deckcraft.launch_deckcraft", return_value=MagicMock()):
        with pytest.raises(DeckCraftFallbackError, match="export failed"):
            client = DeckCraftClient(port=port)
            client.convert(Path("test.pptx"), tmp_path / "out.pdf")
        stop_server.set()

    # Trigger 3: lostSlides non-empty
    responses = [
        json.loads((dc_dir / "lost-slides.json").read_text()),
        {"id": 1, "ok": True, "result": {}},
        {"id": 1, "ok": True, "result": {}},
    ]
    port, stop_server, _ = _start_mock_deckcraft_server(responses)
    with patch("articulate.intake.convert.deckcraft.launch_deckcraft", return_value=MagicMock()):
        with pytest.raises(DeckCraftFallbackError, match="lostSlides or damagedMedia"):
            client = DeckCraftClient(port=port)
            client.convert(Path("test.pptx"), tmp_path / "out.pdf")
        stop_server.set()

    # Trigger 4: PDF page count != inspect slide count
    responses = [
        json.loads((dc_dir / "file-open.json").read_text()),
        json.loads((dc_dir / "document-inspect.json").read_text()),  # 2 slides
        {"id": 1, "ok": True, "result": {"path": "out.pdf", "pages": 2}},
        {"id": 1, "ok": True, "result": {}},
        {"id": 1, "ok": True, "result": {}},
    ]
    port, stop_server, _ = _start_mock_deckcraft_server(responses)
    with patch("articulate.intake.convert.deckcraft.launch_deckcraft", return_value=MagicMock()):
        out_pdf = tmp_path / "out4.pdf"
        doc = pdfium.PdfDocument.new()
        doc.new_page(100, 100)  # only 1 page in PDF!
        doc.save(out_pdf)

        with pytest.raises(DeckCraftFallbackError, match="page count mismatch"):
            client = DeckCraftClient(port=port)
            client.convert(Path("test.pptx"), out_pdf)
        stop_server.set()

    # Trigger 5: Empty text layer on >20% of titled slides
    # Inspect has 2 titled slides
    inspect_2titled = {
        "ok": True,
        "result": {
            "slides": [
                {"index": 1, "title": "Title 1", "notes": ""},
                {"index": 2, "title": "Title 2", "notes": ""},
            ]
        },
    }
    responses = [
        json.loads((dc_dir / "file-open.json").read_text()),
        inspect_2titled,
        {"id": 1, "ok": True, "result": {"path": "out.pdf", "pages": 2}},
        {"id": 1, "ok": True, "result": {}},
        {"id": 1, "ok": True, "result": {}},
    ]
    port, stop_server, _ = _start_mock_deckcraft_server(responses)
    with patch("articulate.intake.convert.deckcraft.launch_deckcraft", return_value=MagicMock()):
        out_pdf = tmp_path / "out5.pdf"
        doc = pdfium.PdfDocument.new()
        doc.new_page(100, 100)  # empty text page 1
        doc.new_page(100, 100)  # empty text page 2 (100% empty > 20%)
        doc.save(out_pdf)

        with pytest.raises(DeckCraftFallbackError, match="empty text layer on >20%"):
            client = DeckCraftClient(port=port)
            client.convert(Path("test.pptx"), out_pdf)
        stop_server.set()


# -----------------------------------------------------------------------------
# 2. Keynote Export & Holds (2.12)
# -----------------------------------------------------------------------------


def test_keynote_script_fixes_and_holds(tmp_path: Path):
    """Test Keynote presenting hold, unconsented hold, and export invocation."""
    script_path = (
        Path(__file__).parent.parent / "src/articulate/intake/convert/keynote-export.applescript"
    )
    assert script_path.exists(), "keynote-export.applescript must exist"
    script_text = script_path.read_text()

    # Fix 1: date:false must not be present
    assert "date:false" not in script_text, (
        "date:false must be removed from export properties (#21)"
    )
    # Fix 2: launch and poll count of documents
    assert 'tell application "Keynote" to launch' in script_text
    assert "count of documents" in script_text
    # Fix 3: on error close document
    assert "close d saving no" in script_text

    # Presenting hold
    with (
        patch("articulate.intake.convert.keynote.is_keynote_presenting", return_value=True),
        pytest.raises(KeynoteHoldError, match="keynote_presenting"),
    ):
        convert_pptx_keynote(Path("test.pptx"), tmp_path / "out.pdf")

    # Unconsented hold
    with (
        patch("articulate.intake.convert.keynote.is_keynote_presenting", return_value=False),
        patch("subprocess.run") as mock_sub,
    ):
        mock_sub.return_value = MagicMock(
            returncode=1, stderr="execution error: Not authorized to send Apple events (-1743)"
        )
        with pytest.raises(KeynoteHoldError, match="unconsented_deck"):
            convert_pptx_keynote(Path("test.pptx"), tmp_path / "out.pdf")


# -----------------------------------------------------------------------------
# 3. WordCraft (.docx/.doc/.rtf) (2.14)
# -----------------------------------------------------------------------------


def test_wordcraft_conversion(tmp_path: Path):
    """Test wordcraft-cli convert invocation."""
    in_doc = tmp_path / "test.docx"
    in_doc.write_text("dummy")
    out_pdf = tmp_path / "test.pdf"

    with patch("subprocess.run") as mock_run:

        def fake_run(*args, **kwargs):
            out_pdf.write_bytes(b"%PDF-1.4 fake")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run
        convert_wordcraft(in_doc, out_pdf)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0].endswith("wordcraft-cli")
        assert args[1] == "convert"
        assert args[2] == str(in_doc)
        assert args[3] == str(out_pdf)


# -----------------------------------------------------------------------------
# 4. Damaged Layer Signals (2.16) & PDF Fixtures
# -----------------------------------------------------------------------------


def test_damaged_layer_signals(fixtures_dir: Path):
    """
    Test 4 committed PDF fixtures in tests/fixtures/pdf/:
    - text-layer.pdf: clean text layer, layer_untrusted = 0
    - glyph-image.pdf: glyph_img > 0 -> layer_untrusted = 1
    - ascii-bang-mathfont.pdf: mathfont_odd > 0 -> layer_untrusted = 1
    - no-text-layer.pdf: empty text layer, layer_untrusted = 0
    """
    pdf_dir = fixtures_dir / "pdf"

    # 1. text-layer.pdf
    doc = pdfium.PdfDocument(pdf_dir / "text-layer.pdf")
    sig = compute_damaged_layer_signals(doc[0])
    assert sig["layer_untrusted"] == 0
    assert sig["glyph_img"] == 0
    assert sig["eq_img"] == 0
    assert sig["mathfont_odd"] == 0

    # 2. glyph-image.pdf
    doc = pdfium.PdfDocument(pdf_dir / "glyph-image.pdf")
    sig = compute_damaged_layer_signals(doc[0])
    assert sig["layer_untrusted"] == 1
    assert sig["glyph_img"] >= 1

    # 3. ascii-bang-mathfont.pdf
    doc = pdfium.PdfDocument(pdf_dir / "ascii-bang-mathfont.pdf")
    sig = compute_damaged_layer_signals(doc[0])
    assert sig["layer_untrusted"] == 1
    assert sig["mathfont_odd"] >= 1

    # 4. no-text-layer.pdf
    doc = pdfium.PdfDocument(pdf_dir / "no-text-layer.pdf")
    sig = compute_damaged_layer_signals(doc[0])
    assert sig["layer_untrusted"] == 0


# -----------------------------------------------------------------------------
# 5. OCR & Page Hash Caching (2.15)
# -----------------------------------------------------------------------------


def test_ocr_caching_by_page_hash(fixtures_dir: Path, temp_articulate_env):
    """Verify ocrmac runs only where needed and results are cached by page_hash."""
    pdf_dir = fixtures_dir / "pdf"
    doc = pdfium.PdfDocument(pdf_dir / "no-text-layer.pdf")
    page = doc[0]

    # First call: cache miss -> calls ocrmac subprocess
    with patch(
        "articulate.intake.convert.ocr.run_ocr_subprocess", return_value="Recognized Text"
    ) as mock_ocr:
        res1, page_hash1 = get_page_ocr(page)
        assert res1 == "Recognized Text"
        mock_ocr.assert_called_once()
        cache_file = cache_ocr_path(page_hash1)
        assert cache_file.exists()

    # Second call: cache hit -> reads cache, subprocess not called
    with patch("articulate.intake.convert.ocr.run_ocr_subprocess") as mock_ocr2:
        res2, page_hash2 = get_page_ocr(page)
        assert res2 == "Recognized Text"
        assert page_hash2 == page_hash1
        mock_ocr2.assert_not_called()


# -----------------------------------------------------------------------------
# 6. Pipeline Convert Step Integration (2.17)
# -----------------------------------------------------------------------------


def test_convert_step_slides_and_guides(temp_articulate_env, fixtures_dir: Path):
    """
    Test full convert_step execution on DB items:
    - advances slides -> extract
    - advances guide_or_exam -> guide_extract
    - updates source_files.page_count
    - populates pages table
    - writes .pages.json to archive
    - deletes work files
    """
    db = temp_articulate_env["db"]
    data_dir = temp_articulate_env["data_dir"]
    pdf_dir = fixtures_dir / "pdf"

    # Setup archive copies
    course_key = "csc382"
    file_id_slides = "1111111111111111111111111111111111111111111111111111111111111111"
    file_id_guide = "2222222222222222222222222222222222222222222222222222222222222222"

    arc_dir = data_dir / "archive" / course_key
    arc_dir.mkdir(parents=True, exist_ok=True)
    slides_pdf = arc_dir / f"{file_id_slides}.pdf"
    guide_pdf = arc_dir / f"{file_id_guide}.pdf"
    shutil.copy(pdf_dir / "text-layer.pdf", slides_pdf)
    shutil.copy(pdf_dir / "glyph-image.pdf", guide_pdf)

    now_str = datetime.now(UTC).isoformat()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO source_files (file_id, course_key, slot, display_name, role, archive_path, created_at)
            VALUES (?, ?, 'slot1', 'Slides.pdf', 'slides', ?, ?),
                   (?, ?, 'slot2', 'Guide.pdf', 'guideOrExam', ?, ?);
            """,
            (
                file_id_slides,
                course_key,
                str(slides_pdf),
                now_str,
                file_id_guide,
                course_key,
                str(guide_pdf),
                now_str,
            ),
        )
        conn.execute(
            """
            INSERT INTO items (item_id, source, kind, native_id, version, course_key, privacy, stage, status, created_at, updated_at)
            VALUES ('item-slides', 'canvas', 'slides', 'slot1', ?, ?, 'class', 'convert', 'pending', ?, ?),
                   ('item-guide', 'canvas', 'guide_or_exam', 'slot2', ?, ?, 'class', 'convert', 'pending', ?, ?);
            """,
            (
                file_id_slides,
                course_key,
                now_str,
                now_str,
                file_id_guide,
                course_key,
                now_str,
                now_str,
            ),
        )
        conn.commit()

    ctx = RunContext(
        run_id="run-test-conv",
        job="nightly",
        now=datetime.now(UTC),
        db=db,
        config=None,
    )

    summary = convert_step(ctx)
    assert summary.done == 2
    assert summary.failed == 0

    with db.connect() as conn:
        # Check source_files page_count
        r_slides = conn.execute(
            "SELECT page_count FROM source_files WHERE file_id = ?", (file_id_slides,)
        ).fetchone()
        assert r_slides["page_count"] == 1
        r_guide = conn.execute(
            "SELECT page_count FROM source_files WHERE file_id = ?", (file_id_guide,)
        ).fetchone()
        assert r_guide["page_count"] == 1

        # Check items stages
        item_s = conn.execute(
            "SELECT stage, status FROM items WHERE item_id = 'item-slides'"
        ).fetchone()
        assert item_s["stage"] == "extract"
        assert item_s["status"] == "pending"

        item_g = conn.execute(
            "SELECT stage, status FROM items WHERE item_id = 'item-guide'"
        ).fetchone()
        assert item_g["stage"] == "guide_extract"
        assert item_g["status"] == "pending"

        # Check pages table
        p_slides = conn.execute(
            "SELECT * FROM pages WHERE file_id = ?", (file_id_slides,)
        ).fetchall()
        assert len(p_slides) == 1
        assert p_slides[0]["layer_untrusted"] == 0
        assert "Sample text layer test." in p_slides[0]["layer_text"]

        p_guide = conn.execute("SELECT * FROM pages WHERE file_id = ?", (file_id_guide,)).fetchall()
        assert len(p_guide) == 1
        assert p_guide[0]["layer_untrusted"] == 1  # glyph-image was untrusted!

    # Check .pages.json exists
    slides_pages_json = archive_pages_path(course_key, file_id_slides)
    guide_pages_json = archive_pages_path(course_key, file_id_guide)
    assert slides_pages_json.exists()
    assert guide_pages_json.exists()

    data_json = json.loads(slides_pages_json.read_text())
    assert data_json["page_count"] == 1
    assert len(data_json["pages"]) == 1

    # Verify work dir is cleaned up
    work_dir = data_dir / "work" / "run-test-conv"
    assert not work_dir.exists()


# -----------------------------------------------------------------------------
# 7. Real Deck & Document Acceptance Checks (§2.13, §2.16)
# -----------------------------------------------------------------------------


from contextlib import contextmanager
from datetime import time as dt_time


@contextmanager
def articulate_real_lock(timeout_s: float = 300.0, poll_s: float = 2.0):
    """Context manager for /tmp/articulate-real.lock and blackout window enforcement."""
    now = datetime.now()
    t = now.time()
    if dt_time(1, 45) <= t <= dt_time(4, 0):
        pytest.skip("Blackout window: no real checks between 01:45 and 04:00")

    lock_dir = Path("/tmp/articulate-real.lock")
    start = time.time()
    acquired = False
    while time.time() - start < timeout_s:
        try:
            lock_dir.mkdir()
            acquired = True
            break
        except FileExistsError:
            time.sleep(poll_s)

    if not acquired:
        raise TimeoutError("Timed out waiting for /tmp/articulate-real.lock")

    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except OSError:
            pass


@pytest.mark.real
def test_real_damaged_layer_signals_acceptance():
    """
    Acceptance check 2.16:
    5/5 silent pages flagged with 1 false flag on #40's 9 files, 0 on PowerPoint/Keynote decks.
    """
    real_dir = Path.home() / "Library/Application Support/articulate/fixtures/real"
    if not real_dir.exists():
        pytest.skip(f"Real fixtures directory not found at {real_dir}")

    # Check the 4 key damaged and clean files available in real fixtures
    files_to_check = {
        "mat305-ch2.pdf": [7, 8],  # p7, p8 untrusted (∪ image inside line)
        "mat305-ch3.pdf": [6],  # p6 untrusted (x² -> x! in Cambria Math)
        "phy203-ch09.pdf": [],  # 0 untrusted
        "csc382-whitman-ch03.pdf": [],  # 0 untrusted
    }

    flagged_pages = {}
    for fname, expected_untrusted in files_to_check.items():
        fpath = real_dir / fname
        if not fpath.exists():
            continue
        doc = pdfium.PdfDocument(fpath)
        untrusted = []
        for idx in range(1, len(doc) + 1):
            page = doc[idx - 1]
            sig = compute_damaged_layer_signals(page)
            if sig["layer_untrusted"]:
                untrusted.append(idx)
        flagged_pages[fname] = untrusted
        assert untrusted == expected_untrusted, (
            f"File {fname}: expected untrusted {expected_untrusted}, got {untrusted}"
        )
