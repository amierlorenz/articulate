"""Tests for Encore hand-off package (§3.7, Acceptance check 2.35–2.38, Issue #65)."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from articulate.core.config import ArticulateConfig, Config, EncoreConfig, VaultConfig
from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.handoff_schemas import (
    ImportBatch,
    load_handoff_file,
)
from articulate.core.health import Health
from articulate.core.pipeline import RunContext
from articulate.core.taskqueue import TaskQueue
from articulate.core.vault import Vault
from articulate.handoff import step
from articulate.handoff.batch import build_import_batch
from articulate.handoff.encore import LaunchResult, launch_and_wait
from articulate.handoff.ingest import ingest_from_encore
from articulate.handoff.write import validate_import_batch, write_import_batch
from articulate.registry import STEPS


@pytest.fixture
def test_db(tmp_path):
    """Database with core and handoff migrations applied."""
    db_file = tmp_path / "test_articulate.sqlite3"
    db = Database(path=db_file)
    db.migrate()
    return db


@pytest.fixture
def mock_ctx(tmp_path, test_db, monkeypatch):
    """RunContext pointing to temporary directories."""
    handoff_dir = tmp_path / "Encore" / "Handoff"
    to_enc = handoff_dir / "to-encore"
    to_enc_failed = to_enc / "failed"
    from_enc = handoff_dir / "from-encore"
    vault_dir = tmp_path / "Vault"

    for d in [to_enc, to_enc_failed, from_enc, vault_dir]:
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("ARTICULATE_HANDOFF", str(handoff_dir))
    monkeypatch.setenv("ARTICULATE_DATA", str(tmp_path / "data"))

    vault = Vault(vault_dir, test_db)
    tq = TaskQueue(test_db)
    health = Health(test_db, taskqueue=tq, vault=vault)
    flags = Flags(test_db, vault=vault, taskqueue=tq)

    cfg = Config(
        articulate=ArticulateConfig(
            vault=VaultConfig(path=str(vault_dir)),
            encore=EncoreConfig(bundle_id="com.amierlorenz.Encore", state_wait_s=180),
        )
    )

    ctx = RunContext(
        run_id="run-test",
        job="nightly",
        now=datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC),
        db=test_db,
        config=cfg,
        vault=vault,
        flags=flags,
        health=health,
        tasks=tq,
    )
    return ctx


def _seed_sample_course(conn: sqlite3.Connection):
    """Helper to seed course, units, contrast groups, and source files."""
    conn.execute(
        """
        INSERT INTO courses (course_key, name, canvas_id, coding, active)
        VALUES ('csc301', 'Operating Systems', 101, 1, 1);
        """
    )
    conn.execute(
        """
        INSERT INTO units (unit_id, course_key, name, position)
        VALUES ('csc301.u.processes', 'csc301', 'Processes', 1);
        """
    )
    conn.execute(
        """
        INSERT INTO units (unit_id, course_key, name, position)
        VALUES ('csc301.u.memory', 'csc301', 'Memory Management', 2);
        """
    )
    conn.execute(
        """
        INSERT INTO source_files (file_id, course_key, origin, slot, display_name, page_count, archive_path)
        VALUES ('f.1234567890abcdef', 'csc301', 'canvas', 'slides', 'Lec1.pdf', 40, 'sha256abc123');
        """
    )
    contrast_groups_json = json.dumps(
        [
            {
                "conceptA": "csc301.mutex",
                "conceptB": "csc301.semaphore",
                "decidingFeature": {
                    "text": "Mutex allows only one holder; semaphore maintains a counter.",
                    "provenance": "cited",
                    "citations": [
                        {
                            "sourceFileId": "f.1234567890abcdef",
                            "page": 10,
                            "quote": "A mutex is locked by a single thread.",
                            "status": "verified",
                            "verifiedBy": "text",
                        }
                    ],
                },
            }
        ]
    )
    conn.execute(
        """
        INSERT INTO contrast_groups (group_id, course_key, name, question, member_ids_json, contrasts_json, status)
        VALUES ('csc301.g.sync', 'csc301', 'Synchronization', 'Which primitive?', '["csc301.mutex", "csc301.semaphore"]', ?, 'active');
        """,
        (contrast_groups_json,),
    )


def test_registry_registration():
    """Verify handoff.export is registered at order 340 for nightly and pass2 (§3.12)."""
    step_found = next((s for s in STEPS if s.name == "handoff.export"), None)
    assert step_found is not None
    assert step_found.order == 340
    assert step_found.jobs == {"nightly", "pass2"}
    assert step_found.entry == "articulate.handoff:step"


def test_batch_carries_only_changed_content_and_validates(mock_ctx, tmp_path):
    """
    Test delta building:
    - Active concepts must have active prompts to ship.
    - Full sourceFiles and contrastGroups included per course.
    - Validates against schemas/encore.import.v2.json.
    - Written atomically via tmp + rename.
    """
    with mock_ctx.db.connect() as conn:
        _seed_sample_course(conn)

        # 1. Concept without prompts -> should NOT ship
        conn.execute(
            """
            INSERT INTO concepts (concept_id, course_key, unit_id, kind, title, tier, status)
            VALUES ('csc301.thread', 'csc301', 'csc301.u.processes', 'definition', 'Thread', 'core', 'active');
            """
        )

        # 2. Concept with active prompt -> SHOULD ship
        details_json = json.dumps(
            {
                "explanationCitations": [
                    {
                        "sourceFileId": "f.1234567890abcdef",
                        "page": 2,
                        "quote": "A process is a program in execution.",
                        "status": "verified",
                        "verifiedBy": "text",
                    }
                ],
                "aliases": ["task"],
                "useWhen": [],
                "conditions": [],
                "workedExamples": [],
            }
        )
        conn.execute(
            """
            INSERT INTO concepts (concept_id, course_key, unit_id, kind, title, confidence_note, tier, status, details_json)
            VALUES ('csc301.process', 'csc301', 'csc301.u.processes', 'definition', 'Process', 'A program in execution.', 'core', 'active', ?);
            """,
            (details_json,),
        )
        prompt_content = json.dumps(
            {"front": "What is a process?", "back": "A program in execution."}
        )
        conn.execute(
            """
            INSERT INTO prompts (prompt_id, concept_id, kind, content_json, status)
            VALUES ('csc301.process.flashcard.1', 'csc301.process', 'flashcard', ?, 'active');
            """,
            (prompt_content,),
        )

        # 3. Exam with valid date
        conn.execute(
            """
            INSERT INTO exams (exam_id, course_key, name, kind, date, unit_ids_json)
            VALUES ('csc301.x.midterm', 'csc301', 'Midterm', 'test', '2026-12-05', '["csc301.u.processes"]');
            """
        )
        conn.commit()

    batch = build_import_batch(mock_ctx)
    assert batch is not None
    assert len(batch.courses) == 1
    course = batch.courses[0]
    assert course.id == "csc301"
    assert course.coding is True
    assert len(course.sourceFiles) == 1
    assert len(course.contrastGroups) == 1
    assert len(course.units) == 1
    unit = course.units[0]
    assert unit.id == "csc301.u.processes"
    assert len(unit.concepts) == 1
    assert unit.concepts[0].id == "csc301.process"
    assert len(unit.concepts[0].prompts) == 1

    # Validate against schema
    validate_import_batch(batch)

    # Write atomically
    out_dir = tmp_path / "test_out"
    written_file = write_import_batch(batch, dest_dir=out_dir)
    assert written_file.exists()
    assert written_file.name.startswith("import-")
    assert written_file.name.endswith(".json")

    # Round trip verify
    loaded = load_handoff_file(
        written_file, ImportBatch, expected_schema="encore.import", expected_version=2
    )
    assert loaded.header.id == batch.header.id


def test_exam_without_date_excluded_and_empty_unit_included(mock_ctx):
    """
    Exams with no date aren't shipped (Rule E).
    Units an exam names ship even with no concepts (so Encore's validator knows unit id).
    """
    with mock_ctx.db.connect() as conn:
        _seed_sample_course(conn)
        # Exam 1 without date -> excluded
        conn.execute(
            """
            INSERT INTO exams (exam_id, course_key, name, kind, date, unit_ids_json)
            VALUES ('csc301.x.nodate', 'csc301', 'No Date Exam', 'test', '', '["csc301.u.memory"]');
            """
        )
        # Exam 2 with date -> included; references csc301.u.memory (which has no concepts)
        conn.execute(
            """
            INSERT INTO exams (exam_id, course_key, name, kind, date, unit_ids_json)
            VALUES ('csc301.x.final', 'csc301', 'Final', 'final', '2026-12-15', '["csc301.u.memory"]');
            """
        )
        conn.commit()

    batch = build_import_batch(mock_ctx)
    assert batch is not None
    assert len(batch.exams) == 1
    assert batch.exams[0].id == "csc301.x.final"

    # Unit csc301.u.memory must be present in course units with 0 concepts
    course = batch.courses[0]
    unit_ids = [u.id for u in course.units]
    assert "csc301.u.memory" in unit_ids
    memory_unit = next(u for u in course.units if u.id == "csc301.u.memory")
    assert len(memory_unit.concepts) == 0


def test_retired_concept_ships(mock_ctx):
    """A concept with status='retired' ships as retired (§3.7)."""
    with mock_ctx.db.connect() as conn:
        _seed_sample_course(conn)
        conn.execute(
            """
            INSERT INTO concepts (concept_id, course_key, unit_id, kind, title, tier, status)
            VALUES ('csc301.old', 'csc301', 'csc301.u.processes', 'definition', 'Old Concept', 'out', 'retired');
            """
        )
        conn.commit()

    batch = build_import_batch(mock_ctx)
    assert batch is not None
    c = batch.courses[0].units[0].concepts[0]
    assert c.id == "csc301.old"
    assert c.status == "retired"


def test_ingest_attempts_deduped_and_deleted(mock_ctx, monkeypatch):
    """Attempts files are committed to SQLite (deduped on attemptId) and then deleted (§3.7, 2.38)."""
    from articulate.core.paths import from_encore_dir

    from_dir = from_encore_dir()
    att_file = from_dir / "attempts-export-01.jsonl"

    hdr = {
        "schema": "encore.attempt",
        "version": 1,
        "id": "exp-1",
        "created_at": "2026-11-20T12:00:00Z",
        "producer": "encore 1.0",
    }
    att1 = {
        "attemptId": "a0000000-0000-0000-0000-000000000001",
        "promptId": "csc301.process.flashcard.1",
        "conceptId": "csc301.process",
        "answeredAt": "2026-11-20T12:01:00Z",
        "answerText": "A running program",
        "grade": 3,
        "msTaken": 4500,
        "sessionKind": "plan",
        "calibrated": True,
    }
    # Duplicate attempt with same attemptId
    att2 = dict(att1)

    lines = [json.dumps(hdr), json.dumps(att1), json.dumps(att2)]
    att_file.write_text("\n".join(lines), encoding="utf-8")

    counts = ingest_from_encore(mock_ctx)
    assert counts["attempts"] == 2  # 2 parsed
    assert not att_file.exists()  # deleted after commit

    with mock_ctx.db.connect() as conn:
        rows = conn.execute("SELECT * FROM attempts;").fetchall()
        assert len(rows) == 1  # deduped in SQLite on attempt_id!
        assert rows[0]["attempt_id"] == "a0000000-0000-0000-0000-000000000001"
        assert rows[0]["grade"] == 3


def test_ingest_flag_withdraws_prompt_and_handles_regeneration(mock_ctx):
    """
    Flag withdraws prompt (status='retired').
    First flag: calls Shape regeneration -> state='regenerated', new prompt active.
    Second flag: added to weekly list (§3.7, Proposal 2, 2.38).
    """
    from articulate.core.paths import from_encore_dir

    # Insert concept and prompt
    with mock_ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO concepts (concept_id, course_key, unit_id, kind, title, status)
            VALUES ('csc301.concept', 'csc301', 'csc301.u.processes', 'definition', 'Concept', 'active');
            """
        )
        conn.execute(
            """
            INSERT INTO prompts (prompt_id, concept_id, kind, content_json, status)
            VALUES ('csc301.p1', 'csc301.concept', 'flashcard', '{"front":"Front question","back":"Old answer"}', 'active');
            """
        )
        conn.commit()

    from_dir = from_encore_dir()
    flag1_file = from_dir / "flag-1.json"
    flag1_data = {
        "header": {
            "schema": "encore.flag",
            "version": 1,
            "id": "flag-1",
            "created_at": "2026-11-20T12:00:00Z",
            "producer": "encore 1.0",
        },
        "flagId": str(uuid.uuid4()),
        "promptId": "csc301.p1",
        "conceptId": "csc301.concept",
        "reason": "wrong",
        "note": "Definition is inverted",
        "suggestedFix": "Corrected answer",
        "flaggedAt": "2026-11-20T12:00:00Z",
    }
    flag1_file.write_text(json.dumps(flag1_data), encoding="utf-8")

    # Ingest first flag
    counts = ingest_from_encore(mock_ctx)
    assert counts["flags"] == 1
    assert not flag1_file.exists()  # deleted

    with mock_ctx.db.connect() as conn:
        # Prompt retired
        p_row = conn.execute("SELECT status FROM prompts WHERE prompt_id = 'csc301.p1';").fetchone()
        assert p_row["status"] == "retired"

        # Flag regenerated in handoff_card_flags
        f_row = conn.execute(
            "SELECT state FROM handoff_card_flags WHERE prompt_id = 'csc301.p1';"
        ).fetchone()
        assert f_row["state"] == "regenerated"

        # New prompt created
        new_p = conn.execute(
            "SELECT * FROM prompts WHERE concept_id = 'csc301.concept' AND status = 'active';"
        ).fetchone()
        assert new_p is not None
        assert new_p["prompt_id"] != "csc301.p1"
        assert "Corrected answer" in new_p["content_json"]

    # Second flag on same prompt
    flag2_file = from_dir / "flag-2.json"
    flag2_data = dict(flag1_data)
    flag2_data["flagId"] = str(uuid.uuid4())
    flag2_file.write_text(json.dumps(flag2_data), encoding="utf-8")

    ingest_from_encore(mock_ctx)
    assert not flag2_file.exists()

    with mock_ctx.db.connect() as conn:
        f_row = conn.execute(
            "SELECT state FROM handoff_card_flags WHERE prompt_id = 'csc301.p1';"
        ).fetchone()
        assert f_row["state"] == "listed"

        # Check weekly flags table
        flags_rows = conn.execute("SELECT * FROM flags WHERE item_id = 'csc301.p1';").fetchall()
        assert len(flags_rows) == 1
        assert "flagged second time" in flags_rows[0]["subject"]


