"""Tests for launchd plist templates and rendering (§3.12)."""
import plistlib
from pathlib import Path

from articulate.core.launchd import (
    PMSET_COMMAND,
    get_launchd_templates_dir,
    install_launch_agents,
    render_all_plists,
    render_plist,
)


def test_launchd_templates_exist():
    template_dir = get_launchd_templates_dir()
    assert template_dir.exists()
    assert (template_dir / "com.amierlorenz.articulate.nightly.plist").exists()
    assert (template_dir / "com.amierlorenz.articulate.pass2.plist").exists()
    assert (template_dir / "com.amierlorenz.articulate.watch.plist").exists()


def test_render_nightly_plist():
    repo = Path("/Users/test/projects/articulate")
    uv = "/usr/local/bin/uv"
    log_dir = Path("/Users/test/data/logs")

    rendered = render_plist("nightly", repo_path=repo, uv_path=uv, log_dir=log_dir)
    data = plistlib.loads(rendered.encode("utf-8"))

    assert data["Label"] == "com.amierlorenz.articulate.nightly"
    assert data["ProgramArguments"] == [
        uv,
        "run",
        "--project",
        str(repo),
        "articulate",
        "run",
        "nightly",
    ]
    assert data["StartCalendarInterval"] == {"Hour": 2, "Minute": 0}
    assert data["StandardOutPath"] == str(log_dir / "launchd.nightly.out.log")
    assert data["StandardErrorPath"] == str(log_dir / "launchd.nightly.err.log")


def test_render_pass2_plist():
    repo = Path("/Users/test/projects/articulate")
    uv = "/opt/homebrew/bin/uv"
    log_dir = Path("/Users/test/data/logs")

    rendered = render_plist("pass2", repo_path=repo, uv_path=uv, log_dir=log_dir)
    data = plistlib.loads(rendered.encode("utf-8"))

    assert data["Label"] == "com.amierlorenz.articulate.pass2"
    assert data["ProgramArguments"] == [
        uv,
        "run",
        "--project",
        str(repo),
        "articulate",
        "run",
        "pass2",
    ]
    assert data["StartCalendarInterval"] == {"Hour": 3, "Minute": 5}
    assert data["StandardOutPath"] == str(log_dir / "launchd.pass2.out.log")
    assert data["StandardErrorPath"] == str(log_dir / "launchd.pass2.err.log")


def test_render_watch_plist():
    repo = Path("/Users/test/projects/articulate")
    uv = "uv"
    log_dir = Path("/Users/test/data/logs")

    rendered = render_plist("watch", repo_path=repo, uv_path=uv, log_dir=log_dir)
    data = plistlib.loads(rendered.encode("utf-8"))

    assert data["Label"] == "com.amierlorenz.articulate.watch"
    assert data["ProgramArguments"] == [
        uv,
        "run",
        "--project",
        str(repo),
        "articulate",
        "run",
        "watch",
    ]
    # Minute 15 and 45
    intervals = data["StartCalendarInterval"]
    assert isinstance(intervals, list)
    assert len(intervals) == 2
    assert {"Minute": 15} in intervals
    assert {"Minute": 45} in intervals


def test_render_all_plists():
    plists = render_all_plists(repo_path=Path("/tmp/repo"), uv_path="/bin/uv")
    assert set(plists.keys()) == {
        "com.amierlorenz.articulate.nightly.plist",
        "com.amierlorenz.articulate.pass2.plist",
        "com.amierlorenz.articulate.watch.plist",
    }
    for content in plists.values():
        parsed = plistlib.loads(content.encode("utf-8"))
        assert "Label" in parsed


def test_pmset_command_constant():
    assert PMSET_COMMAND == "sudo pmset repeat wake MTWRFSU 01:55:00"


def test_install_launch_agents(tmp_path):
    dest = tmp_path / "LaunchAgents"
    installed = install_launch_agents(
        repo_path=Path("/tmp/repo"),
        uv_path="/bin/uv",
        destination_dir=dest,
    )
    assert len(installed) == 3
    for p in installed:
        assert p.exists()
        assert p.suffix == ".plist"
