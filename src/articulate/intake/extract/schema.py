"""Extract output schema (§3.5, #22, #61)."""

from typing import Any

PATTERN = r"^[^\t\x08\x0c]*$"  # no tab, backspace, form feed


def s(desc: str, enum: list[str] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "string", "description": desc}
    if enum:
        d["enum"] = enum
    else:
        d["pattern"] = PATTERN
    return d


def obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(props),
        "properties": props,
    }


def arr(items: dict[str, Any], desc: str) -> dict[str, Any]:
    return {"type": "array", "items": items, "description": desc}


CITATION = obj(
    {
        "page": {"type": "integer", "description": "1-based page/slide number"},
        "quote": s(
            "Short passage copied character-for-character from that page, including text inside images"
        ),
    }
)

CLAIM = obj(
    {
        "citations": arr(
            CITATION, "1 to 3 places that support this claim; empty only when gapFill is true"
        ),
        "text": s("The claim itself. LaTeX in $...$ for math"),
        "gapFill": {
            "type": "boolean",
            "description": "true if the claim comes from outside the document (no citation)",
        },
    }
)

HINT = obj(
    {
        "kind": s("What the slide says about this concept", ["examHint", "dismissal", "emphasis"]),
        "page": {"type": "integer", "description": "1-based page/slide number"},
        "quote": s("The exact sentence on the slide"),
    }
)

CONCEPT = obj(
    {
        "key": s("Short unique slug for this concept within this output, e.g. paired-t-test"),
        "title": s("Short name of the concept, as the course names it"),
        "kind": s(
            "definition = a term's meaning; fact = a statement to recall (a theorem you state); "
            "method = something you choose and apply to a problem (formula, test, theorem you apply, procedure)",
            ["definition", "fact", "method"],
        ),
        "explanation": CLAIM,
        "aliases": arr(
            s("Another name or tell-tale keyword"),
            "Names and keywords that give the concept away; methods only",
        ),
        "useWhen": arr(
            CLAIM, "Cues visible in a problem that signal this method applies; methods only"
        ),
        "conditions": arr(CLAIM, "Assumptions that must hold to use it; methods only"),
        "workedExamples": arr(
            CLAIM,
            "Worked examples from the document, markdown steps each labelled by the principle used; methods only",
        ),
        "slideHints": arr(
            HINT,
            "Only explicit slide statements that this is on the exam, can be skipped, or is important",
        ),
        "confidence": s(
            "How sure you are the concept and its explanation are right and correctly located",
            ["high", "medium", "low"],
        ),
        "confidenceNote": s("Why it is not high; empty when confidence is high"),
    }
)

CONTRAST = obj(
    {
        "a": s("key of one member"),
        "b": s("key of the other member"),
        "decidingFeature": CLAIM,
    }
)

GROUP = obj(
    {
        "name": s("Short name for what the members compete over"),
        "question": s(
            "The choice a student faces, e.g. 'Which test compares two means?'; empty if there is none"
        ),
        "memberKeys": arr(s("key of a member concept"), "2 or more concept keys from this output"),
        "contrasts": arr(
            CONTRAST,
            "Pairs whose deciding feature the document states; leave a pair out when it doesn't",
        ),
    }
)

SCHEMA = obj(
    {
        "concepts": arr(CONCEPT, "Every concept the document teaches"),
        "contrastGroups": arr(
            GROUP, "Sets of concepts in this document a student could plausibly mix up"
        ),
    }
)

__all__ = ["PATTERN", "SCHEMA"]
