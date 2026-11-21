"""Drop folder scanner and archiver (§3.1, §3.5, Issue #10, Issue #18, Issue #23)."""
import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from articulate.core import paths
from articulate.core.pipeline import RunContext

logger = logging.getLogger(__name__)


@dataclass
class DropItem:
    course_key: str
    slot: str
    file_id: str
    display_name: str
    archive_path: Path
    is_new: bool = False
    is_changed: bool = False
    is_identical: bool = False


def _compute_sha256(path: Path) -> str:
    """Compute sha256 hash of file content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def process_drop_folder(course_key: str, ctx: RunContext) -> list[DropItem]:
    """
    Scan HUMAN/Drop/<course_key>/, copy new files into ARCHIVE by content hash,
    and detect slots (new, changed, or identical re-upload) (§3.5, SPEC 2.4).
    Files in Drop/ are never moved or deleted.
    """
    human = paths.get_human_dir()
    course_drop = human / "Drop" / course_key
    if not course_drop.exists() or not course_drop.is_dir():
        return []

    # Read existing slots from source_files
    existing_slots: dict[str, str] = {}
    with ctx.db.connect() as conn:
        rows = conn.execute(
            "SELECT slot, file_id FROM source_files WHERE course_key = ? AND origin = 'drop'",
            (course_key,),
        ).fetchall()
        for r in rows:
            existing_slots[r["slot"]] = r["file_id"]

    items: list[DropItem] = []

    for p in sorted(course_drop.rglob("*")):
        if p.is_dir() or p.name.startswith("."):
            continue

        rel_slot = str(p.relative_to(course_drop))
        sha256 = _compute_sha256(p)
        ext = p.suffix.lstrip(".") or "bin"

        # Archive path: DATA/archive/<course_key>/<sha256>.<ext>
        arch_path = paths.archive_path(course_key, sha256, ext)
        if not arch_path.exists():
            arch_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, arch_path)

        old_hash = existing_slots.get(rel_slot)
        is_new = old_hash is None
        is_identical = old_hash == sha256
        is_changed = old_hash is not None and old_hash != sha256

        items.append(
            DropItem(
                course_key=course_key,
                slot=rel_slot,
                file_id=sha256,
                display_name=p.name,
                archive_path=arch_path,
                is_new=is_new,
                is_changed=is_changed,
                is_identical=is_identical,
            )
        )

    return items
