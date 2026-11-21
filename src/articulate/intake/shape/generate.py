"""Prompt generation for Shape slice (§3.5, §3.6, §3.7, SPEC-requirements 2.32, 2.33, Issue #64)."""
import logging
from pathlib import Path
from typing import Any

from articulate.core.models.runner import ModelRequest, Part
from articulate.core.pipeline import Privacy, RunContext
from articulate.intake.shape.checks import run_deterministic_checks
from articulate.intake.shape.recipes import get_recipe
from articulate.intake.shape.verify import verify_prompt

logger = logging.getLogger(__name__)

PROMPT_GEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prompts"],
    "properties": {
        "prompts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "content"],
                "properties": {
                    "kind": {"type": "string"},
                    "content": {"type": "object"},
                },
            },
        }
    },
}


def _get_template(name: str) -> str:
    prompt_file = Path(__file__).parents[4] / "prompts" / name
    if prompt_file.exists():
        try:
            return prompt_file.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001, S110
            pass
    return ""


def get_style_examples(
    course_key: str,
    ctx: RunContext,
    kind: str | None = None,
    limit: int = 3,
) -> list[str]:
    """
    Retrieve up to 3 verified style examples from the same course, newest exam first (§2.33).
    """
    examples: list[str] = []
    with ctx.db.connect() as conn:
        # Check if source_files exists and join to order by exam/file created_at desc
        query = """
            SELECT g.quote
            FROM guide_items g
            LEFT JOIN source_files s ON g.file_id = s.file_id
            WHERE (s.course_key = ? OR (s.course_key IS NULL AND ? IS NOT NULL))
              AND g.verified = 1
        """
        params: list[Any] = [course_key, course_key]
        if kind:
            query += " AND g.kind = ?"
            params.append(kind)

        query += " ORDER BY s.created_at DESC, g.file_id DESC, g.position ASC LIMIT ?;"
        params.append(limit)

        try:
            rows = conn.execute(query, params).fetchall()
            for r in rows:
                q = r["quote"]
                if q and q.strip():
                    examples.append(q.strip())
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not query guide_items for style examples: %s", e)

    return examples


