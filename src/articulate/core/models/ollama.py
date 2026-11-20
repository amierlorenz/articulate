"""Ollama client using REST /api/chat and /api/embed (§3.6)."""
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from articulate.core.clock import local_night, pacific_date
from articulate.core.config import Config
from articulate.core.db import Database
from articulate.core.models.gemini import _contains_control_chars, _update_call
from articulate.core.models.runner import Held, ModelRequest, ModelResult

logger = logging.getLogger(__name__)


def strip_pattern_from_schema(schema: Any) -> Any:
    """Recursively remove 'pattern' keys from JSON schema for Ollama (§3.6, #26)."""
    if isinstance(schema, dict):
        return {k: strip_pattern_from_schema(v) for k, v in schema.items() if k != "pattern"}
    elif isinstance(schema, list):
        return [strip_pattern_from_schema(item) for item in schema]
    return schema


def call_ollama(
    db: Database,
    config: Config | None,
    req: ModelRequest,
    model: str,
    run_id: str | None = None,
    now: datetime | None = None,
    client: httpx.Client | None = None,
) -> tuple[str, ModelResult | None]:
    """
    Execute one Ollama call over REST (§3.6).
    Handles pattern stripping, VL keep_alive: 0, and connection refused health/held.
    """
    if now is None:
        now = datetime.now(UTC)

    p_date = pacific_date(now)
    l_night = local_night(now)
    started_at_str = now.isoformat()

    url_base = "http://127.0.0.1:11434"
    vl_model = "qwen3-vl:8b-instruct"
    if config and config.models and config.models.ollama:
        url_base = config.models.ollama.url
        vl_model = config.models.ollama.vl_model

    # Insert preliminary model_calls row before HTTP request
    call_id: int | None = None
    with db.connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO model_calls (
                run_id, item_id, step, provider, model, pacific_date, local_night,
                started_at, outcome, counts_quota
            ) VALUES (?, ?, ?, 'ollama', ?, ?, ?, ?, 'running', 0);
            """,
            (run_id, req.item_id, req.step, model, p_date, l_night, started_at_str),
        )
        call_id = cursor.lastrowid
        conn.commit()

    start_time = time.monotonic()
    close_client = False
    if client is None:
        client = httpx.Client(timeout=req.timeout_s or 600)
        close_client = True

    try:
        if req.embed:
            embed_url = f"{url_base}/api/embed"
            texts = [p.text or "" for p in req.parts]
            body = {"model": model, "input": texts}
            resp = client.post(embed_url, json=body)
            ms = int((time.monotonic() - start_time) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                vectors = data.get("embeddings", [])
                _update_call(db, call_id, ms=ms, outcome="ok", counts_quota=0)
                return "ok", ModelResult(
                    text="",
                    parsed=None,
                    vectors=vectors,
                    model=model,
                    provider="ollama",
                    attempts=["ok"],
                )
            else:
                outcome = f"http_{resp.status_code}"
                _update_call(db, call_id, ms=ms, outcome=outcome, counts_quota=0)
                return outcome, None
        else:
            chat_url = f"{url_base}/api/chat"
            prompt_texts = [p.text for p in req.parts if p.text]
            full_prompt = "\n\n".join(prompt_texts)

            body = {
                "model": model,
                "stream": False,
                "think": False,
                "options": {"temperature": 0},
                "messages": [{"role": "user", "content": full_prompt}],
            }

            # VL model runs with keep_alive: 0 (§3.6, #35)
            if model == vl_model or "vl" in model.lower():
                body["keep_alive"] = 0

            # Strip pattern from schema (§3.6, #26)
            if req.json_schema is not None:
                stripped = strip_pattern_from_schema(req.json_schema)
                body["format"] = stripped

            resp = client.post(chat_url, json=body)
            ms = int((time.monotonic() - start_time) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("message", {})
                content = msg.get("content", "")

                parsed_json: Any | None = None
                if req.json_schema is not None:
                    try:
                        parsed_json = json.loads(content)
                    except json.JSONDecodeError:
                        _update_call(db, call_id, ms=ms, outcome="invalid_json", counts_quota=0)
                        return "invalid_json", None

                    if _contains_control_chars(parsed_json):
                        _update_call(db, call_id, ms=ms, outcome="invalid_json", counts_quota=0)
                        return "invalid_json", None

                tokens_in = data.get("prompt_eval_count")
                tokens_out = data.get("eval_count")

                _update_call(
                    db,
                    call_id,
                    ms=ms,
                    outcome="ok",
                    counts_quota=0,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )
                return "ok", ModelResult(
                    text=content,
                    parsed=parsed_json,
                    vectors=None,
                    model=model,
                    provider="ollama",
                    usage={"tokens_in": tokens_in, "tokens_out": tokens_out},
                    attempts=["ok"],
                )
            else:
                outcome = f"http_{resp.status_code}"
                _update_call(db, call_id, ms=ms, outcome=outcome, counts_quota=0)
                return outcome, None

    except httpx.ConnectError as e:
        ms = int((time.monotonic() - start_time) * 1000)
        logger.warning("Ollama connection refused: %s", e)
        _update_call(db, call_id, ms=ms, outcome="refused", counts_quota=0)

        # Raise health row (§3.6, §3.10)
        now_str = now.isoformat()
        try:
            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO health (kind, key, detail, interrupt, first_seen, last_seen)
                    VALUES ('ollama_down', 'ollama', 'Ollama connection refused: service is down', 0, ?, ?)
                    ON CONFLICT(kind, key, first_seen) DO UPDATE SET last_seen = excluded.last_seen;
                    """,
                    (now_str, now_str),
                )
                conn.commit()
        except Exception as ex:  # noqa: BLE001
            logger.warning("Could not write health row for ollama_down: %s", ex)

        raise Held("ollama_down") from e

    except httpx.TimeoutException:
        ms = int((time.monotonic() - start_time) * 1000)
        _update_call(db, call_id, ms=ms, outcome="timeout", counts_quota=0)
        return "timeout", None
    except Exception as e:  # noqa: BLE001
        ms = int((time.monotonic() - start_time) * 1000)
        logger.warning("Ollama call failed with exception: %s", e)
        _update_call(db, call_id, ms=ms, outcome="refused", counts_quota=0)
        return "refused", None
    finally:
        if close_client:
            client.close()
