"""Dependency checker and doctor command (§3.11, §3.12)."""
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from articulate.core.secrets import (
    SecretNotFoundError,
    get_canvas_token,
    get_gemini_key,
    get_google_oauth,
    get_pocket_key,
)

logger = logging.getLogger(__name__)


@dataclass
class DependencyReport:
    name: str
    status: str  # ok | missing | down | warning
    notes: str


def check_ollama(url: str = "http://127.0.0.1:11434") -> DependencyReport:
    """Check Ollama daemon and required models (§3.12)."""
    tags_url = f"{url.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(tags_url, headers={"User-Agent": "articulate"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return DependencyReport(
            name="Ollama",
            status="down",
            notes=f"local daemon not reachable ({e.reason})",
        )
    except Exception as e:  # noqa: BLE001
        return DependencyReport(
            name="Ollama",
            status="down",
            notes=f"local daemon error ({e})",
        )

    models = [m.get("name", "") for m in data.get("models", [])]
    required = ["qwen3:8b", "embeddinggemma", "qwen3-vl:8b-instruct"]
    missing = []
    for req_name in required:
        if not any(req_name in m for m in models):
            missing.append(req_name)

    if missing:
        return DependencyReport(
            name="Ollama",
            status="missing",
            notes=f"daemon up, missing models: {', '.join(missing)}",
        )

    return DependencyReport(
        name="Ollama",
        status="ok",
        notes="local daemon up, required models present",
    )


def check_deckcraft_keynote() -> DependencyReport:
    """Check DeckCraft and Keynote PPTX converters (§3.12, #28)."""
    dc_path = Path("/Applications/DeckCraft.app")
    kn_path = Path("/Applications/Keynote.app")

    dc_exists = dc_path.exists()
    kn_exists = kn_path.exists()

    if dc_exists and kn_exists:
        return DependencyReport(
            name="DeckCraft / Keynote",
            status="ok",
            notes="DeckCraft.app and Keynote.app installed",
        )
    if dc_exists:
        return DependencyReport(
            name="DeckCraft / Keynote",
            status="ok",
            notes="DeckCraft.app installed (Keynote missing)",
        )
    if kn_exists:
        return DependencyReport(
            name="DeckCraft / Keynote",
            status="ok",
            notes="Keynote.app installed (DeckCraft missing)",
        )
    return DependencyReport(
        name="DeckCraft / Keynote",
        status="missing",
        notes="neither DeckCraft.app nor Keynote.app installed",
    )


def check_canvas_token() -> DependencyReport:
    """Check Canvas token in Keychain (§3.3)."""
    try:
        get_canvas_token()
        return DependencyReport(
            name="Canvas token",
            status="ok",
            notes="keychain item present",
        )
    except SecretNotFoundError:
        return DependencyReport(
            name="Canvas token",
            status="missing",
            notes="keychain item missing (service=articulate, account=canvas_token)",
        )
    except Exception as e:  # noqa: BLE001
        return DependencyReport(
            name="Canvas token",
            status="missing",
            notes=f"keychain read failed: {e}",
        )


def check_pocket_api() -> DependencyReport:
    """Check Pocket API key in Keychain (§3.3)."""
    try:
        get_pocket_key()
        return DependencyReport(
            name="Pocket API",
            status="ok",
            notes="keychain item present",
        )
    except SecretNotFoundError:
        return DependencyReport(
            name="Pocket API",
            status="missing",
            notes="keychain item missing (service=articulate, account=pocket_api_key)",
        )
    except Exception as e:  # noqa: BLE001
        return DependencyReport(
            name="Pocket API",
            status="missing",
            notes=f"keychain read failed: {e}",
        )


def check_google_token() -> DependencyReport:
    """Check Google OAuth token in Keychain (§3.3)."""
    try:
        get_google_oauth()
        return DependencyReport(
            name="Google token",
            status="ok",
            notes="keychain item present",
        )
    except SecretNotFoundError:
        return DependencyReport(
            name="Google token",
            status="missing",
            notes="keychain item missing (service=articulate, account=google_oauth)",
        )
    except Exception as e:  # noqa: BLE001
        return DependencyReport(
            name="Google token",
            status="missing",
            notes=f"keychain read failed: {e}",
        )


def check_gemini_key() -> DependencyReport:
    """Check Gemini API key in Keychain (§3.3)."""
    try:
        get_gemini_key()
        return DependencyReport(
            name="Gemini key",
            status="ok",
            notes="keychain item present",
        )
    except SecretNotFoundError:
        return DependencyReport(
            name="Gemini key",
            status="missing",
            notes="keychain item missing (service=articulate-gemini)",
        )
    except Exception as e:  # noqa: BLE001
        return DependencyReport(
            name="Gemini key",
            status="missing",
            notes=f"keychain read failed: {e}",
        )


def check_dependencies() -> list[DependencyReport]:
    """Run all dependency checks (§3.11, §3.12)."""
    return [
        check_ollama(),
        check_deckcraft_keynote(),
        check_canvas_token(),
        check_pocket_api(),
        check_google_token(),
        check_gemini_key(),
    ]


def format_dependencies_table(reports: list[DependencyReport]) -> str:
    """Format reports as Markdown table for Dashboard section 6."""
    lines = [
        "## Dependencies",
        "| Dependency | Status | Notes |",
        "|---|---|---|",
    ]
    for r in reports:
        lines.append(f"| {r.name} | {r.status} | {r.notes} |")
    return "\n".join(lines)


def run_doctor() -> int:
    """CLI handler for `articulate doctor`."""
    reports = check_dependencies()
    table = format_dependencies_table(reports)
    print(table)
    # doctor is a read-only diagnostics command: return 0 when run completes
    return 0