def test_ingest_failed_batch_and_acks(mock_ctx):
    """
    Failed import moves to weekly flags with error text (2.37).
    state.appliedBatches marks handoff_files.acked_at.
    """
    from articulate.core.paths import from_encore_dir, to_encore_failed_dir

    # 1. Test failed batch
    failed_dir = to_encore_failed_dir()
    failed_json = failed_dir / "import-01FAILED.json"
    failed_json.write_text("{}", encoding="utf-8")
    error_txt = failed_dir / "01FAILED.error.txt"
    error_txt.write_text("Schema validation failed: missing courses", encoding="utf-8")

    # Pre-record in handoff_files
    with mock_ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO handoff_files (file_id, kind, written_at)
            VALUES ('import-01FAILED', 'import', '2026-11-20T10:00:00Z');
            """
        )
        conn.execute(
            """
            INSERT INTO handoff_files (file_id, kind, written_at)
            VALUES ('import-01ACKED', 'import', '2026-11-20T10:00:00Z');
            """
        )
        conn.commit()

    # 2. Test state.json with appliedBatches
    from_dir = from_encore_dir()
    state_file = from_dir / "state.json"
    state_data = {
        "header": {
            "schema": "encore.state",
            "version": 1,
            "id": "st-1",
            "created_at": "2026-11-20T12:00:00Z",
            "producer": "encore 1.0",
        },
        "exportedAt": "2026-11-20T12:00:00Z",
        "appliedBatches": ["01ACKED"],
        "planIdSeen": None,
        "concepts": [],
        "prompts": [],
    }
    state_file.write_text(json.dumps(state_data), encoding="utf-8")

    counts = ingest_from_encore(mock_ctx)
    assert counts["failures"] == 1
    assert counts["acks"] == 1
    assert not failed_json.exists()  # cleaned up after handling

    with mock_ctx.db.connect() as conn:
        f_row = conn.execute(
            "SELECT failed_at, error FROM handoff_files WHERE file_id = 'import-01FAILED';"
        ).fetchone()
        assert f_row["failed_at"] is not None
        assert "Schema validation failed" in f_row["error"]

        a_row = conn.execute(
            "SELECT acked_at FROM handoff_files WHERE file_id = 'import-01ACKED';"
        ).fetchone()
        assert a_row["acked_at"] is not None

        # Check weekly flags table for failure
        flag_row = conn.execute("SELECT * FROM flags WHERE kind = 'import_failed';").fetchone()
        assert flag_row is not None
        assert "01FAILED" in flag_row["subject"]


def test_launch_and_wait_success(mock_ctx):
    """Launch Encore and successfully receive fresh state.json within wait limit (2.36)."""
    from articulate.core.paths import from_encore_dir

    from_dir = from_encore_dir()
    state_file = from_dir / "state.json"

    def fake_runner(cmd):
        assert cmd == ["open", "-g", "-b", "com.amierlorenz.Encore"]
        # Simulate Encore writing fresh state.json
        fresh_data = {
            "header": {
                "schema": "encore.state",
                "version": 1,
                "id": "st-fresh",
                "created_at": "2026-11-20T12:05:00Z",
                "producer": "encore 1.0",
            },
            "exportedAt": "2026-11-20T12:05:00Z",
            "appliedBatches": ["batch-123"],
            "planIdSeen": None,
            "concepts": [],
            "prompts": [],
        }
        state_file.write_text(json.dumps(fresh_data), encoding="utf-8")

    res = launch_and_wait(mock_ctx, timeout_s=2.0, poll_interval_s=0.05, command_runner=fake_runner)
    assert res.ok is True
    assert res.timed_out is False
    assert res.state is not None
    assert res.state.header.id == "st-fresh"

    # Verify stored in encore_snapshots
    with mock_ctx.db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM encore_snapshots WHERE schema = 'encore.state';"
        ).fetchone()
        assert row is not None
        assert row["exported_at"] == "2026-11-20T12:05:00+00:00"


def test_launch_and_wait_timeout_and_health(mock_ctx):
    """
    On timeout:
    - Plans from last state + raises encore_timeout flag.
    - State > 48h old -> dashboard row (interrupt=False).
    - No ack or state for 2 days -> interrupting task (interrupt=True) (2.36).
    """
    from articulate.core.paths import from_encore_dir

    from_dir = from_encore_dir()
    state_file = from_dir / "state.json"

    # Write old state (3 days old)
    old_dt = (mock_ctx.now - timedelta(days=3)).isoformat()
    old_data = {
        "header": {
            "schema": "encore.state",
            "version": 1,
            "id": "st-old",
            "created_at": old_dt,
            "producer": "encore 1.0",
        },
        "exportedAt": old_dt,
        "appliedBatches": [],
        "planIdSeen": None,
        "concepts": [],
        "prompts": [],
    }
    state_file.write_text(json.dumps(old_data), encoding="utf-8")

    # Command runner does NOT update state.json
    res = launch_and_wait(
        mock_ctx, timeout_s=0.1, poll_interval_s=0.05, command_runner=lambda cmd: None
    )
    assert res.ok is False
    assert res.timed_out is True
    assert res.state is not None
    assert res.state.header.id == "st-old"

    with mock_ctx.db.connect() as conn:
        # Check weekly flags
        flag_row = conn.execute("SELECT * FROM flags WHERE kind = 'encore_timeout';").fetchone()
        assert flag_row is not None

        # Check health table: encore_stale raised with interrupt=1 (no ack or state for 2 days)
        h_row = conn.execute("SELECT * FROM health WHERE kind = 'encore_stale';").fetchone()
        assert h_row is not None
        assert h_row["interrupt"] == 1


def test_full_step_export_and_ingest(mock_ctx):
    """Full execution of step handoff.export."""
    with mock_ctx.db.connect() as conn:
        _seed_sample_course(conn)
        conn.execute(
            """
            INSERT INTO concepts (concept_id, course_key, unit_id, kind, title, tier, status)
            VALUES ('csc301.process', 'csc301', 'csc301.u.processes', 'definition', 'Process', 'core', 'active');
            """
        )
        conn.execute(
            """
            INSERT INTO prompts (prompt_id, concept_id, kind, content_json, status)
            VALUES ('csc301.process.flashcard.1', 'csc301.process', 'flashcard', '{"front":"Q","back":"A"}', 'active');
            """
        )
        conn.commit()

    # Run step with mocked launch_and_wait
    with patch("articulate.handoff.launch_and_wait") as mock_launch:
        mock_launch.return_value = LaunchResult(ok=True, timed_out=False, detail="Fresh state")
        summary = step(mock_ctx)
        assert summary.done == 1
        assert "Exported" in summary.detail

    # Verify shipped_in marked
    with mock_ctx.db.connect() as conn:
        c_row = conn.execute(
            "SELECT shipped_in FROM concepts WHERE concept_id = 'csc301.process';"
        ).fetchone()
        assert c_row["shipped_in"] is not None
        p_row = conn.execute(
            "SELECT shipped_in FROM prompts WHERE prompt_id = 'csc301.process.flashcard.1';"
        ).fetchone()
        assert p_row["shipped_in"] is not None
        h_row = conn.execute("SELECT * FROM handoff_files WHERE kind = 'import';").fetchone()
        assert h_row is not None
