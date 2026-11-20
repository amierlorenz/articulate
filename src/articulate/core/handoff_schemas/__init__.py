"""Handoff schemas package: models, loaders, writers, and schema generator (§3.7)."""
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from articulate.core.atomic import write_atomic
from articulate.core.handoff_schemas.attempt import Attempt, AttemptBatch
from articulate.core.handoff_schemas.flag import Flag
from articulate.core.handoff_schemas.header import Header
from articulate.core.handoff_schemas.imports import (
    Citation,
    Claim,
    Concept,
    ConceptDetails,
    ContrastGroup,
    ContrastPair,
    Course,
    Exam,
    ImportBatch,
    Prompt,
    SourceFile,
    Unit,
)
from articulate.core.handoff_schemas.library import Library
from articulate.core.handoff_schemas.plan import ExamFocus, Plan, PlanNew
from articulate.core.handoff_schemas.state import State, StateConcept, StatePrompt

__all__ = [
    "Attempt",
    "AttemptBatch",
    "Citation",
    "Claim",
    "Concept",
    "ConceptDetails",
    "ContrastGroup",
    "ContrastPair",
    "Course",
    "Exam",
    "ExamFocus",
    "Flag",
    "Header",
    "ImportBatch",
    "Library",
    "Plan",
    "PlanNew",
    "Prompt",
    "SourceFile",
    "State",
    "StateConcept",
    "StatePrompt",
    "Unit",
    "load_handoff_file",
    "write_handoff_file",
    "write_schemas",
]

T = TypeVar("T", bound=BaseModel)

SCHEMA_VERSIONS: dict[str, int] = {
    "encore.import": 2,
    "encore.plan": 1,
    "encore.state": 1,
    "encore.library": 1,
    "encore.attempt": 1,
    "encore.flag": 1,
}


def load_handoff_file[T: BaseModel](
    path: Path | str,
    model_cls: type[T],
    expected_schema: str | None = None,
    expected_version: int | None = None,
) -> T:
    """
    Read and parse a hand-off JSON file.
    Validates header schema and version. If version is unknown or does not match
    expected_version, raises ValueError (§3.7).
    """
    p = Path(path).resolve()
    raw_text = p.read_text(encoding="utf-8")
    data = json.loads(raw_text)

    # Validate header first
    header_raw = data.get("header") if isinstance(data, dict) else None
    if not isinstance(header_raw, dict):
        # Could be top-level header or line
        header_raw = data

    header = Header.model_validate(header_raw)

    if expected_schema and header.schema_ != expected_schema:
        raise ValueError(f"Schema mismatch in {p.name}: expected {expected_schema}, got {header.schema_}")

    expected_ver = expected_version if expected_version is not None else SCHEMA_VERSIONS.get(header.schema_)
    if expected_ver is not None and header.version != expected_ver:
        raise ValueError(
            f"Unknown version in {p.name}: schema {header.schema_} version {header.version} refused (expected {expected_ver})"
        )

    return model_cls.model_validate(data)


def write_handoff_file(path: Path | str, model: BaseModel) -> None:
    """Write hand-off file atomically via tmp + rename (§3.7)."""
    p = Path(path).resolve()
    json_bytes = model.model_dump_json(by_alias=True, indent=2).encode("utf-8")
    write_atomic(p, json_bytes)


def write_schemas(target_dir: Path | str | None = None) -> list[Path]:
    """
    Generate JSON Schema files for all six hand-off schemas (§3.7).
    Writes schemas/<schema>.v<N>.json.
    """
    if target_dir is None:
        # Default: repo_root / schemas (find directory containing pyproject.toml)
        curr = Path(__file__).resolve().parent
        repo_root = curr
        for p in [curr, *curr.parents]:
            if (p / "pyproject.toml").exists():
                repo_root = p
                break
        out_dir = repo_root / "schemas"
    else:
        out_dir = Path(target_dir).resolve()

    out_dir.mkdir(parents=True, exist_ok=True)

    targets: list[tuple[str, type[BaseModel]]] = [
        ("encore.import.v2.json", ImportBatch),
        ("encore.plan.v1.json", Plan),
        ("encore.state.v1.json", State),
        ("encore.library.v1.json", Library),
        ("encore.attempt.v1.json", Attempt),
        ("encore.flag.v1.json", Flag),
    ]

    written_paths: list[Path] = []
    for filename, model in targets:
        schema_dict = model.model_json_schema()
        schema_json = json.dumps(schema_dict, indent=2) + "\n"
        dest = out_dir / filename
        write_atomic(dest, schema_json.encode("utf-8"))
        written_paths.append(dest)

    return written_paths
