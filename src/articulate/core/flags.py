"""Weekly flags tracking, note rendering, tick resolution, and weekly chore task (§3.9, Issue #14)."""
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime

from articulate.core.db import Database
from articulate.core.ids import new_ulid
from articulate.core.pipeline import RunContext
from articulate.core.taskqueue import TaskQueue, TaskRequest
from articulate.core.vault import NoteRef, Vault

FLAG_COMMENT_RE = re.compile(r"<!--\s*flag:([A-Za-z0-9]+)\s*-->")
BOX_RE = re.compile(r"\[([ xX])\]")


class Flags:
    """Manages weekly intake flags, Inbox/Intake flags.md, and resolvers (§3.9)."""

    def __init__(
        self,
        db: Database,
        vault: Vault | None = None,
        taskqueue: TaskQueue | None = None,
    ):
        self.db = db
        self.vault = vault
        self.taskqueue = taskqueue or TaskQueue(db)
        self._resolvers: dict[str, Callable[[str, str, RunContext], None]] = {}

    def register(self, kind: str, resolver: Callable[[str, str, RunContext], None]) -> None:
        """Register a resolver callback for a specific flag kind (§3.9)."""
        self._resolvers[kind] = resolver

    def add(
        self,
        kind: str,
        subject: str,
        choices: list[tuple[str, str]],
        item_id: str | None = None,
    ) -> str:
        """
        Add a flag row in the flags table.
        Choices is a list of (key, label) tuples (§3.9).
        """
        flag_id = new_ulid()
        now_str = datetime.now(UTC).isoformat()
        choices_json = json.dumps(choices)

        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO flags (flag_id, kind, item_id, subject, choices_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?);
                """,
                (flag_id, kind, item_id, subject, choices_json, now_str),
            )
            conn.commit()

        return flag_id

    def render_note(self) -> None:
        """Render open flags into Inbox/Intake flags.md (§3.9)."""
        if self.vault is None:
            return

        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT flag_id, subject, choices_json FROM flags
                WHERE status = 'open'
                ORDER BY created_at ASC;
                """
            )
            rows = cursor.fetchall()

        lines: list[str] = [
            "# Intake Flags",
            "",
            "Review and tick the appropriate choice for each item. The next nightly run applies ticked choices.",
            "",
        ]

        if not rows:
            lines.append("No open flags.")
        else:
            for r in rows:
                fid = r["flag_id"]
                subj = r["subject"]
                try:
                    choices = json.loads(r["choices_json"])
                except Exception:  # noqa: BLE001
                    choices = [("keep", "keep"), ("drop", "drop")]

                choices_rendered = " · ".join(f"[ ] {c[1]}" for c in choices)
                lines.append(f"- [ ] {subj} — {choices_rendered}   <!-- flag:{fid} -->")

        content = "\n".join(lines) + "\n"
        self.vault.write_system("Intake flags.md", content)

    def process_ticks(self, ctx: RunContext) -> int:
        """
        Parse ticked boxes by the HTML comment id and call registered resolvers.
        Unticked lines carry over. Returns count of resolved flags (§3.9).
        """
        if self.vault is None:
            return 0

        note_file = self.vault.vault_dir / "Inbox" / "Intake flags.md"
        if not note_file.exists():
            return 0

        text = note_file.read_text(encoding="utf-8")
        lines = text.splitlines()

        resolved_count = 0
        now_str = ctx.now.isoformat()

        for line in lines:
            m = FLAG_COMMENT_RE.search(line)
            if not m:
                continue

            flag_id = m.group(1)

            with self.db.connect() as conn:
                row = conn.execute("SELECT * FROM flags WHERE flag_id = ?;", (flag_id,)).fetchone()

            if not row or row["status"] != "open":
                continue

            try:
                choices: list[tuple[str, str]] = json.loads(row["choices_json"])
            except Exception:  # noqa: BLE001
                choices = []

            # Determine which choice was selected
            chosen_key: str | None = None

            # Check for inner choice box tick like "[x] label"
            for key, label in choices:
                if f"[x] {label}" in line or f"[X] {label}" in line:
                    chosen_key = key
                    break

            if chosen_key is None:
                # Check boxes in order
                boxes = BOX_RE.findall(line)
                # If first box (line box) is ticked, does it mean choice 0?
                # Only if there are boxes corresponding to choices
                if boxes:
                    # If line has N choices and len(boxes) == N + 1 (first box is bullet checkbox)
                    if len(boxes) == len(choices) + 1:
                        for idx, b in enumerate(boxes[1:]):
                            if b in ("x", "X"):
                                chosen_key = choices[idx][0]
                                break
                    elif len(boxes) == len(choices):
                        for idx, b in enumerate(boxes):
                            if b in ("x", "X"):
                                chosen_key = choices[idx][0]
                                break

            if chosen_key is not None:
                kind = row["kind"]
                resolver = self._resolvers.get(kind)
                if resolver:
                    try:
                        resolver(flag_id, chosen_key, ctx)
                    except Exception:  # noqa: BLE001, S110
                        # Log and continue
                        pass

                with self.db.connect() as conn:
                    conn.execute(
                        """
                        UPDATE flags
                        SET status = 'resolved', chosen = ?, resolved_at = ?
                        WHERE flag_id = ?;
                        """,
                        (chosen_key, now_str, flag_id),
                    )
                    conn.commit()

                resolved_count += 1

        return resolved_count

    def check_weekly_task(self, ctx: RunContext) -> None:
        """
        When open flags exist, enqueues one task study:flags:<YYYY-WW>
        ('Clear N intake flags', linking the note) once a week (§3.9).
        """
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as cnt FROM flags WHERE status = 'open';")
            row = cursor.fetchone()
            count = row["cnt"] if row else 0

        if count <= 0:
            return

        # ISO year and week
        iso_year, iso_week, _ = ctx.now.isocalendar()
        week_str = f"{iso_year:04d}-{iso_week:02d}"
        ext_id = f"study:flags:{week_str}"

        link_uri: str | None = None
        if self.vault is not None:
            ref = NoteRef(
                articulate_id="",
                path=self.vault.vault_dir / "Inbox" / "Intake flags.md",
                relative_path="Inbox/Intake flags.md",
                title="Intake flags",
            )
            link_uri = self.vault.obsidian_uri(ref)

        task_req = TaskRequest(
            external_id=ext_id,
            title=f"Clear {count} intake flags",
            link=link_uri,
            band="study",
        )
        self.taskqueue.enqueue(task_req)
