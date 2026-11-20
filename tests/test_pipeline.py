"""Tests for core.pipeline."""
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from articulate.core.db import Database
from articulate.core.ids import item_id, new_ulid
from articulate.core.pipeline import (
    Item,
    PipelineRunner,
    Privacy,
    RunContext,
    StageResult,
)


class DummySource:
    name = "dummy_source"

    def __init__(self, items: list[Item]):
        self._items = items

    def discover(self, ctx: RunContext) -> Iterable[Item]:
        return self._items


class DummyHandler:
    name = "dummy_handler"
    kinds: ClassVar[set[str]] = {"dummy_kind"}
    stages: ClassVar[list[str]] = ["stage1", "stage2", "stage3"]

    def __init__(self, behavior: dict[str, StageResult] | None = None):
        # stage -> StageResult
        self.behavior = behavior or {}

    def run(self, item: Item, stage: str, ctx: RunContext) -> StageResult:
        if stage in self.behavior:
            return self.behavior[stage]
        # Default behavior: advance
        if stage == "stage3":
            return StageResult(outcome="done")
        return StageResult(outcome="advance")


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "pipeline.sqlite3")
    db.migrate()
    return db


@pytest.fixture
def run_context(test_db: Database) -> RunContext:
    return RunContext(
        run_id=new_ulid(),
        job="nightly",
        now=datetime.now(UTC),
        db=test_db,
        config=None,
    )


def test_pipeline_advance_three_stages(test_db: Database, run_context: RunContext):
    # Move item through stage1 -> stage2 -> stage3 -> done
    item = Item(
        item_id=item_id("dummy", "it-1"),
        source="dummy",
        kind="dummy_kind",
        native_id="it-1",
        privacy=Privacy.CLASS,
    )
    source = DummySource([item])
    handler = DummyHandler()

    runner = PipelineRunner(test_db, sources=[source], handlers=[handler])

    # Run 1: stage1 -> stage2
    runner.run(run_context)
    with test_db.connect() as conn:
        row = conn.execute("SELECT stage, status FROM items WHERE item_id = ?", (item.item_id,)).fetchone()
        assert row["stage"] == "stage2"
        assert row["status"] == "pending"

    # Run 2: stage2 -> stage3
    runner.run(run_context)
    with test_db.connect() as conn:
        row = conn.execute("SELECT stage, status FROM items WHERE item_id = ?", (item.item_id,)).fetchone()
        assert row["stage"] == "stage3"
        assert row["status"] == "pending"

    # Run 3: stage3 -> done
    runner.run(run_context)
    with test_db.connect() as conn:
        row = conn.execute("SELECT stage, status FROM items WHERE item_id = ?", (item.item_id,)).fetchone()
        assert row["status"] == "done"


def test_pipeline_hold_keeps_stage(test_db: Database, run_context: RunContext):
    item = Item(
        item_id=item_id("dummy", "it-hold"),
        source="dummy",
        kind="dummy_kind",
        native_id="it-hold",
        privacy=Privacy.CLASS,
    )
    source = DummySource([item])
    # Handler holds at stage1
    handler = DummyHandler(behavior={"stage1": StageResult(outcome="hold", hold_reason="quota", detail="waiting for quota")})

    runner = PipelineRunner(test_db, sources=[source], handlers=[handler])
    runner.run(run_context)

    with test_db.connect() as conn:
        row = conn.execute("SELECT stage, status, hold_reason FROM items WHERE item_id = ?", (item.item_id,)).fetchone()
        assert row["stage"] == "stage1"
        assert row["status"] == "held"
        assert row["hold_reason"] == "quota"


