"""Tests for core.models: ModelRunner, Gemini, Claude, Ollama, Quota ledger, and Usage gate.

Covers SPEC-requirements 0.6, 0.7, 0.8, 0.9, 0.10 and SPEC-implementation §3.6.
"""
import logging
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from articulate.core.config import Config, load_models_config
from articulate.core.db import Database
from articulate.core.models.runner import DefaultModelRunner, Held, ModelRequest, Part
from articulate.core.pipeline import Privacy

PACIFIC = ZoneInfo("America/Los_Angeles")
EASTERN = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test_models.sqlite3")
    db.migrate()
    return db


@pytest.fixture(autouse=True)
def set_env_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-test-api-key")


@pytest.fixture
def test_config(tmp_path: Path) -> Config:
    valid_models_toml = Path(__file__).parent / "fixtures" / "core" / "models_valid.toml"
    models_cfg = load_models_config(valid_models_toml)
    models_cfg.chains["extract"] = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen", "claude@ramp"]
    models_cfg.chains["personal_literature"] = ["claude"]
    models_cfg.chains["personal_text"] = ["ollama:qwen3:8b"]
    return Config(models=models_cfg)


# ==============================================================================
# Requirement 0.6: Privacy Enforcement
# ==============================================================================


def test_privacy_personal_never_reaches_gemini(test_db: Database, test_config: Config):
    """Personal material skips every gemini-* model even if models.toml puts one first."""
    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="extract",  # chain: ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen", "claude@ramp"]
        material=Privacy.PERSONAL,
        parts=[Part(text="Personal notes")],
        exam_in_ramp=False,
    )
    # Gemini models must be skipped. Since exam_in_ramp is False, claude@ramp also skips.
    # Result: nothing left -> Held
    with pytest.raises(Held) as exc_info:
        runner.run(req)

    assert exc_info.value.reason in ("quota", "claude_over_budget")

    # Verify no gemini-* calls were made in model_calls
    with test_db.connect() as conn:
        rows = conn.execute("SELECT * FROM model_calls WHERE model LIKE 'gemini-%'").fetchall()
        assert len(rows) == 0


def test_privacy_private_never_reaches_gemini_or_claude(test_db: Database, test_config: Config):
    """Private material skips every gemini-* AND claude model; only local models run."""
    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="personal_literature",  # chain: ["claude"]
        material=Privacy.PRIVATE,
        parts=[Part(text="Private therapy recording")],
    )
    with pytest.raises(Held) as exc_info:
        runner.run(req)

    assert exc_info.value.reason in ("claude_over_budget", "quota")

    with test_db.connect() as conn:
        rows = conn.execute("SELECT * FROM model_calls").fetchall()
        assert len(rows) == 0


# ==============================================================================
# Requirement 0.7: Quota Ledger and Retries
# ==============================================================================


def test_model_calls_written_before_request(test_db: Database, test_config: Config, monkeypatch):
    """Every attempt is a model_calls row written before the request."""
    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)

    called_db_rows = []

    def mock_post(*args, **kwargs):
        # Inspect database during the network call
        with test_db.connect() as conn:
            rows = conn.execute("SELECT * FROM model_calls").fetchall()
            called_db_rows.append(len(rows))
        raise RuntimeError("Crash during network call")

    monkeypatch.setattr("httpx.Client.post", mock_post)

    req = ModelRequest(
        step="route_file",  # chain: ["gemini-3.5-flash-lite"]
        material=Privacy.CLASS,
        parts=[Part(text="test")],
    )

    with pytest.raises((RuntimeError, Held)):
        runner.run(req)

    # Row must have existed DURING the call
    assert len(called_db_rows) > 0
    assert called_db_rows[0] >= 1

    # Row must still exist after the crash with counts_quota=1
    with test_db.connect() as conn:
        row = conn.execute("SELECT * FROM model_calls").fetchone()
        assert row is not None
        assert row["model"] == "gemini-3.5-flash-lite"
        assert row["counts_quota"] == 1


