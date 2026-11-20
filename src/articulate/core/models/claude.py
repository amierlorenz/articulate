"""Claude client using `claude -p` stream-json subprocess (§3.6)."""
import json
import logging
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

from articulate.core.clock import local_night, pacific_date
from articulate.core.config import Config
from articulate.core.db import Database
from articulate.core.models.gemini import _contains_control_chars, _update_call
from articulate.core.models.runner import ModelRequest, ModelResult

logger = logging.getLogger(__name__)


def _run_claude_cmd(model: str, prompt: str, timeout_s: int = 600) -> tuple[int, str]:
    """Execute `claude -p --model <model> --tools "" --output-format stream-json --verbose` with prompt on stdin."""
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", model, "--tools", "", "--output-format", "stream-json", "--verbose"],
            input=prompt,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired:
        logger.warning("Claude command timed out after %d seconds", timeout_s)
        return 124, ""
    except FileNotFoundError:
        logger.warning("Claude CLI not found in PATH")
        return 127, ""
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to run claude command: %s", e)
        return 1, ""


def call_claude(
    db: Database,
    config: Config | None,
    req: ModelRequest,
    model: str,
    run_id: str | None = None,
    now: datetime | None = None,
) -> tuple[str, ModelResult | None]:
    """
    Execute one Claude call via `claude -p` stream-json (§3.6).
    Monitors rate_limit_event for allowed_warning / rejected.
    """
    if now is None:
        now = datetime.now(UTC)

    p_date = pacific_date(now)
    l_night = local_night(now)
    started_at_str = now.isoformat()

    # Insert preliminary model_calls row before subprocess
    call_id: int | None = None
    with db.connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO model_calls (
                run_id, item_id, step, provider, model, pacific_date, local_night,
                started_at, outcome, counts_quota
            ) VALUES (?, ?, ?, 'claude', ?, ?, ?, ?, 'running', 0);
            """,
            (run_id, req.item_id, req.step, model, p_date, l_night, started_at_str),
        )
        call_id = cursor.lastrowid
        conn.commit()

    start_time = time.monotonic()

    prompt_texts = [p.text for p in req.parts if p.text]
    full_prompt = "\n\n".join(prompt_texts)
    if req.json_schema is not None:
        full_prompt += (
            "\n\nRespond ONLY with a valid JSON object matching this schema, with no markdown code blocks:\n"
            + json.dumps(req.json_schema)
        )

    code, stdout = _run_claude_cmd(model=model, prompt=full_prompt)
    ms = int((time.monotonic() - start_time) * 1000)

    if code == 124:
        _update_call(db, call_id, ms=ms, outcome="timeout", counts_quota=0)
        return "timeout", None

    if code != 0 or not stdout:
        _update_call(db, call_id, ms=ms, outcome="refused", counts_quota=0)
        return "refused", None

    # Parse stream-json lines
    collected_text = ""
    finish_reason: str | None = None
    tokens_in = 0
    tokens_out = 0
    hit_rate_limit_warning = False

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        item_type = data.get("type")

        # Check rate_limit_event mid-run brake (§3.6, #27)
        if item_type == "rate_limit_event":
            status = data.get("status", "")
            severity = data.get("severity", "")
            if status in ("allowed_warning", "rejected") or severity in ("warning", "critical"):
                hit_rate_limit_warning = True

        elif item_type == "assistant":
            msg = data.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, str):
                collected_text += content
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        collected_text += c.get("text", "")
            usage_data = msg.get("usage", {})
            tokens_in += usage_data.get("input_tokens", 0)
            tokens_out += usage_data.get("output_tokens", 0)

        elif item_type == "result":
            res_content = data.get("result", "")
            if not collected_text and isinstance(res_content, str):
                collected_text = res_content
            usage_data = data.get("usage", {})
            tokens_in += usage_data.get("input_tokens", 0)
            tokens_out += usage_data.get("output_tokens", 0)

    if hit_rate_limit_warning:
        _update_call(
            db,
            call_id,
            ms=ms,
            outcome="rate_limit_warning",
            counts_quota=0,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )
        return "rate_limit_warning", None

    parsed_json: Any | None = None
    if req.json_schema is not None:
        try:
            parsed_json = json.loads(collected_text)
        except json.JSONDecodeError:
            _update_call(db, call_id, ms=ms, outcome="invalid_json", counts_quota=0)
            return "invalid_json", None

        if _contains_control_chars(parsed_json):
            _update_call(db, call_id, ms=ms, outcome="invalid_json", counts_quota=0)
            return "invalid_json", None

    _update_call(
        db,
        call_id,
        ms=ms,
        outcome="ok",
        counts_quota=0,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        finish_reason=finish_reason,
    )

    return "ok", ModelResult(
        text=collected_text,
        parsed=parsed_json,
        vectors=None,
        model=model,
        provider="claude",
        finish_reason=finish_reason,
        usage={"input_tokens": tokens_in, "output_tokens": tokens_out},
        attempts=["ok"],
    )
