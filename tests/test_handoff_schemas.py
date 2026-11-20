"""Tests for hand-off schemas and schema generation (Acceptance check 0.12)."""
import json
from pathlib import Path

import pytest

from articulate.core.handoff_schemas import (
    Attempt,
    Flag,
    Header,
    ImportBatch,
    Library,
    Plan,
    State,
    load_handoff_file,
    write_handoff_file,
    write_schemas,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "handoff"


def test_header_validation():
    h = Header(
        schema="encore.import",
        version=2,
        id="test-id",
        created_at="2026-11-20T12:00:00Z",
        producer="articulate 0.1.0",
    )
    assert h.schema_ == "encore.import"
    assert h.version == 2
    assert h.id == "test-id"


def test_valid_fixtures_round_trip():
    # 1. import.v2.json
    import_path = FIXTURES_DIR / "valid_import.json"
    batch = load_handoff_file(import_path, ImportBatch, expected_schema="encore.import", expected_version=2)
    assert batch.header.schema_ == "encore.import"
    assert batch.header.version == 2
    assert len(batch.courses) == 1
    assert batch.courses[0].id == "csc301"
    assert len(batch.courses[0].units[0].concepts) == 1
    assert batch.courses[0].units[0].concepts[0].id == "csc301.process"

    # 2. plan.v1.json
    plan_path = FIXTURES_DIR / "valid_plan.json"
    plan = load_handoff_file(plan_path, Plan, expected_schema="encore.plan", expected_version=1)
    assert plan.header.schema_ == "encore.plan"
    assert plan.header.version == 1
    assert plan.kind == "daily"
    assert len(plan.new) == 1
    assert plan.new[0].conceptId == "csc301.process"

    # 3. state.v1.json
    state_path = FIXTURES_DIR / "valid_state.json"
    state = load_handoff_file(state_path, State, expected_schema="encore.state", expected_version=1)
    assert state.header.schema_ == "encore.state"
    assert len(state.concepts) == 1
    assert state.concepts[0].conceptId == "csc301.process"

    # 4. library.v1.json
    lib_path = FIXTURES_DIR / "valid_library.json"
    lib = load_handoff_file(lib_path, Library, expected_schema="encore.library", expected_version=1)
    assert lib.header.schema_ == "encore.library"
    assert len(lib.courses) == 1
    assert lib.courses[0]["id"] == "csc301"

    # 5. flag.v1.json
    flag_path = FIXTURES_DIR / "valid_flag.json"
    flag = load_handoff_file(flag_path, Flag, expected_schema="encore.flag", expected_version=1)
    assert flag.header.schema_ == "encore.flag"
    assert flag.reason == "wrong"
    assert flag.conceptId == "csc301.process"

    # 6. attempts.v1.jsonl
    attempts_path = FIXTURES_DIR / "valid_attempts.jsonl"
    lines = attempts_path.read_text(encoding="utf-8").strip().splitlines()
    header_data = json.loads(lines[0])
    hdr = Header.model_validate(header_data)
    assert hdr.schema_ == "encore.attempt"
    assert hdr.version == 1
    attempt_data = json.loads(lines[1])
    att = Attempt.model_validate(attempt_data)
    assert att.promptId == "csc301.process.flashcard.1"
    assert att.grade == 3


def test_unknown_version_refused():
    unknown_path = FIXTURES_DIR / "unknown_version.json"
    with pytest.raises(ValueError, match="refused|unknown version|expected"):
        load_handoff_file(unknown_path, ImportBatch, expected_schema="encore.import", expected_version=2)


def test_extra_fields_ignored():
    extra_path = FIXTURES_DIR / "extra_fields.json"
    flag = load_handoff_file(extra_path, Flag, expected_schema="encore.flag", expected_version=1)
    assert flag.header.schema_ == "encore.flag"
    assert flag.reason == "unclear"


def test_write_handoff_file_atomic(tmp_path):
    target = tmp_path / "out" / "import-test.json"
    import_path = FIXTURES_DIR / "valid_import.json"
    batch = load_handoff_file(import_path, ImportBatch, expected_schema="encore.import", expected_version=2)

    write_handoff_file(target, batch)
    assert target.exists()

    # Re-read
    loaded = load_handoff_file(target, ImportBatch, expected_schema="encore.import", expected_version=2)
    assert loaded.header.id == batch.header.id


def test_write_schemas_creates_all_six(tmp_path):
    schemas_dir = tmp_path / "schemas"
    write_schemas(schemas_dir)

    expected = [
        "encore.import.v2.json",
        "encore.plan.v1.json",
        "encore.state.v1.json",
        "encore.library.v1.json",
        "encore.attempt.v1.json",
        "encore.flag.v1.json",
    ]
    for filename in expected:
        f = schemas_dir / filename
        assert f.exists(), f"Missing schema file: {filename}"
        schema_json = json.loads(f.read_text(encoding="utf-8"))
        assert "title" in schema_json or "$defs" in schema_json or "properties" in schema_json
