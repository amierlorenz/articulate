"""Source -> Item -> Handler pipeline framework (§3.5)."""
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from articulate.core.db import Database
from articulate.core.ids import new_ulid

logger = logging.getLogger(__name__)


class Privacy(StrEnum):
    CLASS = "class"
    PERSONAL = "personal"
    PRIVATE = "private"


class Item(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item_id: str
    source: str
    kind: str
    native_id: str
    version: str = ""
    course_key: str | None = None
    privacy: Privacy
    exam_key: str | None = None
    priority: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)


class StageResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    outcome: Literal["advance", "done", "hold", "fail", "skip"]
    next_stage: str | None = None
    hold_reason: str | None = None
    detail: str = ""
    new_items: list[Item] = Field(default_factory=list)


class Source(Protocol):
    name: str

    def discover(self, ctx: "RunContext") -> Iterable[Item]:
        ...


class Handler(Protocol):
    name: str
    kinds: set[str]
    stages: list[str]

    def run(self, item: Item, stage: str, ctx: "RunContext") -> StageResult:
        ...


@dataclass
class RunContext:
    run_id: str
    job: str
    now: datetime
    db: Database
    config: Any
    models: Any = None
    vault: Any = None
    flags: Any = None
    health: Any = None
    tasks: Any = None
    runlog: Any = None

    def budget_left(self, step: str) -> int:
        return 0


