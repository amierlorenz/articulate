"""Gemini client using direct REST over httpx (§3.6). No SDK, no hidden retries."""
import base64
import json
import logging
import mimetypes
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from articulate.core.clock import local_night, pacific_date
from articulate.core.config import Config
from articulate.core.db import Database
from articulate.core.models.runner import ModelRequest, ModelResult, Part
from articulate.core.secrets import SecretNotFoundError, get_secret

logger = logging.getLogger(__name__)


def _contains_control_chars(obj: Any) -> bool:
    """Check if any string inside obj contains raw tab, backspace, or form feed (§3.6)."""
    if isinstance(obj, str):
        for ch in ("\t", "\x08", "\x0c"):
            if ch in obj:
                return True
    elif isinstance(obj, dict):
        return any(_contains_control_chars(v) for v in obj.values())
    elif isinstance(obj, list):
        return any(_contains_control_chars(item) for item in obj)
    return False


def _get_gemini_api_key(config: Config | None = None) -> str:
    """Get Gemini API key from Keychain or GEMINI_API_KEY env var (§3.3)."""
    service = "articulate-gemini"
    if config and config.models and config.models.gemini:
        service = config.models.gemini.keychain_service
    try:
        return get_secret(service)
    except (SecretNotFoundError, Exception):
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key:
            return env_key
        raise


def _build_parts_payload(parts: list[Part]) -> list[dict[str, Any]]:
    gemini_parts = []
    for p in parts:
        if p.text is not None:
            gemini_parts.append({"text": p.text})
        elif p.pdf is not None:
            data = base64.b64encode(p.pdf.read_bytes()).decode("ascii")
            gemini_parts.append({
                "inlineData": {"mimeType": "application/pdf", "data": data}
            })
        elif p.image is not None:
            mime, _ = mimetypes.guess_type(str(p.image))
            if not mime:
                mime = "image/png"
            data = base64.b64encode(p.image.read_bytes()).decode("ascii")
            gemini_parts.append({
                "inlineData": {"mimeType": mime, "data": data}
            })
    return gemini_parts


