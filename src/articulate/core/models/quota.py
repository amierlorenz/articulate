"""Quota ledger tracking and report generation (§3.4, §3.6, §3.12)."""
import logging
from datetime import UTC, datetime
from typing import Any

from articulate.core.clock import pacific_date
from articulate.core.config import Config
from articulate.core.db import Database

logger = logging.getLogger(__name__)


def count_quota_calls_today(db: Database, provider: str, model: str, pacific_date_str: str) -> int:
    """Count calls that count toward quota for a specific model today (§3.4, §3.6)."""
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM model_calls
            WHERE provider = ? AND model = ? AND pacific_date = ? AND counts_quota = 1;
            """,
            (provider, model, pacific_date_str),
        ).fetchone()
        return row["cnt"] if row else 0


def count_local_night_calls(db: Database, provider: str, local_night_str: str) -> int:
    """Count calls made during a local night (for Claude nightly call cap, §3.6)."""
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM model_calls
            WHERE provider = ? AND local_night = ?;
            """,
            (provider, local_night_str),
        ).fetchone()
        return row["cnt"] if row else 0


DEFAULT_GEMINI_LIMITS: dict[str, tuple[int, int]] = {
    "gemini-3.8-flash": (20, 0),
    "gemini-3.6-flash": (20, 0),
    "gemini-4-hydrogen": (20, 0),
    "gemini-3.5-flash-lite": (500, 40),
    "gemini-embedding-2": (1000, 50),
}


def get_remaining_quota(
    db: Database,
    config: Config | None,
    model: str,
    job: str = "manual",
    now: datetime | None = None,
) -> int:
    """
    Calculate remaining quota for a Gemini model today (§3.6).
    remaining = rpd - counted calls today - reserve (only in pass2).
    """
    if now is None:
        now = datetime.now(UTC)

    p_date = pacific_date(now)

    def_rpd, def_reserve = DEFAULT_GEMINI_LIMITS.get(model, (20, 0))
    rpd = def_rpd
    reserve = def_reserve if job == "pass2" else 0

    if config and config.models and config.models.gemini and config.models.gemini.models:
        model_cfg = config.models.gemini.models.get(model)
        if model_cfg:
            rpd = model_cfg.rpd
            reserve = model_cfg.reserve if job == "pass2" else 0

    used = count_quota_calls_today(db, "gemini", model, p_date)
    return max(0, rpd - used - reserve)


def format_quota_report(db: Database, config: Config | None = None) -> str:
    """Format today's model quota ledger and Claude usage for articulate quota (§3.12)."""
    now = datetime.now(UTC)
    p_date = pacific_date(now)

    lines: list[str] = [
        "Articulate Model Quota Ledger",
        f"Pacific Date: {p_date} | UTC: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Gemini Free Tier (RPD):",
        f"{'Model':<26} {'Calls Today':<14} {'Limit (RPD)':<12} {'Reserve':<10} {'Remaining':<10}",
        "-" * 72,
    ]

    gemini_models: dict[str, Any] = {}
    if config and config.models and config.models.gemini and config.models.gemini.models:
        gemini_models = config.models.gemini.models
    else:
        # Default models if config not available
        gemini_models = {
            "gemini-3.8-flash": None,
            "gemini-3.6-flash": None,
            "gemini-4-hydrogen": None,
            "gemini-3.5-flash-lite": None,
            "gemini-embedding-2": None,
        }

    with db.connect() as conn:
        for model_name, m_cfg in gemini_models.items():
            used = count_quota_calls_today(db, "gemini", model_name, p_date)
            def_rpd, def_reserve = DEFAULT_GEMINI_LIMITS.get(model_name, (20, 0))
            rpd = m_cfg.rpd if m_cfg else def_rpd
            reserve = m_cfg.reserve if m_cfg else def_reserve
            rem = max(0, rpd - used)

            # Check model state (exhausted or missing)
            state_row = conn.execute("SELECT * FROM model_state WHERE model = ?", (model_name,)).fetchone()
            extra = ""
            if state_row:
                if state_row["exhausted_until"]:
                    extra = f" (exhausted until {state_row['exhausted_until']})"
                elif state_row["missing_since"]:
                    extra = " (unknown/retired model)"

            lines.append(
                f"{model_name:<26} {used:<14} {rpd:<12} {reserve:<10} {rem:<10}{extra}"
            )

        lines.append("")
        lines.append("Claude Usage Gate:")

        claude_row = conn.execute(
            "SELECT * FROM claude_usage ORDER BY checked_at DESC LIMIT 1;"
        ).fetchone()

        if claude_row and claude_row["ok"] == 1:
            sess_pct = f"{claude_row['session_pct']:.1f}%" if claude_row["session_pct"] is not None else "N/A"
            week_pct = f"{claude_row['weekly_pct']:.1f}%" if claude_row["weekly_pct"] is not None else "N/A"
            sess_reset = claude_row["session_resets_at"] or "N/A"
            week_reset = claude_row["weekly_resets_at"] or "N/A"
            cred_exp = claude_row["credential_expires_at"] or "N/A"
            lines.append(f"  Session usage: {sess_pct} (resets {sess_reset})")
            lines.append(f"  Weekly usage:  {week_pct} (resets {week_reset})")
            lines.append(f"  Credential expires: {cred_exp}")
        elif claude_row and claude_row["ok"] == 0:
            lines.append(f"  Status: Check failed ({claude_row['error'] or 'unknown error'})")
        else:
            lines.append("  Status: No usage check recorded today")

    return "\n".join(lines)
