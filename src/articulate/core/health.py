"""Health tracking and alerting (§3.10, Issue #45)."""
from datetime import UTC, datetime

from articulate.core.db import Database
from articulate.core.runlog import RunLog
from articulate.core.taskqueue import TaskQueue, TaskRequest
from articulate.core.vault import Vault

# Interrupting kinds (and only these) enqueues a health task (§3.10)
INTERRUPTING_KINDS: set[str] = {
    "no_nightly_success",
    "job_failing",
    "claude_credential",
    "encore_stale",
    "token_rejected",
}


class Health:
    """Tracks health incidents and raises interrupting tasks (§3.10)."""

    def __init__(
        self,
        db: Database,
        runlog: RunLog | None = None,
        taskqueue: TaskQueue | None = None,
        vault: Vault | None = None,
    ):
        self.db = db
        self.runlog = runlog or (RunLog(vault) if vault else None)
        self.taskqueue = taskqueue or TaskQueue(db)
        self.vault = vault

    def raise_(
        self,
        kind: str,
        key: str = "",
        detail: str = "",
        interrupt: bool = False,
        now: datetime | None = None,
    ) -> None:
        """
        Raise a health incident.
        Logs once to Runs.md when it appears.
        If interrupt=True and kind is an interrupting kind, enqueues exactly one task (§3.10).
        """
        dt = now or datetime.now(UTC)
        now_str = dt.isoformat()

        with self.db.connect() as conn:
            # Check for existing active incident
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM health
                WHERE kind = ? AND key = ? AND recovered_at IS NULL
                ORDER BY first_seen DESC LIMIT 1;
                """,
                (kind, key),
            )
            active = cursor.fetchone()

            if active is not None:
                # Update last seen and detail
                conn.execute(
                    """
                    UPDATE health
                    SET last_seen = ?, detail = ?
                    WHERE kind = ? AND key = ? AND first_seen = ?;
                    """,
                    (now_str, detail, kind, key, active["first_seen"]),
                )
                conn.commit()
                return

            # New incident
            is_interrupt = interrupt and (kind in INTERRUPTING_KINDS)
            task_ext_id: str | None = None

            if is_interrupt:
                date_str = dt.strftime("%Y-%m-%d")
                task_ext_id = f"health:{kind}:{date_str}"
                title = f"Health alert: {kind}" + (f" ({key})" if key else "")
                task_req = TaskRequest(
                    external_id=task_ext_id,
                    title=title,
                    notes=detail,
                    band="health",
                )
                self.taskqueue.enqueue(task_req)

            conn.execute(
                """
                INSERT INTO health (
                    kind, key, detail, interrupt, first_seen, last_seen,
                    recovered_at, task_external_id
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?);
                """,
                (
                    kind,
                    key,
                    detail,
                    1 if is_interrupt else 0,
                    now_str,
                    now_str,
                    task_ext_id,
                ),
            )
            conn.commit()

        # Log appear to Runs.md once
        if self.runlog:
            self.runlog.health_appeared(kind, key=key, now=dt)

    def recover(self, kind: str, key: str = "", now: datetime | None = None) -> None:
        """
        Mark a health incident as recovered.
        Logs once to Runs.md. Does NOT close the enqueued task (§3.10).
        """
        dt = now or datetime.now(UTC)
        now_str = dt.isoformat()

        recovered_any = False
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT first_seen FROM health
                WHERE kind = ? AND key = ? AND recovered_at IS NULL;
                """,
                (kind, key),
            )
            rows = cursor.fetchall()
            if rows:
                conn.execute(
                    """
                    UPDATE health
                    SET recovered_at = ?, last_seen = ?
                    WHERE kind = ? AND key = ? AND recovered_at IS NULL;
                    """,
                    (now_str, now_str, kind, key),
                )
                conn.commit()
                recovered_any = True

        if recovered_any and self.runlog:
            self.runlog.health_recovered(kind, key=key, now=dt)
