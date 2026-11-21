"""Configuration loader and Pydantic models for Articulate (§3.2)."""
import logging
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from articulate.core.paths import articulate_toml_path, courses_toml_path, models_toml_path

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when a config file is missing or invalid (§3.2). Names file and key."""

    def __init__(self, file_name: str, key: str | None, message: str):
        self.file_name = file_name
        self.key = key
        self.message = message
        full_msg = f"Config error in '{file_name}'" + (f" at key '{key}'" if key else "") + f": {message}"
        super().__init__(full_msg)


def _record_health_error(db: Any, kind: str, file_name: str, detail: str) -> None:
    """Helper to record health row when config fails (§3.2, §3.10)."""
    if db is None:
        return
    try:
        now_str = datetime.now(UTC).isoformat()
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO health (kind, key, detail, interrupt, first_seen, last_seen)
                VALUES (?, ?, ?, 0, ?, ?);
                """,
                (kind, file_name, detail, now_str, now_str),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not write health row for config error: %s", e)


def jobs_needing_file(file_name: str) -> set[str]:
    """Return which jobs need a given config file (§3.2)."""
    base = Path(file_name).name
    if base == "courses.toml":
        # Needed by class intake (nightly, pass2)
        return {"nightly", "pass2"}
    if base in ("models.toml", "articulate.toml"):
        return {"nightly", "pass2", "watch"}
    return {"nightly", "pass2", "watch"}


# --- Pydantic Models for courses.toml ---


class ExamConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    name: str
    kind: Literal["test", "final"] = "final"
    date: str | None = None
    units: list[str] = Field(default_factory=list)


class CourseConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    canvas_id: int | None = None
    name: str
    skip: list[str] | None = None
    instructor: str = ""
    active: bool = True
    exams: list[ExamConfig] = Field(default_factory=list)


class CoursesConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    default_skip: list[str] = Field(
        default_factory=lambda: ["Syllabus", "Start Here*", "Course Information*"]
    )
    courses: dict[str, CourseConfig] = Field(default_factory=dict)


# --- Pydantic Models for models.toml ---


class GeminiModelConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rpm: int
    rpd: int
    reserve: int = 0


class GeminiConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keychain_service: str = "articulate-gemini"
    timeout_s: int = 600
    long_timeout_s: int = 1200
    models: dict[str, GeminiModelConfig] = Field(default_factory=dict)


class ClaudeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = "sonnet"
    weekly_ceiling_pct: float = 50.0
    session_ceiling_pct: float = 70.0
    max_calls_per_night: int = 15
    usage_timeout_s: int = 30


class OllamaConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = "http://127.0.0.1:11434"
    text_model: str = "qwen3:8b"
    embed_model: str = "embeddinggemma"
    vl_model: str = "qwen3-vl:8b-instruct"


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    chains: dict[str, list[str]] = Field(default_factory=dict)


# --- Pydantic Models for articulate.toml ---


class VaultConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str
    name: str = ""


class PocketConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_base: str = "https://public.heypocketai.com"
    mcp_url: str = "https://public.heypocketai.com/mcp"
    private_folder_id: str = "5194b8b6-ec40-4d99-91a9-059fb610141e"
    private_tag_id: str = "7dbebf3f-418b-4ea1-936d-61c67089d829"
    lookback_days: int = 3
    give_up_pending_days: int = 3


class CalendarConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    classes_calendar: str = "Classes"
    private_events: list[str] = Field(default_factory=lambda: ["Therapy"])
    private_calendars: list[str] = Field(default_factory=list)


class CanvasConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str = ""


class EncoreConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bundle_id: str = "com.amierlorenz.Encore"
    state_wait_s: int = 180


class ArticulateConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vault: VaultConfig
    pocket: PocketConfig = Field(default_factory=PocketConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    canvas: CanvasConfig = Field(default_factory=CanvasConfig)
    encore: EncoreConfig = Field(default_factory=EncoreConfig)


class Config(BaseModel):
    """Unified container for all three configuration sources."""

    model_config = ConfigDict(extra="ignore")

    courses: CoursesConfig | None = None
    models: ModelsConfig | None = None
    articulate: ArticulateConfig | None = None


# --- Loader Functions ---


def _load_toml(path: Path, db: Any = None) -> dict[str, Any]:
    file_name = path.name
    if not path.exists():
        msg = f"File not found: {path}"
        _record_health_error(db, "missing_config", file_name, msg)
        raise ConfigError(file_name=file_name, key=None, message=msg)

    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        msg = f"Could not parse TOML: {e}"
        _record_health_error(db, "invalid_config", file_name, msg)
        raise ConfigError(file_name=file_name, key=None, message=msg) from e


def _handle_validation_error(e: ValidationError, file_name: str, db: Any = None) -> None:
    first_err = e.errors()[0]
    loc = ".".join(str(p) for p in first_err["loc"])
    msg = first_err["msg"]
    _record_health_error(db, "invalid_config", file_name, f"Key '{loc}': {msg}")
    raise ConfigError(file_name=file_name, key=loc, message=msg) from e


def load_courses_config(path: Path | None = None, db: Any = None) -> CoursesConfig:
    target = Path(path).resolve() if path else courses_toml_path()
    raw = _load_toml(target, db)
    try:
        return CoursesConfig.model_validate(raw)
    except ValidationError as e:
        _handle_validation_error(e, target.name, db)


def load_models_config(path: Path | None = None, db: Any = None) -> ModelsConfig:
    target = Path(path).resolve() if path else models_toml_path()
    raw = _load_toml(target, db)
    try:
        return ModelsConfig.model_validate(raw)
    except ValidationError as e:
        _handle_validation_error(e, target.name, db)


def load_articulate_config(path: Path | None = None, db: Any = None) -> ArticulateConfig:
    target = Path(path).resolve() if path else articulate_toml_path()
    raw = _load_toml(target, db)
    try:
        return ArticulateConfig.model_validate(raw)
    except ValidationError as e:
        _handle_validation_error(e, target.name, db)


def load_config(
    courses_path: Path | None = None,
    models_path: Path | None = None,
    articulate_path: Path | None = None,
    db: Any = None,
) -> Config:
    """Load all configs into a single Config instance."""
    courses = load_courses_config(courses_path, db) if (courses_path or courses_toml_path().exists()) else None
    models = load_models_config(models_path, db) if (models_path or models_toml_path().exists()) else None
    articulate = (
        load_articulate_config(articulate_path, db)
        if (articulate_path or articulate_toml_path().exists())
        else None
    )

    return Config(courses=courses, models=models, articulate=articulate)
