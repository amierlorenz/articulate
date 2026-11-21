"""Study guide and past exam Extract slice (§3.5, SPEC 2.21, 2.22, 2.25, 2.27, Issue #62)."""
from articulate.intake.guides.checks import (
    check_control_chars_and_math,
    check_exam_hint,
    check_reading_order_and_parts,
    check_unit_hints,
    check_unverified_share,
)
from articulate.intake.guides.join import join_answer_key, norm_stem
from articulate.intake.guides.mix import QuestionMix, count_mix
from articulate.intake.guides.run import process_guide_file, step
from articulate.intake.guides.schema import (
    DOCUMENT_KINDS,
    ITEM_KINDS,
    NOT_COUNTED,
    RECOGNITION,
    SCHEMA,
    GuideDocument,
    GuideItem,
    GuideOutput,
    Hint,
    LocatedQuote,
)

__all__ = [
    "DOCUMENT_KINDS",
    "ITEM_KINDS",
    "NOT_COUNTED",
    "RECOGNITION",
    "SCHEMA",
    "GuideDocument",
    "GuideItem",
    "GuideOutput",
    "Hint",
    "LocatedQuote",
    "QuestionMix",
    "check_control_chars_and_math",
    "check_exam_hint",
    "check_reading_order_and_parts",
    "check_unit_hints",
    "check_unverified_share",
    "count_mix",
    "join_answer_key",
    "norm_stem",
    "process_guide_file",
    "step",
]
