"""PROTOTYPE — throwaway. Transcript pass output schema (articulate#39).

Same rules as Extract output schema (#22): control-char pattern on every string, every field required,
no anyOf/minItems/maxItems, citations before claim text. Citations point at a numbered window, not a page.
`python3 schema.py > schema.json` writes the reference copy.
"""
import json

PATTERN = "^[^\\t\\x08\\x0c]*$"


def s(desc, enum=None):
    d = {"type": "string", "description": desc}
    if enum:
        d["enum"] = enum
    else:
        d["pattern"] = PATTERN
    return d


def obj(props):
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def arr(items, desc):
    return {"type": "array", "items": items, "description": desc}


CITATION = obj({
    "window": {"type": "integer", "description": "Number of the transcript window the quote is in"},
    "quote": s("Words copied exactly from that window's transcript, as transcribed (keep misheard words as they are)"),
})

CLAIM = obj({
    "citations": arr(CITATION, "1 to 3 places in the transcript that support this claim; empty only when gapFill is true"),
    "text": s("The claim, written clearly. Fix obvious mistranscriptions here (not in quotes). LaTeX in $...$ for math"),
    "gapFill": {"type": "boolean", "description": "true if the claim comes from outside the lecture (no citation)"},
})

CONCEPT = obj({
    "key": s("Short unique slug for this concept within this output, e.g. separation-of-duties"),
    "title": s("Short name of the concept, spelled correctly even if the transcript misheard it"),
    "kind": s("definition = a term's meaning; fact = a statement to recall; "
              "method = something you choose and apply to a problem", ["definition", "fact", "method"]),
    "explanation": CLAIM,
    "aliases": arr(s("Another name or tell-tale keyword"), "Names and keywords that give the concept away; methods only"),
    "useWhen": arr(CLAIM, "Cues visible in a problem that signal this method applies; methods only"),
    "conditions": arr(CLAIM, "Assumptions that must hold to use it; methods only"),
    "workedExamples": arr(CLAIM, "Examples the instructor works through or tells (board work, stories that illustrate the concept)"),
    "confidence": s("How sure you are the concept is taught here and its explanation is right", ["high", "medium", "low"]),
    "confidenceNote": s("Why it is not high (e.g. unclear transcription); empty when confidence is high"),
})

SPOKEN_CLAIM = obj({
    "conceptKey": s("key of a deck concept from the list in the prompt"),
    "field": s("Where this claim adds to the deck concept", ["explanation", "workedExample", "condition"]),
    "correctsSlide": {"type": "boolean", "description": "true only if the instructor explicitly says the slide is wrong"},
    "claim": CLAIM,
})

HINT = obj({
    "kind": s("examHint = will be on a test/quiz; dismissal = won't be tested or can be skipped; "
              "emphasis = instructor stresses it as important", ["examHint", "dismissal", "emphasis"]),
    "conceptKey": s("key of the concept (new or deck) the hint is about; empty if it is about a chapter/unit/topic"),
    "scope": s("For a hint not tied to one concept: the chapter, unit or topic it names; empty otherwise"),
    "window": {"type": "integer", "description": "Window number"},
    "quote": s("The instructor's exact sentence, copied from the window"),
})

CONTRAST = obj({"a": s("key of one member"), "b": s("key of the other member"), "decidingFeature": CLAIM})

GROUP = obj({
    "name": s("Short name for what the members compete over"),
    "question": s("The choice a student faces; empty if there is none"),
    "memberKeys": arr(s("key of a member concept (new or deck)"), "2 or more keys"),
    "contrasts": arr(CONTRAST, "Pairs whose deciding feature the instructor states; leave a pair out when not"),
})

SCHEMA = obj({
    "newConcepts": arr(CONCEPT, "Concepts the lecture teaches that are not in the deck list"),
    "spokenClaims": arr(SPOKEN_CLAIM, "What the lecture adds to deck concepts; empty when there is no deck list"),
    "spokenHints": arr(HINT, "Explicit statements about what is or isn't on a test, or is important"),
    "contrastGroups": arr(GROUP, "Sets of concepts from this lecture a student could plausibly mix up"),
})

if __name__ == "__main__":
    print(json.dumps(SCHEMA, indent=2))