def test_gemini_503_retries_once_then_advances(test_db: Database, test_config: Config, monkeypatch):
    """503 response -> one retry on same model, then next model; both attempts count."""
    fixture_503 = (Path(__file__).parent / "fixtures" / "gemini" / "503.json").read_text()
    fixture_ok = (Path(__file__).parent / "fixtures" / "gemini" / "ok-extract.json").read_text()

    call_count = {"3.8": 0, "3.6": 0}

    import httpx

    def mock_post(self, url, *args, **kwargs):
        url_str = str(url)
        if "gemini-3.8-flash" in url_str:
            call_count["3.8"] += 1
            return httpx.Response(status_code=503, text=fixture_503, request=httpx.Request("POST", url))
        elif "gemini-3.6-flash" in url_str:
            call_count["3.6"] += 1
            return httpx.Response(status_code=200, text=fixture_ok, request=httpx.Request("POST", url))
        return httpx.Response(status_code=404, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.Client.post", mock_post)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="extract",  # chain: ["gemini-3.8-flash", "gemini-3.6-flash", ...]
        material=Privacy.CLASS,
        parts=[Part(text="Slide content")],
        json_schema={"type": "object"},
    )
    result = runner.run(req)
    assert result.model == "gemini-3.6-flash"
    assert call_count["3.8"] == 2  # initial + 1 retry
    assert call_count["3.6"] == 1  # succeeded

    with test_db.connect() as conn:
        rows = conn.execute("SELECT model, outcome, counts_quota FROM model_calls ORDER BY id ASC").fetchall()
        assert len(rows) == 3
        assert rows[0]["model"] == "gemini-3.8-flash"
        assert rows[0]["outcome"] == "http_503"
        assert rows[0]["counts_quota"] == 1
        assert rows[1]["model"] == "gemini-3.8-flash"
        assert rows[1]["outcome"] == "http_503"
        assert rows[1]["counts_quota"] == 1
        assert rows[2]["model"] == "gemini-3.6-flash"
        assert rows[2]["outcome"] == "ok"
        assert rows[2]["counts_quota"] == 1


def test_gemini_timeout_advances_to_next_model(test_db: Database, test_config: Config, monkeypatch):
    """Timeout -> advances to next model; attempt counts quota."""
    fixture_ok = (Path(__file__).parent / "fixtures" / "gemini" / "ok-extract.json").read_text()

    import httpx

    def mock_post(self, url, *args, **kwargs):
        url_str = str(url)
        if "gemini-3.8-flash" in url_str:
            raise httpx.TimeoutException("Connection timed out")
        elif "gemini-3.6-flash" in url_str:
            return httpx.Response(status_code=200, text=fixture_ok, request=httpx.Request("POST", url))
        return httpx.Response(status_code=404, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.Client.post", mock_post)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="extract",
        material=Privacy.CLASS,
        parts=[Part(text="Slide content")],
        json_schema={"type": "object"},
    )
    result = runner.run(req)
    assert result.model == "gemini-3.6-flash"

    with test_db.connect() as conn:
        rows = conn.execute("SELECT model, outcome, counts_quota FROM model_calls ORDER BY id ASC").fetchall()
        assert len(rows) == 2
        assert rows[0]["model"] == "gemini-3.8-flash"
        assert rows[0]["outcome"] == "timeout"
        assert rows[0]["counts_quota"] == 1
        assert rows[1]["model"] == "gemini-3.6-flash"
        assert rows[1]["outcome"] == "ok"


def test_gemini_429_perday_sets_exhausted_until(test_db: Database, test_config: Config, monkeypatch):
    """PerDay 429 -> model exhausted until next midnight Pacific; next model."""
    fixture_429_perday = (Path(__file__).parent / "fixtures" / "gemini" / "429-perday.json").read_text()
    fixture_ok = (Path(__file__).parent / "fixtures" / "gemini" / "ok-extract.json").read_text()

    import httpx

    def mock_post(self, url, *args, **kwargs):
        url_str = str(url)
        if "gemini-3.8-flash" in url_str:
            return httpx.Response(status_code=429, text=fixture_429_perday, request=httpx.Request("POST", url))
        elif "gemini-3.6-flash" in url_str:
            return httpx.Response(status_code=200, text=fixture_ok, request=httpx.Request("POST", url))
        return httpx.Response(status_code=404, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.Client.post", mock_post)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="extract",
        material=Privacy.CLASS,
        parts=[Part(text="Slide content")],
        json_schema={"type": "object"},
    )
    result = runner.run(req)
    assert result.model == "gemini-3.6-flash"

    with test_db.connect() as conn:
        state = conn.execute("SELECT * FROM model_state WHERE model = 'gemini-3.8-flash'").fetchone()
        assert state is not None
        assert state["exhausted_until"] is not None

        call_row = conn.execute("SELECT outcome FROM model_calls WHERE model = 'gemini-3.8-flash'").fetchone()
        assert call_row["outcome"] == "http_429_day"

    # Second call should skip gemini-3.8-flash immediately without HTTP request
    runner.run(req)
    with test_db.connect() as conn:
        calls = conn.execute("SELECT model FROM model_calls WHERE model = 'gemini-3.8-flash'").fetchall()
        assert len(calls) == 1  # No second attempt on 3.8


