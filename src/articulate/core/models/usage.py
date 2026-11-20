"""Claude usage reader and gate checker (§3.6, research/claude-usage-from-script.md)."""
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from articulate.core.config import Config
from articulate.core.db import Database

logger = logging.getLogger(__name__)


@dataclass
class ClaudeUsageResult:
    ok: bool
    session_pct: float | None = None
    weekly_pct: float | None = None
    session_resets_at: str | None = None
    weekly_resets_at: str | None = None
    credential_expires_at: str | None = None
    cli_version: str | None = None
    error: str | None = None


def _parse_iso(iso_str: str) -> datetime:
    """Parse ISO 8601 string to timezone-aware datetime."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _run_usage_cmd(timeout_s: int = 30) -> tuple[int, str]:
    """Run `claude -p "/usage" --output-format stream-json --verbose` with timeout."""
    try:
        proc = subprocess.run(
            ["claude", "-p", "/usage", "--output-format", "stream-json", "--verbose"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired:
        logger.warning("Claude /usage command timed out after %d seconds", timeout_s)
        return 124, ""
    except FileNotFoundError:
        logger.warning("Claude CLI not found in PATH")
        return 127, ""
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to run claude /usage: %s", e)
        return 1, ""


def read_claude_usage(
    db: Database | None = None,
    config: Config | None = None,
    now: datetime | None = None,
) -> ClaudeUsageResult:
    """
    Read Claude session and weekly usage percentages from `claude -p "/usage"`.
    Fails closed on non-zero exit, timeout, missing report, missing rows, or past resets_at.
    Stores every check in `claude_usage` table.
    """
    if now is None:
        now = datetime.now(UTC)

    timeout_s = 30
    if config and config.models and config.models.claude:
        timeout_s = config.models.claude.usage_timeout_s

    code, stdout = _run_usage_cmd(timeout_s=timeout_s)

    checked_at_str = now.isoformat()

    if code != 0 or not stdout:
        err = f"Non-zero exit code ({code}) or empty stdout"
        _record_usage(db, checked_at_str, ok=0, error=err)
        return ClaudeUsageResult(ok=False, error=err)

    cli_version: str | None = None
    credential_expires_at: str | None = None
    session_row: dict[str, Any] | None = None
    weekly_row: dict[str, Any] | None = None
    found_usage_report = False

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        item_type = data.get("type")

        if item_type == "init":
            cli_version = data.get("claude_code_version")

        elif item_type == "assistant":
            usage_report = data.get("usage_report")
            if usage_report and isinstance(usage_report, dict):
                found_usage_report = True
                rate_limits = usage_report.get("rate_limits")
                if rate_limits and isinstance(rate_limits, dict):
                    limits = rate_limits.get("limits")
                    if isinstance(limits, list) and limits:
                        for row in limits:
                            if isinstance(row, dict):
                                kind = row.get("kind")
                                if kind == "session":
                                    session_row = row
                                elif kind == "weekly_all":
                                    weekly_row = row

        elif item_type == "result":
            credential_expires_at = data.get("credential_expires_at")

    # Fail closed checks (§3.6)
    if not found_usage_report:
        err = "No usage_report found in assistant output"
        _record_usage(db, checked_at_str, ok=0, cli_version=cli_version, error=err)
        return ClaudeUsageResult(ok=False, cli_version=cli_version, error=err)

    if session_row is None or weekly_row is None:
        err = "Missing session or weekly_all limit row in usage_report"
        _record_usage(db, checked_at_str, ok=0, cli_version=cli_version, error=err)
        return ClaudeUsageResult(ok=False, cli_version=cli_version, error=err)

    try:
        session_pct = float(session_row["percent"])
        weekly_pct = float(weekly_row["percent"])
        session_resets_at = session_row.get("resets_at")
        weekly_resets_at = weekly_row.get("resets_at")
    except (KeyError, ValueError, TypeError) as e:
        err = f"Malformed limit numbers or fields: {e}"
        _record_usage(db, checked_at_str, ok=0, cli_version=cli_version, error=err)
        return ClaudeUsageResult(ok=False, cli_version=cli_version, error=err)

    # Check if resets_at is in the past
    if session_resets_at:
        try:
            if _parse_iso(session_resets_at) < now:
                err = f"session resets_at ({session_resets_at}) is in the past"
                _record_usage(db, checked_at_str, ok=0, cli_version=cli_version, error=err)
                return ClaudeUsageResult(ok=False, cli_version=cli_version, error=err)
        except Exception as pe:  # noqa: BLE001
            err = f"Could not parse session resets_at: {pe}"
            _record_usage(db, checked_at_str, ok=0, cli_version=cli_version, error=err)
            return ClaudeUsageResult(ok=False, cli_version=cli_version, error=err)

    if weekly_resets_at:
        try:
            if _parse_iso(weekly_resets_at) < now:
                err = f"weekly resets_at ({weekly_resets_at}) is in the past"
                _record_usage(db, checked_at_str, ok=0, cli_version=cli_version, error=err)
                return ClaudeUsageResult(ok=False, cli_version=cli_version, error=err)
        except Exception as pe:  # noqa: BLE001
            err = f"Could not parse weekly resets_at: {pe}"
            _record_usage(db, checked_at_str, ok=0, cli_version=cli_version, error=err)
            return ClaudeUsageResult(ok=False, cli_version=cli_version, error=err)

    # Check credential expiration for health alert (§3.10)
    if credential_expires_at:
        try:
            exp_dt = _parse_iso(credential_expires_at)
            if exp_dt <= now + timedelta(days=7):
                _record_health_alert(
                    db,
                    kind="claude_credential",
                    key="claude",
                    detail=f"Claude credential expires within 7 days or is expired: {credential_expires_at}",
                    interrupt=1,
                    now=now,
                )
        except Exception as ce:  # noqa: BLE001
            logger.warning("Could not parse credential_expires_at: %s", ce)

    # Valid check
    _record_usage(
        db,
        checked_at_str,
        ok=1,
        session_pct=session_pct,
        weekly_pct=weekly_pct,
        session_resets_at=session_resets_at,
        weekly_resets_at=weekly_resets_at,
        credential_expires_at=credential_expires_at,
        cli_version=cli_version,
    )

    return ClaudeUsageResult(
        ok=True,
        session_pct=session_pct,
        weekly_pct=weekly_pct,
        session_resets_at=session_resets_at,
        weekly_resets_at=weekly_resets_at,
        credential_expires_at=credential_expires_at,
        cli_version=cli_version,
    )


def _record_usage(
    db: Database | None,
    checked_at: str,
    ok: int,
    session_pct: float | None = None,
    weekly_pct: float | None = None,
    session_resets_at: str | None = None,
    weekly_resets_at: str | None = None,
    credential_expires_at: str | None = None,
    cli_version: str | None = None,
    error: str | None = None,
) -> None:
    if db is None:
        return
    try:
        with db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO claude_usage (
                    checked_at, ok, session_pct, weekly_pct, session_resets_at,
                    weekly_resets_at, credential_expires_at, cli_version, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    checked_at,
                    ok,
                    session_pct,
                    weekly_pct,
                    session_resets_at,
                    weekly_resets_at,
                    credential_expires_at,
                    cli_version,
                    error,
                ),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not insert claude_usage row: %s", e)


def _record_health_alert(
    db: Database | None,
    kind: str,
    key: str,
    detail: str,
    interrupt: int,
    now: datetime,
) -> None:
    if db is None:
        return
    try:
        now_str = now.isoformat()
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO health (kind, key, detail, interrupt, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, key, first_seen) DO UPDATE SET last_seen = excluded.last_seen;
                """,
                (kind, key, detail, interrupt, now_str, now_str),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not insert health row: %s", e)
