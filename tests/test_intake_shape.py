"""Tests for intake.shape package (§3.5, §3.6, §3.7, SPEC-requirements 2.32, 2.33, 2.34, 2.38, Issue #64)."""
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.pipeline import RunContext
from articulate.core.vault import Vault
from articulate.intake.shape import (
    calculate_free_recall_ratio,
    check_alias_leak,
    check_options_resolve,
    check_style_overlap,
    get_recipe,
    is_free_recall,
    regenerate,
    run_deterministic_checks,
    shape_course,
    step,
    validate_prompt_payload,
)


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "shape"


@pytest.fixture
def library_fixture_data(fixtures_dir: Path) -> dict:
    return json.loads((fixtures_dir / "library.json").read_text(encoding="utf-8"))


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test_shape.sqlite3")
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
    ctx = RunContext(
        run_id="01TESTSHAPE000000000000000",
        job="nightly",
        now=datetime(2026, 11, 20, 2, 0, 0, tzinfo=UTC),
        db=test_db,
        config=test_config,
        vault=vault,
        flags=flags,
    )
    return ctx


def test_recipe_table_counts_and_kinds():
    """Verify recipe table counts and prompt kinds per concept kind and tier (§2.32)."""
    # definition / fact
    assert get_recipe("definition", is_in_group=False, tier="core") == ["shortAnswer", "flashcard", "multipleChoice"]
    assert get_recipe("fact", is_in_group=False, tier="standard") == ["shortAnswer", "flashcard"]
    assert get_recipe("definition", is_in_group=False, tier="background") == ["flashcard"]
    assert get_recipe("definition", is_in_group=False, tier="out") == []

    # method in a group
    assert get_recipe("method", is_in_group=True, tier="core") == ["methodPick", "setUp", "shortAnswer"]
    assert get_recipe("method", is_in_group=True, tier="standard") == ["setUp", "shortAnswer"]
    assert get_recipe("method", is_in_group=True, tier="background") == ["shortAnswer"]
    assert get_recipe("method", is_in_group=True, tier="out") == []

    # method alone
    assert get_recipe("method", is_in_group=False, tier="core") == ["setUp", "shortAnswer", "multipleChoice"]
    assert get_recipe("method", is_in_group=False, tier="standard") == ["setUp", "shortAnswer"]
    assert get_recipe("method", is_in_group=False, tier="background") == ["shortAnswer"]
    assert get_recipe("method", is_in_group=False, tier="out") == []


def test_free_recall_ratio_per_recipe():
    """Verify that every tier recipe produces ≥½ free recall prompts (§2.32)."""
    # Free recall prompts: flashcard, shortAnswer, setUp
    assert is_free_recall("flashcard")
    assert is_free_recall("shortAnswer")
    assert is_free_recall("setUp")
    assert not is_free_recall("multipleChoice")
    assert not is_free_recall("methodPick")

    # Core definition: 2/3 = 66.7%
    assert calculate_free_recall_ratio(get_recipe("definition", is_in_group=False, tier="core")) >= 0.5
    # Standard definition: 2/2 = 100%
    assert calculate_free_recall_ratio(get_recipe("definition", is_in_group=False, tier="standard")) >= 0.5
    # Background definition: 1/1 = 100%
    assert calculate_free_recall_ratio(get_recipe("definition", is_in_group=False, tier="background")) >= 0.5

    # Core method in group: 2/3 = 66.7%
    assert calculate_free_recall_ratio(get_recipe("method", is_in_group=True, tier="core")) >= 0.5
    # Core method alone: 2/3 = 66.7%
    assert calculate_free_recall_ratio(get_recipe("method", is_in_group=False, tier="core")) >= 0.5


