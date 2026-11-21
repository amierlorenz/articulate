"""Private folder and tag rules for Pocket (fail-closed, Acceptance 1.2, §3.5)."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class PrivateConfigError(Exception):
    """Raised when configured private_folder_id or private_tag_id is missing from Pocket."""

    def __init__(self, message: str):
        super().__init__(message)


def _collect_subtree_ids(folder_node: dict[str, Any], collected: set[str]) -> None:
    """Recursively collect folder id and all children folder ids."""
    f_id = folder_node.get("id")
    if f_id:
        collected.add(str(f_id))
    for child in folder_node.get("children") or []:
        _collect_subtree_ids(child, collected)


def _find_folder_node(folders: list[dict[str, Any]], target_id: str) -> dict[str, Any] | None:
    """Find a folder node by id anywhere in the folder hierarchy."""
    for f in folders:
        if str(f.get("id")) == target_id:
            return f
        child_match = _find_folder_node(f.get("children") or [], target_id)
        if child_match is not None:
            return child_match
    return None


def resolve_private_rules(
    folders_data: list[dict[str, Any]],
    tags_data: list[dict[str, Any]],
    private_folder_id: str,
    private_tag_id: str,
    health: Any = None,
) -> tuple[set[str], set[str]]:
    """
    Validate that configured private folder and tag exist in Pocket, and build the private ID sets.
    Fails closed: if configured folder or tag id is missing, raises PrivateConfigError and records health row.
    Returns: (private_folder_ids_subtree, private_tag_ids)
    """
    # 1. Validate private_folder_id exists
    folder_node = _find_folder_node(folders_data, private_folder_id)
    if folder_node is None:
        msg = f"Configured private_folder_id '{private_folder_id}' missing from Pocket /public/folders"
        if health is not None:
            health.raise_("missing_config", key="private_folder_id", detail=msg, interrupt=False)
        raise PrivateConfigError(msg)

    private_folders: set[str] = set()
    _collect_subtree_ids(folder_node, private_folders)

    # 2. Validate private_tag_id exists
    found_tag = any(str(t.get("id")) == private_tag_id for t in tags_data)
    if not found_tag:
        msg = f"Configured private_tag_id '{private_tag_id}' missing from Pocket /public/tags"
        if health is not None:
            health.raise_("missing_config", key="private_tag_id", detail=msg, interrupt=False)
        raise PrivateConfigError(msg)

    private_tags: set[str] = {private_tag_id}

    return private_folders, private_tags


def is_private_recording(
    recording: dict[str, Any],
    private_folder_ids: set[str],
    private_tag_ids: set[str],
) -> bool:
    """
    Check if a recording belongs to Private (folder subtree or tag by ID or name 'private' case-insensitive).
    """
    folder_id = recording.get("folder_id")
    if folder_id and str(folder_id) in private_folder_ids:
        return True

    tags = recording.get("tags") or []
    for tag in tags:
        if isinstance(tag, dict):
            tag_id = str(tag.get("id", ""))
            tag_name = str(tag.get("name", "")).strip().lower()
        else:
            tag_id = str(tag)
            tag_name = str(tag).strip().lower()

        if tag_id in private_tag_ids:
            return True
        if tag_name == "private":
            return True

    return False
