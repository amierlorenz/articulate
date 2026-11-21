"""Corrections, skipped post-class quizzes and contrasts in `new` (#70, SPEC-requirements 3.5, §7.1 rules 5–7)."""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from articulate.core.handoff_schemas import Header, State, StateConcept, StatePrompt
from articulate.plan.contrasts import ContrastPrompt, eligible, pick
from articulate.plan.daily import Correction, PlanInputs, build_daily_plan
from articulate.plan.ramp import ramps
from articulate.plan.simple import Candidate, ExamDate

EASTERN = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 21)
NOW = datetime.combine(TODAY, time(3, 5), tzinfo=EASTERN)
INTRO = datetime(2026, 11, 10, 14, tzinfo=UTC)
NEXT_DAY = INTRO + timedelta(days=1)


def _c(cid, tier="core", unit="u1", course="csc301") -> Candidate:
    return Candidate(cid, course, unit, None, tier, created_at=cid)


CONCEPTS = {c.concept_id: c for c in [
    _c("a"), _c("b"), _c("c", tier="standard"), _c("d", tier="background"), _c("e", unit="u2"), _c("f"),
]}
INTRO_AT = {cid: INTRO for cid in CONCEPTS}
REVIEWS = {cid: [NEXT_DAY] for cid in CONCEPTS}


def _cp(a, b) -> ContrastPrompt:
    return ContrastPrompt(f"contrast:{a}:{b}", (a, b), "g1")


# --- contrast eligibility ---------------------------------------------------------------------------------------

def test_contrast_is_eligible_once_both_are_introduced_and_reviewed_a_day_later():
    assert eligible(_cp("a", "b"), CONCEPTS, INTRO_AT, REVIEWS, set())


def test_contrast_waits_for_a_review_at_least_a_day_after_introduction():
    same_day = {**REVIEWS, "b": [INTRO + timedelta(hours=3)]}
    assert not eligible(_cp("a", "b"), CONCEPTS, INTRO_AT, same_day, set())


def test_contrast_waits_until_both_members_are_introduced():
    assert not eligible(_cp("a", "b"), CONCEPTS, {"a": INTRO}, REVIEWS, set())


def test_contrast_needs_both_core_or_standard():
    assert eligible(_cp("a", "c"), CONCEPTS, INTRO_AT, REVIEWS, set())
    assert not eligible(_cp("a", "d"), CONCEPTS, INTRO_AT, REVIEWS, set())


def test_contrast_encore_already_has_is_not_new():
    assert not eligible(_cp("a", "b"), CONCEPTS, INTRO_AT, REVIEWS, {"contrast:a:b"})


def test_at_most_two_a_day_with_the_lower_members_tier():
    picked = pick([_cp("a", "c"), _cp("a", "b"), _cp("b", "f")], CONCEPTS, INTRO_AT, REVIEWS, set(), [])
    assert [(n.conceptId, n.promptIds, n.reason) for n in picked] == [
        ("a", ["contrast:a:b"], "core"),
        ("b", ["contrast:b:f"], "core"),
    ]


def test_in_a_ramp_contrasts_inside_the_exam_scope_come_first():
    exams = [ExamDate("csc301:test3", "csc301", TODAY + timedelta(days=5), units=["u2"], kind="test")]
    concepts = {**CONCEPTS, "g": _c("g", unit="u2")}
    active = ramps(exams, TODAY, ["csc301"])
    both = {**INTRO_AT, "g": INTRO}
    reviewed = {**REVIEWS, "g": [NEXT_DAY]}
    picked = pick([_cp("a", "b"), _cp("a", "e"), _cp("e", "g")], concepts, both, reviewed, set(), active, limit=2)
    assert [n.promptIds[0] for n in picked] == ["contrast:e:g", "contrast:a:b"]   # a:e straddles the scope


# --- the cap ----------------------------------------------------------------------------------------------------

def _state() -> State:
    intro = ["a", "b", "f"]
    return State(
        header=Header(schema="encore.state", version=1, id="s", created_at=NOW, producer="encore"),
        exportedAt=NOW,
        concepts=[StateConcept(conceptId=c, introducedAt=INTRO) for c in intro],
        prompts=[StatePrompt(promptId=f"{c}.flashcard.1", conceptId=c, introducedAt=INTRO,
                             due=datetime(2026, 12, 30, tzinfo=UTC)) for c in intro],
    )


def _plan(**kw):
    fresh = [_c(f"n{i:02d}") for i in range(12)]
    return build_daily_plan(PlanInputs(
        candidates=fresh + [CONCEPTS["a"], CONCEPTS["b"], CONCEPTS["f"]],
        exams=[], introduced={"a", "b", "f"}, active_courses=["csc301"],
        now=NOW, tz=EASTERN, plan_id="p", producer="t", state=_state(),
        attempts=[("a", NEXT_DAY), ("b", NEXT_DAY), ("f", NEXT_DAY), ("a", datetime(2026, 11, 20, 15, tzinfo=UTC))],
        **kw,
    ))


def test_corrections_and_postclass_skipped_lead_outside_the_cap():
    plan = _plan(
        corrections=[Correction("a", "a.flashcard.2"), Correction("a", "a.cloze.2"),
                     Correction("b", "b.flashcard.1")],   # Encore already introduced b's: not a correction any more
        postclass_skipped=["n05", "n07"],
    )
    assert [(n.conceptId, n.reason) for n in plan.new[:3]] == [
        ("a", "correction"), ("n05", "postclassSkipped"), ("n07", "postclassSkipped"),
    ]
    assert plan.new[0].promptIds == ["a.cloze.2", "a.flashcard.2"]
    regular = plan.new[3:]
    assert len(regular) == 10                                  # the cap still holds for the rest
    assert "n05" not in [n.conceptId for n in regular]
    assert plan.newPaused is False and plan.reviewLimit is None


def test_contrasts_take_cap_room_and_go_last():
    plan = _plan(contrasts=[_cp("a", "b"), _cp("a", "f"), _cp("b", "f")])
    assert len(plan.new) == 10
    assert [n.promptIds for n in plan.new[-2:]] == [["contrast:a:b"], ["contrast:a:f"]]
    assert [n.conceptId for n in plan.new[:8]] == [f"n{i:02d}" for i in range(8)]
