"""Pocket client, private rules, sensitivity, and recording ingest source (§3.5, #55)."""
from articulate.pocket.client import PocketClient, TokenRejectedError
from articulate.pocket.private import (
    PrivateConfigError,
    is_private_recording,
    resolve_private_rules,
)
from articulate.pocket.sensitivity import check_sensitivity
from articulate.pocket.source import PocketSource, archive_transcript, step

__all__ = [
    "PocketClient",
    "PocketSource",
    "PrivateConfigError",
    "TokenRejectedError",
    "archive_transcript",
    "check_sensitivity",
    "is_private_recording",
    "resolve_private_rules",
    "step",
]
