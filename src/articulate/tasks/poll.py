"""Nightly Get Task State poll for sent tasks (1.24, #7).

Stops at done or removed, or 90 days after sending. A done task ticks its fallback-list checkbox; a removed
task is tombstoned so it is never sent again.
"""
import logging
from datetime import datetime, timedelta

from articulate.core.jobs import StepSummary
from articulate.core.pipeline import RunContext
from articulate.tasks import fallback
from articulate.tasks.send import SendError, ShortcutsClient

logger = logging.getLogger(__name__)

POLL_DAYS = 90


def poll_states(ctx: RunContext, client: ShortcutsClient) -> StepSummary:
    summary = StepSummary()
    if not client.available():
        return summary
    cutoff = ctx.now - timedelta(days=POLL_DAYS)
    now = ctx.now.isoformat()
    with ctx.db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE state IN ('sent', 'open', 'unknown') AND sent_at IS NOT NULL;"
        ).fetchall()

    for r in rows:
        if datetime.fromisoformat(r["sent_at"]) < cutoff:
            continue
        try:
            state = client.get_state(r["source"] or "articulate", r["external_id"])
        except SendError as e:
            logger.warning("tasks: state poll for %s failed: %s", r["external_id"], e)
            summary.failed += 1
            continue
        closed = state in ("done", "removed")
        with ctx.db.connect() as conn:
            conn.execute(
                """UPDATE tasks SET state = ?, last_polled_at = ?,
                          closed_at = CASE WHEN ? THEN ? ELSE closed_at END
                   WHERE external_id = ?;""",
                (state, now, closed, now, r["external_id"]),
            )
            if state == "removed":
                conn.execute(
                    """INSERT OR IGNORE INTO tombstones (kind, key, reason, created_at)
                       VALUES ('task', ?, 'removed in master-todo', ?);""",
                    (r["external_id"], now),
                )
            conn.commit()
        summary.done += 1

    fallback.render(ctx)
    return summary
