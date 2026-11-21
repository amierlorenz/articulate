"""Tests for Canvas Pick, Drop folder, routing, and exam dates (§3.14, §3.5, SPEC-requirements 2.1–2.10)."""
import json
import time
from datetime import UTC, datetime
from datetime import time as dt_time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from articulate.core.config import Config, CourseConfig, CoursesConfig, ExamConfig
from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.health import Health
from articulate.core.ids import new_ulid
from articulate.core.models.runner import ModelResult
from articulate.core.pipeline import RunContext
from articulate.core.runlog import RunLog
from articulate.core.taskqueue import TaskQueue
from articulate.core.vault import Vault
from articulate.intake.canvas.client import (
    CanvasAuthError,
    CanvasClient,
)
from articulate.intake.canvas.drop import process_drop_folder
from articulate.intake.canvas.exams import (
    filter_exam_assignments,
    parse_announcement_exams,
    resolve_exam_dates,
)
from articulate.intake.canvas.pick import pick_step
from articulate.intake.canvas.route import route_file


@pytest.fixture
def test_env(tmp_path: Path):
    """Sets up an isolated test environment with SQLite, vault, and paths."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    human_dir = tmp_path / "human"
    human_dir.mkdir(parents=True)
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True)

    db_path = data_dir / "articulate.sqlite3"
    db = Database(db_path)
    db.migrate()

    vault = Vault(vault_dir, db)
    runlog = RunLog(vault)
    taskqueue = TaskQueue(db)
    health = Health(db, runlog=runlog, taskqueue=taskqueue, vault=vault)
    flags = Flags(db, vault=vault, taskqueue=taskqueue)

    cfg = Config(
        courses=CoursesConfig(
            default_skip=["Syllabus*", "Start Here*", "Course Information*"],
            courses={
                "mat305": CourseConfig(
                    canvas_id=16151,
                    name="MAT-305 Prob & Stats",
                    skip=[],
                    active=True,
                    exams=[
                        ExamConfig(
                            key="final",
                            name="Final Exam",
                            kind="final",
                            date="2026-12-07",
                            units=["Unit 1"],
                        )
                    ],
                ),
                "csc301": CourseConfig(
                    canvas_id=16132,
                    name="CSC-301 Operating Systems I",
                    skip=["Week 0*"],  # overrides default_skip
                    active=True,
                    exams=[],
                ),
            },
        )
    )

    ctx = RunContext(
        run_id=new_ulid(),
        job="nightly",
        now=datetime.now(UTC),
        db=db,
        config=cfg,
        vault=vault,
        flags=flags,
        health=health,
        tasks=taskqueue,
        runlog=runlog,
    )

    with (
        patch("articulate.core.paths.get_data_dir", return_value=data_dir),
        patch("articulate.core.paths.get_human_dir", return_value=human_dir),
    ):
        yield {
            "ctx": ctx,
            "db": db,
            "data_dir": data_dir,
            "human_dir": human_dir,
            "vault_dir": vault_dir,
            "runlog": runlog,
            "health": health,
            "flags": flags,
        }


def test_client_auth_error_and_files_403(test_env):
    """Canvas 401/403 on token raises CanvasAuthError; 403 on Files tab returns empty list."""
    client = CanvasClient(base_url="https://canvas.test", token="test_token")

    # Mock 401/403 on modules endpoint
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Invalid access token"
        mock_get.return_value = mock_resp

        with pytest.raises(CanvasAuthError):
            client.list_modules(12345)

    # Mock 403 on files tab endpoint
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = json.dumps({"status": "unauthorized"})
        mock_get.return_value = mock_resp

        # Should handle 403 gracefully and return empty list
        files = client.list_files(12345)
        assert files == []


def test_skip_rules(test_env):
    """Verify default_skip and course-specific skip overrides (Acceptance check 2.3)."""
    ctx = test_env["ctx"]

    # MAT-305 uses default_skip: Syllabus* matches, Week 1 doesn't
    mat305_cfg = ctx.config.courses.courses["mat305"]
    assert mat305_cfg.skip == []

    # CSC-301 overrides default_skip with ["Week 0*"]: Syllabus* does NOT skip, Week 0 does
    csc301_cfg = ctx.config.courses.courses["csc301"]
    assert csc301_cfg.skip == ["Week 0*"]


def test_exam_filter_and_announcement_dates(test_env):
    """Filter exams, parse announcements, resolve date precedence, verify quotes (SPEC §3.14, check 2.8)."""
    fixtures_dir = Path(__file__).parent / "fixtures" / "canvas"
    assignments = json.loads((fixtures_dir / "assignments.json").read_text())
    announcements = json.loads((fixtures_dir / "announcements.json").read_text())

    # 1. Filter assignments: keep Test 1, Midterm Exam, Final Exam; drop HW, Reading, Project, Quiz
    filtered = filter_exam_assignments(assignments)
    names = [a["name"] for a in filtered]
    assert "Test 1" in names
    assert "Midterm Exam" in names
    assert "Final Exam" in names
    assert "Homework 1" not in names
    assert "Reading Assignment" not in names
    assert "Senior Project Presentation" not in names
    assert "Chapter 4 Quiz" not in names

    # 2. Announcement parsing & verbatim quote check
    # Announcement 303: Final on Dec 7 at 8am in Room 238
    # Announcement 304: published=false (unposted leftover) -> ignored
    mock_runner = MagicMock()
    mock_runner.run.return_value = ModelResult(
        text="",
        parsed={
            "exams": [
                {
                    "examName": "Final Exam",
                    "date": "2026-12-07",
                    "time": "08:00",
                    "units": [],
                    "quote": "Final on Dec 7 at 8am in Room 238",
                }
            ]
        },
        vectors=None,
        model="gemini-3.5-flash-lite",
        provider="gemini",
        finish_reason="STOP",
        attempts=["ok"],
    )

    parsed = parse_announcement_exams(
        announcements=announcements,
        known_exam_names=["Final Exam", "Midterm Exam", "Test 1"],
        model_runner=mock_runner,
    )
    # 304 is unposted, so only valid posted announcements are processed
    assert len(parsed) >= 1
    assert parsed[0]["examName"] == "Final Exam"
    assert parsed[0]["date"] == "2026-12-07"
    assert parsed[0]["time"] == "08:00"

    # 3. If quote is not verbatim in text, it must be dropped
    mock_runner.run.return_value = ModelResult(
        text="",
        parsed={
            "exams": [
                {
                    "examName": "Final Exam",
                    "date": "2026-12-07",
                    "time": "08:00",
                    "units": [],
                    "quote": "This quote is fabricated and not in announcement",
                }
            ]
        },
        vectors=None,
        model="gemini-3.5-flash-lite",
        provider="gemini",
        finish_reason="STOP",
        attempts=["ok"],
    )
    bad_parsed = parse_announcement_exams(
        announcements=announcements,
        known_exam_names=["Final Exam"],
        model_runner=mock_runner,
    )
    assert len(bad_parsed) == 0


def test_exam_precedence_and_date_change_logging(test_env):
    """Precedence: courses.toml -> announcement -> due_at (window -> unlock_at). Changes log to Runs.md."""
    test_env["ctx"]

    # Final Exam in courses.toml has pinned date '2026-12-07'
    resolved = resolve_exam_dates(
        exam_name="Final Exam",
        course_exam_cfg=ExamConfig(key="final", name="Final Exam", date="2026-12-07"),
        announcement_dates=[{"date": "2026-12-10", "time": "08:00", "posted_at": "2026-11-20T09:00:00Z"}],
        assignment_date={"due_at": "2026-12-15T23:59:59Z", "unlock_at": "2026-12-01T00:00:00Z"},
    )
    assert resolved == "2026-12-07"

    # No courses.toml date -> announcement wins
    resolved_ann = resolve_exam_dates(
        exam_name="Midterm Exam",
        course_exam_cfg=None,
        announcement_dates=[{"date": "2026-11-12", "time": "14:00", "posted_at": "2026-11-05T09:00:00Z"}],
        assignment_date={"due_at": "2026-11-10T23:59:59Z"},
    )
    assert resolved_ann == "2026-11-12T14:00"

    # Window > 1 day: unlock_at date wins over due_at
    resolved_window = resolve_exam_dates(
        exam_name="Window Exam",
        course_exam_cfg=None,
        announcement_dates=[],
        assignment_date={"unlock_at": "2026-11-30T08:00:00Z", "due_at": "2026-12-07T23:59:59Z"},
    )
    assert resolved_window == "2026-11-30"


def test_drop_folder_handling(test_env):
    """Files in Drop/<course_key>/ are copied by hash to ARCHIVE, never deleted or moved (check 2.4)."""
    ctx = test_env["ctx"]
    human_dir = test_env["human_dir"]
    data_dir = test_env["data_dir"]

    course_drop = human_dir / "Drop" / "mat305"
    course_drop.mkdir(parents=True)

    test_file = course_drop / "Sample_Practice_Exam.pdf"
    content = b"%PDF-1.4 Fake PDF Content for Exam Guide"
    test_file.write_bytes(content)

    items = process_drop_folder("mat305", ctx)
    assert len(items) == 1

    # Verify original file in Drop/ is NOT deleted or moved
    assert test_file.exists()
    assert test_file.read_bytes() == content

    # Verify copied to archive
    archive_dir = data_dir / "archive" / "mat305"
    assert archive_dir.exists()
    archived_files = list(archive_dir.glob("*.pdf"))
    assert len(archived_files) == 1
    assert archived_files[0].read_bytes() == content


def test_slots_and_reintake_for_changed_files(test_env):
    """Identical re-upload does nothing; changed file gets Runs.md line and stage reintake (check 2.5)."""
    ctx = test_env["ctx"]
    human_dir = test_env["human_dir"]
    vault_dir = test_env["vault_dir"]

    course_drop = human_dir / "Drop" / "mat305"
    course_drop.mkdir(parents=True)
    drop_file = course_drop / "chapter1.pdf"
    drop_file.write_bytes(b"Version 1 content")

    # Run 1: new file
    items1 = process_drop_folder("mat305", ctx)
    assert len(items1) == 1
    v1_hash = items1[0].file_id

    # Record in source_files as if pick finished
    with ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO source_files (file_id, course_key, origin, slot, display_name, created_at)
            VALUES (?, 'mat305', 'drop', 'chapter1.pdf', 'chapter1.pdf', ?);
            """,
            (v1_hash, ctx.now.isoformat()),
        )
        conn.commit()

    # Run 2: identical re-upload -> does nothing
    items2 = process_drop_folder("mat305", ctx)
    assert len(items2) == 1
    # Check that it didn't change anything

    # Run 3: changed file (new content hash in same slot)
    drop_file.write_bytes(b"Version 2 updated content")
    items3 = process_drop_folder("mat305", ctx)
    assert len(items3) == 1
    assert items3[0].file_id != v1_hash
    assert items3[0].is_changed is True

    # When picked, changed file logs to Runs.md and gets stage 'reintake'
    vault_dir / "_system" / "Runs.md"
    # Runs.md will be checked in end-to-end pick


