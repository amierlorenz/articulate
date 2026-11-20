"""Task queue for funneling tasks into master-todo (§3.10, Issue #7)."""
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from articulate.core.db import Database


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    external_id: str
    title: str
    due_at: str | None = None
    has_due_time: bool = False
    notes: str | None = None
    link: str | None = None
    origin_item_id: str | None = None
    band: str = "general"
    prompt_version: str | None = None


class TaskQueue:
    """Manages the tasks table and ensures idempotent enqueue (§3.10)."""

    def __init__(self, db: Database):
        self.db = db

    def enqueue(self, t: TaskRequest) -> bool:
        """
        Inserts task with state pending. Idempotent by external_id.
        Returns True if newly inserted, False if already exists (§3.10).
        """
        now_str = datetime.now(UTC).isoformat()
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO tasks (
                    external_id, source, title, due_at, has_due_time,
                    notes, link, origin_item_id, band, prompt_version,
                    state, send_failures, created_at
                ) VALUES (?, 'articulate', ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?);
                """,
                (
                    t.external_id,
                    t.title,
                    t.due_at,
                    1 if t.has_due_time else 0,
                    t.notes,
                    t.link,
                    t.origin_item_id,
                    t.band,
                    t.prompt_version,
                    now_str,
                ),
            )
            inserted = cursor.rowcount > 0
            conn.commit()
            return inserted