class PipelineRunner:
    """Orchestrates Source discovery and Handler execution with resumable state (§3.5)."""

    def __init__(
        self,
        db: Database,
        sources: list[Source] | None = None,
        handlers: list[Handler] | None = None,
    ):
        self.db = db
        self.sources: list[Source] = sources or []
        self.handlers: list[Handler] = handlers or []

    def register_source(self, source: Source) -> None:
        self.sources.append(source)

    def register_handler(self, handler: Handler) -> None:
        self.handlers.append(handler)

    def _find_handler(self, kind: str) -> Handler | None:
        for h in self.handlers:
            if kind in h.kinds:
                return h
        return None

    def discover_items(self, ctx: RunContext) -> None:
        """Run discovery for all registered sources and upsert items."""
        now_str = ctx.now.isoformat()
        with self.db.connect() as conn:
            for source in self.sources:
                try:
                    discovered = list(source.discover(ctx))
                except Exception as e:  # noqa: BLE001  # noqa: BLE001
                    logger.error("Error discovering from source %s: %s", source.name, e)
                    continue

                for item in discovered:
                    # Check if (source, native_id, version) exists
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT item_id, status FROM items WHERE source = ? AND native_id = ? AND version = ?;",
                        (item.source, item.native_id, item.version),
                    )
                    existing = cursor.fetchone()

                    if existing is None:
                        handler = self._find_handler(item.kind)
                        entry_stage = handler.stages[0] if (handler and handler.stages) else "entry"

                        conn.execute(
                            """
                            INSERT INTO items (
                                item_id, source, kind, native_id, version, course_key,
                                privacy, exam_key, priority, stage, status,
                                attempts, consecutive_failures, payload_json,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, ?, ?, ?);
                            """,
                            (
                                item.item_id,
                                item.source,
                                item.kind,
                                item.native_id,
                                item.version,
                                item.course_key,
                                item.privacy.value,
                                item.exam_key,
                                item.priority,
                                entry_stage,
                                json.dumps(item.payload),
                                now_str,
                                now_str,
                            ),
                        )
                        conn.execute(
                            """
                            INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                            VALUES (?, ?, ?, ?, 'discovered', 'Item discovered');
                            """,
                            (item.item_id, ctx.run_id, now_str, entry_stage),
                        )

                        # If new version of a known native_id, supersede older versions not yet done (#23 rule 7)
                        cursor.execute(
                            """
                            SELECT item_id, stage FROM items
                            WHERE source = ? AND native_id = ? AND version != ? AND status != 'done' AND status != 'superseded';
                            """,
                            (item.source, item.native_id, item.version),
                        )
                        older_items = cursor.fetchall()
                        for old in older_items:
                            old_id = old["item_id"]
                            conn.execute(
                                "UPDATE items SET status = 'superseded', updated_at = ? WHERE item_id = ?;",
                                (now_str, old_id),
                            )
                            conn.execute(
                                """
                                INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                                VALUES (?, ?, ?, ?, 'superseded', ?);
                                """,
                                (old_id, ctx.run_id, now_str, old["stage"], f"Superseded by {item.item_id}"),
                            )

            conn.commit()

    def run(self, ctx: RunContext) -> None:
        """Execute one pipeline cycle: discover then run items in priority order."""
        self.discover_items(ctx)
        now_str = ctx.now.isoformat()

        # Fetch runnable items: pending, running (resuming crashed run), or held
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM items
                WHERE status IN ('pending', 'running', 'held', 'failed')
                ORDER BY priority ASC, created_at ASC;
                """
            )
            rows = cursor.fetchall()

        for row in rows:
            item_id_val = row["item_id"]
            kind = row["kind"]
            stage = row["stage"]
            consec_fails = row["consecutive_failures"]

            handler = self._find_handler(kind)
            if not handler or stage not in handler.stages:
                logger.warning("No handler or unknown stage for item %s (%s, stage %s)", item_id_val, kind, stage)
                continue

            # Mark item as running
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE items
                    SET status = 'running', attempts = attempts + 1, updated_at = ?
                    WHERE item_id = ?;
                    """,
                    (now_str, item_id_val),
                )
                conn.execute(
                    """
                    INSERT INTO item_events (item_id, run_id, at, stage, event)
                    VALUES (?, ?, ?, ?, 'started');
                    """,
                    (item_id_val, ctx.run_id, now_str, stage),
                )
                conn.commit()

            try:
                payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}

            item_obj = Item(
                item_id=item_id_val,
                source=row["source"],
                kind=row["kind"],
                native_id=row["native_id"],
                version=row["version"] or "",
                course_key=row["course_key"],
                privacy=Privacy(row["privacy"]),
                exam_key=row["exam_key"],
                priority=row["priority"] or 0.0,
                payload=payload,
            )

            try:
                result = handler.run(item_obj, stage, ctx)
            except Exception as e:  # noqa: BLE001
                logger.error("Handler %s raised exception on item %s: %s", handler.name, item_id_val, e)
                result = StageResult(outcome="fail", detail=str(e))

            now_str = datetime.now(UTC).isoformat()
            with self.db.connect() as conn:
                if result.outcome == "advance":
                    curr_idx = handler.stages.index(stage)
                    if result.next_stage:
                        next_stage = result.next_stage
                    elif curr_idx + 1 < len(handler.stages):
                        next_stage = handler.stages[curr_idx + 1]
                    else:
                        next_stage = None

                    if next_stage is None or stage == handler.stages[-1]:
                        # Completed last stage
                        conn.execute(
                            """
                            UPDATE items
                            SET status = 'done', consecutive_failures = 0, updated_at = ?
                            WHERE item_id = ?;
                            """,
                            (now_str, item_id_val),
                        )
                        conn.execute(
                            """
                            INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                            VALUES (?, ?, ?, ?, 'done', ?);
                            """,
                            (item_id_val, ctx.run_id, now_str, stage, result.detail),
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE items
                            SET stage = ?, status = 'pending', consecutive_failures = 0, updated_at = ?
                            WHERE item_id = ?;
                            """,
                            (next_stage, now_str, item_id_val),
                        )
                        conn.execute(
                            """
                            INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                            VALUES (?, ?, ?, ?, 'advance', ?);
                            """,
                            (item_id_val, ctx.run_id, now_str, stage, f"Advanced to {next_stage}"),
                        )

                elif result.outcome == "done":
                    conn.execute(
                        """
                        UPDATE items
                        SET status = 'done', consecutive_failures = 0, updated_at = ?
                        WHERE item_id = ?;
                        """,
                        (now_str, item_id_val),
                    )
                    conn.execute(
                        """
                        INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                        VALUES (?, ?, ?, ?, 'done', ?);
                        """,
                        (item_id_val, ctx.run_id, now_str, stage, result.detail),
                    )

                elif result.outcome == "hold":
                    conn.execute(
                        """
                        UPDATE items
                        SET status = 'held', hold_reason = ?, hold_since = COALESCE(hold_since, ?), updated_at = ?
                        WHERE item_id = ?;
                        """,
                        (result.hold_reason, now_str, now_str, item_id_val),
                    )
                    conn.execute(
                        """
                        INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                        VALUES (?, ?, ?, ?, 'held', ?);
                        """,
                        (item_id_val, ctx.run_id, now_str, stage, result.detail),
                    )

                elif result.outcome == "skip":
                    conn.execute(
                        """
                        UPDATE items
                        SET status = 'skipped', updated_at = ?
                        WHERE item_id = ?;
                        """,
                        (now_str, item_id_val),
                    )
                    conn.execute(
                        """
                        INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                        VALUES (?, ?, ?, ?, 'skipped', ?);
                        """,
                        (item_id_val, ctx.run_id, now_str, stage, result.detail),
                    )

                elif result.outcome == "fail":
                    new_consec = consec_fails + 1
                    conn.execute(
                        """
                        UPDATE items
                        SET status = 'failed', consecutive_failures = ?, last_error = ?, updated_at = ?
                        WHERE item_id = ?;
                        """,
                        (new_consec, result.detail, now_str, item_id_val),
                    )
                    conn.execute(
                        """
                        INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                        VALUES (?, ?, ?, ?, 'failed', ?);
                        """,
                        (item_id_val, ctx.run_id, now_str, stage, result.detail),
                    )

                    # 3 consecutive failures -> flag row
                    if new_consec >= 3:
                        flag_id = new_ulid()
                        subj = f"Item {item_id_val} failed 3 times: {result.detail or 'unknown error'}"
                        choices_json = json.dumps([
                            {"key": "retry", "label": "retry"},
                            {"key": "drop", "label": "drop"},
                        ])
                        conn.execute(
                            """
                            INSERT INTO flags (flag_id, kind, item_id, subject, choices_json, status, created_at)
                            VALUES (?, 'item_failures', ?, ?, ?, 'open', ?);
                            """,
                            (flag_id, item_id_val, subj, choices_json, now_str),
                        )
                        if ctx.flags is not None and hasattr(ctx.flags, "add"):
                            try:
                                ctx.flags.add(
                                    "item_failures",
                                    subj,
                                    [("retry", "retry"), ("drop", "drop")],
                                    item_id=item_id_val,
                                )
                            except Exception as ex:  # noqa: BLE001
                                logger.warning("Could not add to ctx.flags: %s", ex)

                # Insert any new items spawned by the stage result
                if result.new_items:
                    for new_item in result.new_items:
                        # Privacy rule: can only move toward private, never away from it
                        # If parent was private, child must be private; if parent was personal, child can be personal or private
                        if item_obj.privacy == Privacy.PRIVATE:
                            effective_privacy = Privacy.PRIVATE
                        elif item_obj.privacy == Privacy.PERSONAL and new_item.privacy == Privacy.CLASS:
                            effective_privacy = Privacy.PERSONAL
                        else:
                            effective_privacy = new_item.privacy

                        new_handler = self._find_handler(new_item.kind)
                        new_stage = new_handler.stages[0] if (new_handler and new_handler.stages) else "entry"

                        conn.execute(
                            """
                            INSERT OR IGNORE INTO items (
                                item_id, source, kind, native_id, version, course_key,
                                privacy, exam_key, priority, stage, status,
                                attempts, consecutive_failures, payload_json,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, ?, ?, ?);
                            """,
                            (
                                new_item.item_id,
                                new_item.source,
                                new_item.kind,
                                new_item.native_id,
                                new_item.version,
                                new_item.course_key,
                                effective_privacy.value,
                                new_item.exam_key,
                                new_item.priority,
                                new_stage,
                                json.dumps(new_item.payload),
                                now_str,
                                now_str,
                            ),
                        )

                conn.commit()
