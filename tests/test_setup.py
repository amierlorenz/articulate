"""Tests for articulate setup (§3.3, §3.12)."""
import subprocess
import tomllib
from pathlib import Path

from articulate.core.setup import (
    copy_real_fixtures,
    setup_gemini_models,
    setup_launch_agents_step,
    setup_pocket_host,
    setup_shortcuts,
    store_secret,
)


def test_store_secret_calls_security(monkeypatch, caplog):
    commands = []

    def mock_run(cmd, *args, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    secret_val = "sensitive_secret_token_abc"
    res = store_secret("articulate", "canvas_token", secret_val)
    assert res is True
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd[0] == "security"
    assert cmd[1] == "add-generic-password"
    assert "-U" in cmd
    assert "-s" in cmd and "articulate" in cmd
    assert "-a" in cmd and "canvas_token" in cmd
    assert "-w" in cmd and secret_val in cmd

    # Check that the secret was not logged
    for record in caplog.records:
        assert secret_val not in record.message


def test_setup_gemini_models(tmp_path, monkeypatch):
    import io
    import json
    import urllib.request

    mock_resp = io.BytesIO(json.dumps({
        "models": [
            {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-4-hydrogen", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-2", "supportedGenerationMethods": ["embedContent"]},
        ]
    }).encode())

    class MockResp:
        def __enter__(self): return mock_resp
        def __exit__(self, *args): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: MockResp())

    out_toml = tmp_path / "models.toml"
    confirmed = setup_gemini_models(api_key="mock_key", dest_path=out_toml)
    assert "gemini-3.8-flash" in confirmed
    assert "gemini-3.5-flash-lite" in confirmed
    assert out_toml.exists()

    with open(out_toml, "rb") as f:
        data = tomllib.load(f)
    assert "gemini" in data
    assert "gemini-3.8-flash" in data["gemini"]["models"]
    assert data["gemini"]["models"]["gemini-3.8-flash"]["rpd"] == 20
    assert "chains" in data
    assert "ollama" in data


def test_setup_pocket_host(monkeypatch):
    import urllib.request

    class MockResp:
        def __init__(self, code=200):
            self.code = code
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"status":"ok"}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: MockResp(200))
    ok = setup_pocket_host("https://public.heypocketai.com")
    assert ok is True


def test_setup_launch_agents_step(tmp_path):
    dest = tmp_path / "LaunchAgents"
    installed = setup_launch_agents_step(
        repo_path=Path("/tmp/fake_repo"),
        uv_path="/bin/uv",
        dest_dir=dest,
    )
    assert len(installed) == 3
    for p in installed:
        assert p.exists()


def test_copy_real_fixtures(tmp_path):
    manifest_content = """
[[fixtures]]
filename = "sample.pptx"
sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
source = "/tmp/source/sample.pptx"
tests = "convert"
expected = "pages = slides"
"""
    manifest_file = tmp_path / "real-manifest.toml"
    manifest_file.write_text(manifest_content)

    source_file = tmp_path / "sample.pptx"
    source_file.write_bytes(b"")  # sha256 of empty is e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855

    # update source in manifest
    manifest_file.write_text(manifest_content.replace("/tmp/source/sample.pptx", str(source_file)))

    real_data_dir = tmp_path / "fixtures" / "real"
    copied, missing = copy_real_fixtures(manifest_file, real_data_dir)
    assert len(copied) == 1
    assert len(missing) == 0
    assert (real_data_dir / "sample.pptx").exists()


def test_setup_shortcuts_flow(monkeypatch):
    calls = 0

    def mock_check(shortcuts_bin=None):
        nonlocal calls
        calls += 1
        return calls > 1

    monkeypatch.setattr("articulate.core.setup.check_shortcuts_available", mock_check)
    monkeypatch.setattr("articulate.core.setup.build_master_todo", lambda **kw: True)
    monkeypatch.setattr("articulate.core.setup.install_shortcuts", lambda **kw: True)

    assert setup_shortcuts(interactive=False) is True
    assert calls == 2