def test_pipeline_three_fails_adds_flag_row(test_db: Database, run_context: RunContext):
    item = Item(
        item_id=item_id("dummy", "it-fail"),
        source="dummy",
        kind="dummy_kind",
        native_id="it-fail",
        privacy=Privacy.CLASS,
    )
    source = DummySource([item])
    handler = DummyHandler(behavior={"stage1": StageResult(outcome="fail", detail="Syntax error in extraction")})

    runner = PipelineRunner(test_db, sources=[source], handlers=[handler])

    # Run 1
    runner.run(run_context)
    with test_db.connect() as conn:
        row = conn.execute("SELECT consecutive_failures, status FROM items WHERE item_id = ?", (item.item_id,)).fetchone()
        assert row["consecutive_failures"] == 1
        flags_count = conn.execute("SELECT count(*) FROM flags").fetchone()[0]
        assert flags_count == 0

    # Run 2
    # Item is re-run
    runner.run(run_context)
    with test_db.connect() as conn:
        row = conn.execute("SELECT consecutive_failures FROM items WHERE item_id = ?", (item.item_id,)).fetchone()
        assert row["consecutive_failures"] == 2
        flags_count = conn.execute("SELECT count(*) FROM flags").fetchone()[0]
        assert flags_count == 0

    # Run 3 -> hits 3 consecutive failures
    runner.run(run_context)
    with test_db.connect() as conn:
        row = conn.execute("SELECT consecutive_failures, status FROM items WHERE item_id = ?", (item.item_id,)).fetchone()
        assert row["consecutive_failures"] == 3
        flag = conn.execute("SELECT * FROM flags WHERE item_id = ?", (item.item_id,)).fetchone()
        assert flag is not None
        assert flag["status"] == "open"
        assert "Syntax error" in flag["subject"]


def test_pipeline_new_version_supersedes_old(test_db: Database, run_context: RunContext):
    item_v1 = Item(
        item_id=item_id("dummy", "it-v", "v1"),
        source="dummy",
        kind="dummy_kind",
        native_id="it-v",
        version="v1",
        privacy=Privacy.CLASS,
    )
    source1 = DummySource([item_v1])
    handler = DummyHandler()
    runner1 = PipelineRunner(test_db, sources=[source1], handlers=[handler])
    runner1.run(run_context)

    # v1 is now at stage2 (unfinished)
    with test_db.connect() as conn:
        row = conn.execute("SELECT status, stage FROM items WHERE item_id = ?", (item_v1.item_id,)).fetchone()
        assert row["status"] == "pending"
        assert row["stage"] == "stage2"

    # Now discover v2
    item_v2 = Item(
        item_id=item_id("dummy", "it-v", "v2"),
        source="dummy",
        kind="dummy_kind",
        native_id="it-v",
        version="v2",
        privacy=Privacy.CLASS,
    )
    source2 = DummySource([item_v2])
    runner2 = PipelineRunner(test_db, sources=[source2], handlers=[handler])
    runner2.run(run_context)

    with test_db.connect() as conn:
        v1_row = conn.execute("SELECT status FROM items WHERE item_id = ?", (item_v1.item_id,)).fetchone()
        assert v1_row["status"] == "superseded"

        v2_row = conn.execute("SELECT status, stage FROM items WHERE item_id = ?", (item_v2.item_id,)).fetchone()
        assert v2_row["status"] == "pending"
        assert v2_row["stage"] == "stage2"


def test_pipeline_resume_after_kill(test_db: Database, run_context: RunContext):
    # Simulate an item left in 'running' state mid-stage (e.g. at stage2) because process died
    item = Item(
        item_id=item_id("dummy", "it-kill"),
        source="dummy",
        kind="dummy_kind",
        native_id="it-kill",
        privacy=Privacy.CLASS,
    )
    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO items (
                item_id, source, kind, native_id, version, privacy, priority,
                stage, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '', ?, 0, 'stage2', 'running', ?, ?)
            """,
            (item.item_id, item.source, item.kind, item.native_id, item.privacy.value,
             run_context.now.isoformat(), run_context.now.isoformat())
        )
        conn.commit()

    source = DummySource([])  # no new items
    handler = DummyHandler()
    runner = PipelineRunner(test_db, sources=[source], handlers=[handler])

    # Runner picks up 'running' items and advances from stage2 to stage3
    runner.run(run_context)

    with test_db.connect() as conn:
        row = conn.execute("SELECT stage, status FROM items WHERE item_id = ?", (item.item_id,)).fetchone()
        assert row["stage"] == "stage3"
        assert row["status"] == "pending"
