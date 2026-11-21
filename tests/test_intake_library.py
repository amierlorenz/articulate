"""Tests for intake.library package: matching, tiers, gap-fill, resolvers, and public matcher."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.pipeline import RunContext
from articulate.core.vault import Vault
from articulate.intake.library import (
    match_candidates,
    match_or_create_concept,
    process_gap_fill,
    register_resolvers,
    step,
)
from articulate.intake.library.embed import cosine_similarity
from articulate.intake.library.judge import run_judge
from articulate.intake.library.resolvers import resolve_merge
from articulate.intake.library.tiers import TierSignal, calculate_concept_tier


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "library"


@pytest.fixture
def candidates_data(fixtures_dir: Path) -> list[dict]:
    return json.loads((fixtures_dir / "candidates.json").read_text(encoding="utf-8"))


@pytest.fixture
def library_data(fixtures_dir: Path) -> list[dict]:
    return json.loads((fixtures_dir / "library_concepts.json").read_text(encoding="utf-8"))


@pytest.fixture
def judge_mock_output(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / "judge_output.json").read_text(encoding="utf-8"))


@pytest.fixture
def guide_and_exam_data(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / "guide_and_exam.json").read_text(encoding="utf-8"))


@pytest.fixture
def contrast_candidates_data(fixtures_dir: Path) -> list[dict]:
    return json.loads((fixtures_dir / "contrast_candidates.json").read_text(encoding="utf-8"))


@pytest.fixture
def contrast_library_data(fixtures_dir: Path) -> list[dict]:
    return json.loads((fixtures_dir / "contrast_library.json").read_text(encoding="utf-8"))


@pytest.fixture
def contrast_groups_data(fixtures_dir: Path) -> list[dict]:
    return json.loads((fixtures_dir / "contrast_groups.json").read_text(encoding="utf-8"))


@pytest.fixture
def contrast_judge_mock_output(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / "contrast_judge_output.json").read_text(encoding="utf-8"))


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    return db


@pytest.fixture
def test_config():
    from articulate.core.config import Config, ModelsConfig

    return Config(courses=None, models=ModelsConfig(), articulate=None)


@pytest.fixture
def run_ctx(test_db: Database, test_config, tmp_path: Path) -> RunContext:
    vault = Vault(tmp_path / "vault", test_db)
    flags = Flags(test_db, vault=vault)
    register_resolvers(flags)
    ctx = RunContext(
        run_id="01TEST00000000000000000000",
        job="nightly",
        now=datetime(2026, 11, 20, 2, 0, 0, tzinfo=UTC),
        db=test_db,
        config=test_config,
        vault=vault,
        flags=flags,
    )
    return ctx


def test_cosine_similarity():
    """Cosine similarity computation on normalized vectors."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v2)) == 1.0

    v3 = [0.0, 1.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v3)) == 0.0

    v4 = [0.7071, 0.7071, 0.0]
    assert pytest.approx(cosine_similarity(v1, v4), abs=1e-3) == 0.7071


