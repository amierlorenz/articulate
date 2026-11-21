"""Study guide and past exam output schema (§3.5, SPEC 2.21, Issue #32, Issue #62).

One Gemini call per guide, past exam, practice test, or answer key returns { document, items }.
Same rules as Extract (#22): control-char pattern on every string, every field required
(empty when unused), no anyOf/minItems/maxItems; limits re-checked in checks.py.
"""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PATTERN = r"^[^\t\x08\x0c]*$"  # no tab, backspace, form feed

ITEM_KINDS = [
    "topic",
    "context",
    "multipleChoice",
    "trueFalse",
    "matching",
    "fillIn",
    "shortAnswer",
    "explain",
    "problem",
    "proof",
    "code",
]
RECOGNITION = {"multipleChoice", "trueFalse", "matching"}
NOT_COUNTED = {"topic", "context"}
DOCUMENT_KINDS = ["studyGuide", "pastExam", "practiceTest", "answerKey"]


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


LOCATED = obj({
    "page": {"type": "integer", "description": "1-based page number; 0 when quote is empty"},
    "quote": s("Printed text copied character-for-character from that page; empty if none"),
})

HINT = obj({
    "name": s("A name copied exactly from the exam or unit list in the prompt"),
    "page": {"type": "integer", "description": "1-based page of the supporting quote; 0 when quote is empty"},
    "quote": s(
        "Printed sentence that says so, e.g. 'Test 4 covers Ch. 7-9'; empty if you inferred it from the content"
    ),
})

ITEM = obj({
    "number": s(
        "The item's number as printed, with part letter: '3', '3b'; for unnumbered topic lines, "
        "the next number in reading order prefixed with t: 't1', 't2'"
    ),
    "kind": s(
        "topic = a study-guide line naming something to know; context = a shared stem or setup "
        "for lettered parts; the rest are question formats",
        ITEM_KINDS,
    ),
    "page": {"type": "integer", "description": "1-based page where the item starts"},
    "quote": s(
        "The item's printed text, copied character-for-character (line breaks allowed); "
        "for multipleChoice include the options; exclude the answer"
    ),
    "answer": LOCATED,
})

DOCUMENT = obj({
    "kind": s(
        "studyGuide = topics/review list for an exam; pastExam = an exam given before; "
        "practiceTest = practice questions for an upcoming exam; answerKey = answers/solutions only",
        DOCUMENT_KINDS,
    ),
    "examHint": HINT,
    "unitHints": arr(HINT, "Units (from the unit list) this document covers"),
})

SCHEMA = obj({
    "document": DOCUMENT,
    "items": arr(ITEM, "Every item in reading order"),
})


# ---------------------------------------------------------------------------
# Pydantic models for validation and typing
# ---------------------------------------------------------------------------


class LocatedQuote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page: int = 0
    quote: str = ""


class Hint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    page: int = 0
    quote: str = ""


class GuideItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: str = ""
    kind: str = "topic"
    page: int = 1
    quote: str = ""
    answer: LocatedQuote = Field(default_factory=LocatedQuote)


class GuideDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str = "studyGuide"
    examHint: Hint = Field(default_factory=Hint)
    unitHints: list[Hint] = Field(default_factory=list)


class GuideOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document: GuideDocument = Field(default_factory=GuideDocument)
    items: list[GuideItem] = Field(default_factory=list)