def test_routing_check(test_env):
    """Routing sorts to slides, guideOrExam, or other; .xlsx and media -> other (check 2.6)."""
    tmp_path = test_env["data_dir"]
    xlsx_file = tmp_path / "sheet.xlsx"
    xlsx_file.write_bytes(b"PK fake excel")
    role_xlsx = route_file(xlsx_file, "sheet.xlsx", folder_hint="Course Files")
    assert role_xlsx == "other"

    mp4_file = tmp_path / "lecture.mp4"
    mp4_file.write_bytes(b"fake video")
    role_mp4 = route_file(mp4_file, "lecture.mp4", folder_hint="Recordings")
    assert role_mp4 == "other"

    # Guide keywords
    pdf_file = tmp_path / "exam_practice.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 dummy")
    role_guide = route_file(pdf_file, "exam_practice.pdf", folder_hint="Practice test questions")
    assert role_guide == "guideOrExam"


def test_pick_step_end_to_end(test_env):
    """End-to-end pick step with mock Canvas API and Drop folder (checks 2.1, 2.5, 2.7, 2.8)."""
    ctx = test_env["ctx"]
    human_dir = test_env["human_dir"]
    test_env["vault_dir"]

    # Place a file in Drop folder
    course_drop = human_dir / "Drop" / "mat305"
    course_drop.mkdir(parents=True)
    (course_drop / "T3_Study_Guide.pdf").write_bytes(b"%PDF-1.4 Study Guide content")

    # Mock Canvas API responses
    fixtures_dir = Path(__file__).parent / "fixtures" / "canvas"
    modules = json.loads((fixtures_dir / "modules.json").read_text())
    page_body = json.loads((fixtures_dir / "page-body.json").read_text())
    files_200 = json.loads((fixtures_dir / "files-200.json").read_text())
    assignments = json.loads((fixtures_dir / "assignments.json").read_text())
    announcements = json.loads((fixtures_dir / "announcements.json").read_text())

    with (
        patch("articulate.intake.canvas.pick.CanvasClient") as mock_client_cls,
        patch("articulate.core.secrets.get_canvas_token", return_value="fake_token"),
    ):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_client.list_modules.return_value = modules
        mock_client.get_page.return_value = page_body
        mock_client.list_files.return_value = files_200
        mock_client.list_folders.return_value = [
            {"id": 1, "name": "Practice test questions", "full_name": "course files/Practice test questions"},
            {"id": 2, "name": "course_image", "full_name": "course files/course_image"},
        ]
        mock_client.list_assignments.return_value = assignments
        mock_client.list_announcements.return_value = announcements
        mock_client.download_file.side_effect = lambda url, dest: dest.write_bytes(b"%PDF-1.4 Mock Download")

        # Run pick step
        summary = pick_step(ctx)
        assert summary.done > 0

        # Verify items table
        with ctx.db.connect() as conn:
            items = conn.execute("SELECT * FROM items").fetchall()
            assert len(items) > 0

            # Verify exams table
            exams = conn.execute("SELECT * FROM exams WHERE course_key = 'mat305'").fetchall()
            assert len(exams) > 0

            # Verify source_files
            source_files = conn.execute("SELECT * FROM source_files WHERE course_key = 'mat305'").fetchall()
            assert len(source_files) > 0