def test_judge_outcomes_new_same_confusable_and_batch_comparison(
    run_ctx: RunContext,
    candidates_data: list[dict],
    library_data: list[dict],
    judge_mock_output: dict,
    monkeypatch,
):
    """
    Judge produces new / same / confusable outcomes, and compares
    candidates within the night's batch with each other (§2.28, Issue #63).
    """
    mock_runner = MagicMock()
    mock_runner.run.return_value = MagicMock(
        parsed=judge_mock_output,
        text=json.dumps(judge_mock_output),
    )
    run_ctx.models = mock_runner

    # Shortlist map: candidate index -> list of (lib_id, score)
    shortlist = {
        0: [("lib.cs101.bst", 0.75)],
        1: [("lib.cs101.quicksort", 0.92)],
        2: [],
        3: [("candidate:1", 0.88)],
        4: [],
    }

    results = run_judge(
        candidates=candidates_data,
        library=library_data,
        shortlist=shortlist,
        ctx=run_ctx,
    )

    assert len(results) == 5

    # Candidate 0: confusable with lib.cs101.bst
    assert results[0]["candidate_id"] == "cs101.binary_search"
    assert len(results[0]["matches"]) == 1
    assert results[0]["matches"][0]["relation"] == "confusable"
    assert results[0]["matches"][0]["id"] == "lib.cs101.bst"

    # Candidate 1: same as lib.cs101.quicksort (merge candidate)
    assert results[1]["candidate_id"] == "cs101.quick_sort"
    assert len(results[1]["matches"]) == 1
    assert results[1]["matches"][0]["relation"] == "same"
    assert results[1]["matches"][0]["id"] == "lib.cs101.quicksort"
    assert len(results[1]["matches"][0]["adds"]) > 0

    # Candidate 2: new (no matches)
    assert results[2]["candidate_id"] == "cs101.merge_sort"
    assert len(results[2]["matches"]) == 0

    # Candidate 3: same as candidate 1 in same night's batch!
    assert results[3]["candidate_id"] == "cs101.fast_sort"
    assert len(results[3]["matches"]) == 1
    assert results[3]["matches"][0]["relation"] == "same"
    assert results[3]["matches"][0]["id"] == "candidate:1"

    # Candidate 4: new
    assert results[4]["candidate_id"] == "cs101.intro_agenda"
    assert len(results[4]["matches"]) == 0


def test_tier_rules_each_rule_from_issue_10_and_230():
    """
    Tests every tier rule from #10 and SPEC-requirements 2.30:
    - exam hint -> core
    - dismissal -> background
    - admin, title and agenda slides -> out
    - no signal -> standard
    - matched guide item -> at least standard and on_guide
    - concepts in a guide's units not on it -> background
    - dismissed and not on guide -> out
    - matched past-exam question -> one tier up
    - units no exam covers -> out
    - cumulative final emphasis -> one tier up
    All with reasons stored.
    """
    # 1. Exam hint -> core
    t1, r1 = calculate_concept_tier(
        concept_title="Dynamic Programming",
        unit_id="u1",
        exam_hint={"quote": "This will definitely appear on the midterm exam."},
        covered_by_exams=True,
    )
    assert t1 == "core"
    assert any(r["signal"] == TierSignal.EXAM_HINT and "midterm" in r["quote"] for r in r1)

    # 2. Dismissal -> background
    t2, r2 = calculate_concept_tier(
        concept_title="B-Tree Derivation Details",
        unit_id="u1",
        dismissal={"quote": "You don't need to memorize these derivations for the test."},
        covered_by_exams=True,
    )
    assert t2 == "background"
    assert any(r["signal"] == TierSignal.DISMISSAL for r in r2)

    # 3. Admin / agenda -> out
    t3, r3 = calculate_concept_tier(
        concept_title="Lecture Outline and Office Hours",
        unit_id="u1",
        is_admin_or_agenda=True,
        covered_by_exams=True,
    )
    assert t3 == "out"
    assert any(r["signal"] == TierSignal.ADMIN_OR_AGENDA for r in r3)

    # 4. Default no signal -> standard
    t4, r4 = calculate_concept_tier(
        concept_title="Breadth First Search",
        unit_id="u1",
        covered_by_exams=True,
    )
    assert t4 == "standard"
    assert any(r["signal"] == TierSignal.DEFAULT_STANDARD for r in r4)

    # 5. Study guide matched item -> at least standard and on_guide
    t5, r5 = calculate_concept_tier(
        concept_title="Dijkstra Algorithm",
        unit_id="u1",
        dismissal={"quote": "Not tested heavily"},  # was background
        matched_guide_items=[{"quote": "Explain Dijkstra shortest path step by step."}],
        guide_exists_for_unit=True,
        covered_by_exams=True,
    )
    assert t5 == "standard"  # upgraded from background
    assert any(r["signal"] == TierSignal.STUDY_GUIDE for r in r5)

    # 6. Concept in guide's unit not on guide -> drops to background
    t6, r6 = calculate_concept_tier(
        concept_title="Bellman-Ford Algorithm",
        unit_id="u1",
        guide_exists_for_unit=True,
        matched_guide_items=[],  # not on guide
        covered_by_exams=True,
    )
    assert t6 == "background"
    assert any(r["signal"] == TierSignal.NOT_ON_GUIDE for r in r6)

    # 7. Dismissed and not on guide -> out
    t7, r7 = calculate_concept_tier(
        concept_title="Floyd-Warshall Historical Context",
        unit_id="u1",
        dismissal={"quote": "Skip this section."},
        guide_exists_for_unit=True,
        matched_guide_items=[],  # dismissed and not on guide
        covered_by_exams=True,
    )
    assert t7 == "out"
    assert any(r["signal"] == TierSignal.DISMISSED_NOT_ON_GUIDE for r in r7)

    # 8. Matched past exam question -> one tier up
    # out -> background
    t8a, r8a = calculate_concept_tier(
        concept_title="Sample Concept",
        unit_id="u1",
        is_admin_or_agenda=True,  # out
        matched_exam_questions=[{"quote": "Sample question"}],
        covered_by_exams=True,
    )
    assert t8a == "background"
    assert any(r["signal"] == TierSignal.PAST_EXAM for r in r8a)

    # background -> standard
    t8b, _ = calculate_concept_tier(
        concept_title="Sample Concept B",
        unit_id="u1",
        dismissal={"quote": "Low priority"},  # background
        matched_exam_questions=[{"quote": "Sample question"}],
        covered_by_exams=True,
    )
    assert t8b == "standard"

    # standard -> core
    t8c, _ = calculate_concept_tier(
        concept_title="Sample Concept C",
        unit_id="u1",
        matched_exam_questions=[{"quote": "Sample question"}],
        covered_by_exams=True,
    )
    assert t8c == "core"

    # 9. Unit no exam covers -> out
    t9, r9 = calculate_concept_tier(
        concept_title="Post-term Enrichment",
        unit_id="u_untested",
        covered_by_exams=False,
    )
    assert t9 == "out"
    assert any(r["signal"] == TierSignal.NO_EXAM_UNIT for r in r9)

    # 10. Cumulative final emphasis -> one tier up
    t10, r10 = calculate_concept_tier(
        concept_title="Graph Representations",
        unit_id="u1",
        emphasized_in_multiple_units_or_midterm=True,
        covered_by_exams=True,
    )
    assert t10 == "core"  # standard -> core
    assert any(r["signal"] == TierSignal.CUMULATIVE_FINAL_EMPHASIS for r in r10)


