"""PROTOTYPE — throwaway. Study guide and past exam output (articulate#32).

One Gemini call per guide/exam/practice test/answer key returns `{ document, items }`.
Same rules as schema.py (articulate#22): control-char pattern on every string, every field
required (empty when unused), no anyOf/minItems/maxItems; limits re-checked in check_exam.py.
`python3 schema_exam.py > schema_exam.json` writes the reference copy.
"""
import json

from schema import PATTERN, arr, obj, s

ITEM_KINDS = ["topic", "context", "multipleChoice", "trueFalse", "matching", "fillIn",
              "shortAnswer", "explain", "problem", "proof", "code"]
RECOGNITION = {"multipleChoice", "trueFalse", "matching"}
NOT_COUNTED = {"topic", "context"}

LOCATED = obj({
    "page": {"type": "integer", "description": "1-based page number; 0 when quote is empty"},
    "quote": s("Printed text copied character-for-character from that page; empty if none"),
})

HINT = obj({
    "name": s("A name copied exactly from the exam or unit list in the prompt"),
    "page": {"type": "integer", "description": "1-based page of the supporting quote; 0 when quote is empty"},
    "quote": s("Printed sentence that says so, e.g. 'Test 4 covers Ch. 7-9'; empty if you inferred it from the content"),
})

ITEM = obj({
    "number": s("The item's number as printed, with part letter: '3', '3b'; for unnumbered topic lines, "
                "the next number in reading order prefixed with t: 't1', 't2'"),
    "kind": s("topic = a study-guide line naming something to know; context = a shared stem or setup "
              "for lettered parts; the rest are question formats", ITEM_KINDS),
    "page": {"type": "integer", "description": "1-based page where the item starts"},
    "quote": s("The item's printed text, copied character-for-character (line breaks allowed); "
               "for multipleChoice include the options; exclude the answer"),
    "answer": LOCATED,
})

DOCUMENT = obj({
    "kind": s("studyGuide = topics/review list for an exam; pastExam = an exam given before; "
              "practiceTest = practice questions for an upcoming exam; answerKey = answers/solutions only",
              ["studyGuide", "pastExam", "practiceTest", "answerKey"]),
    "examHint": HINT,
    "unitHints": arr(HINT, "Units (from the unit list) this document covers"),
})

SCHEMA = obj({
    "document": DOCUMENT,
    "items": arr(ITEM, "Every item in reading order"),
})

if __name__ == "__main__":
    print(json.dumps(SCHEMA, indent=2))
