"""Path definitions and resolution for Articulate (§3.1)."""
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from articulate.core.config import Config


def get_data_dir() -> Path:
    """DATA: ~/Library/Application Support/articulate/"""
    env = os.environ.get("ARTICULATE_DATA")
    if env:
        return Path(env).resolve()
    return Path("~/Library/Application Support/articulate").expanduser().resolve()


def get_human_dir() -> Path:
    """HUMAN: ~/Library/Mobile Documents/com~apple~CloudDocs/Articulate/"""
    env = os.environ.get("ARTICULATE_HUMAN")
    if env:
        return Path(env).resolve()
    return Path("~/Library/Mobile Documents/com~apple~CloudDocs/Articulate").expanduser().resolve()


def get_handoff_dir() -> Path:
    """HANDOFF: ~/Library/Application Support/Encore/Handoff/"""
    env = os.environ.get("ARTICULATE_HANDOFF")
    if env:
        return Path(env).resolve()
    return Path("~/Library/Application Support/Encore/Handoff").expanduser().resolve()


def get_db_path() -> Path:
    """DB: DATA/articulate.sqlite3"""
    return get_data_dir() / "articulate.sqlite3"


# Dynamic path helpers
def transcript_path(recording_id: str, year: str | int = "", month: str | int = "") -> Path:
    """TRANSCRIPTS: DATA/transcripts/YYYY/MM/<recording_id>.json.gz"""
    y_str = str(year).zfill(4) if year else "unknown"
    m_str = str(month).zfill(2) if month else "unknown"
    return get_data_dir() / "transcripts" / y_str / m_str / f"{recording_id}.json.gz"


def archive_path(course_key: str, sha256: str, ext: str) -> Path:
    """ARCHIVE: DATA/archive/<course_key>/<sha256>.<ext>"""
    clean_ext = ext.lstrip(".")
    return get_data_dir() / "archive" / course_key / f"{sha256}.{clean_ext}"


def archive_pages_path(course_key: str, sha256: str) -> Path:
    """DATA/archive/<course_key>/<sha256>.pages.json"""
    return get_data_dir() / "archive" / course_key / f"{sha256}.pages.json"


def work_dir(run_id: str) -> Path:
    """WORK: DATA/work/<run_id>/"""
    return get_data_dir() / "work" / run_id


def cache_ocr_path(page_hash: str) -> Path:
    """CACHE: DATA/cache/ocr/<page_hash>.json"""
    return get_data_dir() / "cache" / "ocr" / f"{page_hash}.json"


def cache_vl_path(page_hash: str) -> Path:
    """CACHE: DATA/cache/vl/<page_hash>.json"""
    return get_data_dir() / "cache" / "vl" / f"{page_hash}.json"


def cache_embeddings_dir() -> Path:
    """CACHE: DATA/cache/embeddings/"""
    return get_data_dir() / "cache" / "embeddings"


def private_evals_dir() -> Path:
    """PRIVATE_EVALS: DATA/evals/"""
    return get_data_dir() / "evals"


def real_fixtures_dir() -> Path:
    """REAL_FIXTURES: DATA/fixtures/real/"""
    return get_data_dir() / "fixtures" / "real"


def log_path(job: str) -> Path:
    """LOGS: DATA/logs/<job>.log"""
    return get_data_dir() / "logs" / f"{job}.log"


def run_lock_path() -> Path:
    """DATA/run.lock"""
    return get_data_dir() / "run.lock"


def courses_toml_path() -> Path:
    """HUMAN/courses.toml"""
    return get_human_dir() / "courses.toml"


def models_toml_path() -> Path:
    """HUMAN/models.toml"""
    return get_human_dir() / "models.toml"


def drop_dir(course_key: str) -> Path:
    """HUMAN/Drop/<course_key>/"""
    return get_human_dir() / "Drop" / course_key


def to_encore_dir() -> Path:
    """HANDOFF/to-encore/"""
    return get_handoff_dir() / "to-encore"


def to_encore_failed_dir() -> Path:
    """HANDOFF/to-encore/failed/"""
    return get_handoff_dir() / "to-encore" / "failed"


def from_encore_dir() -> Path:
    """HANDOFF/from-encore/"""
    return get_handoff_dir() / "from-encore"


def articulate_toml_path() -> Path:
    """DATA/articulate.toml"""
    return get_data_dir() / "articulate.toml"


def vault_path(cfg: "Config | None" = None) -> Path:
    """VAULT from articulate.toml vault.path"""
    if cfg is not None and cfg.articulate and cfg.articulate.vault:
        return Path(cfg.articulate.vault.path).expanduser().resolve()
    # Default fallback if config not yet loaded
    return Path("~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Vault").expanduser().resolve()
