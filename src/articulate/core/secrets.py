"""Keychain secrets reader (§3.3)."""
import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class SecretNotFoundError(Exception):
    """Raised when a secret is missing from Keychain. Never contains the secret value."""

    def __init__(self, service: str, account: str | None = None, message: str = ""):
        self.service = service
        self.account = account
        msg = message or f"Secret not found in Keychain for service '{service}'" + (
            f", account '{account}'" if account else ""
        )
        super().__init__(msg)


def get_secret(service: str, account: str | None = None) -> str:
    """
    Read a secret password from macOS Keychain (§3.3).
    Command: security find-generic-password -s <service> [-a <account>] -w
    Never logs secrets or puts secrets in exception messages.
    """
    cmd = ["security", "find-generic-password", "-s", service]
    if account:
        cmd.extend(["-a", account])
    cmd.append("-w")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        raise SecretNotFoundError(service, account, f"Failed executing security command: {e}") from e

    if proc.returncode != 0:
        raise SecretNotFoundError(service, account)

    secret = proc.stdout.rstrip("\r\n")
    if not secret:
        raise SecretNotFoundError(service, account, "Secret found but empty in Keychain")

    return secret


def get_gemini_key() -> str:
    """Gemini API key: service articulate-gemini, any account."""
    return get_secret("articulate-gemini")


def get_pocket_key() -> str:
    """Pocket API key: service articulate, account pocket_api_key."""
    return get_secret("articulate", "pocket_api_key")


def get_canvas_token() -> str:
    """Canvas token: service articulate, account canvas_token."""
    return get_secret("articulate", "canvas_token")


def get_google_oauth() -> dict[str, Any]:
    """Google OAuth credentials JSON: service articulate, account google_oauth."""
    secret_str = get_secret("articulate", "google_oauth")
    try:
        return json.loads(secret_str)
    except Exception as e:
        raise SecretNotFoundError(
            "articulate",
            "google_oauth",
            f"Invalid JSON in google_oauth secret: {e}",
        ) from e
