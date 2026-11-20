"""Tests for fixtures (§4, Acceptance check 0.18)."""
import hashlib
import tomllib
from pathlib import Path

import pytest

from articulate.core.paths import real_fixtures_dir


def get_fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


def test_committed_fixtures_folders_exist():
    """Verify all folders in SPEC-implementation §4 exist in tests/fixtures/."""
    fix_dir = get_fixtures_dir()
    required_folders = [
        "pocket",
        "calendar",
        "canvas",
        "gemini",
        "claude",
        "deckcraft",
        "handoff",
        "pdf",
        "gold",
    ]
    for folder in required_folders:
        p = fix_dir / folder
        assert p.exists() and p.is_dir(), f"Missing fixture folder: tests/fixtures/{folder}"


def test_real_manifest_valid():
    """Verify tests/fixtures/real-manifest.toml exists and conforms to schema."""
    manifest_path = get_fixtures_dir() / "real-manifest.toml"
    assert manifest_path.exists(), "tests/fixtures/real-manifest.toml does not exist"

    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)

    assert "fixtures" in data
    fixtures = data["fixtures"]
    assert len(fixtures) > 0

    filenames = set()
    for item in fixtures:
        assert "filename" in item
        assert "sha256" in item
        assert len(item["sha256"]) == 64
        assert "source" in item
        assert "tests" in item
        assert "expected" in item
        filenames.add(item["filename"])

    # Ensure key expected items are represented
    expected_seeds = [
        "chapter25_4e.pptx",
        "Cryptography.pptx",
        "ch10.pptx",
        "csc382-whitman-ch03.pdf",
        "phy203-ch09.pdf",
    ]
    for seed in expected_seeds:
        assert any(seed in fn for fn in filenames), f"Expected seed {seed} not in manifest"


@pytest.mark.real
def test_real_fixtures_present_report():
    """Acceptance check 0.18: pytest -m real reports which files are present and verifies sha256."""
    manifest_path = get_fixtures_dir() / "real-manifest.toml"
    assert manifest_path.exists()

    with open(manifest_path, "rb") as f:
        manifest = tomllib.load(f)

    dest_dir = real_fixtures_dir()
    present = []
    missing = []

    for item in manifest.get("fixtures", []):
        fn = item["filename"]
        target = dest_dir / fn
        if target.exists():
            data = target.read_bytes()
            actual_sha = hashlib.sha256(data).hexdigest()
            expected_sha = item["sha256"]
            assert actual_sha == expected_sha, f"sha256 mismatch for {fn}: expected {expected_sha}, got {actual_sha}"
            present.append((fn, actual_sha))
        else:
            missing.append((fn, item["sha256"]))

    print(f"\nReal fixtures report ({dest_dir}):")
    print(f"Present ({len(present)}):")
    for fn, sha in present:
        print(f"  + {fn} ({sha[:12]}...)")
    print(f"Missing ({len(missing)}):")
    for fn, sha in missing:
        print(f"  - {fn} ({sha[:12]}...)")

    # At least the report ran and verified any present files
    assert True