def test_deterministic_payload_validation():
    """Validate prompt payloads against v2 schema specifications (§2.32, §3.7)."""
    # Flashcard valid & invalid
    ok, err = validate_prompt_payload("flashcard", {"front": "Q", "back": "A"})
    assert ok and err is None
    ok, err = validate_prompt_payload("flashcard", {"front": "", "back": "A"})
    assert not ok and "front" in err

    # ShortAnswer valid & invalid
    ok, err = validate_prompt_payload("shortAnswer", {
        "question": "Explain mean",
        "modelAnswer": "Sum divided by count",
        "keyPoints": ["Sum", "Count"],
    })
    assert ok and err is None
    ok, err = validate_prompt_payload("shortAnswer", {"question": "Q", "modelAnswer": "A", "keyPoints": []})
    assert not ok and "keyPoints" in err

    # MultipleChoice valid & invalid
    ok, err = validate_prompt_payload("multipleChoice", {
        "question": "Which is central?",
        "options": ["Mean", "Variance"],
        "correctIndex": 0,
        "explanation": "Mean is centre.",
    })
    assert ok and err is None
    ok, err = validate_prompt_payload("multipleChoice", {
        "question": "Which is central?",
        "options": ["Mean"],
        "correctIndex": 0,
        "explanation": "Need at least 2 options.",
    })
    assert not ok and "options" in err

    # MethodPick valid & invalid
    ok, err = validate_prompt_payload("methodPick", {
        "statement": "Two paired groups measured before and after.",
        "targetConceptId": "stat101.paired-t-test",
        "explanation": "Paired observations require paired t-test.",
        "verifiedSiblingIds": ["stat101.independent-t-test"],
    })
    assert ok and err is None
    ok, err = validate_prompt_payload("methodPick", {
        "statement": "Two groups",
        "targetConceptId": "stat101.paired-t-test",
        "explanation": "Why",
        "verifiedSiblingIds": [],
    })
    assert not ok and "verifiedSiblingIds" in err

    # SetUp valid & invalid
    ok, err = validate_prompt_payload("setUp", {
        "statement": "Investigate difference between means.",
        "targetConceptId": "stat101.paired-t-test",
        "modelSetUp": "H0: mu_d = 0, Ha: mu_d != 0",
        "keyPoints": ["State hypotheses", "Compute d"],
    })
    assert ok and err is None


def test_deterministic_options_resolve():
    """Verify that methodPick sibling and target IDs resolve to real concepts (§2.32)."""
    valid_ids = {"stat101.paired-t-test", "stat101.independent-t-test"}

    # Valid resolution
    ok, err = check_options_resolve("methodPick", {
        "targetConceptId": "stat101.paired-t-test",
        "verifiedSiblingIds": ["stat101.independent-t-test"],
    }, valid_ids)
    assert ok and err is None

    # Invalid target
    ok, err = check_options_resolve("methodPick", {
        "targetConceptId": "stat101.unknown",
        "verifiedSiblingIds": ["stat101.independent-t-test"],
    }, valid_ids)
    assert not ok and "unknown" in err

    # Invalid sibling
    ok, err = check_options_resolve("methodPick", {
        "targetConceptId": "stat101.paired-t-test",
        "verifiedSiblingIds": ["stat101.hallucinated"],
    }, valid_ids)
    assert not ok and "hallucinated" in err


def test_deterministic_alias_leak():
    """Ensure no alias leaks into multipleChoice distractors or methodPick statements (§2.32)."""
    concept_title = "Mean"
    aliases = ["average", "arithmetic mean"]

    # Multiple choice with alias distractor -> rejected
    ok, err = check_alias_leak("multipleChoice", {
        "question": "Identify the measure.",
        "options": ["Median", "Average", "Mode"],
        "correctIndex": 0,
    }, concept_title, aliases)
    assert not ok and "Average" in err

    # Multiple choice with clean distractors -> allowed
    ok, err = check_alias_leak("multipleChoice", {
        "question": "Identify the measure.",
        "options": ["Median", "Mode", "Variance"],
        "correctIndex": 0,
    }, concept_title, aliases)
    assert ok and err is None

    # Method pick leaking alias in statement -> rejected
    ok, err = check_alias_leak("methodPick", {
        "statement": "When calculating the average of the group, which formula applies?",
    }, concept_title, aliases)
    assert not ok and "average" in err