def test_unmatched_guide_item_and_exam_question(
    run_ctx: RunContext,
    guide_and_exam_data: dict,
):
    """
    Unmatched guide item -> held gap-fill candidate (answer can be source);
    unmatched guide item with no source -> weekly flag;
    unmatched exam question -> logged only (§2.31, Issue #63).
    """
    study_guide = guide_and_exam_data["study_guide"]
    items = study_guide["items"]

    # Item 1: unmatched with printed answer
    res1 = process_gap_fill(
        guide_item=items[1],
        course_key="cs101",
        unit_id="cs101.u.sorting",
        ctx=run_ctx,
    )
    assert res1.concept_id.startswith("cs101.")
    assert res1.status == "held"
    assert res1.hold_reason == "gap_fill"
    assert res1.has_source is True
    # Citation is recorded from printed answer
    assert "Building the max heap" in res1.details_json

    # Item 2: unmatched with NO source / answer
    res2 = process_gap_fill(
        guide_item=items[2],
        course_key="cs101",
        unit_id="cs101.u.sorting",
        ctx=run_ctx,
    )
    assert res2.status == "held"
    assert res2.hold_reason == "gap_fill"
    assert res2.has_source is False

    # Check flags table: gap-fill without source added a flag
    with run_ctx.db.connect() as conn:
        flags_rows = conn.execute("SELECT * FROM flags WHERE kind = 'intake.gap_fill';").fetchall()
        assert len(flags_rows) >= 1
        assert any(res2.concept_id == r["item_id"] for r in flags_rows)