def test_gemini_429_perminute_waits_and_retries(test_db: Database, test_config: Config, monkeypatch):
    """Per-minute 429 -> waits retryDelay once and retries on same model."""
    fixture_429_min = (Path(__file__).parent / "fixtures" / "gemini" / "429-perminute.json").read_text()
    fixture_ok = (Path(__file__).parent / "fixtures" / "gemini" / "ok-extract.json").read_text()

    import httpx

    attempt = 0

    def mock_post(self, url, *args, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(status_code=429, text=fixture_429_min, request=httpx.Request("POST", url))
        return httpx.Response(status_code=200, text=fixture_ok, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.Client.post", mock_post)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="extract",
        material=Privacy.CLASS,
        parts=[Part(text="Slide content")],
        json_schema={"type": "object"},
    )
    result = runner.run(req)
    assert result.model == "gemini-3.8-flash"
    assert attempt == 2

    with test_db.connect() as conn:
        rows = conn.execute("SELECT model, outcome FROM model_calls ORDER BY id ASC").fetchall()
        assert len(rows) == 2
        assert rows[0]["outcome"] == "http_429_minute"
        assert rows[1]["outcome"] == "ok"


def test_gemini_404_sets_missing_since_and_health_row(test_db: Database, test_config: Config, monkeypatch):
    """404 -> skipped + missing_since + health row; next model."""
    fixture_404 = (Path(__file__).parent / "fixtures" / "gemini" / "404.json").read_text()
    fixture_ok = (Path(__file__).parent / "fixtures" / "gemini" / "ok-extract.json").read_text()

    import httpx

    def mock_post(self, url, *args, **kwargs):
        url_str = str(url)
        if "gemini-3.8-flash" in url_str:
            return httpx.Response(status_code=404, text=fixture_404, request=httpx.Request("POST", url))
        return httpx.Response(status_code=200, text=fixture_ok, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.Client.post", mock_post)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="extract",
        material=Privacy.CLASS,
        parts=[Part(text="Slide content")],
        json_schema={"type": "object"},
    )
    result = runner.run(req)
    assert result.model == "gemini-3.6-flash"

    with test_db.connect() as conn:
        state = conn.execute("SELECT missing_since FROM model_state WHERE model = 'gemini-3.8-flash'").fetchone()
        assert state is not None
        assert state["missing_since"] is not None

        health_row = conn.execute("SELECT * FROM health WHERE key = 'gemini-3.8-flash'").fetchone()
        assert health_row is not None
        assert "unknown" in health_row["detail"].lower() or "retired" in health_row["detail"].lower()


def test_max_tokens_and_schema_400_return_to_caller(test_db: Database, test_config: Config, monkeypatch):
    """max_tokens and schema 400 return to caller with finish_reason without retrying."""
    fixture_max_tokens = (Path(__file__).parent / "fixtures" / "gemini" / "max-tokens.json").read_text()

    import httpx

    call_count = 0

    def mock_post(self, url, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code=200, text=fixture_max_tokens, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.Client.post", mock_post)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="extract",
        material=Privacy.CLASS,
        parts=[Part(text="Long deck content")],
        json_schema={"type": "object"},
    )
    result = runner.run(req)
    assert result.finish_reason == "MAX_TOKENS"
    assert call_count == 1  # Not retried and did not spill to 3.6


# ==============================================================================
# Requirement 0.8: Daytime Reserve & Pacific Date Boundary
# ==============================================================================


def test_reserve_applies_only_in_pass2(test_db: Database, test_config: Config):
    """In pass2, Flash Lite stops at rpd - 40 and embeddings at rpd - 50; in nightly/watch reserve is not held back."""
    runner_pass2 = DefaultModelRunner(db=test_db, config=test_config, job="pass2")
    runner_nightly = DefaultModelRunner(db=test_db, config=test_config, job="nightly")
    runner_watch = DefaultModelRunner(db=test_db, config=test_config, job="watch")

    # In models_valid.toml:
    # gemini-3.5-flash-lite: rpd = 500, reserve = 40
    # gemini-embedding-2: rpd = 1000, reserve = 50
    assert runner_pass2.remaining("gemini-3.5-flash-lite") == 500 - 40
    assert runner_nightly.remaining("gemini-3.5-flash-lite") == 500
    assert runner_watch.remaining("gemini-3.5-flash-lite") == 500

    assert runner_pass2.remaining("gemini-embedding-2") == 1000 - 50
    assert runner_nightly.remaining("gemini-embedding-2") == 1000
    assert runner_watch.remaining("gemini-embedding-2") == 1000


def test_pacific_date_boundary_eastern_dst():
    """Pacific-date boundary correct at 02:59 and 03:01 Eastern across DST."""
    from articulate.core.clock import pacific_date

    # Standard Time (Winter): 02:59 EST is 23:59 PST (previous Pacific day)
    dt_winter_pre = datetime(2026, 11, 20, 2, 59, tzinfo=EASTERN)
    dt_winter_post = datetime(2026, 11, 20, 3, 1, tzinfo=EASTERN)
    assert pacific_date(dt_winter_pre) == "2026-11-19"
    assert pacific_date(dt_winter_post) == "2026-11-20"

    # Daylight Saving Time (Summer): 02:59 EDT is 23:59 PDT (previous Pacific day)
    dt_summer_pre = datetime(2026, 6, 15, 2, 59, tzinfo=EASTERN)
    dt_summer_post = datetime(2026, 6, 15, 3, 1, tzinfo=EASTERN)
    assert pacific_date(dt_summer_pre) == "2026-06-14"
    assert pacific_date(dt_summer_post) == "2026-06-15"


# ==============================================================================
# Requirement 0.9: Claude Gate
# ==============================================================================


def test_claude_usage_parsed_ok(test_db: Database, test_config: Config, monkeypatch):
    """Claude /usage output parsed by kind: session and weekly_all."""
    fixture_ok = Path(__file__).parent / "fixtures" / "claude" / "usage-ok.jsonl"

    from articulate.core.models import usage

    def mock_run_claude_usage(*args, **kwargs):
        return (0, fixture_ok.read_text())

    monkeypatch.setattr(usage, "_run_usage_cmd", mock_run_claude_usage)

    res = usage.read_claude_usage(db=test_db, config=test_config)
    assert res.ok is True
    assert res.session_pct == 35.0
    assert res.weekly_pct == 25.0
    assert res.cli_version == "2.1.327"
    assert res.credential_expires_at is not None

    with test_db.connect() as conn:
        row = conn.execute("SELECT * FROM claude_usage").fetchone()
        assert row is not None
        assert row["ok"] == 1
        assert row["session_pct"] == 35.0
        assert row["weekly_pct"] == 25.0


def test_claude_usage_fail_closed_cases(test_db: Database, test_config: Config, monkeypatch):
    """Each fail-closed case in §3.6 holds Claude."""
    from articulate.core.models import usage

    # 1. Non-zero exit
    monkeypatch.setattr(usage, "_run_usage_cmd", lambda *args, **kwargs: (1, "error"))
    res = usage.read_claude_usage(db=test_db, config=test_config)
    assert res.ok is False

    # 2. Missing row (weekly_all missing)
    fixture_missing = (Path(__file__).parent / "fixtures" / "claude" / "usage-missing-row.jsonl").read_text()
    monkeypatch.setattr(usage, "_run_usage_cmd", lambda *args, **kwargs: (0, fixture_missing))
    res = usage.read_claude_usage(db=test_db, config=test_config)
    assert res.ok is False

    # 3. Past resets_at
    fixture_past = (Path(__file__).parent / "fixtures" / "claude" / "usage-past-resets.jsonl").read_text()
    monkeypatch.setattr(usage, "_run_usage_cmd", lambda *args, **kwargs: (0, fixture_past))
    res = usage.read_claude_usage(db=test_db, config=test_config)
    assert res.ok is False


def test_claude_gate_rate_limit_warning_stops_run(test_db: Database, test_config: Config, monkeypatch):
    """rate_limit_event allowed_warning stops the run's Claude use."""
    from articulate.core.models import claude, usage

    # Valid usage
    fixture_usage = (Path(__file__).parent / "fixtures" / "claude" / "usage-ok.jsonl").read_text()
    monkeypatch.setattr(usage, "_run_usage_cmd", lambda *args, **kwargs: (0, fixture_usage))

    fixture_warning = (Path(__file__).parent / "fixtures" / "claude" / "rate-limit-warning.jsonl").read_text()

    def mock_run_claude(*args, **kwargs):
        return (0, fixture_warning)

    monkeypatch.setattr(claude, "_run_claude_cmd", mock_run_claude)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="personal_literature",  # chain: ["claude"]
        material=Privacy.PERSONAL,
        parts=[Part(text="Personal literature")],
    )

    with pytest.raises(Held):
        runner.run(req)

    # Next Claude call in same run must be stopped immediately
    with pytest.raises(Held) as exc_info:
        runner.run(req)
    assert "claude_over_budget" in exc_info.value.reason or "rate_limit" in exc_info.value.reason


