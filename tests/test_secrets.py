"""Tests for core.secrets."""
import json
import logging
import subprocess

import pytest

from articulate.core.secrets import (
    SecretNotFoundError,
    get_canvas_token,
    get_gemini_key,
    get_google_oauth,
    get_pocket_key,
    get_secret,
)


def test_get_secret_success(monkeypatch):
    fixture_secret = "super-secret-key-12345"

    def mock_run(cmd, *args, **kwargs):
        assert cmd[0] == "security"
        assert cmd[1] == "find-generic-password"
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=fixture_secret + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    secret = get_secret("articulate-gemini")
    assert secret == fixture_secret


def test_get_secret_not_found(monkeypatch, caplog):
    def mock_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=44, stdout="", stderr="password not found\n")

    monkeypatch.setattr(subprocess, "run", mock_run)

    with caplog.at_level(logging.DEBUG), pytest.raises(SecretNotFoundError) as exc_info:
        get_secret("articulate", "missing_key")

    # Verify secret is not in exception message
    assert "missing_key" in str(exc_info.value)
    # Ensure nothing sensitive leaked into log
    for record in caplog.records:
        assert "super-secret" not in record.message


def test_get_google_oauth(monkeypatch):
    oauth_json = json.dumps({
        "client_id": "cid",
        "client_secret": "csec",
        "refresh_token": "refr"
    })

    def mock_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=oauth_json + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    oauth_data = get_google_oauth()
    assert oauth_data["client_id"] == "cid"
    assert oauth_data["refresh_token"] == "refr"


def test_specific_secret_helpers(monkeypatch):
    def mock_run(cmd, *args, **kwargs):
        if "-a" in cmd:
            idx = cmd.index("-a")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"val_{cmd[idx+1]}\n", stderr="")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="val_gemini\n", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    assert get_gemini_key() == "val_gemini"
    assert get_pocket_key() == "val_pocket_api_key"
    assert get_canvas_token() == "val_canvas_token"
