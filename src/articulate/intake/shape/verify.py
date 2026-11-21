"""Prompt verification on Flash Lite (§3.5, §3.6, SPEC-requirements 2.32, Issue #64)."""
import json
import logging
from pathlib import Path
from typing import Any

from articulate.core.models.runner import ModelRequest, Part
from articulate.core.pipeline import Privacy, RunContext

logger = logging.getLogger(__name__)

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["valid", "reason"],
    "properties": {
        "valid": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}


def _get_verify_prompt_template() -> str:
    prompt_file = Path(__file__).parents[4] / "prompts" / "shape-verify.md"
    if prompt_file.exists():
        try:
            return prompt_file.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001, S110
            pass
    return "You verify study prompts generated for a concept. Return JSON with valid and reason."


def verify_prompt(
    prompt_kind: str,
    content: dict[str, Any],
    concept: dict[str, Any],
    ctx: RunContext,
) -> tuple[bool, str]:
    """
    Run verification call on Flash Lite (step 'prompt_verify') (§2.32).
    Returns (is_valid, reason).
    """
    if ctx.models is None:
        return True, "No model runner configured; verification passed by default"

    template = _get_verify_prompt_template()
    title = concept.get("title", "")
    course_key = concept.get("course_key", "")
    details = concept.get("details", {})
    explanation = details.get("explanation", {})
    exp_text = explanation.get("text", "") if isinstance(explanation, dict) else str(explanation)

    prompt_text = (
        f"{template}\n\n"
        f"Course: {course_key}\n"
        f"Concept Title: {title}\n"
        f"Concept Kind: {concept.get('kind', '')}\n"
        f"Explanation: {exp_text}\n\n"
        f"Prompt Kind: {prompt_kind}\n"
        f"Prompt Content:\n{json.dumps(content, indent=2)}\n"
    )

    req = ModelRequest(
        step="prompt_verify",
        material=Privacy.CLASS,
        parts=[Part(text=prompt_text)],
        json_schema=VERIFY_SCHEMA,
    )

    try:
        res = ctx.models.run(req)
        parsed = res.parsed
        if parsed and isinstance(parsed, dict) and "valid" in parsed:
            is_valid = bool(parsed["valid"])
            reason = str(parsed.get("reason", ""))
            return is_valid, reason
    except Exception as e:  # noqa: BLE001
        logger.warning("Error running prompt_verify model check: %s", e)
        # If verification model is unavailable or throws, log and fail closed or report
        return False, f"Verification model error: {e}"

    return True, "Verified"
