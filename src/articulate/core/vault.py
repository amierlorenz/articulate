"""Vault writer: create-only note creation, tombstoning, regenerate, and system files (§3.8)."""
import hashlib
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from articulate.core.atomic import write_atomic
from articulate.core.db import Database
from articulate.core.ids import new_ulid


@dataclass(frozen=True)
class NoteRef:
    articulate_id: str
    path: Path
    relative_path: str
    title: str


def _format_frontmatter(fm: dict[str, Any]) -> str:
    """Simple YAML frontmatter formatter."""
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        elif isinstance(v, list):
            items_str = ", ".join(f'"{x}"' if isinstance(x, str) and (" " in x or ":" in x) else str(x) for x in v)
            lines.append(f"{k}: [{items_str}]")
        elif v is None:
            lines.append(f"{k}: null")
        else:
            s_val = str(v)
            if any(ch in s_val for ch in [":", "#", "[", "]", "{", "}", "\n", '"', "'"]):
                escaped = s_val.replace('"', '\\"')
                lines.append(f'{k}: "{escaped}"')
            else:
                lines.append(f"{k}: {s_val}")
    lines.append("---\n")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Simple parser for YAML frontmatter."""
    fm: dict[str, Any] = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fm

    for line in lines[1:]:
        line = line.strip()
        if line == "---":
            break
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if v in ("true", "True"):
                fm[k] = True
            elif v in ("false", "False"):
                fm[k] = False
            elif v.startswith('"') and v.endswith('"'):
                fm[k] = v[1:-1].replace('\\"', '"')
            elif v.startswith("'") and v.endswith("'"):
                fm[k] = v[1:-1]
            elif v.startswith("[") and v.endswith("]"):
                inner = v[1:-1].strip()
                if not inner:
                    fm[k] = []
                else:
                    fm[k] = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
            else:
                fm[k] = v
    return fm


class Vault:
    """Manages Obsidian vault writes, frontmatter tracking, and system files (§3.8)."""

    def __init__(self, vault_dir: Path | str, db: Database | None = None):
        self.vault_dir = Path(vault_dir).resolve()
        self.db = db

    def create_note(
        self,
        folder: Literal["Inbox", "Literature Notes", "People", "_system"],
        title: str,
        frontmatter: dict[str, Any],
        body: str,
        *,
        kind: str,
        source_item_id: str | None = None,
        private: bool = False,
    ) -> NoteRef | None:
        """
        Create-only: never overwrites. Title clash gets ' (2)', etc.
        Skips (returns None) when articulate_id or source_item_id is tombstoned (§3.8).
        """
        art_id = str(frontmatter.get("articulate_id") or new_ulid())

        if self.db is not None:
            with self.db.connect() as conn:
                # Check tombstones
                keys = [art_id]
                if source_item_id:
                    keys.append(source_item_id)
                placeholders = ",".join("?" for _ in keys)
                tomb = conn.execute(
                    f"SELECT 1 FROM tombstones WHERE key IN ({placeholders});",
                    keys,
                ).fetchone()
                if tomb is not None:
                    return None

        fm = dict(frontmatter)
        fm["agent"] = True
        fm["articulate_id"] = art_id
        if private:
            fm["private"] = True

        # Resolve path and handle title clashes
        target_dir = self.vault_dir / folder
        target_dir.mkdir(parents=True, exist_ok=True)

        effective_title = title
        candidate_path = target_dir / f"{effective_title}.md"
        counter = 2
        while candidate_path.exists():
            effective_title = f"{title} ({counter})"
            candidate_path = target_dir / f"{effective_title}.md"
            counter += 1

        full_content = _format_frontmatter(fm) + body
        content_bytes = full_content.encode("utf-8")
        write_atomic(candidate_path, content_bytes)

        written_hash = hashlib.sha256(content_bytes).hexdigest()
        rel_path = f"{folder}/{candidate_path.name}"
        now_str = datetime.now(UTC).isoformat()

        if self.db is not None:
            with self.db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO vault_notes (
                        articulate_id, path, kind, written_hash, touched,
                        private, source_item_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?);
                    """,
                    (
                        art_id,
                        rel_path,
                        kind,
                        written_hash,
                        1 if private else 0,
                        source_item_id,
                        now_str,
                        now_str,
                    ),
                )
                conn.commit()

        return NoteRef(
            articulate_id=art_id,
            path=candidate_path,
            relative_path=rel_path,
            title=effective_title,
        )

    def regenerate(self, articulate_id: str, frontmatter: dict[str, Any], body: str) -> bool:
        """
        Regenerate a note only while untouched (file hash == written_hash and path unchanged).
        Returns False if edited, moved, or missing (§3.8).
        """
        if self.db is None:
            return False

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM vault_notes WHERE articulate_id = ?;",
                (articulate_id,),
            ).fetchone()

        if row is None or row["touched"] == 1:
            return False

        note_path = self.vault_dir / row["path"]
        if not note_path.exists():
            return False

        # Check existing hash
        current_hash = hashlib.sha256(note_path.read_bytes()).hexdigest()
        if current_hash != row["written_hash"]:
            return False

        fm = dict(frontmatter)
        fm["agent"] = True
        fm["articulate_id"] = articulate_id
        if row["private"] == 1:
            fm["private"] = True

        full_content = _format_frontmatter(fm) + body
        content_bytes = full_content.encode("utf-8")
        write_atomic(note_path, content_bytes)

        new_hash = hashlib.sha256(content_bytes).hexdigest()
        now_str = datetime.now(UTC).isoformat()

        with self.db.connect() as conn:
            conn.execute(
                "UPDATE vault_notes SET written_hash = ?, updated_at = ? WHERE articulate_id = ?;",
                (new_hash, now_str, articulate_id),
            )
            conn.commit()

        return True

    def write_system(
        self,
        name: Literal["Runs.md", "Dashboard.md", "Intake flags.md", str],
        text: str,
    ) -> bool:
        """
        Whole-file rewrite of Articulate-owned system files.
        Only rewrites when content hash changed (§3.8).
        Returns True if written, False if skipped.
        """
        if name in ("Intake flags.md", "Inbox/Intake flags.md") or name.startswith("Inbox/"):
            clean_name = Path(name).name
            target = self.vault_dir / "Inbox" / clean_name
        elif name.startswith("_system/"):
            clean_name = Path(name).name
            target = self.vault_dir / "_system" / clean_name
        else:
            target = self.vault_dir / "_system" / name

        target.parent.mkdir(parents=True, exist_ok=True)
        new_bytes = text.encode("utf-8")
        new_hash = hashlib.sha256(new_bytes).hexdigest()

        if target.exists():
            existing_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing_hash == new_hash:
                return False

        write_atomic(target, new_bytes)
        return True

    def append_system(self, name: Literal["Runs.md", str], line: str) -> None:
        """Append a single line to an Articulate-owned system file (§3.8, §3.11)."""
        target = self.vault_dir / "_system" / Path(name).name
        target.parent.mkdir(parents=True, exist_ok=True)

        entry = line if line.endswith("\n") else line + "\n"
        with open(target, "a", encoding="utf-8") as f:
            f.write(entry)

    def scan(self) -> None:
        """
        Re-finds moved notes by articulate_id in frontmatter.
        Missing -> tombstone (deleted = tombstone).
        Moved or edited -> touched = 1 (§3.8).
        """
        if self.db is None or not self.vault_dir.exists():
            return

        # Find all .md files in the vault
        found_notes: dict[str, tuple[str, str]] = {}
        for p in self.vault_dir.rglob("*.md"):
            if p.name.startswith("."):
                continue
            # Skip hidden or system folders like .obsidian
            if any(part.startswith(".") for part in p.relative_to(self.vault_dir).parts):
                continue

            try:
                content = p.read_text(encoding="utf-8")
                fm = _parse_frontmatter(content)
                art_id = fm.get("articulate_id")
                if art_id:
                    rel_p = str(p.relative_to(self.vault_dir))
                    chash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    found_notes[str(art_id)] = (rel_p, chash)
            except Exception:  # noqa: BLE001, S112
                continue

        now_str = datetime.now(UTC).isoformat()

        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM vault_notes;").fetchall()

            for row in rows:
                art_id = row["articulate_id"]
                if art_id not in found_notes:
                    # Missing note -> create tombstone and delete row
                    conn.execute(
                        "INSERT OR IGNORE INTO tombstones (kind, key, reason, created_at) VALUES ('articulate_id', ?, 'deleted in vault', ?);",
                        (art_id, now_str),
                    )
                    if row["source_item_id"]:
                        conn.execute(
                            "INSERT OR IGNORE INTO tombstones (kind, key, reason, created_at) VALUES ('source_item_id', ?, 'deleted in vault', ?);",
                            (row["source_item_id"], now_str),
                        )
                    conn.execute("DELETE FROM vault_notes WHERE articulate_id = ?;", (art_id,))
                else:
                    found_path, found_hash = found_notes[art_id]
                    is_moved = found_path != row["path"]
                    is_edited = found_hash != row["written_hash"]

                    if is_moved or is_edited:
                        conn.execute(
                            "UPDATE vault_notes SET path = ?, touched = 1, updated_at = ? WHERE articulate_id = ?;",
                            (found_path, now_str, art_id),
                        )

            conn.commit()

    def obsidian_uri(self, ref: NoteRef) -> str:
        """Construct obsidian://open URI for a given note ref (§3.8)."""
        vault_name = urllib.parse.quote(self.vault_dir.name)
        file_path = urllib.parse.quote(ref.relative_path.removesuffix(".md"))
        return f"obsidian://open?vault={vault_name}&file={file_path}"