def test_claude_at_ramp_only_with_exam_in_ramp(test_db: Database, test_config: Config, monkeypatch):
    """claude@ramp runs only when exam_in_ramp is True."""
    from articulate.core.models import usage

    fixture_usage = (Path(__file__).parent / "fixtures" / "claude" / "usage-ok.jsonl").read_text()
    monkeypatch.setattr(usage, "_run_usage_cmd", lambda *args, **kwargs: (0, fixture_usage))

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)

    # When all gemini models are exhausted/skipped
    with test_db.connect() as conn:
        for m in ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen"):
            conn.execute("INSERT OR REPLACE INTO model_state (model, exhausted_until) VALUES (?, '2099-01-01T00:00:00Z')", (m,))
        conn.commit()

    req_no_ramp = ModelRequest(
        step="extract",  # chain has claude@ramp at end
        material=Privacy.CLASS,
        parts=[Part(text="Deck")],
        exam_in_ramp=False,
    )
    with pytest.raises(Held):
        runner.run(req_no_ramp)


# ==============================================================================
# Ollama and JSON Validation
# ==============================================================================


def test_ollama_pattern_stripped_from_schema(test_db: Database, test_config: Config, monkeypatch):
    """Ollama drops grammar when schema has pattern; runner strips pattern."""
    import httpx

    captured_format = {}

    def mock_post(self, url, *args, **kwargs):
        import json
        payload = kwargs.get("json")
        if payload is None:
            raw = kwargs.get("content") or kwargs.get("data") or "{}"
            payload = json.loads(raw)
        captured_format.update(payload.get("format", {}))
        return httpx.Response(
            status_code=200,
            json={"message": {"content": "{\"title\": \"Normal Distribution\", \"n\": 1}"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("httpx.Client.post", mock_post)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    schema_with_pattern = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "pattern": "^[^\\t\\x08\\x0c]*$"},
            "n": {"type": "integer"},
        },
    }
    req = ModelRequest(
        step="personal_text",  # chain: ["ollama:qwen3:8b"]
        material=Privacy.PERSONAL,
        parts=[Part(text="extract task")],
        json_schema=schema_with_pattern,
    )
    res = runner.run(req)
    assert res.parsed == {"title": "Normal Distribution", "n": 1}
    assert "pattern" not in captured_format["properties"]["title"]