def test_merge_candidate_holding_and_resolvers(run_ctx: RunContext):
    """
    Merge candidate is held on weekly list with keep / merge / drop.
    Ticking keep / merge / drop applies once (§2.29, Issue #63).
    """
    now_str = run_ctx.now.isoformat()
    # Insert existing concept and candidate concept into DB
    with run_ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO concepts (concept_id, course_key, unit_id, kind, title, details_json, status, created_at, updated_at)
            VALUES ('lib_quick', 'cs101', 'u1', 'method', 'Quick Sort', '{"aliases": ["Quicksort"], "explanationCitations": []}', 'active', ?, ?);
            """,
            (now_str, now_str),
        )
        conn.execute(
            """
            INSERT INTO concepts (concept_id, course_key, unit_id, kind, title, details_json, status, hold_reason, created_at, updated_at)
            VALUES ('cand_quick', 'cs101', 'u1', 'method', 'Quicksort', '{"aliases": ["Partition exchange"], "explanationCitations": [{"quote": "New quote", "sourceFileId": "f1"}]}', 'held', 'weekly_review', ?, ?);
            """,
            (now_str, now_str),
        )
        conn.commit()

    # Add merge flag
    flag_id = run_ctx.flags.add(
        kind="intake.merge",
        subject="Merge 'Quicksort' into 'Quick Sort'",
        choices=[("keep", "keep"), ("merge", "merge"), ("drop", "drop")],
        item_id="cand_quick",
    )

    # 1. Test resolve_merge with choice 'keep'
    resolve_merge(flag_id, "keep", run_ctx)
    with run_ctx.db.connect() as conn:
        cand = conn.execute("SELECT * FROM concepts WHERE concept_id = 'cand_quick';").fetchone()
        assert cand["status"] == "held"
        assert cand["hold_reason"] == "shape"  # unheld from weekly review, ready for shape

    # Reset candidate for test 2: 'merge'
    with run_ctx.db.connect() as conn:
        conn.execute(
            "UPDATE concepts SET status = 'held', hold_reason = 'weekly_review' WHERE concept_id = 'cand_quick';"
        )
        conn.commit()

    resolve_merge(flag_id, "merge", run_ctx)
    with run_ctx.db.connect() as conn:
        cand = conn.execute("SELECT * FROM concepts WHERE concept_id = 'cand_quick';").fetchone()
        lib = conn.execute("SELECT * FROM concepts WHERE concept_id = 'lib_quick';").fetchone()
        assert cand["status"] in ("superseded", "retired")
        lib_details = json.loads(lib["details_json"])
        assert "Partition exchange" in lib_details.get("aliases", [])

    # Reset candidate for test 3: 'drop'
    with run_ctx.db.connect() as conn:
        conn.execute(
            "UPDATE concepts SET status = 'held', hold_reason = 'weekly_review' WHERE concept_id = 'cand_quick';"
        )
        conn.commit()

    resolve_merge(flag_id, "drop", run_ctx)
    with run_ctx.db.connect() as conn:
        cand = conn.execute("SELECT * FROM concepts WHERE concept_id = 'cand_quick';").fetchone()
        assert cand["status"] == "retired"

    # Test tick processing applies once
    run_ctx.flags.render_note()
    note_path = run_ctx.vault.vault_dir / "Inbox" / "Intake flags.md"
    content = note_path.read_text(encoding="utf-8")
    # Simulate user ticking [x] keep
    ticked_content = content.replace("[ ] keep", "[x] keep")
    note_path.write_text(ticked_content, encoding="utf-8")

    processed1 = run_ctx.flags.process_ticks(run_ctx)
    assert processed1 == 1

    # Second pass: flag is already resolved, so process_ticks does not re-apply
    processed2 = run_ctx.flags.process_ticks(run_ctx)
    assert processed2 == 0


def test_public_matcher_interface(
    run_ctx: RunContext,
    candidates_data: list[dict],
    library_data: list[dict],
    monkeypatch,
):
    """
    Public matcher matches candidates and attaches items to existing
    concepts or makes new ones at tier: standard (§7.1 #71, Issue #63).
    """
    mock_runner = MagicMock()
    # Mock embedding vectors
    mock_runner.run.side_effect = [
        # candidate embeddings
        MagicMock(vectors=[[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.0, 1.0], [0.1, 0.2]]),
        # library embeddings
        MagicMock(vectors=[[0.0, 1.0], [0.99, 0.01]]),
        # judge call
        MagicMock(
            parsed={
                "candidates": [
                    {
                        "candidate": 0,
                        "matches": [
                            {
                                "id": "lib.cs101.bst",
                                "relation": "confusable",
                                "reason": "...",
                                "adds": [],
                            }
                        ],
                    },
                    {
                        "candidate": 1,
                        "matches": [
                            {
                                "id": "lib.cs101.quicksort",
                                "relation": "same",
                                "reason": "...",
                                "adds": [],
                            }
                        ],
                    },
                    {"candidate": 2, "matches": []},
                    {
                        "candidate": 3,
                        "matches": [
                            {"id": "candidate:1", "relation": "same", "reason": "...", "adds": []}
                        ],
                    },
                    {"candidate": 4, "matches": []},
                ]
            }
        ),
    ]
    run_ctx.models = mock_runner

    results = match_candidates(
        candidates=candidates_data,
        course_key="cs101",
        ctx=run_ctx,
        existing_library=library_data,
    )
    assert len(results.matches) == 5

    # Test single item helper match_or_create_concept used by post-class quiz
    matched_id, _is_new = match_or_create_concept(
        item_payload={
            "title": "Quick Sort",
            "kind": "method",
            "explanation": "Quicksort algorithm partitioning around pivot.",
        },
        course_key="cs101",
        ctx=run_ctx,
        existing_library=library_data,
        reserve=True,
    )
    # Attaches to existing or standard
    assert matched_id is not None


def test_pipeline_step_advances_to_shape(run_ctx: RunContext, monkeypatch):
    """
    Stage library handler processes items and advances them to stage shape (§3.5).
    """
    now_str = run_ctx.now.isoformat()
    with run_ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO items (
                item_id, source, kind, native_id, version,
                course_key, privacy, stage, status, created_at, updated_at
            ) VALUES ('it_slides', 'canvas', 'slides', 'slot_1', 'hash1', 'cs101', 'class', 'library', 'pending', ?, ?);
            """,
            (now_str, now_str),
        )
        conn.execute(
            """
            INSERT INTO items (
                item_id, source, kind, native_id, version,
                course_key, privacy, stage, status, created_at, updated_at
            ) VALUES ('it_guide', 'canvas', 'guide_or_exam', 'slot_2', 'hash2', 'cs101', 'class', 'library', 'pending', ?, ?);
            """,
            (now_str, now_str),
        )
        conn.commit()

    # Mock match_candidates to avoid real calls
    monkeypatch.setattr(
        "articulate.intake.library.match_candidates",
        lambda *args, **kwargs: MagicMock(matches=[]),
    )

    summary = step(run_ctx)
    assert summary.done == 2

    with run_ctx.db.connect() as conn:
        rows = conn.execute("SELECT item_id, stage, status FROM items;").fetchall()
        for r in rows:
            assert r["stage"] == "shape"
            assert r["status"] == "pending"


