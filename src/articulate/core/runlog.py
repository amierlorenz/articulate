"""Append-only log for _system/Runs.md (§3.11, Issue #11)."""
from datetime import UTC, datetime

from articulate.core.vault import Vault


class RunLog:
    """Manages _system/Runs.md log lines (§3.11)."""

    def __init__(self, vault: Vault | None = None):
        self.vault = vault

    def line(self, text: str) -> None:
        """Append a single line to _system/Runs.md."""
        if self.vault is not None:
            clean_line = text.rstrip("\n")
            self.vault.append_system("Runs.md", clean_line)

    def health_appeared(self, kind: str, key: str = "", now: datetime | None = None) -> None:
        """Log health incident appear line (§3.11)."""
        dt = now or datetime.now(UTC)
        ts_str = dt.strftime("%Y-%m-%d %H:%M")
        msg = f"{kind}: {key}" if key else kind
        self.line(f"- {ts_str} ⚠️ appeared: {msg}")

    def health_recovered(self, kind: str, key: str = "", now: datetime | None = None) -> None:
        """Log health incident recovery line (§3.11)."""
        dt = now or datetime.now(UTC)
        ts_str = dt.strftime("%Y-%m-%d %H:%M")
        msg = f"{kind}: {key}" if key else kind
        self.line(f"- {ts_str} ✅ recovered: {msg}")