def test_style_example_overlap_rejection():
    """Reject prompts that share an 8+ word run with a style example (§2.33)."""
    style_examples = [
        "A coach records the sprint times of the same ten runners before and after a new training plan and wants to know whether the plan helped.",
        "Explain in your own words what a confidence interval tells a reader about the population value.",
    ]

    # Exactly 8 consecutive words from example 1:
    # "times of the same ten runners before and after" (9 words)
    overlapping_prompt = "In an experiment, we measure times of the same ten runners before and after the season ends."
    ok, err = check_style_overlap(overlapping_prompt, style_examples)
    assert not ok
    assert "8+ word run" in err

    # Less than 8 words overlap (e.g. 5 words) -> passes
    clean_prompt = "A coach records sprint times for twelve runners in a school."
    ok, err = check_style_overlap(clean_prompt, style_examples)
    assert ok and err is None


def test_shape_course_fixtures(run_ctx: RunContext, library_fixture_data: dict):
    """
    Test full course prompt shaping against committed fixture library.json (§2.32, §2.33, §2.34).
    Verifies per-tier counts, free recall ratio, and held concept exclusion.
    """
    db = run_ctx.db
    now_str = run_ctx.now.isoformat()

    # Seed concepts, contrast groups, and guide items into DB
    with db.connect() as conn:
        for c in library_fixture_data["concepts"]:
            conn.execute(
                """
                INSERT INTO concepts (
                    concept_id, course_key, unit_id, kind, title, aliases_json,
                    details_json, tier, status, hold_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    c["concept_id"],
                    c["course_key"],
                    c.get("unit_id"),
                    c["kind"],
                    c["title"],
                    json.dumps(c.get("aliases", [])),
                    json.dumps(c.get("details", {})),
                    c["tier"],
                    c["status"],
                    c.get("hold_reason"),
                    now_str,
                    now_str,
                ),
            )

        for cg in library_fixture_data.get("contrast_groups", []):
            conn.execute(
                """
                INSERT INTO contrast_groups (
                    group_id, course_key, name, question, member_ids_json, contrasts_json, status
                ) VALUES (?, ?, ?, ?, ?, '[]', ?);
                """,
                (
                    cg["group_id"],
                    cg["course_key"],
                    cg["name"],
                    cg.get("question"),
                    json.dumps(cg.get("member_ids", [])),
                    cg.get("status", "active"),
                ),
            )

        for sf in library_fixture_data.get("source_files", []):
            conn.execute(
                """
                INSERT INTO source_files (file_id, course_key, slot, display_name, created_at)
                VALUES (?, ?, 'class_file', ?, ?);
                """,
                (sf["file_id"], sf["course_key"], sf["display_name"], sf["created_at"]),
            )

        for gi in library_fixture_data.get("guide_items", []):
            conn.execute(
                """
                INSERT INTO guide_items (file_id, position, number, kind, page, quote, verified)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (gi["file_id"], gi["position"], gi["number"], gi["kind"], gi["page"], gi["quote"], gi["verified"]),
            )
        conn.commit()

    # Run shape_course for stat101
    total = shape_course("stat101", run_ctx)
    assert total > 0

    with db.connect() as conn:
        prompts = conn.execute("SELECT * FROM prompts;").fetchall()
        p_by_concept: dict[str, list] = {}
        all_kinds: list[str] = []
        for p in prompts:
            cid = p["concept_id"]
            p_by_concept.setdefault(cid, []).append(p)
            all_kinds.append(p["kind"])
            # All generated prompts must be active
            assert p["status"] == "active"
            assert p["prompt_id"].startswith(f"{cid}.")

        # 1. stat101.mean (definition, core) -> 3 prompts: shortAnswer, flashcard, multipleChoice
        assert len(p_by_concept["stat101.mean"]) == 3
        mean_kinds = [p["kind"] for p in p_by_concept["stat101.mean"]]
        assert set(mean_kinds) == {"shortAnswer", "flashcard", "multipleChoice"}

        # 2. stat101.standard-error (fact, standard) -> 2 prompts: shortAnswer, flashcard
        assert len(p_by_concept["stat101.standard-error"]) == 2
        se_kinds = [p["kind"] for p in p_by_concept["stat101.standard-error"]]
        assert set(se_kinds) == {"shortAnswer", "flashcard"}

        # 3. stat101.sample (definition, background) -> 1 prompt: flashcard
        assert len(p_by_concept["stat101.sample"]) == 1
        assert p_by_concept["stat101.sample"][0]["kind"] == "flashcard"

        # 4. stat101.course-logistics (tier: out) -> 0 prompts
        assert "stat101.course-logistics" not in p_by_concept

        # 5. stat101.paired-t-test (method in group, core) -> 3 prompts: methodPick, setUp, shortAnswer
        assert len(p_by_concept["stat101.paired-t-test"]) == 3
        paired_kinds = [p["kind"] for p in p_by_concept["stat101.paired-t-test"]]
        assert set(paired_kinds) == {"methodPick", "setUp", "shortAnswer"}

        # 6. stat101.independent-t-test (method in group, standard) -> 2 prompts: setUp, shortAnswer
        assert len(p_by_concept["stat101.independent-t-test"]) == 2
        ind_kinds = [p["kind"] for p in p_by_concept["stat101.independent-t-test"]]
        assert set(ind_kinds) == {"setUp", "shortAnswer"}

        # 7. stat101.chi-square-test (method alone, core) -> 3 prompts: setUp, shortAnswer, multipleChoice
        assert len(p_by_concept["stat101.chi-square-test"]) == 3
        chi_kinds = [p["kind"] for p in p_by_concept["stat101.chi-square-test"]]
        assert set(chi_kinds) == {"setUp", "shortAnswer", "multipleChoice"}

        # 8. Held concepts (§2.34): gap_fill and weekly_review must have 0 prompts!
        assert "stat101.gap-fill-median" not in p_by_concept
        assert "stat101.merge-candidate-mode" not in p_by_concept

        # Verify free recall ratio for the entire course is ≥ 50% (§2.32)
        ratio = calculate_free_recall_ratio(all_kinds)
        assert ratio >= 0.5, f"Expected free recall ratio >= 0.5, got {ratio}"

        # Verify concepts that finished shape are now status = 'active' and hold_reason IS NULL
        mean_row = conn.execute("SELECT status, hold_reason FROM concepts WHERE concept_id = 'stat101.mean';").fetchone()
        assert mean_row["status"] == "active"
        assert mean_row["hold_reason"] is None


