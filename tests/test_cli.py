"""Tests for core.cli."""
import pytest

from articulate.core.cli import main


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "articulate" in captured.out


def test_cli_quota_stub(capsys):
    ret = main(["quota"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "ledger" in captured.out.lower() or "quota" in captured.out.lower()


def test_cli_run_help(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--only" in captured.out


def test_cli_run_execution(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    human_dir = tmp_path / "human"
    monkeypatch.setenv("ARTICULATE_DATA", str(data_dir))
    monkeypatch.setenv("ARTICULATE_HUMAN", str(human_dir))

    # Run watch with empty steps (since no slices registered)
    ret = main(["run", "watch"])
    assert ret == 0

    # Verify DB was created and migrated
    db_path = data_dir / "articulate.sqlite3"
    assert db_path.exists()
