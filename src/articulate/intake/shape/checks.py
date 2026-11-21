"""Deterministic validation checks for prompts (§3.5, §3.7, SPEC-requirements 2.32, 2.33, Issue #64).

Checks:
- Schema validation against v2 prompt content schemas (flashcard, shortAnswer, multipleChoice, methodPick, setUp).
- No alias leak in questions/statements or choices.
- Options / sibling IDs resolve to real concepts in the course.
- Style example overlap: any prompt sharing an 8+ word run with a style example is rejected.
"""
import re
from typing import Any


def _tokenize_words(text: str) -> list[str]:
    """Extract lowercase words from text for n-gram comparison."""
    return re.findall(r"\b[A-Za-z0-9_-]+\b", text.lower())


def validate_prompt_payload(kind: str, content: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate prompt payload against the Encore v2 schema for its kind."""
    if not isinstance(content, dict):
        return False, "Prompt content must be a dictionary"

    if kind == "flashcard":
        front = content.get("front")
        back = content.get("back")
        if not isinstance(front, str) or not front.strip():
            return False, "flashcard must have a non-empty 'front' string"
        if not isinstance(back, str) or not back.strip():
            return False, "flashcard must have a non-empty 'back' string"
        return True, None

    if kind == "shortAnswer":
        question = content.get("question")
        model_answer = content.get("modelAnswer")
        key_points = content.get("keyPoints")
        if not isinstance(question, str) or not question.strip():
            return False, "shortAnswer must have a non-empty 'question' string"
        if not isinstance(model_answer, str) or not model_answer.strip():
            return False, "shortAnswer must have a non-empty 'modelAnswer' string"
        if not isinstance(key_points, list) or not key_points or not all(isinstance(k, str) and k.strip() for k in key_points):
            return False, "shortAnswer must have a non-empty list of 'keyPoints'"
        return True, None

    if kind == "multipleChoice":
        question = content.get("question")
        options = content.get("options")
        correct_index = content.get("correctIndex")
        explanation = content.get("explanation")
        if not isinstance(question, str) or not question.strip():
            return False, "multipleChoice must have a non-empty 'question' string"
        if not isinstance(options, list) or len(options) < 2 or not all(isinstance(o, str) and o.strip() for o in options):
            return False, "multipleChoice must have at least 2 non-empty string 'options'"
        if not isinstance(correct_index, int) or correct_index < 0 or correct_index >= len(options):
            return False, f"multipleChoice 'correctIndex' must be an integer between 0 and {len(options) - 1}"
        if not isinstance(explanation, str) or not explanation.strip():
            return False, "multipleChoice must have a non-empty 'explanation' string"
        return True, None

    if kind == "methodPick":
        statement = content.get("statement")
        target_concept_id = content.get("targetConceptId")
        explanation = content.get("explanation")
        verified_sibling_ids = content.get("verifiedSiblingIds")
        if not isinstance(statement, str) or not statement.strip():
            return False, "methodPick must have a non-empty 'statement' string"
        if not isinstance(target_concept_id, str) or not target_concept_id.strip():
            return False, "methodPick must have a non-empty 'targetConceptId' string"
        if not isinstance(explanation, str) or not explanation.strip():
            return False, "methodPick must have a non-empty 'explanation' string"
        if not isinstance(verified_sibling_ids, list) or not verified_sibling_ids or not all(isinstance(s, str) and s.strip() for s in verified_sibling_ids):
            return False, "methodPick must have a non-empty list of 'verifiedSiblingIds'"
        return True, None

    if kind == "setUp":
        statement = content.get("statement")
        target_concept_id = content.get("targetConceptId")
        model_set_up = content.get("modelSetUp")
        key_points = content.get("keyPoints")
        if not isinstance(statement, str) or not statement.strip():
            return False, "setUp must have a non-empty 'statement' string"
        if not isinstance(target_concept_id, str) or not target_concept_id.strip():
            return False, "setUp must have a non-empty 'targetConceptId' string"
        if not isinstance(model_set_up, str) or not model_set_up.strip():
            return False, "setUp must have a non-empty 'modelSetUp' string"
        if not isinstance(key_points, list) or not key_points or not all(isinstance(k, str) and k.strip() for k in key_points):
            return False, "setUp must have a non-empty list of 'keyPoints'"
        return True, None

    return False, f"Unknown prompt kind: {kind}"


def check_options_resolve(
    kind: str,
    content: dict[str, Any],
    valid_concept_ids: set[str],
) -> tuple[bool, str | None]:
    """Verify that methodPick sibling IDs and target concept IDs resolve to real concepts."""
    if kind == "methodPick":
        target_id = content.get("targetConceptId")
        if target_id not in valid_concept_ids:
            return False, f"methodPick targetConceptId '{target_id}' does not resolve to an existing concept"
        sibling_ids = content.get("verifiedSiblingIds", [])
        for sid in sibling_ids:
            if sid not in valid_concept_ids:
                return False, f"methodPick sibling ID '{sid}' does not resolve to an existing concept"

    if kind == "setUp":
        target_id = content.get("targetConceptId")
        if target_id not in valid_concept_ids:
            return False, f"setUp targetConceptId '{target_id}' does not resolve to an existing concept"

    return True, None


def check_alias_leak(
    kind: str,
    content: dict[str, Any],
    concept_title: str,
    aliases: list[str],
) -> tuple[bool, str | None]:
    """
    Ensure the prompt question, statement, or options do not leak aliases of the concept.
    """
    if not aliases:
        return True, None

    norm_aliases = [a.strip().lower() for a in aliases if a.strip()]
    if not norm_aliases:
        return True, None

    # In multipleChoice, distractor options must not match the concept's aliases
    if kind == "multipleChoice":
        options = content.get("options", [])
        correct_idx = content.get("correctIndex", 0)
        for i, opt in enumerate(options):
            if i != correct_idx:
                opt_lower = opt.strip().lower()
                for alias in norm_aliases:
                    if opt_lower == alias:
                        return False, f"multipleChoice distractor option leaks alias: '{opt}'"

    # In methodPick, statement must not contain the concept title or aliases
    if kind == "methodPick":
        statement = content.get("statement", "").lower()
        title_lower = concept_title.strip().lower()
        if title_lower and re.search(r"\b" + re.escape(title_lower) + r"\b", statement):
            return False, f"methodPick statement leaks target concept title: '{concept_title}'"
        for alias in norm_aliases:
            if re.search(r"\b" + re.escape(alias) + r"\b", statement):
                return False, f"methodPick statement leaks concept alias: '{alias}'"

    # In flashcards or shortAnswer questions where the answer is the concept name, check question
    question_text = ""
    if kind == "flashcard":
        question_text = content.get("front", "").lower()
    elif kind in ("shortAnswer", "multipleChoice"):
        question_text = content.get("question", "").lower()

    if question_text and any(term in question_text for term in ("what is the term", "name the", "which concept", "what is another name")):
        for alias in norm_aliases:
            if re.search(r"\b" + re.escape(alias) + r"\b", question_text):
                return False, f"Prompt question leaks alias: '{alias}'"

    return True, None


def check_style_overlap(
    text: str,
    style_examples: list[str],
    max_run_words: int = 8,
) -> tuple[bool, str | None]:
    """
    Check if the text shares an 8+ word run with any style example (§2.33).
    Returns (True, None) if clean, (False, reason) if rejected.
    """
    if not text or not style_examples:
        return True, None

    prompt_words = _tokenize_words(text)
    if len(prompt_words) < max_run_words:
        return True, None

    # Build set of all 8-grams in the prompt
    prompt_ngrams: set[tuple[str, ...]] = set()
    for i in range(len(prompt_words) - max_run_words + 1):
        prompt_ngrams.add(tuple(prompt_words[i : i + max_run_words]))

    # Check each style example
    for example in style_examples:
        ex_words = _tokenize_words(example)
        if len(ex_words) < max_run_words:
            continue
        for j in range(len(ex_words) - max_run_words + 1):
            ngram = tuple(ex_words[j : j + max_run_words])
            if ngram in prompt_ngrams:
                matching_run = " ".join(ngram)
                return False, f"Rejected: prompt shares an 8+ word run with a style example: '{matching_run}'"

    return True, None


def get_prompt_searchable_text(kind: str, content: dict[str, Any]) -> str:
    """Concatenate text from a prompt payload for overlap checking."""
    parts: list[str] = []
    if kind == "flashcard":
        parts.extend([content.get("front", ""), content.get("back", "")])
    elif kind == "shortAnswer":
        parts.extend([content.get("question", ""), content.get("modelAnswer", "")])
        parts.extend(content.get("keyPoints", []))
    elif kind == "multipleChoice":
        parts.extend([content.get("question", ""), content.get("explanation", "")])
        parts.extend(content.get("options", []))
    elif kind == "methodPick":
        parts.extend([content.get("statement", ""), content.get("explanation", "")])
    elif kind == "setUp":
        parts.extend([content.get("statement", ""), content.get("modelSetUp", "")])
        parts.extend(content.get("keyPoints", []))
    return " ".join(parts)


def run_deterministic_checks(
    kind: str,
    content: dict[str, Any],
    *,
    concept_title: str,
    aliases: list[str],
    valid_concept_ids: set[str],
    style_examples: list[str],
) -> tuple[bool, str | None]:
    """Run all deterministic checks for a prompt payload."""
    ok, err = validate_prompt_payload(kind, content)
    if not ok:
        return False, err

    ok, err = check_options_resolve(kind, content, valid_concept_ids)
    if not ok:
        return False, err

    ok, err = check_alias_leak(kind, content, concept_title, aliases)
    if not ok:
        return False, err

    full_text = get_prompt_searchable_text(kind, content)
    ok, err = check_style_overlap(full_text, style_examples)
    if not ok:
        return False, err

    return True, None