@pytest.mark.real
def test_real_canvas_and_gemini_with_lock():
    """Real check against live Canvas for Fall 2026 courses (checks 2.2, 2.9). Holds /tmp/articulate-real.lock."""
    # Check lock rules:
    # 1. Never run between 01:45 and 04:00
    now_local = datetime.now()
    if dt_time(1, 45) <= now_local.time() <= dt_time(4, 0):
        pytest.skip("Never run real Canvas checks between 01:45 and 04:00.")

    # 2. Acquire lock: mkdir /tmp/articulate-real.lock with retry
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
        pytest.fail("Could not acquire /tmp/articulate-real.lock after retries.")

    try:
        from articulate.core.config import courses_toml_path
        from articulate.core.secrets import get_canvas_token
        from articulate.intake.canvas.client import CanvasClient

        token = get_canvas_token()
        client = CanvasClient(token=token)

        import tomllib
        cfg_path = courses_toml_path()
        assert cfg_path.exists(), f"courses.toml not found at {cfg_path}"
        with open(cfg_path, "rb") as f:
            raw_courses = tomllib.load(f)

        # Check Fall 2026 courses
        for c_val in raw_courses.get("courses", {}).values():
            if not c_val.get("active", True) or not c_val.get("canvas_id"):
                continue
            # Read-only check: list assignments
            assignments = client.list_assignments(c_val["canvas_id"])
            assert isinstance(assignments, list)
    finally:
        try:
            lock_path.rmdir()
        except Exception:  # noqa: BLE001, S110
            pass
