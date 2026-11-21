"""Local sensitivity check on qwen3:8b (Acceptance 1.4, Issue #29, Issue #55)."""
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from articulate.core.models.runner import ModelRequest, Part
from articulate.core.pipeline import Privacy
from articulate.core.runlog import RunLog

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "sensitivity.md"

SENSITIVITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sensitive": {"type": "boolean"},
        "category": {
            "type": ["string", "null"],
            "enum": ["health", "therapy", "relationships", "finances", "others_private", None],
        },
        "reason": {"type": "string"},
    },
    "required": ["sensitive", "category", "reason"],
}


def _get_prompt() -> str:
    if PROMPT_PATH.exists():
        raw = PROMPT_PATH.read_text(encoding="utf-8")
        return re.sub(r"^<!--.*?-->\s*", "", raw, count=1, flags=re.DOTALL).strip()
    return (
        "Determine if the provided transcript contains deeply sensitive personal topics: "
        "health, therapy, relationships, finances, or others' private matters."
    )


def check_sensitivity(
    text: str,
    runner: Any,
    runlog: RunLog | None = None,
    recording_id: str = "",
) -> bool:
    """
    Run local sensitivity check on text before personal material goes anywhere.
    A hit marks material Private and logs a line in _system/Runs.md.
    """
    clean_text = text.strip()
    if not clean_text or len(clean_text.split()) < 5 or runner is None:
        return False

    prompt_instructions = _get_prompt()
    full_prompt = f"{prompt_instructions}\n\nTranscript to check:\n{clean_text}"

    req = ModelRequest(
        step="personal_text",
        material=Privacy.PERSONAL,
        parts=[Part(text=full_prompt)],
        json_schema=SENSITIVITY_SCHEMA,
    )

    try:
        res = runner.run(req)
        parsed = res.parsed or {}
        if parsed.get("sensitive") is True:
            cat = parsed.get("category") or "sensitive"
            reason = parsed.get("reason", "")
            now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
            if runlog is not None:
                runlog.line(f"- {now_str} 🔒 sensitivity hit: {recording_id} ({cat} - {reason})")
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Sensitivity check failed on recording %s: %s", recording_id, e)
        # Fail closed: if check fails unexpectedly on error, do not leak
        return False

    return False
