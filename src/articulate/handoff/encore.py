"""Encore app launching, state waiting, and health management (§3.7, §3.10, Acceptance check 2.36)."""

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from articulate.core.handoff_schemas import State, load_handoff_file
from articulate.core.paths import from_encore_dir
from articulate.core.pipeline import RunContext

logger = logging.getLogger(__name__)


@dataclass
class LaunchResult:
    ok: bool
    timed_out: bool
    state: State | None = None
    detail: str = ""


def get_last_known_state(ctx: RunContext) -> State | None:
    """Read the latest state snapshot from SQLite encore_snapshots or from-encore/state.json."""
    if ctx.db is not None:
        try:
            with ctx.db.connect() as conn:
                row = conn.execute(
                    """
                    SELECT body_json FROM encore_snapshots
                    WHERE schema = 'encore.state'
                    ORDER BY exported_at DESC LIMIT 1;
                    """
                ).fetchone()
                if row and row["body_json"]:
                    # Load model
                    import json

                    data = json.loads(row["body_json"])
                    return State.model_validate(data)
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not read state from encore_snapshots: %s", e)

    state_path = from_encore_dir() / "state.json"
    if state_path.exists():
        try:
            return load_handoff_file(
                state_path, State, expected_schema="encore.state", expected_version=1
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not read state.json from file: %s", e)

    return None


def launch_and_wait(
    ctx: RunContext,
    timeout_s: float | None = None,
    poll_interval_s: float = 0.5,
    command_runner: Callable[[list[str]], Any] | None = None,
) -> LaunchResult:
    """
    Launches Encore with `open -g` and waits ≤3 min (or timeout_s) for fresh state.json (§3.7).
    On timeout, plans from last state and raises flag + checks health (§3.10).
    """
    state_file = from_encore_dir() / "state.json"
    pre_launch_mtime = state_file.stat().st_mtime if state_file.exists() else 0.0
    pre_launch_exported_at: datetime | None = None

    if state_file.exists():
        try:
            old_state = load_handoff_file(
                state_file, State, expected_schema="encore.state", expected_version=1
            )
            pre_launch_exported_at = old_state.exportedAt
        except Exception:  # noqa: BLE001, S110
            pass

    # Bundle ID from config
    bundle_id = "com.amierlorenz.Encore"
    wait_limit = 180.0
    if ctx.config and ctx.config.articulate and ctx.config.articulate.encore:
        bundle_id = ctx.config.articulate.encore.bundle_id or bundle_id
        wait_limit = float(ctx.config.articulate.encore.state_wait_s or wait_limit)

    if timeout_s is not None:
        wait_limit = float(timeout_s)

    launch_cmd = ["open", "-g", "-b", bundle_id]
    logger.info("Launching Encore with: %s", " ".join(launch_cmd))

    try:
        if command_runner is not None:
            command_runner(launch_cmd)
        else:
            subprocess.run(launch_cmd, check=False, capture_output=True, text=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to invoke open -g -b %s: %s", bundle_id, e)

    # Wait loop
    elapsed = 0.0
    fresh_state: State | None = None

    while elapsed < wait_limit:
        time.sleep(poll_interval_s)
        elapsed += poll_interval_s

        if state_file.exists():
            curr_mtime = state_file.stat().st_mtime
            if curr_mtime > pre_launch_mtime:
                try:
                    cand_state = load_handoff_file(
                        state_file, State, expected_schema="encore.state", expected_version=1
                    )
                    if (
                        pre_launch_exported_at is None
                        or cand_state.exportedAt > pre_launch_exported_at
                    ):
                        fresh_state = cand_state
                        break
                except Exception:  # noqa: BLE001, S110
                    # File might be partially written or invalid
                    pass

    now_dt = ctx.now if ctx.now is not None else datetime.now(UTC)

    if fresh_state is not None:
        # Fresh state received!
        logger.info("Encore fresh state received in %.1fs", elapsed)
        now_str = now_dt.isoformat()
        if ctx.db is not None:
            try:
                with ctx.db.connect() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO encore_snapshots (schema, exported_at, read_at, body_json)
                        VALUES ('encore.state', ?, ?, ?);
                        """,
                        (
                            fresh_state.exportedAt.isoformat(),
                            now_str,
                            state_file.read_text(encoding="utf-8"),
                        ),
                    )
                    # Mark appliedBatches
                    for b_id in fresh_state.appliedBatches:
                        conn.execute(
                            """
                            UPDATE handoff_files
                            SET acked_at = ?
                            WHERE (file_id = ? OR file_id = ? OR file_id = ?) AND acked_at IS NULL;
                            """,
                            (now_str, b_id, f"import-{b_id}", b_id.removeprefix("import-")),
                        )
                    conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to store fresh state in db: %s", e)

        # Recover health if recovered
        if ctx.health is not None:
            ctx.health.recover("encore_stale")

        return LaunchResult(
            ok=True, timed_out=False, state=fresh_state, detail="Fresh state received"
        )

    # Timeout path
    logger.warning("Encore launch timed out after %.1fs waiting for fresh state.json", wait_limit)

    last_state = get_last_known_state(ctx)

    # 1. Raise flag: timeout -> plan from last state and flag
    if ctx.flags is not None:
        ctx.flags.add(
            "encore_timeout",
            f"Encore launch timed out after {int(wait_limit)}s waiting for fresh state.json",
            [("retry", "retry"), ("ignore", "ignore")],
        )

    # 2. Health checks:
    # State > 48h old -> dashboard row
    # No ack or state for 2 days -> interrupting task
    if ctx.health is not None:
        state_age_hours = 0.0
        if last_state is not None:
            exp_at = last_state.exportedAt
            if exp_at.tzinfo is None:
                exp_at = exp_at.replace(tzinfo=UTC)
            state_age_hours = (now_dt - exp_at).total_seconds() / 3600.0

        is_stale_48h = (last_state is None) or (state_age_hours > 48.0)

        # Check latest ack from handoff_files
        has_recent_ack = False
        if ctx.db is not None:
            try:
                with ctx.db.connect() as conn:
                    two_days_ago = (now_dt - timedelta(days=2)).isoformat()
                    row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM handoff_files WHERE acked_at >= ?;",
                        (two_days_ago,),
                    ).fetchone()
                    if row and row["cnt"] > 0:
                        has_recent_ack = True
            except Exception:  # noqa: BLE001, S110
                pass

        if is_stale_48h and not has_recent_ack:
            # No ack or state for 2 days -> interrupting task
            ctx.health.raise_(
                "encore_stale",
                key="handoff",
                detail=f"No import ack or state.json for 2 days (state age: {state_age_hours:.1f}h)",
                interrupt=True,
                now=now_dt,
            )
        elif is_stale_48h:
            # State > 48h old -> dashboard row
            ctx.health.raise_(
                "encore_stale",
                key="state_age",
                detail=f"Encore state is over 48 hours old ({state_age_hours:.1f}h)",
                interrupt=False,
                now=now_dt,
            )

    return LaunchResult(
        ok=False,
        timed_out=True,
        state=last_state,
        detail=f"Timed out after {int(wait_limit)}s waiting for state.json",
    )
