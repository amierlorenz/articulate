"""Tests for core.jobs, registry, and run lock."""
from pathlib import Path

import pytest

from articulate.core.db import Database
from articulate.core.jobs import RunLock, Step, StepSummary, run_job
from articulate.core.pipeline import RunContext
from articulate.registry import STEPS


def dummy_success_step(ctx: RunContext) -> StepSummary:
    return StepSummary(done=1, detail="Success")


def dummy_failing_step(ctx: RunContext) -> StepSummary:
    raise RuntimeError("Step failed!")


def test_registry_steps_defined():
    step_names = [s.name for s in STEPS]
    assert "core.encore_read" in step_names
    assert "core.flags" in step_names
    assert "core.plan_hook" in step_names


def test_run_lock_second_job_exits_quietly(tmp_path: Path):
    lock_file = tmp_path / "run.lock"
    db_path = tmp_path / "lock_test.sqlite3"
    db = Database(db_path)
    db.migrate()

    # Process 1 holds the lock
    lock1 = RunLock(lock_file)
    assert lock1.acquire() is True

    # Process 2 attempts 'watch' -> should exit quietly (returncode 0)
    ret_watch = run_job("watch", db=db, lock_path=lock_file, steps=[])
    assert ret_watch == 0

    # Process 2 attempts 'nightly' -> should fail or raise
    with pytest.raises(RuntimeError) as exc_info:
        run_job("nightly", db=db, lock_path=lock_file, steps=[])
    assert "lock" in str(exc_info.value).lower()

    lock1.release()

    # Now nightly can acquire and run
    ret_nightly = run_job("nightly", db=db, lock_path=lock_file, steps=[])
    assert ret_nightly == 0


def test_run_job_records_in_runs_table(tmp_path: Path):
    db_path = tmp_path / "runs_test.sqlite3"
    lock_file = tmp_path / "run.lock"
    db = Database(db_path)
    db.migrate()

    steps = [
        Step("test.step", "tests.test_jobs:dummy_success_step", jobs={"nightly"}, order=1)
    ]

    ret = run_job("nightly", db=db, lock_path=lock_file, steps=steps)
    assert ret == 0

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE job = 'nightly'").fetchone()
        assert row is not None
        assert row["status"] == "ok"
        assert row["started_at"] is not None
        assert row["ended_at"] is not None


def test_run_job_with_only_step(tmp_path: Path):
    db_path = tmp_path / "only_test.sqlite3"
    lock_file = tmp_path / "run.lock"
    db = Database(db_path)
    db.migrate()

    steps = [
        Step("step.a", "tests.test_jobs:dummy_success_step", jobs={"nightly"}, order=1),
        Step("step.b", "tests.test_jobs:dummy_failing_step", jobs={"nightly"}, order=2),
    ]

    # Running only step.a should skip step.b and succeed
    ret = run_job("nightly", only_step="step.a", db=db, lock_path=lock_file, steps=steps)
    assert ret == 0
