"""Pipeline steps for core package (§3.12, §3.7, §3.9)."""
import json
import logging

from articulate.core.dashboard import write_dashboard
from articulate.core.flags import Flags
from articulate.core.handoff_schemas import (
    Attempt,
    Flag,
    Header,
    Library,
    State,
    load_handoff_file,
)
from articulate.core.health import Health
from articulate.core.jobs import StepSummary
from articulate.core.paths import from_encore_dir, vault_path
from articulate.core.pipeline import RunContext
from articulate.core.runlog import RunLog
from articulate.core.taskqueue import TaskQueue
from articulate.core.vault import Vault

logger = logging.getLogger(__name__)


def _ensure_context_services(ctx: RunContext) -> None:
    """Ensure vault, flags, health, tasks, and runlog are populated on ctx."""
    if ctx.vault is None:
        try:
            v_path = vault_path(ctx.config)
            ctx.vault = Vault(v_path, ctx.db)
        except Exception:  # noqa: BLE001, S110
            pass

    if ctx.tasks is None and ctx.db is not None:
        ctx.tasks = TaskQueue(ctx.db)

    if ctx.runlog is None and ctx.vault is not None:
        ctx.runlog = RunLog(ctx.vault)

    if ctx.health is None and ctx.db is not None:
        ctx.health = Health(ctx.db, runlog=ctx.runlog, taskqueue=ctx.tasks, vault=ctx.vault)

    if ctx.flags is None and ctx.db is not None:
        ctx.flags = Flags(ctx.db, vault=ctx.vault, taskqueue=ctx.tasks)


def read_encore(ctx: RunContext) -> StepSummary:
    """
    Step core.encore_read (jobs: nightly, pass2, watch, order=10).
    Reads state.json, library.json, attempts-*.jsonl, flag-*.json from from-encore/ (§3.7).
    """
    _ensure_context_services(ctx)
    handoff_from = from_encore_dir()
    summary = StepSummary()

    if not handoff_from.exists():
        return summary

    now_str = ctx.now.isoformat()

    for p in sorted(handoff_from.iterdir()):
        if p.name.startswith("."):
            continue  # Skip dotfiles / partially written files

        # 1. state.json
        if p.name == "state.json":
            try:
                state = load_handoff_file(p, State, expected_schema="encore.state", expected_version=1)
                with ctx.db.connect() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO encore_snapshots (schema, exported_at, read_at, body_json)
                        VALUES ('encore.state', ?, ?, ?);
                        """,
                        (state.exportedAt.isoformat(), now_str, p.read_text(encoding="utf-8")),
                    )
                    conn.commit()
                summary.done += 1
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to read state.json: %s", e)
                summary.failed += 1
                if ctx.flags:
                    ctx.flags.add(
                        "encore_schema",
                        f"Encore state.json failed schema or unknown version: {e}",
                        [("retry", "retry"), ("drop", "drop")],
                    )

        # 2. library.json
        elif p.name == "library.json":
            try:
                lib = load_handoff_file(p, Library, expected_schema="encore.library", expected_version=1)
                with ctx.db.connect() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO encore_snapshots (schema, exported_at, read_at, body_json)
                        VALUES ('encore.library', ?, ?, ?);
                        """,
                        (lib.exportedAt.isoformat(), now_str, p.read_text(encoding="utf-8")),
                    )
                    conn.commit()
                summary.done += 1
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to read library.json: %s", e)
                summary.failed += 1
                if ctx.flags:
                    ctx.flags.add(
                        "encore_schema",
                        f"Encore library.json failed schema or unknown version: {e}",
                        [("retry", "retry"), ("drop", "drop")],
                    )

        # 3. attempts-<exportId>.jsonl
        elif p.name.startswith("attempts-") and p.name.endswith(".jsonl"):
            try:
                lines = p.read_text(encoding="utf-8").strip().splitlines()
                if not lines:
                    p.unlink(missing_ok=True)
                    continue

                hdr_data = json.loads(lines[0])
                hdr = Header.model_validate(hdr_data)
                if hdr.schema_ != "encore.attempt" or hdr.version != 1:
                    raise ValueError(f"Unknown attempt schema {hdr.schema_} version {hdr.version}")

                for line in lines[1:]:
                    if not line.strip():
                        continue
                    att_data = json.loads(line)
                    Attempt.model_validate(att_data)

                # Successfully committed -> delete file (§3.7)
                p.unlink(missing_ok=True)
                summary.done += 1
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to read attempts file %s: %s", p.name, e)
                summary.failed += 1
                if ctx.flags:
                    ctx.flags.add(
                        "encore_schema",
                        f"Encore attempts file {p.name} failed: {e}",
                        [("retry", "retry"), ("drop", "drop")],
                    )

        # 4. flag-<uuid>.json
        elif p.name.startswith("flag-") and p.name.endswith(".json"):
            try:
                flag = load_handoff_file(p, Flag, expected_schema="encore.flag", expected_version=1)
                subj = f"Card flagged in Encore: {flag.conceptId} ({flag.reason})"
                note = flag.note
                if flag.suggestedFix:
                    note += f"\nSuggested fix: {flag.suggestedFix}"

                if ctx.flags:
                    ctx.flags.add(
                        "card_wrong",
                        subj,
                        [("regenerate", "regenerate"), ("keep", "keep"), ("drop", "drop")],
                        item_id=flag.conceptId,
                    )

                # Delete flag file after committing (§3.7)
                p.unlink(missing_ok=True)
                summary.done += 1
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to read flag file %s: %s", p.name, e)
                summary.failed += 1
                if ctx.flags:
                    ctx.flags.add(
                        "encore_schema",
                        f"Encore flag file {p.name} failed: {e}",
                        [("retry", "retry"), ("drop", "drop")],
                    )

    if ctx.vault and ctx.db:
        try:
            write_dashboard(ctx.db, ctx.vault, now=ctx.now)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not write dashboard in read_encore: %s", e)

    return summary


def apply_flags(ctx: RunContext) -> StepSummary:
    """
    Step core.flags (jobs: nightly, order=20).
    Processes ticked boxes in Inbox/Intake flags.md and enqueues weekly chore task (§3.9).
    """
    _ensure_context_services(ctx)
    summary = StepSummary()

    if ctx.flags:
        resolved = ctx.flags.process_ticks(ctx)
        ctx.flags.check_weekly_task(ctx)
        ctx.flags.render_note()
        summary.done = resolved
        summary.detail = f"Resolved {resolved} flags"

    if ctx.vault and ctx.db:
        try:
            write_dashboard(ctx.db, ctx.vault, now=ctx.now)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not write dashboard in apply_flags: %s", e)

    return summary


def noop(ctx: RunContext) -> StepSummary:
    """Step core.plan_hook (jobs: pass2, order=900) - stub (§3.12)."""
    return StepSummary()
