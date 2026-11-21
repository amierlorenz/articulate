"""Rules of the simple daily plan (#66, SPEC-requirements 2.39). Plain code, no model calls."""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from articulate.core.handoff_schemas import Header, State, StateConcept, StatePrompt
from articulate.plan.simple import (
    Candidate,
    ExamDate,
    build_plan,
    introduced_ids,
    nearest_exam,
    order_new,
    plan_date,
    valid_until,
)

EASTERN = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 21)


def _state(concepts=(), prompts=()) -> State:
    return State(
        header=Header(schema="encore.state", version=1, id="s1", created_at=datetime(2026, 11, 21, tzinfo=UTC),
                      producer="encore"),
        exportedAt=datetime(2026, 11, 21, tzinfo=UTC),
        concepts=list(concepts),
        prompts=list(prompts),
    )


def _c(cid, tier="core", course="csc301", unit=None, unit_name=None, created="2026-11-01") -> Candidate:
    return Candidate(concept_id=cid, course_key=course, unit_id=unit, unit_name=unit_name, tier=tier,
                     created_at=created)


# --- introduced -------------------------------------------------------------------------------------------------

def test_no_state_means_nothing_is_introduced():
    assert introduced_ids(None) == set()


def test_introduced_comes_from_concepts_and_prompts_in_state():
    at = datetime(2026, 11, 20, tzinfo=UTC)
    state = _state(
        concepts=[StateConcept(conceptId="a", introducedAt=at), StateConcept(conceptId="b", introducedAt=None)],
        prompts=[StatePrompt(promptId="c.flashcard.1", conceptId="c", introducedAt=at),
                 StatePrompt(promptId="d.flashcard.1", conceptId="d", introducedAt=None)],
    )
    assert introduced_ids(state) == {"a", "c"}


def test_suspended_concepts_count_as_introduced_so_they_are_never_new():
    state = _state(concepts=[StateConcept(conceptId="s", introducedAt=None, suspended=True)])
    assert introduced_ids(state) == {"s"}


# --- nearest exam -----------------------------------------------------------------------------------------------

def test_nearest_exam_is_the_soonest_dated_exam_on_or_after_today_that_covers_the_unit():
    exams = [
        ExamDate("csc301:final", "csc301", date(2026, 12, 7), units=[]),
        ExamDate("csc301:test3", "csc301", date(2026, 11, 30), units=["csc301.u.memory"]),
        ExamDate("csc301:test2", "csc301", date(2026, 11, 10), units=["csc301.u.memory"]),  # past
        ExamDate("mat305:final", "mat305", date(2026, 11, 22), units=[]),  # other course
    ]
    assert nearest_exam(_c("m", unit="csc301.u.memory"), exams, TODAY) == date(2026, 11, 30)
    assert nearest_exam(_c("p", unit="csc301.u.processes"), exams, TODAY) == date(2026, 12, 7)


def test_exam_units_match_by_unit_name_too():
    exams = [ExamDate("csc301:test3", "csc301", date(2026, 11, 30), units=["Memory"])]
    assert nearest_exam(_c("m", unit="csc301.u.memory", unit_name="Memory"), exams, TODAY) == date(2026, 11, 30)
    assert nearest_exam(_c("p", unit="csc301.u.processes", unit_name="Processes"), exams, TODAY) is None


def test_undated_exam_gives_no_date():
    exams = [ExamDate("csc301:final", "csc301", None, units=[])]
    assert nearest_exam(_c("x"), exams, TODAY) is None


# --- ordering ---------------------------------------------------------------------------------------------------

def test_order_is_tier_first_then_nearest_exam():
    exams = [
        ExamDate("mat305:final", "mat305", date(2026, 11, 25), units=[]),
        ExamDate("csc301:final", "csc301", date(2026, 12, 7), units=[]),
    ]
    cands = [
        _c("csc.std", tier="standard"),
        _c("csc.core", tier="core"),
        _c("mat.std", tier="standard", course="mat305"),
        _c("mat.core", tier="core", course="mat305"),
        _c("phy.core", tier="core", course="phy203"),  # no exam: after dated exams
    ]
    ordered = order_new(cands, exams, TODAY, introduced=set())
    assert [n.conceptId for n in ordered] == ["mat.core", "csc.core", "phy.core", "mat.std", "csc.std"]
    assert [n.reason for n in ordered] == ["core", "core", "core", "standard", "standard"]
    assert all(n.promptIds == [] for n in ordered)


def test_introduced_concepts_are_skipped():
    ordered = order_new([_c("a"), _c("b")], [], TODAY, introduced={"a"})
    assert [n.conceptId for n in ordered] == ["b"]