def test_shape_step_pipeline_advance(run_ctx: RunContext, library_fixture_data: dict):
    """Test intake.shape pipeline step advances item to 'export' (§3.5)."""
    db = run_ctx.db
    now_str = run_ctx.now.isoformat()

    # Seed concepts and an item in stage 'shape'
    with db.connect() as conn:
        for c in library_fixture_data["concepts"][:2]:
            conn.execute(
                """
                INSERT INTO concepts (concept_id, course_key, kind, title, aliases_json, details_json, tier, status, hold_reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, '[]', ?, ?, 'held', 'shape', ?, ?);
                """,
                (c["concept_id"], c["course_key"], c["kind"], c["title"], json.dumps(c.get("details", {})), c["tier"], now_str, now_str),
            )

        conn.execute(
            """
            INSERT INTO items (
                item_id, source, kind, native_id, version, course_key, privacy, stage, status, created_at, updated_at
            ) VALUES ('item-shape-1', 'canvas', 'slides', 'deck1', 'v1', 'stat101', 'class', 'shape', 'pending', ?, ?);
            """,
            (now_str, now_str),
        )
        conn.commit()

    summary = step(run_ctx)
    assert summary.done == 1
    assert summary.failed == 0

    with db.connect() as conn:
        item = conn.execute("SELECT stage, status FROM items WHERE item_id = 'item-shape-1';").fetchone()
        assert item["stage"] == "export"
        assert item["status"] == "pending"