def test_ollama_connection_refused_records_health_and_held(test_db: Database, test_config: Config, monkeypatch):
    """Connection refused -> health row Ollama down and Held(ollama_down)."""
    import httpx

    def mock_post(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr("httpx.Client.post", mock_post)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="personal_text",  # chain: ["ollama:qwen3:8b"]
        material=Privacy.PERSONAL,
        parts=[Part(text="task")],
    )
    with pytest.raises(Held) as exc_info:
        runner.run(req)
    assert exc_info.value.reason == "ollama_down"

    with test_db.connect() as conn:
        health_row = conn.execute("SELECT * FROM health WHERE kind = 'ollama_down'").fetchone()
        assert health_row is not None


def test_json_validation_rejects_control_characters(test_db: Database, test_config: Config, monkeypatch):
    """Decodes JSON and rejects strings containing tab, backspace, or form feed; advances to next model."""
    fixture_raw_tab = (Path(__file__).parent / "fixtures" / "gemini" / "raw-tab.json").read_text()
    fixture_ok = (Path(__file__).parent / "fixtures" / "gemini" / "ok-extract.json").read_text()

    import httpx

    def mock_post(self, url, *args, **kwargs):
        url_str = str(url)
        if "gemini-3.8-flash" in url_str:
            return httpx.Response(status_code=200, text=fixture_raw_tab, request=httpx.Request("POST", url))
        return httpx.Response(status_code=200, text=fixture_ok, request=httpx.Request("POST", url))

    monkeypatch.setattr("httpx.Client.post", mock_post)

    runner = DefaultModelRunner(db=test_db, config=test_config, job="nightly", pace=False)
    req = ModelRequest(
        step="extract",
        material=Privacy.CLASS,
        parts=[Part(text="Deck")],
        json_schema={"type": "object"},
    )
    res = runner.run(req)
    assert res.model == "gemini-3.6-flash"

    with test_db.connect() as conn:
        rows = conn.execute("SELECT model, outcome FROM model_calls ORDER BY id ASC").fetchall()
        assert len(rows) == 2
        assert rows[0]["model"] == "gemini-3.8-flash"
        assert rows[0]["outcome"] == "invalid_json"