def test_judge_contrast_groups_join_new_and_multiple(
    run_ctx: RunContext,
    contrast_candidates_data: list[dict],
    contrast_library_data: list[dict],
    contrast_groups_data: list[dict],
    contrast_judge_mock_output: dict,
):
    """
    Issue #72 / SPEC §7.1, 3.13:
    - Join an existing group when candidate is confusable with its members and answers its question.
    - Else a new pair group.
    - A candidate may join several existing groups.
    - Shortlisted concept's same-course groups (name, question, members) are received in the prompt.
    """
    # Populate existing contrast_groups in database
    with run_ctx.db.connect() as conn:
        for cg in contrast_groups_data:
            conn.execute(
                """
                INSERT INTO contrast_groups (
                    group_id, course_key, name, question, member_ids_json, contrasts_json, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    cg["group_id"],
                    cg["course_key"],
                    cg["name"],
                    cg["question"],
                    cg["member_ids_json"],
                    cg["contrasts_json"],
                    cg["status"],
                ),
            )
        conn.commit()

    captured_requests: list[Any] = []
    mock_runner = MagicMock()

    def mock_run(req):
        captured_requests.append(req)
        return MagicMock(
            parsed=contrast_judge_mock_output,
            text=json.dumps(contrast_judge_mock_output),
        )

    mock_runner.run.side_effect = mock_run
    run_ctx.models = mock_runner

    # Shortlist map:
    # Cand 0 (selection_sort) -> lib.cs101.quicksort
    # Cand 1 (radix_sort) -> lib.cs101.quicksort
    # Cand 2 (avl_tree) -> lib.cs101.bst, lib.cs101.red_black
    shortlist = {
        0: [("lib.cs101.quicksort", 0.85)],
        1: [("lib.cs101.quicksort", 0.78)],
        2: [("lib.cs101.bst", 0.90), ("lib.cs101.red_black", 0.88)],
    }

    results = run_judge(
        candidates=contrast_candidates_data[:3],
        library=contrast_library_data,
        shortlist=shortlist,
        ctx=run_ctx,
    )

    assert len(results) == 3

    # Check that shortlisted concept groups were sent in prompt
    assert len(captured_requests) == 1
    prompt_text = captured_requests[0].parts[0].text
    assert "Contrast group: [cs101.g.sorting]" in prompt_text
    assert "Comparison Sorting Algorithms" in prompt_text
    assert "Contrast group: [cs101.g.balanced_trees]" in prompt_text
    assert "Contrast group: [cs101.g.tree_rotations]" in prompt_text

    # 1. Candidate 0 (selection_sort) joined existing group cs101.g.sorting
    with run_ctx.db.connect() as conn:
        sorting_row = conn.execute(
            "SELECT member_ids_json FROM contrast_groups WHERE group_id = 'cs101.g.sorting';"
        ).fetchone()
        sorting_members = json.loads(sorting_row["member_ids_json"])
        assert "cs101.selection_sort" in sorting_members
        assert "lib.cs101.quicksort" in sorting_members
        assert len(sorting_members) == 3

    # 2. Candidate 1 (radix_sort) created a new pair group
    with run_ctx.db.connect() as conn:
        all_groups = conn.execute("SELECT * FROM contrast_groups;").fetchall()
        # Find group with radix_sort
        radix_group = next(
            (g for g in all_groups if "cs101.radix_sort" in json.loads(g["member_ids_json"])),
            None,
        )
        assert radix_group is not None
        radix_members = json.loads(radix_group["member_ids_json"])
        assert "cs101.radix_sort" in radix_members
        assert "lib.cs101.quicksort" in radix_members
        assert len(radix_members) == 2

    # 3. Candidate 2 (avl_tree) joined several groups (balanced_trees and tree_rotations)
    with run_ctx.db.connect() as conn:
        bt_row = conn.execute(
            "SELECT member_ids_json FROM contrast_groups WHERE group_id = 'cs101.g.balanced_trees';"
        ).fetchone()
        bt_members = json.loads(bt_row["member_ids_json"])
        assert "cs101.avl_tree" in bt_members

        tr_row = conn.execute(
            "SELECT member_ids_json FROM contrast_groups WHERE group_id = 'cs101.g.tree_rotations';"
        ).fetchone()
        tr_members = json.loads(tr_row["member_ids_json"])
        assert "cs101.avl_tree" in tr_members


def test_judge_cross_course_confusable_is_note_only(
    run_ctx: RunContext,
    contrast_candidates_data: list[dict],
    contrast_library_data: list[dict],
):
    """
    Issue #72 / SPEC §7.1, 3.13:
    Cross-course confusable match stays note only (no contrast group created or modified).
    """
    # Candidate 3 is cs101.hash_map (course cs101)
    cand = contrast_candidates_data[3]
    assert cand["concept_id"] == "cs101.hash_map"

    # lib.cs382.hash_table is course cs382
    shortlist = {0: [("lib.cs382.hash_table", 0.88)]}

    mock_output = {
        "candidates": [
            {
                "candidate": 0,
                "matches": [
                    {
                        "id": "lib.cs382.hash_table",
                        "relation": "confusable",
                        "reason": "Both are hash table concepts from different courses.",
                        "adds": [],
                        "group": "new",
                    }
                ],
            }
        ]
    }

    mock_runner = MagicMock()
    mock_runner.run.return_value = MagicMock(parsed=mock_output, text=json.dumps(mock_output))
    run_ctx.models = mock_runner

    results = run_judge(
        candidates=[cand],
        library=contrast_library_data,
        shortlist=shortlist,
        ctx=run_ctx,
    )

    # Outcome has the match with reason (note preserved)
    assert len(results[0]["matches"]) == 1
    assert results[0]["matches"][0]["relation"] == "confusable"
    assert results[0]["matches"][0]["id"] == "lib.cs382.hash_table"

    # No contrast group created in database
    with run_ctx.db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM contrast_groups;").fetchone()[0]
        assert count == 0


def test_judge_tombstoned_pair_is_never_readded(
    run_ctx: RunContext,
    contrast_candidates_data: list[dict],
    contrast_library_data: list[dict],
):
    """
    Issue #72 / SPEC §7.1, 3.17:
    A tombstoned pair ('split the pair') is never re-added to contrast groups.
    """
    # Candidate 4 is cs101.bubble_sort
    cand = contrast_candidates_data[4]
    assert cand["concept_id"] == "cs101.bubble_sort"

    # Add tombstone for pair (cs101.bubble_sort, lib.cs101.insertion_sort)
    with run_ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO tombstones (kind, key, reason, created_at)
            VALUES ('pair', 'cs101.bubble_sort|lib.cs101.insertion_sort', 'Split the pair on weekly list', '2026-11-20T00:00:00Z');
            """
        )
        conn.commit()

    shortlist = {0: [("lib.cs101.insertion_sort", 0.90)]}

    mock_output = {
        "candidates": [
            {
                "candidate": 0,
                "matches": [
                    {
                        "id": "lib.cs101.insertion_sort",
                        "relation": "confusable",
                        "reason": "Both are elementary sorting algorithms.",
                        "adds": [],
                        "group": "new",
                    }
                ],
            }
        ]
    }

    mock_runner = MagicMock()
    mock_runner.run.return_value = MagicMock(parsed=mock_output, text=json.dumps(mock_output))
    run_ctx.models = mock_runner

    results = run_judge(
        candidates=[cand],
        library=contrast_library_data,
        shortlist=shortlist,
        ctx=run_ctx,
    )

    assert len(results[0]["matches"]) == 1

    # Contrast group must NOT be created for tombstoned pair
    with run_ctx.db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM contrast_groups;").fetchone()[0]
        assert count == 0


