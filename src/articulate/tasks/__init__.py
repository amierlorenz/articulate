"""tasks slice: extract first-person tasks, send them to master-todo, poll their state (§2, #57).

Registry line `tasks.extract_send` (order 130, `watch` + `nightly`) calls `step`.
"""
import logging

from articulate.core.config import ConfigError, load_config
from articulate.core.flags import Flags
from articulate.core.health import Health
from articulate.core.jobs import StepSummary
from articulate.core.models.runner import DefaultModelRunner
from articulate.core.paths import vault_path
from articulate.core.pipeline import RunContext
from articulate.core.runlog import RunLog
from articulate.core.taskqueue import TaskQueue
from articulate.core.vault import Vault
from articulate.tasks.extract import FLAG_KIND, apply_flag_choices, extract_pending, resolve_flag
from articulate.tasks.poll import poll_states
from articulate.tasks.send import SendError, ShortcutsClient, send_pending

logger = logging.getLogger(__name__)

__all__ = [
    "SendError",
    "ShortcutsClient",
    "apply_flag_choices",
    "extract_pending",
    "poll_states",
    "send_pending",
    "step",
]


def _ensure_services(ctx: RunContext) -> None:
    """Fill the services `run_job` leaves empty; a test or another step may have set them already."""
    if ctx.config is None and (ctx.vault is None or ctx.models is None):
        try:
            ctx.config = load_config(db=ctx.db)
        except ConfigError as e:
            logger.warning("tasks: config not loaded: %s", e)
    if ctx.vault is None and ctx.config is not None and ctx.config.articulate is not None:
        ctx.vault = Vault(vault_path(ctx.config), ctx.db)
    if ctx.tasks is None:
        ctx.tasks = TaskQueue(ctx.db)
    if ctx.runlog is None and ctx.vault is not None:
        ctx.runlog = RunLog(ctx.vault)
    if ctx.health is None:
        ctx.health = Health(ctx.db, runlog=ctx.runlog, taskqueue=ctx.tasks, vault=ctx.vault)
    if ctx.flags is None:
        ctx.flags = Flags(ctx.db, vault=ctx.vault, taskqueue=ctx.tasks)
    if ctx.models is None:
        ctx.models = DefaultModelRunner(ctx.db, ctx.config, job=ctx.job, run_id=ctx.run_id)


def step(ctx: RunContext) -> StepSummary:
    """Apply flag choices, extract new recordings, send pending tasks; poll states in `nightly`."""
    _ensure_services(ctx)
    ctx.flags.register(FLAG_KIND, resolve_flag)
    apply_flag_choices(ctx)

    extracted = extract_pending(ctx)
    client = ShortcutsClient()
    sent = send_pending(ctx, client)
    polled = poll_states(ctx, client) if ctx.job == "nightly" else StepSummary()

    return StepSummary(
        done=sent.done,
        held=extracted.held,
        failed=sent.failed + polled.failed,
        detail="; ".join(d for d in (sent.detail,) if d),
    )