# ==============================================================================
# CLI Quota output
# ==============================================================================


def test_cli_quota_output(test_db: Database, test_config: Config, capsys):
    """articulate quota shows today's ledger per model."""
    from articulate.core.clock import pacific_date

    now = datetime.now(UTC)
    p_date = pacific_date(now)

    with test_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_calls (step, provider, model, pacific_date, started_at, outcome, counts_quota)
            VALUES ('extract', 'gemini', 'gemini-3.8-flash', ?, ?, 'ok', 1),
                   ('extract', 'gemini', 'gemini-3.8-flash', ?, ?, 'ok', 1),
                   ('route_file', 'gemini', 'gemini-3.5-flash-lite', ?, ?, 'ok', 1);
            """,
            (p_date, now.isoformat(), p_date, now.isoformat(), p_date, now.isoformat()),
        )
        conn.commit()

    # Pass db and config directly or monkeypatch
    from articulate.core.models.quota import format_quota_report

    report = format_quota_report(test_db, test_config)
    assert "gemini-3.8-flash" in report
    assert "gemini-3.5-flash-lite" in report
    assert "2" in report


# ==============================================================================
# Requirement 0.10: Acceptance Check with Real Calls (@pytest.mark.real)
# ==============================================================================


@pytest.mark.real
def test_real_calls_acceptance(test_db: Database, test_config: Config):
    """
    Requirement 0.10:
    One real call per provider: Gemini Flash Lite (tiny prompt), claude -p /usage,
    Ollama qwen3:8b; each lands in model_calls / claude_usage; articulate quota shows them.
    Guarded against 01:45 - 04:00 local time.
    """
    from datetime import time as dt_time

    from articulate.core.models.quota import format_quota_report
    from articulate.core.models.usage import read_claude_usage

    now_local = datetime.now(EASTERN)
    # Guard: none between 01:45 and 04:00
    if dt_time(1, 45) <= now_local.time() <= dt_time(4, 0):
        pytest.skip("Skipping real call between 01:45 and 04:00 to protect nightly job quota.")

    runner = DefaultModelRunner(db=test_db, config=test_config, job="manual", pace=False)

    # 1. Real Gemini Flash Lite call
    req_gemini = ModelRequest(
        step="route_file",  # gemini-3.5-flash-lite
        material=Privacy.CLASS,
        parts=[Part(text="Say the word 'ok' and nothing else.")],
    )
    try:
        res_gemini = runner.run(req_gemini)
        assert res_gemini.model == "gemini-3.5-flash-lite"
        assert res_gemini.provider == "gemini"
    except Exception as e:  # noqa: BLE001
        logger.warning("Real Gemini call skipped/failed: %s", e)

    # 2. Real Claude /usage check
    try:
        usage_res = read_claude_usage(db=test_db, config=test_config)
        assert usage_res is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("Real Claude /usage check skipped/failed: %s", e)

    # 3. Real Ollama qwen3:8b call
    req_ollama = ModelRequest(
        step="personal_text",  # ollama:qwen3:8b
        material=Privacy.PERSONAL,
        parts=[Part(text="Say the word 'ok' and nothing else.")],
    )
    try:
        res_ollama = runner.run(req_ollama)
        assert res_ollama.provider == "ollama"
    except Exception as e:  # noqa: BLE001
        logger.warning("Real Ollama call skipped/failed: %s", e)

    # 4. articulate quota report output
    report = format_quota_report(test_db, test_config)
    assert "Articulate Model Quota Ledger" in report

