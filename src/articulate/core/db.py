"""SQLite database and migration runner for Articulate (§3.4)."""
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from articulate.core.paths import get_db_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    package: str
    name: str
    path: Path


def discover_migrations(root_dir: Path | None = None, search_dirs: list[Path] | None = None) -> list[Migration]:
    """
    Discover all migration files across core and slice packages (§3.4).
    Each slice keeps its migrations in src/articulate/<slice>/migrations/NNN_<name>.sql.
    Core migrations run first, followed by slice migrations sorted by package and filename.
    """
    migrations: list[Migration] = []

    dirs_to_search: list[Path] = []
    if search_dirs:
        dirs_to_search.extend(search_dirs)
    if root_dir:
        dirs_to_search.append(root_dir)
    if not dirs_to_search:
        # Default to articulate source root
        pkg_root = Path(__file__).resolve().parent.parent
        dirs_to_search.append(pkg_root)

    seen_files: set[str] = set()

    for base_dir in dirs_to_search:
        if not base_dir.exists():
            continue
        # Search for any folder named 'migrations'
        for mig_dir in base_dir.rglob("migrations"):
            if not mig_dir.is_dir():
                continue
            # Package name is parent folder name
            pkg = mig_dir.parent.name
            for sql_file in sorted(mig_dir.glob("*.sql")):
                rel_key = f"{pkg}:{sql_file.name}"
                if rel_key not in seen_files:
                    seen_files.add(rel_key)
                    migrations.append(Migration(package=pkg, name=sql_file.name, path=sql_file))

    # Sort migrations: 'core' first, then package, then filename
    def sort_key(m: Migration) -> tuple[int, str, str]:
        is_core = 0 if m.package == "core" else 1
        return (is_core, m.package, m.name)

    return sorted(migrations, key=sort_key)


class Database:
    """Manages SQLite connections, pragmas, and migrations."""

    def __init__(self, path: Path | str | None = None, search_dirs: list[Path] | None = None):
        self.path = Path(path).resolve() if path is not None else get_db_path()
        self.search_dirs = search_dirs

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Context manager for SQLite connection with WAL and foreign keys."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path),
            timeout=5.0,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            yield conn
        finally:
            conn.close()

    def migrate(self) -> list[tuple[str, str]]:
        """
        Apply pending migrations from core and discovered slices.
        Returns a list of applied (package, name) tuples.
        """
        applied: list[tuple[str, str]] = []

        with self.connect() as conn:
            # Ensure schema_migrations table exists
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    package    TEXT NOT NULL,
                    name       TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    PRIMARY KEY (package, name)
                );
                """
            )
            conn.commit()

            # Read already applied migrations
            cursor = conn.cursor()
            cursor.execute("SELECT package, name FROM schema_migrations;")
            already_applied = {(row["package"], row["name"]) for row in cursor.fetchall()}

            migrations = discover_migrations(search_dirs=self.search_dirs)

            for mig in migrations:
                if (mig.package, mig.name) in already_applied:
                    continue

                logger.info("Applying migration %s/%s from %s", mig.package, mig.name, mig.path)
                sql_content = mig.path.read_text(encoding="utf-8")
                now_str = datetime.now(UTC).isoformat()

                with conn:
                    conn.executescript(sql_content)
                    conn.execute(
                        "INSERT INTO schema_migrations (package, name, applied_at) VALUES (?, ?, ?);",
                        (mig.package, mig.name, now_str),
                    )

                applied.append((mig.package, mig.name))

        return applied
