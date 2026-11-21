"""Tests for articulate setup (§3.3, §3.12) and shortcuts setup helper."""
import json
import subprocess

from articulate.core.setup import (
    build_master_todo,
    check_shortcuts_available,
    install_shortcuts,
    setup_shortcuts,
)


def test_check_shortcuts_available_true(monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        stdout = "Timer Test\nAdd Task\nGet Task State\n"
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    assert check_shortcuts_available() is True


def test_check_shortcuts_available_false_when_missing(monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        stdout = "Timer Test\nAdd Task\n"
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)
    assert check_shortcuts_available() is False


def test_check_shortcuts_available_false_on_error(monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        raise OSError("shortcuts command not found")

    monkeypatch.setattr(subprocess, "run", mock_run)
    assert check_shortcuts_available() is False


def test_setup_shortcuts_already_available(monkeypatch):
    monkeypatch.setattr("articulate.core.setup.check_shortcuts_available", lambda shortcuts_bin=None: True)
    built = False
    installed = False

    def mock_build(**kwargs):
        nonlocal built
        built = True
        return True

    def mock_install(**kwargs):
        nonlocal installed
        installed = True
        return True

    monkeypatch.setattr("articulate.core.setup.build_master_todo", mock_build)
    monkeypatch.setattr("articulate.core.setup.install_shortcuts", mock_install)

    assert setup_shortcuts(interactive=False) is True
    assert built is False
    assert installed is False


def test_setup_shortcuts_builds_and_installs_when_missing(monkeypatch):
    calls = 0

    def mock_check(shortcuts_bin=None):
        nonlocal calls
        calls += 1
        return calls > 1

    monkeypatch.setattr("articulate.core.setup.check_shortcuts_available", mock_check)
    build_called = False
    install_called = False

    def mock_build(**kwargs):
        nonlocal build_called
        build_called = True
        return True

    def mock_install(**kwargs):
        nonlocal install_called
        install_called = True
        return True

    monkeypatch.setattr("articulate.core.setup.build_master_todo", mock_build)
    monkeypatch.setattr("articulate.core.setup.install_shortcuts", mock_install)

    assert setup_shortcuts(interactive=False) is True
    assert build_called is True
    assert install_called is True


def test_setup_shortcuts_fails_when_still_missing(monkeypatch):
    monkeypatch.setattr("articulate.core.setup.check_shortcuts_available", lambda shortcuts_bin=None: False)
    monkeypatch.setattr("articulate.core.setup.build_master_todo", lambda **kwargs: True)
    monkeypatch.setattr("articulate.core.setup.install_shortcuts", lambda **kwargs: True)

    assert setup_shortcuts(interactive=False) is False


def test_build_master_todo_missing_dir(tmp_path):
    missing_dir = tmp_path / "nonexistent"
    assert build_master_todo(project_dir=missing_dir) is False


def test_build_master_todo_success(tmp_path, monkeypatch):
    proj_dir = tmp_path / "master-todo" / "MasterToDo"
    proj_dir.mkdir(parents=True)
    xcodeproj = proj_dir / "MasterToDo.xcodeproj"
    xcodeproj.mkdir()

    dd_dir = tmp_path / "DerivedData"
    dd_dir.mkdir()
    app_dir = dd_dir / "Build" / "Products" / "Debug" / "MasterToDo.app"
    app_dir.mkdir(parents=True)

    executed_cmds = []

    def mock_run(cmd, *args, **kwargs):
        executed_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    ok = build_master_todo(project_dir=tmp_path / "master-todo", derived_data_dir=dd_dir)
    assert ok is True
    assert any("xcodebuild" in str(c[0]) for c in executed_cmds)


def test_build_master_todo_xcodebuild_failure(tmp_path, monkeypatch):
    proj_dir = tmp_path / "master-todo" / "MasterToDo"
    proj_dir.mkdir(parents=True)
    (proj_dir / "MasterToDo.xcodeproj").mkdir()

    def mock_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="Build failed")

    monkeypatch.setattr(subprocess, "run", mock_run)

    ok = build_master_todo(project_dir=tmp_path / "master-todo")
    assert ok is False


def test_install_shortcuts_applescript_and_assets(tmp_path, monkeypatch):
    assets = tmp_path / "resources" / "shortcuts"
    assets.mkdir(parents=True)
    (assets / "Add Task.shortcut").write_bytes(b"mock")
    (assets / "Get Task State.shortcut").write_bytes(b"mock")

    executed_cmds = []

    def mock_run(cmd, *args, **kwargs):
        executed_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    ok = install_shortcuts(assets_dir=assets)
    assert ok is True
    assert any("osascript" in str(c[0]) for c in executed_cmds)
    assert any("open" in str(c[0]) for c in executed_cmds)


def test_install_shortcuts_updates_fake_state(tmp_path, monkeypatch):
    fake_state_file = tmp_path / "fake-shortcuts.json"
    fake_state_file.write_text(json.dumps({"installed": False}), encoding="utf-8")
    monkeypatch.setenv("FAKE_SHORTCUTS_STATE", str(fake_state_file))

    ok = install_shortcuts()
    assert ok is True
    data = json.loads(fake_state_file.read_text(encoding="utf-8"))
    assert data["installed"] is True
