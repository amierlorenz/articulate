"""Send ledger tasks to master-todo through its Add Task shortcut (1.22, 1.23, 1.25, master-todo#33).

`shortcuts run "Add Task" -i <json> -o <json>`: the input is the JSON payload master-todo's AddTaskIntent
parses (`title`, `source`, `externalID`, `dueAt`, `hasDueTime`, `notes`, `link`); the output is its
AddTaskResult (`id`, `state`, `status` = created | existing | skipped). `shortcuts run` only runs library
shortcuts, so "Add Task" and "Get Task State" must exist in Shortcuts; until they do, every pending task goes
to the fallback list instead (Q10).
"""
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from articulate.core.health import Health
from articulate.core.jobs import StepSummary
from articulate.core.pipeline import RunContext
from articulate.tasks import fallback

logger = logging.getLogger(__name__)

ADD_TASK = "Add Task"
GET_TASK_STATE = "Get Task State"
HEALTH_KIND = "task_send_failing"
FAILED_RUNS_ALERT = 3


class SendError(Exception):
    """A shortcut run failed or returned something that isn't the intent's JSON."""


class ShortcutsClient:
    """Runs master-todo's shortcuts with JSON in and JSON out."""

    def __init__(self, binary: str | None = None, timeout_s: int = 60):
        self.binary = binary or os.environ.get("ARTICULATE_SHORTCUTS", "shortcuts")
        self.timeout_s = timeout_s

    def available(self) -> bool:
        """Both shortcuts exist in the Shortcuts library (checked on each call: Amier may add them any time)."""
        try:
            out = subprocess.run([self.binary, "list"], capture_output=True, text=True,
                                 timeout=self.timeout_s, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("tasks: could not list shortcuts: %s", e)
            return False
        names = {ln.strip() for ln in out.stdout.splitlines()}
        return out.returncode == 0 and {ADD_TASK, GET_TASK_STATE} <= names

    def _run(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="articulate-tasks-") as tmp:
            in_path, out_path = Path(tmp) / "in.json", Path(tmp) / "out.json"
            in_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                proc = subprocess.run(
                    [self.binary, "run", name, "-i", str(in_path), "-o", str(out_path)],
                    capture_output=True, text=True, timeout=self.timeout_s, check=False,
                )
            except (OSError, subprocess.SubprocessError) as e:
                raise SendError(f"{name}: {e}") from e
            if proc.returncode != 0:
                first = (proc.stderr or proc.stdout or "").strip().splitlines()[:1]
                raise SendError(f"{name} exited {proc.returncode}: {first[0] if first else ''}")
            text = out_path.read_text(encoding="utf-8") if out_path.exists() else proc.stdout
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise SendError(f"{name} returned non-JSON output") from e
        if not isinstance(data, dict):
            raise SendError(f"{name} returned {type(data).__name__}, not an object")
        return data

    def add_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = self._run(ADD_TASK, payload)
        if res.get("status") not in ("created", "existing", "skipped"):
            raise SendError(f"Add Task returned status {res.get('status')!r}")
        return res

    def get_state(self, source: str, external_id: str) -> str:
        res = self._run(GET_TASK_STATE, {"source": source, "externalID": external_id})
        state = res.get("state")
        if state not in ("open", "done", "removed", "unknown"):
            raise SendError(f"Get Task State returned state {state!r}")
        return str(state)


def payload_for(row: Any) -> dict[str, Any]:
    p: dict[str, Any] = {"title": row["title"], "source": row["source"] or "articulate",
                         "externalID": row["external_id"]}
    if row["due_at"]:
        p["dueAt"] = row["due_at"]
        p["hasDueTime"] = bool(row["has_due_time"])
    if row["notes"]:
        p["notes"] = row["notes"]
    if row["link"]:
        p["link"] = row["link"]
    return p


def _meta_int(ctx: RunContext, key: str) -> int:
    with ctx.db.connect() as conn:
        row = conn.execute("SELECT value FROM task_meta WHERE key = ?;", (key,)).fetchone()
    return int(row["value"]) if row else 0


def _set_meta(ctx: RunContext, key: str, value: int) -> None:
    with ctx.db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO task_meta (key, value) VALUES (?, ?);", (key, str(value)))
        conn.commit()


def _record_result(ctx: RunContext, external_id: str, res: dict[str, Any], now: str) -> None:
    with ctx.db.connect() as conn:
        if res["status"] == "skipped":
            conn.execute(
                """UPDATE tasks SET state = 'skipped', closed_at = ?, send_failures = 0
                   WHERE external_id = ?;""", (now, external_id))
            conn.execute(
                """INSERT OR IGNORE INTO tombstones (kind, key, reason, created_at)
                   VALUES ('task', ?, 'removed in master-todo', ?);""", (external_id, now))
        else:  # created or existing: master-todo's answer wins over the ledger
            done = res.get("state") == "done"
            conn.execute(
                """UPDATE tasks SET state = ?, master_todo_id = ?, sent_at = COALESCE(sent_at, ?),
                          closed_at = CASE WHEN ? THEN ? ELSE closed_at END, send_failures = 0
                   WHERE external_id = ?;""",
                ("done" if done else "sent", res.get("id"), now, done, now, external_id))
        conn.commit()


def _track_failed_runs(ctx: RunContext, failed: bool, detail: str, now: datetime) -> None:
    health = ctx.health or Health(ctx.db, runlog=ctx.runlog)
    if failed:
        runs = _meta_int(ctx, "send_failed_runs") + 1
        _set_meta(ctx, "send_failed_runs", runs)
        if runs >= FAILED_RUNS_ALERT:
            # Dashboard row only: a task can't report that tasks can't be sent (#45).
            health.raise_(HEALTH_KIND, detail=f"task sends failed {runs} runs in a row: {detail}",
                          interrupt=False, now=now)
    else:
        _set_meta(ctx, "send_failed_runs", 0)
        health.recover(HEALTH_KIND, now=now)


def send_pending(ctx: RunContext, client: ShortcutsClient) -> StepSummary:
    """Send every pending task (extracted, ticked on the flag list, health and study chores)."""
    summary = StepSummary()
    now = ctx.now.isoformat()
    fallback.apply_hand_ticks(ctx)

    with ctx.db.connect() as conn:
        rows = conn.execute(
            """SELECT * FROM tasks WHERE state IN ('pending', 'fallback')
               AND external_id NOT IN (SELECT key FROM tombstones WHERE kind = 'task')
               ORDER BY created_at, external_id;"""
        ).fetchall()

    if rows and not client.available():
        for r in rows:
            if r["state"] == "pending":
                fallback.add(ctx, r["external_id"])
        summary.detail = f"{len(rows)} tasks on the fallback list"
    elif rows:
        failures: list[str] = []
        for r in rows:
            try:
                res = client.add_task(payload_for(r))
            except SendError as e:
                logger.warning("tasks: send %s failed: %s", r["external_id"], e)
                failures.append(str(e))
                with ctx.db.connect() as conn:
                    conn.execute("UPDATE tasks SET send_failures = send_failures + 1 WHERE external_id = ?;",
                                 (r["external_id"],))
                    conn.commit()
                continue
            _record_result(ctx, r["external_id"], res, now)
            summary.done += 1
        summary.failed = len(failures)
        _track_failed_runs(ctx, bool(failures), failures[0] if failures else "", ctx.now)

    fallback.render(ctx)
    return summary