def test_judge_caller_provided_groups_updated_in_memory():
    """
    When groups are provided explicitly as a list of dicts without ctx.db,
    membership is updated and new groups are added to the list in-place.
    """
    cand = {
        "concept_id": "cs101.quick_sort",
        "course_key": "cs101",
        "title": "Quick Sort",
        "explanation": "Quicksort algorithm",
    }
    lib = [
        {
            "concept_id": "lib.cs101.merge_sort",
            "course_key": "cs101",
            "title": "Merge Sort",
            "explanation": "Mergesort algorithm",
        }
    ]
    groups = [
        {
            "group_id": "cs101.g.divide_conquer",
            "course_key": "cs101",
            "name": "Divide and Conquer",
            "question": "Which divide-and-conquer strategy sorts in-place?",
            "members": ["lib.cs101.merge_sort"],
        }
    ]

    shortlist = {0: [("lib.cs101.merge_sort", 0.9)]}
    mock_runner = MagicMock()
    mock_runner.run.return_value = MagicMock(
        parsed={
            "candidates": [
                {
                    "candidate": 0,
                    "matches": [
                        {
                            "id": "lib.cs101.merge_sort",
                            "relation": "confusable",
                            "reason": "Both are divide-and-conquer sorting methods.",
                            "adds": [],
                            "group": "group:cs101.g.divide_conquer",
                        }
                    ],
                }
            ]
        }
    )

    ctx = MagicMock(models=mock_runner, db=None)

    run_judge(
        candidates=[cand],
        library=lib,
        shortlist=shortlist,
        ctx=ctx,
        groups=groups,
    )

    assert "cs101.quick_sort" in groups[0]["members"]
