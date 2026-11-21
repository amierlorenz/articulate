"""Exam ramps, practice tests, review quiz, the 2-day hold, finals boost and interval caps (#70, 3.1–3.3, 3.6)."""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from articulate.plan.daily import PlanInputs, build_daily_plan
from articulate.plan.ramp import Ramp, emphasis_units, practice_test, ramps
from articulate.plan.simple import Candidate, ExamDate

EASTERN = ZoneInfo("America/New_York")
FIXTURE = json.loads((Path(__file__).resolve().parent.parent / "fixtures" / "plan" / "ramp.json").read_text())
EXAMS = [
    ExamDate(e["exam_id"], e["course_key"], date.fromisoformat(e["date"]), units=e["units"], kind=e["kind"],
             name=e["name"])
    for e in FIXTURE["exams"]
]


def _candidates() -> list[Candidate]:
    from articulate.plan.ramp import is_boosted

    out = []
    for c in FIXTURE["concepts"]:
        cand = Candidate(c["concept_id"], c["course_key"], c["unit_id"], None, c["tier"], c["created_at"])
        reasons = json.dumps(c["tier_reasons"]) if "tier_reasons" in c else None
        out.append(Candidate(**{**cand.__dict__, "boosted": is_boosted(cand, reasons, EXAMS)}))
    return out


def _plan(day: date, course: str, reading_share: float = 0.0):
    return build_daily_plan(PlanInputs(
        candidates=_candidates(), exams=EXAMS, introduced=set(), active_courses=[course],
        now=datetime.combine(day, time(3, 5), tzinfo=EASTERN), tz=EASTERN, plan_id="p", producer="test",
        reading_share=reading_share,
    ))


def _ids(plan) -> list[str]:
    return [n.conceptId for n in plan.new]


# --- 10-day ramp: bio101 Test 1 on Dec 11, Genes unit only ----------------------------------------------------------

TEST_DAY = date(2026, 12, 11)
GENES_FIRST = ["bio101.dna", "bio101.rna", "bio101.cell", "bio101.membrane", "bio101.mendel"]


@pytest.mark.parametrize("ramp_day", range(10, 0, -1))
def test_ten_day_ramp_day_by_day(ramp_day):
    plan = _plan(TEST_DAY - timedelta(days=ramp_day), "bio101", reading_share=0.15)
    assert [f.model_dump() for f in plan.examFocus] == [{"examId": "bio101:test1", "rampDay": ramp_day}]
    assert plan.budgetMinutes == {"target": 20, "planned": 35}
    assert plan.readingShare == 0
    if ramp_day <= 2:   # last 2 days: no new for that exam
        assert plan.holdNewForExams == ["bio101:test1"]
        assert _ids(plan) == ["bio101.cell", "bio101.membrane"]
    else:               # the exam's unit concepts first; background still only gets what is left
        assert plan.holdNewForExams == []
        assert _ids(plan) == GENES_FIRST
    if ramp_day in (9, 6, 3):
        assert plan.practiceTest.model_dump() == {"examId": "bio101:test1", "itemCount": 15, "freeRecallMin": 0.5}
    else:
        assert plan.practiceTest is None
    if ramp_day == 1:
        assert plan.reviewQuiz.model_dump() == {"examId": "bio101:test1", "conceptIds": ["bio101.dna"]}
    else:
        assert plan.reviewQuiz is None


def test_day_before_the_ramp_is_the_phase_2_plan_with_reading():
    plan = _plan(TEST_DAY - timedelta(days=11), "bio101", reading_share=0.15)
    assert plan.examFocus == []
    assert plan.budgetMinutes == {"target": 20, "planned": 20}
    assert plan.readingShare == 0.15
    assert _ids(plan) == ["bio101.dna", "bio101.cell", "bio101.rna", "bio101.membrane", "bio101.mendel"]
    assert plan.practiceTest is None and plan.reviewQuiz is None and plan.holdNewForExams == []


def test_reading_share_never_exceeds_a_fifth():
    assert _plan(TEST_DAY - timedelta(days=20), "bio101", reading_share=0.5).readingShare == 0.2


def test_exam_day_itself_is_not_a_ramp_day():
    plan = _plan(TEST_DAY, "bio101")
    assert plan.examFocus == [] and plan.holdNewForExams == []