def call_gemini(
    db: Database,
    config: Config | None,
    req: ModelRequest,
    model: str,
    run_id: str | None = None,
    timeout_s: int | None = None,
    now: datetime | None = None,
    client: httpx.Client | None = None,
) -> tuple[str, ModelResult | None, float | None]:
    """
    Execute one Gemini request over plain REST (§3.6).
    Records attempt in model_calls before the request.
    Returns (outcome, result_if_any, retry_delay_if_any).
    """
    if now is None:
        now = datetime.now(UTC)

    p_date = pacific_date(now)
    l_night = local_night(now)
    started_at_str = now.isoformat()

    if timeout_s is None:
        timeout_s = 600
        if config and config.models and config.models.gemini:
            timeout_s = config.models.gemini.timeout_s

    # Insert preliminary model_calls row before HTTP request (§3.6)
    call_id: int | None = None
    with db.connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO model_calls (
                run_id, item_id, step, provider, model, pacific_date, local_night,
                started_at, outcome, counts_quota
            ) VALUES (?, ?, ?, 'gemini', ?, ?, ?, ?, 'running', 1);
            """,
            (run_id, req.item_id, req.step, model, p_date, l_night, started_at_str),
        )
        call_id = cursor.lastrowid
        conn.commit()

    start_time = time.monotonic()
    api_key = _get_gemini_api_key(config)

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    close_client = False
    if client is None:
        client = httpx.Client(timeout=timeout_s)
        close_client = True

    try:
        if req.embed:
            # Embedding call
            if len(req.parts) > 1:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
                body = {
                    "requests": [
                        {
                            "model": f"models/{model}",
                            "content": {"parts": [{"text": p.text or ""}]},
                        }
                        for p in req.parts
                    ]
                }
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
                text_val = req.parts[0].text if req.parts else ""
                body = {
                    "content": {"parts": [{"text": text_val}]}
                }

            resp = client.post(url, headers=headers, json=body)
        else:
            # Generation call
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            gemini_parts = _build_parts_payload(req.parts)
            body = {
                "contents": [{"role": "user", "parts": gemini_parts}]
            }
            gen_config: dict[str, Any] = {}
            if req.json_schema is not None:
                gen_config["responseMimeType"] = "application/json"
                gen_config["responseJsonSchema"] = req.json_schema
            if gen_config:
                body["generationConfig"] = gen_config

            resp = client.post(url, headers=headers, json=body)

        ms = int((time.monotonic() - start_time) * 1000)

        # Parse HTTP response
        if resp.status_code == 200:
            data = resp.json()

            if req.embed:
                vectors: list[list[float]] = []
                if "embeddings" in data:
                    vectors = [e.get("values", []) for e in data["embeddings"]]
                elif "embedding" in data:
                    vectors = [data["embedding"].get("values", [])]

                _update_call(db, call_id, ms=ms, outcome="ok", counts_quota=1)
                return "ok", ModelResult(
                    text="",
                    parsed=None,
                    vectors=vectors,
                    model=model,
                    provider="gemini",
                    attempts=["ok"],
                ), None

            candidates = data.get("candidates", [])
            cand = candidates[0] if candidates else {}
            finish = cand.get("finishReason")

            # Check max_tokens
            if finish == "MAX_TOKENS":
                _update_call(db, call_id, ms=ms, outcome="max_tokens", counts_quota=1, finish_reason=finish)
                return "max_tokens", ModelResult(
                    text="",
                    parsed=None,
                    vectors=None,
                    model=model,
                    provider="gemini",
                    finish_reason=finish,
                    attempts=["max_tokens"],
                ), None

            content = cand.get("content", {})
            parts_resp = content.get("parts", [])
            # Drop thought parts
            text = "".join(p.get("text", "") for p in parts_resp if not p.get("thought"))

            parsed_json: Any | None = None
            if req.json_schema is not None:
                try:
                    parsed_json = json.loads(text)
                except json.JSONDecodeError:
                    _update_call(db, call_id, ms=ms, outcome="invalid_json", counts_quota=1, finish_reason=finish)
                    return "invalid_json", None, None

                # Control character hit rule (§3.6)
                if _contains_control_chars(parsed_json):
                    _update_call(db, call_id, ms=ms, outcome="invalid_json", counts_quota=1, finish_reason=finish)
                    return "invalid_json", None, None

            usage_meta = data.get("usageMetadata", {})
            t_in = usage_meta.get("promptTokenCount")
            t_out = usage_meta.get("candidatesTokenCount")
            t_think = usage_meta.get("thoughtsTokenCount")

            _update_call(
                db,
                call_id,
                ms=ms,
                outcome="ok",
                counts_quota=1,
                tokens_in=t_in,
                tokens_out=t_out,
                tokens_thinking=t_think,
                finish_reason=finish,
            )
            return "ok", ModelResult(
                text=text,
                parsed=parsed_json,
                vectors=None,
                model=model,
                provider="gemini",
                finish_reason=finish,
                usage=usage_meta,
                attempts=["ok"],
            ), None

        # Non-200 responses
        status = resp.status_code
        resp_text = resp.text

        if status in (500, 503):
            outcome = f"http_{status}"
            _update_call(db, call_id, ms=ms, outcome=outcome, counts_quota=1)
            return outcome, None, None

        if status == 404:
            _update_call(db, call_id, ms=ms, outcome="http_404", counts_quota=1)
            return "http_404", None, None

        if status == 429:
            # Check if PerDay limit
            is_perday = False
            retry_delay_s: float | None = None

            try:
                err_data = resp.json().get("error", {})
                details = err_data.get("details", [])
                for d in details:
                    violations = d.get("violations", [])
                    for v in violations:
                        if "PerDay" in v.get("quotaId", ""):
                            is_perday = True
                    # Check retryDelay
                    rd = d.get("retryDelay")
                    if rd:
                        m = re.match(r"^([\d.]+)s?$", str(rd))
                        if m:
                            retry_delay_s = float(m.group(1))
            except Exception:  # noqa: BLE001, S110
                pass

            if "PerDay" in resp_text:
                is_perday = True

            if is_perday:
                _update_call(db, call_id, ms=ms, outcome="http_429_day", counts_quota=1)
                return "http_429_day", None, None
            else:
                _update_call(db, call_id, ms=ms, outcome="http_429_minute", counts_quota=1)
                return "http_429_minute", None, retry_delay_s or 5.0

        if status == 400:
            if "schema" in resp_text.lower() or "INVALID_ARGUMENT" in resp_text:
                _update_call(db, call_id, ms=ms, outcome="schema_rejected", counts_quota=1, finish_reason="schema_rejected")
                return "schema_rejected", ModelResult(
                    text="",
                    parsed=None,
                    vectors=None,
                    model=model,
                    provider="gemini",
                    finish_reason="schema_rejected",
                    attempts=["schema_rejected"],
                ), None
            else:
                _update_call(db, call_id, ms=ms, outcome="http_400", counts_quota=1)
                return "http_400", None, None

        outcome = f"http_{status}"
        _update_call(db, call_id, ms=ms, outcome=outcome, counts_quota=1)
        return outcome, None, None

    except httpx.TimeoutException:
        ms = int((time.monotonic() - start_time) * 1000)
        _update_call(db, call_id, ms=ms, outcome="timeout", counts_quota=1)
        return "timeout", None, None
    except Exception as e:  # noqa: BLE001
        ms = int((time.monotonic() - start_time) * 1000)
        logger.warning("Gemini call failed with exception: %s", e)
        _update_call(db, call_id, ms=ms, outcome="refused", counts_quota=1)
        return "refused", None, None
    finally:
        if close_client:
            client.close()


def _update_call(
    db: Database,
    call_id: int | None,
    ms: int | None = None,
    outcome: str = "ok",
    counts_quota: int = 1,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    tokens_thinking: int | None = None,
    finish_reason: str | None = None,
) -> None:
    if call_id is None:
        return
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE model_calls
            SET ms = ?, outcome = ?, counts_quota = ?, tokens_in = ?,
                tokens_out = ?, tokens_thinking = ?, finish_reason = ?
            WHERE id = ?;
            """,
            (ms, outcome, counts_quota, tokens_in, tokens_out, tokens_thinking, finish_reason, call_id),
        )
        conn.commit()
