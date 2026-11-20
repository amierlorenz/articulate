"""Job runner, Step definitions, and run lock management (§3.12)."""
import fcntl
import importlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from articulate.core.db import Database
from articulate.core.ids import new_ulid
from articulate.core.paths import get_db_path, run_lock_path
from articulate.core.pipeline import RunContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Step:
    name: str
    entry: str
    jobs: set[str]
    order: int


@dataclass
class StepSummary:
    done: int = 0
    held: int = 0
    failed: int = 0
    detail: str = ""


class RunLock:
    """Manages the process lock file DATA/run.lock using fcntl.flock (§3.12)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path).resolve() if path else run_lock_path()
        self._fd: int | None = None

    def acquire(self, blocking: bool = False) -> bool:
        """Attempt to acquire an exclusive lock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = open(self.path, "a+")  # noqa: SIM115
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            fcntl.flock(self._fd.fileno(), flags)
            return True
        except (BlockingIOError, OSError):
            if self._fd is not None:
                try:
                    self._fd.close()
                except OSError:
                    pass
                self._fd = None
            return False

    def release(self) -> None:
        """Release the held lock."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                self._fd.close()
            except OSError:
                pass
            finally:
                self._fd = None

    def __enter__(self) -> Self:
        self.acquire(blocking=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()


def run_job(
    job_name: str,
    only_step: str | None = None,
    db: Database | None = None,
    lock_path: Path | None = None,
    steps: list[Step] | None = None,
    config: Any = None,
) -> int:
    """
    Run a named job (nightly, pass2, watch, manual).
    Returns 0 on success or quiet exit, non-zero on failure.
    """
    lock = RunLock(lock_path)
    if not lock.acquire(blocking=False):
        if job_name == "watch":
            logger.info("Another job holds the run lock; watch exiting quietly.")
            return 0
        raise RuntimeError(f"Could not acquire run lock at {lock.path}: another job is running.")

    database = db or Database(get_db_path())
    database.migrate()

    run_id = new_ulid()
    now = datetime.now(UTC)
    now_str = now.isoformat()

    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO runs (run_id, job, started_at, status)
            VALUES (?, ?, ?, 'running');
            """,
            (run_id, job_name, now_str),
        )
        conn.commit()

    ctx = RunContext(
        run_id=run_id,
        job=job_name,
        now=now,
        db=database,
        config=config,
    )

    if steps is None:
        try:
            from articulate.registry import STEPS
            step_list = STEPS
        except (ImportError, AttributeError):
            step_list = []
    else:
        step_list = steps

    # Filter applicable steps
    filtered_steps = [s for s in step_list if job_name in s.jobs]
    if only_step:
        filtered_steps = [s for s in filtered_steps if s.name == only_step]

    filtered_steps.sort(key=lambda s: s.order)

    summary_counts = {"done": 0, "held": 0, "failed": 0}
    run_status = "ok"
    error_msg: str | None = None

    try:
        for s in filtered_steps:
            logger.info("Executing step %s (%s)", s.name, s.entry)
            try:
                mod_name, func_name = s.entry.split(":")
                mod = importlib.import_module(mod_name)
                func: Callable[[RunContext], StepSummary] = getattr(mod, func_name)
                res = func(ctx)
                if isinstance(res, StepSummary):
                    summary_counts["done"] += res.done
                    summary_counts["held"] += res.held
                    summary_counts["failed"] += res.failed
            except (ImportError, AttributeError) as ie:
                logger.warning("Step %s entry %s could not be loaded: %s", s.name, s.entry, ie)
                if only_step:
                    raise
            except Exception as se:
                logger.error("Step %s failed with exception: %s", s.name, se)
                summary_counts["failed"] += 1
                if only_step:
                    raise
    except Exception as e:  # noqa: BLE001
        run_status = "failed"
        error_msg = str(e)
        logger.error("Run %s failed: %s", run_id, e)
    finally:
        end_time_str = datetime.now(UTC).isoformat()
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET ended_at = ?, status = ?, error = ?, summary_json = ?
                WHERE run_id = ?;
                """,
                (end_time_str, run_status, error_msg, json.dumps(summary_counts), run_id),
            )
            conn.commit()
        lock.release()

    return 0 if run_status == "ok" else 1
