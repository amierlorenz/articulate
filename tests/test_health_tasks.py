"""Tests for Health and TaskQueue (Acceptance check 0.15)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from articulate.core.db import Database
from articulate.core.health import INTERRUPTING_KINDS, Health
from articulate.core.runlog import RunLog
from articulate.core.taskqueue import TaskQueue, TaskRequest
from articulate.core.vault import Vault


@pytest.fixture
def health_env(tmp_path: Path):
    db_path = tmp_path / "test.sqlite3"
    db = Database(db_path)
    db.migrate()
    vault_dir = tmp_path / "vault"
    vault = Vault(vault_dir, db)
    runlog = RunLog(vault)
    taskqueue = TaskQueue(db)
    health = Health(db, runlog=runlog, taskqueue=taskqueue)
    return health, vault, db, taskqueue, runlog


def test_taskqueue_idempotent_enqueue(health_env):
    _, _, db, taskqueue, _ = health_env
    req = TaskRequest(
        external_id="health:token_rejected:2026-11-20",
        title="Canvas token rejected",
        band="health",
    )
    first = taskqueue.enqueue(req)
    assert first is True

    second = taskqueue.enqueue(req)
    assert second is False  # idempotent

    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE external_id = ?", (req.external_id,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["state"] == "pending"


def test_health_interrupting_kinds_enqueue_task(health_env):
    health, _vault, db, _, _ = health_env
    now = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)

    # Test all 5 interrupting kinds
    for kind in INTERRUPTING_KINDS:
        health.raise_(kind, key="test-key", detail="Failure detail", interrupt=True, now=now)

        with db.connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE external_id = ?", (f"health:{kind}:2026-11-20",)).fetchone()
            assert task is not None, f"Task for {kind} not enqueued"
            assert task["state"] == "pending"

    # Non-interrupting kind should not enqueue task
    health.raise_("ollama_down", key="model1", detail="Ollama 500", interrupt=False, now=now)
    with db.connect() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE external_id LIKE 'health:ollama_down:%'").fetchone()
        assert task is None


def test_health_incident_logged_once_and_recover_logged_once(health_env):
    health, vault, db, _, _ = health_env
    runs_file = vault.vault_dir / "_system" / "Runs.md"
    now1 = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
    now2 = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
    now3 = datetime(2026, 11, 20, 4, 0, tzinfo=UTC)

    # Raise incident
    health.raise_("token_rejected", key="canvas", detail="401 Unauthorized", interrupt=True, now=now1)
    runs_text = runs_file.read_text(encoding="utf-8")
    assert "⚠️ appeared: token_rejected" in runs_text

    # Re-raising while still active does NOT re-log to Runs.md
    health.raise_("token_rejected", key="canvas", detail="401 Unauthorized", interrupt=True, now=now2)
    appear_count = runs_file.read_text(encoding="utf-8").count("⚠️ appeared: token_rejected")
    assert appear_count == 1

    # Recover incident
    health.recover("token_rejected", key="canvas", now=now3)
    recovered_text = runs_file.read_text(encoding="utf-8")
    assert "✅ recovered: token_rejected" in recovered_text

    # Verify task was NOT closed (Articulate never closes it)
    with db.connect() as conn:
        task = conn.execute("SELECT state FROM tasks WHERE external_id = 'health:token_rejected:2026-11-20'").fetchone()
        assert task["state"] == "pending"

        # Check health row has recovered_at set
        h_row = conn.execute("SELECT recovered_at FROM health WHERE kind = 'token_rejected' AND key = 'canvas'").fetchone()
        assert h_row["recovered_at"] is not None