def test_regenerate_flagged_prompt(run_ctx: RunContext):
    """Test regenerating a flagged prompt against cited source (§2.38, Issue #64, #65)."""
    db = run_ctx.db
    now_str = run_ctx.now.isoformat()

    concept_id = "stat101.mean"
    course_key = "stat101"
    old_prompt_id = "stat101.mean.flashcard.1"

    # Seed pages table for citation check
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
            VALUES ('f.0000000000000001', 2, 'hash1', 'The mean adds every value and divides by the count.', 0, '', '');
            """
        )
        conn.execute(
            """
            INSERT INTO concepts (concept_id, course_key, kind, title, aliases_json, details_json, tier, status, created_at, updated_at)
            VALUES (?, ?, 'definition', 'Mean', '["average"]', ?, 'core', 'active', ?, ?);
            """,
            (
                concept_id,
                course_key,
                json.dumps({
                    "explanationCitations": [
                        {"sourceFileId": "f.0000000000000001", "page": 2, "quote": "The mean adds every value and divides by the count."}
                    ]
                }),
                now_str,
                now_str,
            ),
        )
        conn.execute(
            """
            INSERT INTO prompts (prompt_id, concept_id, kind, content_json, status, generated_by, prompt_version, created_at)
            VALUES (?, ?, 'flashcard', ?, 'active', 'gemini-3.5-flash-lite', 'v1', ?);
            """,
            (
                old_prompt_id,
                concept_id,
                json.dumps({"front": "What is the mean?", "back": "The average of values."}),
                now_str,
            ),
        )
        conn.commit()

    # Regenerate with suggested fix
    res = regenerate(
        old_prompt_id,
        note="Too vague",
        suggested_fix="The sum of all values divided by how many values there are.",
        ctx=run_ctx,
    )

    assert res.ok
    assert res.new_prompt_id == "stat101.mean.flashcard.2"

    with db.connect() as conn:
        new_row = conn.execute("SELECT * FROM prompts WHERE prompt_id = ?;", (res.new_prompt_id,)).fetchone()
        assert new_row is not None
        assert new_row["status"] == "active"
        content = json.loads(new_row["content_json"])
        assert content["back"] == "The sum of all values divided by how many values there are."


def test_regenerate_citation_failure(run_ctx: RunContext):
    """Test that regenerate returns ok=False if the citation check fails (§2.38)."""
    db = run_ctx.db
    now_str = run_ctx.now.isoformat()

    concept_id = "stat101.bogus"
    old_prompt_id = "stat101.bogus.flashcard.1"

    # Seed pages table with different text so quote check fails
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pages (file_id, page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text)
            VALUES ('f.bogus', 1, 'hashbogus', 'Totally different text here.', 0, '', '');
            """
        )
        conn.execute(
            """
            INSERT INTO concepts (concept_id, course_key, kind, title, aliases_json, details_json, tier, status, created_at, updated_at)
            VALUES (?, 'stat101', 'definition', 'Bogus', '[]', ?, 'core', 'active', ?, ?);
            """,
            (
                concept_id,
                json.dumps({
                    "explanationCitations": [
                        {"sourceFileId": "f.bogus", "page": 1, "quote": "Quote that does not exist on page."}
                    ]
                }),
                now_str,
                now_str,
            ),
        )
        conn.execute(
            """
            INSERT INTO prompts (prompt_id, concept_id, kind, content_json, status, generated_by, prompt_version, created_at)
            VALUES (?, ?, 'flashcard', '{"front": "F", "back": "B"}', 'active', 'test', 'v1', ?);
            """,
            (old_prompt_id, concept_id, now_str),
        )
        conn.commit()

    res = regenerate(
        old_prompt_id,
        note="Wrong citation",
        suggested_fix="Try to fix",
        ctx=run_ctx,
    )
    assert not res.ok
    assert "Citation check failed" in res.detail