# --- 14-day ramp: chem110 final on Dec 15, every unit, with the finals boost ---------------------------------------

FINAL_DAY = date(2026, 12, 15)
# bohr and covalent count as core in the final's ordering, so they tie with mass and go by creation date.
BOOSTED = ["chem110.bohr", "chem110.covalent", "chem110.mass", "chem110.ph", "chem110.alchemy"]


@pytest.mark.parametrize("ramp_day", range(14, 0, -1))
def test_fourteen_day_final_ramp_day_by_day(ramp_day):
    plan = _plan(FINAL_DAY - timedelta(days=ramp_day), "chem110")
    assert [f.model_dump() for f in plan.examFocus] == [{"examId": "chem110:final", "rampDay": ramp_day}]
    assert plan.budgetMinutes["planned"] == 35
    assert plan.readingShare == 0
    if ramp_day <= 2:   # every unit is in a final's scope, so nothing new at all
        assert plan.holdNewForExams == ["chem110:final"]
        assert _ids(plan) == []
    else:
        assert _ids(plan) == BOOSTED
    assert (plan.practiceTest is not None) == (ramp_day in (9, 6, 3))
    if ramp_day == 1:   # boosted concepts count as core for the review quiz too
        assert plan.reviewQuiz.conceptIds == ["chem110.bohr", "chem110.covalent", "chem110.mass"]


def test_finals_boost_only_applies_in_the_finals_ordering():
    # Day 15: no ramp yet, so the plain tier order holds (ph was created first among the standards).
    plan = _plan(FINAL_DAY - timedelta(days=15), "chem110")
    assert plan.examFocus == []
    assert _ids(plan) == ["chem110.mass", "chem110.ph", "chem110.bohr", "chem110.covalent", "chem110.alchemy"]


def test_boost_comes_from_two_units_or_a_midterm():
    by_id = {c.concept_id: c for c in _candidates()}
    assert by_id["chem110.bohr"].boosted          # emphasised in atoms and bonds
    assert by_id["chem110.covalent"].boosted      # bonds was on the midterm
    assert not by_id["chem110.ph"].boosted        # one unit only
    assert emphasis_units('[{"unitId": "a"}, {"unitId": "a"}, {"signal": "x"}]') == 1
    assert emphasis_units("not json") == 0


def test_a_final_scope_is_every_unit_even_when_units_are_listed():
    plan = _plan(FINAL_DAY - timedelta(days=5), "chem110")
    assert "chem110.ph" in _ids(plan)[:4]   # acids is not in the final's listed units, still ramped


def test_practice_test_takes_15_to_20_items_from_what_is_introduced():
    (ramp,) = ramps(EXAMS, FINAL_DAY - timedelta(days=9), ["chem110"])
    cands = _candidates()
    assert practice_test([ramp], cands, {}).itemCount == 15
    assert practice_test([ramp], cands, {"chem110.mass": 17}).itemCount == 17
    assert practice_test([ramp], cands, {"chem110.mass": 12, "chem110.ph": 30}).itemCount == 20
    assert practice_test([Ramp(ramp.exam, 8)], cands, {}) is None


def test_nearest_exam_gets_the_practice_test_and_both_are_in_focus():
    day = date(2026, 12, 6)   # bio101 test day 5, chem110 final day 9
    plan = build_daily_plan(PlanInputs(
        candidates=_candidates(), exams=EXAMS, introduced=set(), active_courses=["bio101", "chem110"],
        now=datetime.combine(day, time(3, 5), tzinfo=EASTERN), tz=EASTERN, plan_id="p", producer="test",
    ))
    assert [(f.examId, f.rampDay) for f in plan.examFocus] == [("bio101:test1", 5), ("chem110:final", 9)]
    assert plan.practiceTest.examId == "chem110:final"
    assert _ids(plan)[:2] == ["bio101.dna", "bio101.rna"]


# --- interval cap ---------------------------------------------------------------------------------------------------

def test_interval_cap_holds_until_the_final_has_passed():
    assert _plan(FINAL_DAY, "chem110").intervalCaps == [{"courseId": "chem110", "maxDays": 30}]
    assert _plan(FINAL_DAY + timedelta(days=1), "chem110").intervalCaps == []
    assert _plan(FINAL_DAY + timedelta(days=1), "bio101").intervalCaps == [{"courseId": "bio101", "maxDays": 30}]
