"""Handoff package: nightly v2 import batches and Encore hand-off (§3.7, Issue #65)."""

import logging
from datetime import UTC, datetime

from articulate.core.jobs import StepSummary
from articulate.core.pipeline import RunContext
from articulate.handoff.batch import build_import_batch
from articulate.handoff.encore import launch_and_wait
from articulate.handoff.ingest import ingest_from_encore
from articulate.handoff.write import write_import_batch

logger = logging.getLogger(__name__)


def _ensure_services(ctx: RunContext) -> None:
    """Ensure vault, flags, health, tasks, and runlog are populated on ctx if missing."""
    if ctx.vault is None and ctx.db is not None:
        try:
            from articulate.core.paths import vault_path
            from articulate.core.vault import Vault

            v_path = vault_path(ctx.config)
            ctx.vault = Vault(v_path, ctx.db)
        except Exception:  # noqa: BLE001, S110
            pass

    if ctx.tasks is None and ctx.db is not None:
        try:
            from articulate.core.taskqueue import TaskQueue

            ctx.tasks = TaskQueue(ctx.db)
        except Exception:  # noqa: BLE001, S110
            pass

    if ctx.runlog is None and ctx.vault is not None:
        try:
            from articulate.core.runlog import RunLog

            ctx.runlog = RunLog(ctx.vault)
        except Exception:  # noqa: BLE001, S110
            pass

    if ctx.health is None and ctx.db is not None:
        try:
            from articulate.core.health import Health

            ctx.health = Health(ctx.db, runlog=ctx.runlog, taskqueue=ctx.tasks, vault=ctx.vault)
        except Exception:  # noqa: BLE001, S110
            pass

    if ctx.flags is None and ctx.db is not None:
        try:
            from articulate.core.flags import Flags

            ctx.flags = Flags(ctx.db, vault=ctx.vault, taskqueue=ctx.tasks)
        except Exception:  # noqa: BLE001, S110
            pass


def step(ctx: RunContext) -> StepSummary:
    """
    Step handoff.export (jobs: nightly, pass2, order=340).
    Runs ingest first, builds import batch delta, writes to to-encore/,
    and launches Encore with open -g (§3.7, Issue #65).
    """
    _ensure_services(ctx)
    summary = StepSummary()

    # 1. Ingest runs inside the same step first (§3.7, Issue #65)
    ingest_counts = ingest_from_encore(ctx)
    logger.info("Encore handoff ingest completed: %s", ingest_counts)

    # 2. Build import batch delta
    batch = build_import_batch(ctx)
    if batch is None:
        summary.detail = f"No shippable content (ingested {ingest_counts['attempts']} attempts, {ingest_counts['flags']} flags)"
        return summary

    # 3. Write import batch atomically via tmp + rename
    batch_file = write_import_batch(batch)
    batch_id = batch.header.id
    now_dt = ctx.now if ctx.now is not None else datetime.now(UTC)
    now_str = now_dt.isoformat()

    # Record in handoff_files and mark concepts/prompts shipped_in
    if ctx.db is not None:
        with ctx.db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO handoff_files (file_id, kind, written_at)
                VALUES (?, 'import', ?);
                """,
                (batch_id, now_str),
            )

            # Collect shipped concept IDs and prompt IDs
            shipped_concept_ids: list[str] = []
            shipped_prompt_ids: list[str] = []
            for course in batch.courses:
                for unit in course.units:
                    for concept in unit.concepts:
                        shipped_concept_ids.append(concept.id)
                        for prompt in concept.prompts:
                            shipped_prompt_ids.append(prompt.id)

            for cid in shipped_concept_ids:
                conn.execute(
                    "UPDATE concepts SET shipped_in = ? WHERE concept_id = ?;",
                    (batch_id, cid),
                )

            for pid in shipped_prompt_ids:
                conn.execute(
                    "UPDATE prompts SET shipped_in = ? WHERE prompt_id = ?;",
                    (batch_id, pid),
                )

            conn.commit()

    summary.done += 1

    # 4. Launch Encore with open -g and wait for fresh state.json
    launch_res = launch_and_wait(ctx)
    if not launch_res.ok and launch_res.timed_out:
        summary.detail = f"Exported {batch_id} to {batch_file.name}, but Encore state timed out"
    else:
        summary.detail = f"Exported {batch_id} to {batch_file.name}, Encore state fresh"

    return summary