def _synthesize_fallback_prompt(
    kind: str,
    concept: dict[str, Any],
    contrast_group: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Generate a deterministic, schema-compliant fallback prompt payload
    when no live LLM runner is active or for unit tests.
    """
    cid = concept.get("concept_id", "")
    title = concept.get("title", "")
    details = concept.get("details", {})
    exp = details.get("explanation", {})
    exp_text = exp.get("text", "") if isinstance(exp, dict) else str(exp)
    if not exp_text:
        cits = details.get("explanationCitations", [])
        if cits:
            exp_text = cits[0].get("quote", "")
    if not exp_text:
        exp_text = f"{title} concept in course."

    use_when = details.get("useWhen", [])
    uw_text = use_when[0].get("text", "") if use_when else "standard application scenario"

    if kind == "flashcard":
        return {
            "front": f"Define or describe the fundamental property of {title}.",
            "back": exp_text,
        }

    if kind == "shortAnswer":
        c_kind = concept.get("kind", "")
        if c_kind in ("definition", "fact"):
            return {
                "question": f"Explain {title} in your own words and state what it calculates or measures.",
                "modelAnswer": exp_text,
                "keyPoints": [f"Defines {title}", "Explains primary meaning", "Accurate context"],
            }
        # method full solve
        return {
            "question": f"Given a dataset where {uw_text}, demonstrate how to apply {title} to calculate the test statistic.",
            "modelAnswer": f"1. Verify conditions for {title}. 2. Apply formula using observed sample values. 3. Formulate conclusion.",
            "keyPoints": ["Checks conditions", "Applies formula correctly", "States conclusion"],
        }

    if kind == "multipleChoice":
        return {
            "question": f"Which of the following best describes {title}?",
            "options": [
                exp_text,
                "A measure of maximum dispersion in skewed datasets.",
                "An unrelated non-parametric ranking technique.",
                "A standard error estimation ignoring sample size.",
            ],
            "correctIndex": 0,
            "explanation": f"{title} is defined as {exp_text}.",
        }

    if kind == "methodPick":
        siblings = []
        if contrast_group:
            siblings = [m for m in contrast_group.get("member_ids", []) if m != cid]
        if not siblings:
            siblings = [f"{cid}-alt"]
        return {
            "statement": f"A researcher observes a study scenario where {uw_text}. Which method should be selected?",
            "targetConceptId": cid,
            "explanation": f"{title} is appropriate because {uw_text}.",
            "verifiedSiblingIds": siblings,
        }

    if kind == "setUp":
        return {
            "statement": f"In a study investigating whether two variables differ, suppose {uw_text}.",
            "targetConceptId": cid,
            "modelSetUp": "Define $H_0: \\mu_1 = \\mu_2$ versus $H_a: \\mu_1 \\neq \\mu_2$. Calculate sample difference statistics.",
            "keyPoints": ["State null and alternative hypotheses", "Specify test statistic", "State degrees of freedom"],
        }

    return {}


def generate_prompts_for_concept(
    concept: dict[str, Any],
    course_key: str,
    ctx: RunContext,
    *,
    valid_concept_ids: set[str],
    contrast_groups: list[dict[str, Any]],
    style_examples: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Generate, validate, and verify prompts for a concept according to its recipe (§2.32, §2.33).
    """
    cid = concept.get("concept_id", "")
    c_kind = concept.get("kind", "definition")
    tier = concept.get("tier", "standard")
    title = concept.get("title", "")
    details = concept.get("details", {})
    aliases = details.get("aliases", concept.get("aliases", []))

    # Check if concept is in a contrast group
    matching_group: dict[str, Any] | None = None
    for cg in contrast_groups:
        if cid in cg.get("member_ids", []):
            matching_group = cg
            break

    is_in_group = matching_group is not None
    required_kinds = get_recipe(c_kind, is_in_group=is_in_group, tier=tier)
    if not required_kinds:
        return []

    if style_examples is None:
        style_examples = get_style_examples(course_key, ctx)

    candidate_prompts: list[dict[str, Any]] = []

    # Attempt model generation if model runner is available
    step_name = "definition_prompts" if c_kind in ("definition", "fact") else "method_prompts"
    if ctx.models is not None:
        template_name = "shape-definition.md" if c_kind in ("definition", "fact") else "shape-method.md"
        template = _get_template(template_name)
        exp = details.get("explanation", {})
        exp_text = exp.get("text", "") if isinstance(exp, dict) else str(exp)

        prompt_input = (
            f"{template}\n\n"
            f"Course: {course_key}\n"
            f"Concept ID: {cid}\n"
            f"Title: {title}\n"
            f"Kind: {c_kind}\n"
            f"Tier: {tier}\n"
            f"Aliases: {', '.join(aliases)}\n"
            f"Explanation: {exp_text}\n"
            f"Required Kinds: {', '.join(required_kinds)}\n"
        )
        if matching_group:
            siblings = [m for m in matching_group.get("member_ids", []) if m != cid]
            prompt_input += f"Contrast Group: {matching_group.get('name', '')}\nSibling IDs: {', '.join(siblings)}\n"
        if style_examples:
            prompt_input += "Style Examples:\n" + "\n".join(f"- {ex}" for ex in style_examples) + "\n"

        req = ModelRequest(
            step=step_name,
            material=Privacy.CLASS,
            parts=[Part(text=prompt_input)],
            json_schema=PROMPT_GEN_SCHEMA,
        )

        try:
            res = ctx.models.run(req)
            if res.parsed and isinstance(res.parsed, dict):
                candidate_prompts = res.parsed.get("prompts", [])
        except Exception as e:  # noqa: BLE001
            logger.warning("Model prompt generation failed for %s, falling back: %s", cid, e)

    # Fallback if model didn't return needed kinds
    produced_kinds = {p.get("kind") for p in candidate_prompts if isinstance(p, dict)}
    for r_kind in required_kinds:
        if r_kind not in produced_kinds:
            cand = {
                "kind": r_kind,
                "content": _synthesize_fallback_prompt(r_kind, concept, matching_group),
            }
            candidate_prompts.append(cand)

    # Run deterministic checks and verification on each prompt
    verified_prompts: list[dict[str, Any]] = []
    kind_counts: dict[str, int] = {}

    for cand in candidate_prompts:
        p_kind = cand.get("kind", "")
        p_content = cand.get("content", {})

        if p_kind not in required_kinds:
            continue

        # Deterministic checks (§2.32, §2.33)
        ok, err = run_deterministic_checks(
            p_kind,
            p_content,
            concept_title=title,
            aliases=aliases,
            valid_concept_ids=valid_concept_ids,
            style_examples=style_examples,
        )
        if not ok:
            logger.info("Deterministic check failed for prompt %s (%s): %s", cid, p_kind, err)
            continue

        # Flash Lite verification (§2.32)
        v_ok, v_reason = verify_prompt(p_kind, p_content, concept, ctx)
        if not v_ok:
            logger.info("Verification rejected prompt %s (%s): %s", cid, p_kind, v_reason)
            continue

        kind_counts[p_kind] = kind_counts.get(p_kind, 0) + 1
        prompt_id = f"{cid}.{p_kind}.{kind_counts[p_kind]}"

        verified_prompts.append({
            "prompt_id": prompt_id,
            "concept_id": cid,
            "kind": p_kind,
            "content": p_content,
            "status": "active",
        })

    return verified_prompts
