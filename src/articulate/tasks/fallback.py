"""`Inbox/Articulate tasks.md`: the fallback list while master-todo's shortcuts are missing (1.23, Q10).

Each line carries the task's fields and an id comment. Once the intent works, the line's task is sent and the
line gains "sent ✓"; when master-todo reports it done (or Amier ticks it by hand), the box is ticked.
"""
import re
from datetime import datetime

from articulate.core.pipeline import RunContext

NOTE = "Inbox/Articulate tasks.md"
TASK_COMMENT_RE = re.compile(r"<!--\s*task:(\S+)\s*-->")
TICKED_RE = re.compile(r"^\s*-\s*\[[xX]\]")

HEADER = """---
agent: true
generated: true
---
# Articulate tasks

Tasks Articulate couldn't send to master-todo yet (its Add Task shortcut isn't set up). Articulate rewrites this
list each run; tick a line when it's done and it won't be sent.

"""


def _ticked_ids(ctx: RunContext) -> set[str]:
    if ctx.vault is None:
        return set()
    path = ctx.vault.vault_dir / NOTE
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = TASK_COMMENT_RE.search(line)
        if m and TICKED_RE.match(line):
            out.add(m.group(1))
    return out


def add(ctx: RunContext, external_id: str) -> None:
    now = ctx.now.isoformat()
    with ctx.db.connect() as conn:
        conn.execute("UPDATE tasks SET state = 'fallback' WHERE external_id = ? AND state = 'pending';",
                     (external_id,))
        conn.execute("INSERT OR IGNORE INTO task_fallback (external_id, added_at) VALUES (?, ?);",
                     (external_id, now))
        conn.commit()


def apply_hand_ticks(ctx: RunContext) -> int:
    """A line Amier ticked before it was sent is done: it is closed and never sent."""
    ticked = _ticked_ids(ctx)
    if not ticked:
        return 0
    now = ctx.now.isoformat()
    marks = ",".join("?" for _ in ticked)
    with ctx.db.connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET state = 'done', closed_at = ? "
            f"WHERE state = 'fallback' AND external_id IN ({marks});",
            (now, *ticked),
        )
        conn.commit()
        return cur.rowcount


def _due_text(due_at: str, has_time: int) -> str:
    dt = datetime.fromisoformat(due_at)
    return f"due {dt:%Y-%m-%d %H:%M}" if has_time else f"due {dt:%Y-%m-%d}"


def render(ctx: RunContext) -> bool:
    """Rewrite the list from the ledger, keeping boxes Amier ticked. Returns True when written."""
    if ctx.vault is None:
        return False
    with ctx.db.connect() as conn:
        rows = conn.execute(
            """SELECT t.* FROM task_fallback f JOIN tasks t USING (external_id)
               ORDER BY f.added_at, t.external_id;"""
        ).fetchall()
    if not rows:
        return False
    ticked = _ticked_ids(ctx)
    lines: list[str] = []
    for r in rows:
        box = "x" if r["state"] == "done" or r["external_id"] in ticked else " "
        parts = [r["title"]]
        if r["due_at"]:
            parts.append(_due_text(r["due_at"], r["has_due_time"]))
        if r["notes"]:
            parts.append(" — ".join(ln.strip() for ln in r["notes"].splitlines() if ln.strip()))
        if r["link"]:
            parts.append(f"[note]({r['link']})")
        if r["sent_at"]:
            parts.append("sent ✓")
        elif r["state"] == "skipped":
            parts.append("removed in master-todo")
        lines.append(f"- [{box}] " + " · ".join(parts) + f"   <!-- task:{r['external_id']} -->")
    return ctx.vault.write_system(NOTE, HEADER + "\n".join(lines) + "\n")
