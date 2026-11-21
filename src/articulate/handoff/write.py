"""Handoff file writing and schema validation (§3.7, Acceptance check 2.35)."""

import json
from pathlib import Path
from typing import Any

import jsonschema

from articulate.core.handoff_schemas import ImportBatch, write_handoff_file
from articulate.core.paths import to_encore_dir


def find_schemas_dir() -> Path:
    """Find the project's schemas directory containing JSON Schema files."""
    curr = Path(__file__).resolve().parent
    for p in [curr, *curr.parents]:
        s = p / "schemas"
        if s.is_dir() and (s / "encore.import.v2.json").exists():
            return s
    # Fallback to repo root schemas
    for p in [curr, *curr.parents]:
        if (p / "pyproject.toml").exists():
            s = p / "schemas"
            s.mkdir(parents=True, exist_ok=True)
            return s
    return curr / "schemas"


def validate_against_schema(data: dict[str, Any], schema_filename: str) -> None:
    """Validate a dictionary against the named JSON schema in schemas/."""
    schemas_dir = find_schemas_dir()
    schema_file = schemas_dir / schema_filename
    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")

    schema_dict = json.loads(schema_file.read_text(encoding="utf-8"))
    jsonschema.validate(instance=data, schema=schema_dict)


def validate_import_batch(batch: ImportBatch) -> None:
    """Validate an ImportBatch against schemas/encore.import.v2.json."""
    raw = json.loads(batch.model_dump_json(by_alias=True))
    validate_against_schema(raw, "encore.import.v2.json")


def write_import_batch(batch: ImportBatch, dest_dir: Path | None = None) -> Path:
    """
    Validate and write an ImportBatch to to-encore/import-<batchId>.json.
    Writes atomically via tmp + rename (§3.7).
    """
    validate_import_batch(batch)

    out_dir = Path(dest_dir).resolve() if dest_dir is not None else to_encore_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    batch_id = batch.header.id
    if not batch_id.startswith("import-"):
        filename = f"import-{batch_id}.json"
    else:
        filename = f"{batch_id}.json"

    target_path = out_dir / filename
    write_handoff_file(target_path, batch)
    return target_path
