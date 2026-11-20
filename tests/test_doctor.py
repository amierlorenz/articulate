"""Tests for articulate doctor (§3.11, §3.12)."""
import subprocess

from articulate.core.doctor import (
    DependencyReport,
    check_canvas_token,
    check_deckcraft_keynote,
    check_dependencies,
    check_gemini_key,
    check_google_token,
    check_ollama,
    check_pocket_api,
    format_dependencies_table,
    run_doctor,
)


def test_format_dependencies_table():
    reports = [
        DependencyReport(name="Ollama", status="ok", notes="local daemon"),
        DependencyReport(name="DeckCraft / Keynote", status="missing", notes="neither app installed"),
    ]
    table = format_dependencies_table(reports)
    assert "## Dependencies" in table
    assert "| Dependency | Status | Notes |" in table
    assert "| Ollama | ok | local daemon |" in table
    assert "| DeckCraft / Keynote | missing | neither app installed |" in table


def test_check_ollama_missing(monkeypatch):
    import urllib.error
    import urllib.request

    def mock_urlopen(*args, **kwargs):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    rep = check_ollama(url="http://127.0.0.1:11434")
    assert rep.name == "Ollama"
    assert rep.status == "down"
    assert "Connection refused" in rep.notes or "not reachable" in rep.notes


def test_check_deckcraft_keynote_missing(monkeypatch):
    from pathlib import Path

    orig_exists = Path.exists

    def mock_exists(self):
        if "DeckCraft" in str(self) or "Keynote" in str(self):
            return False
        return orig_exists(self)

    monkeypatch.setattr(Path, "exists", mock_exists)

    rep = check_deckcraft_keynote()
    assert rep.name == "DeckCraft / Keynote"
    assert rep.status == "missing"


def test_check_keychain_dependencies_missing(monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=44, stdout="", stderr="not found\n")

    monkeypatch.setattr(subprocess, "run", mock_run)

    c_rep = check_canvas_token()
    assert c_rep.status == "missing"

    p_rep = check_pocket_api()
    assert p_rep.status == "missing"

    g_rep = check_google_token()
    assert g_rep.status == "missing"

    gem_rep = check_gemini_key()
    assert gem_rep.status == "missing"


def test_check_dependencies_aggregated(monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="dummy\n", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    # Mock ollama urlopen
    import io
    import json
    import urllib.request

    mock_resp = io.BytesIO(json.dumps({
        "models": [
            {"name": "qwen3:8b"},
            {"name": "embeddinggemma:latest"},
            {"name": "qwen3-vl:8b-instruct"},
        ]
    }).encode())

    class MockHTTPResponse:
        def __enter__(self):
            return mock_resp
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: MockHTTPResponse())

    reports = check_dependencies()
    names = [r.name for r in reports]
    assert "Ollama" in names
    assert "DeckCraft / Keynote" in names
    assert "Canvas token" in names
    assert "Pocket API" in names
    assert "Google token" in names


def test_run_doctor_cli(capsys, monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="dummy\n", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    import io
    import json
    import urllib.request

    mock_resp = io.BytesIO(json.dumps({"models": [{"name": "qwen3:8b"}]}).encode())

    class MockResp:
        def __enter__(self): return mock_resp
        def __exit__(self, *args): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: MockResp())

    code = run_doctor()
    assert code == 0
    captured = capsys.readouterr()
    assert "## Dependencies" in captured.out