def test_at_most_ten_new():
    cands = [_c(f"c{i:02d}") for i in range(14)]
    ordered = order_new(cands, [], TODAY, introduced=set())
    assert len(ordered) == 10
    assert [n.conceptId for n in ordered] == [f"c{i:02d}" for i in range(10)]


def test_background_only_fills_room_left_after_core_and_standard():
    exams = [ExamDate("csc301:final", "csc301", date(2026, 11, 22), units=[])]
    # background has the nearest exam but still waits for every core and standard concept
    cands = [_c(f"s{i}", tier="standard", course="mat305") for i in range(9)]
    cands += [_c("bg1", tier="background"), _c("bg2", tier="background")]
    ordered = order_new(cands, exams, TODAY, introduced=set())
    assert [n.conceptId for n in ordered][-1] == "bg1"
    assert len(ordered) == 10

    full = [_c(f"k{i:02d}", tier="core") for i in range(10)] + [_c("bg", tier="background")]
    assert "bg" not in [n.conceptId for n in order_new(full, exams, TODAY, introduced=set())]


def test_out_and_unknown_tiers_are_never_new():
    ordered = order_new([_c("o", tier="out"), _c("n", tier=None), _c("k")], [], TODAY, introduced=set())
    assert [n.conceptId for n in ordered] == ["k"]


def test_ties_keep_creation_order_then_id():
    cands = [_c("b", created="2026-11-02"), _c("c", created="2026-11-01"), _c("a", created="2026-11-02")]
    assert [n.conceptId for n in order_new(cands, [], TODAY, introduced=set())] == ["c", "a", "b"]


# --- dates ------------------------------------------------------------------------------------------------------

def test_plan_date_is_the_local_date_of_the_run():
    now = datetime(2026, 11, 21, 8, 5, tzinfo=UTC)  # 03:05 Eastern
    assert plan_date(now, EASTERN) == date(2026, 11, 21)


def test_valid_until_is_the_next_0200_local():
    now = datetime(2026, 11, 21, 8, 5, tzinfo=UTC)  # 03:05 Eastern, pass2
    vu = valid_until(now, EASTERN)
    assert vu == datetime(2026, 11, 22, 2, 0, tzinfo=EASTERN)
    assert vu.utcoffset() is not None


def test_valid_until_across_the_dst_change():
    now = datetime(2026, 10, 31, 7, 5, tzinfo=UTC)  # Sat 03:05 EDT; DST ends Sun Nov 1
    vu = valid_until(now, EASTERN)
    assert vu == datetime(2026, 11, 1, 2, 0, tzinfo=EASTERN)
    assert vu.utcoffset().total_seconds() == -5 * 3600


# --- whole plan -------------------------------------------------------------------------------------------------

def test_build_plan_sets_the_phase_2_fields():
    now = datetime(2026, 11, 21, 8, 5, 30, 123456, tzinfo=UTC)
    plan = build_plan(
        candidates=[_c("csc301.a"), _c("mat305.b", tier="standard", course="mat305")],
        exams=[],
        introduced=set(),
        active_courses=["mat305", "csc301"],
        now=now,
        tz=EASTERN,
        plan_id="plan-01X",
        producer="articulate 0.1.0",
    )
    assert plan.header.schema_ == "encore.plan" and plan.header.version == 1
    assert plan.header.id == "plan-01X"
    assert plan.header.created_at == datetime(2026, 11, 21, 8, 5, 30, tzinfo=UTC)  # whole seconds for Encore
    assert plan.kind == "daily"
    assert plan.date == date(2026, 11, 21)
    assert plan.validUntil == datetime(2026, 11, 22, 2, 0, tzinfo=EASTERN)
    assert plan.reviewScope == ["csc301", "mat305"]
    assert [n.conceptId for n in plan.new] == ["csc301.a", "mat305.b"]
    assert plan.budgetMinutes == {"target": 20, "planned": 20}
    assert plan.kindsMix == {"freeRecallMin": 0.5}
    assert plan.readingShare == 0.0
    assert plan.intervalCaps == [{"courseId": "csc301", "maxDays": 30}, {"courseId": "mat305", "maxDays": 30}]
    assert plan.examFocus == []


def test_new_concepts_only_from_active_courses():
    plan = build_plan(
        candidates=[_c("csc301.a"), _c("phy203.b", course="phy203")],
        exams=[], introduced=set(), active_courses=["csc301"],
        now=datetime(2026, 11, 21, 8, 5, tzinfo=UTC), tz=EASTERN, plan_id="p", producer="x",
    )
    assert [n.conceptId for n in plan.new] == ["csc301.a"]
