"""Tests for intake.extract: schema, code checks, long-deck split and merge, the stage (#61)."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pypdfium2 as pdfium
import pytest

from articulate.core.config import Config, ModelsConfig
from articulate.core.db import Database
from articulate.core.models.runner import DefaultModelRunner, Held, ModelResult
from articulate.core.pipeline import Item, Privacy, RunContext
from articulate.intake.extract import checks, split
from articulate.intake.extract.run import (
    ExtractFailed,
    extract_deck,
    extract_item,
    load_prompt,
    step,
)
from articulate.intake.extract.schema import PATTERN, SCHEMA

FIX = Path(__file__).parent / "fixtures" / "extract"
GEMINI_FIX = Path(__file__).parent / "fixtures" / "gemini"


def load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def make_pdf(path: Path, n: int) -> Path:
    pdf = pdfium.PdfDocument.new()
    for _ in range(n):
        pdf.new_page(200, 200)
    pdf.save(str(path))
    return path


def page_count(path: Path) -> int:
    return len(pdfium.PdfDocument(str(path)))


class FakeRunner:
    """Returns queued outputs in order and records every request (page count, prompt text)."""

    def __init__(self, outputs: list):
        self.outputs = list(outputs)
        self.calls: list[dict] = []

    def run(self, req):
        pdf = next(p.pdf for p in req.parts if p.pdf)
        text = "".join(p.text or "" for p in req.parts)
        self.calls.append(
            {
                "step": req.step,
                "pages": page_count(pdf),
                "text": text,
                "material": req.material,
                "schema": req.json_schema,
            }
        )
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        if out in ("MAX_TOKENS", "schema_rejected"):
            return ModelResult(model="gemini-3.8-flash", provider="gemini", finish_reason=out)
        return ModelResult(
            model="gemini-3.8-flash",
            provider="gemini",
            finish_reason="STOP",
            parsed=copy.deepcopy(out),
            text=json.dumps(out),
        )

    def remaining(self, model: str) -> int:
        return 20


EMPTY = {"concepts": [], "contrastGroups": []}


# ---------------------------------------------------------------- schema (#22)


def _walk(node, path="$"):
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def test_schema_all_required_pattern_no_anyof_or_min_items():
    objects = 0
    for path, node in _walk(SCHEMA):
        if not isinstance(node, dict):
            continue
        for banned in ("anyOf", "oneOf", "allOf", "minItems", "maxItems"):
            assert banned not in node, f"{banned} at {path}"
        if node.get("type") == "object":
            objects += 1
            assert set(node["required"]) == set(node["properties"]), path
            assert node["additionalProperties"] is False, path
        if node.get("type") == "string" and "enum" not in node:
            assert node.get("pattern") == PATTERN, path
    assert objects >= 7
    assert PATTERN == "^[^\\t\\x08\\x0c]*$"


def test_schema_shape_matches_22():
    concept = SCHEMA["properties"]["concepts"]["items"]["properties"]
    assert list(SCHEMA["properties"]) == ["concepts", "contrastGroups"]
    assert list(concept) == [
        "key",
        "title",
        "kind",
        "explanation",
        "aliases",
        "useWhen",
        "conditions",
        "workedExamples",
        "slideHints",
        "confidence",
        "confidenceNote",
    ]
    claim = concept["explanation"]["properties"]
    assert list(claim) == [
        "citations",
        "text",
        "gapFill",
    ]  # citations first: the model quotes before it writes
    assert concept["kind"]["enum"] == ["definition", "fact", "method"]
    assert concept["confidence"]["enum"] == ["high", "medium", "low"]
    hint = concept["slideHints"]["items"]["properties"]
    assert hint["kind"]["enum"] == ["examHint", "dismissal", "emphasis"]
    group = SCHEMA["properties"]["contrastGroups"]["items"]["properties"]
    assert list(group) == ["name", "question", "memberKeys", "contrasts"]


def test_prompt_has_named_concepts_line():
    prompt = load_prompt()
    assert (
        "Named laws, acts, regulations, standards, organisations, agencies and frameworks" in prompt
    )
    assert prompt.index("Named laws") < prompt.index("One concept per idea")


# ---------------------------------------------------------------- code checks (2.19)


def test_checks_each_rule():
    out, report = checks.check_output(load("checks"), n_pages=5)
    by_key = {c["key"]: c for c in out["concepts"]}

    # unbalanced $, stray \\, control char, cited explanation without citations: concept rejected
    for key in ("bad-dollar", "stray", "tabbed", "no-cite"):
        assert key not in by_key
        assert any(key in r for r in report.rejected), key
    assert any("unbalanced $" in r for r in report.rejected)
    assert any("stray" in r and "\\\\" in r for r in report.rejected)
    assert any("control char" in r for r in report.rejected)

    # 1-3 citations: extras cut and logged
    assert len(by_key["stack"]["explanation"]["citations"]) == 3
    assert any("stack" in line and "extra citation" in line for line in report.logged)

    # method fields only on methods
    assert by_key["queue"]["useWhen"] == [] and by_key["queue"]["aliases"] == []
    assert any("queue" in r and "method fields" in r for r in report.rejected)

    # cited claim with no citation, gapFill with citations, page out of range: claim rejected
    use_when = by_key["hashing"]["useWhen"]
    assert [c["text"] for c in use_when] == ["Constant-time lookup is needed."]
    assert any("no citation" in r for r in report.rejected)
    assert any("gapFill claim has citations" in r for r in report.rejected)
    assert any("page 99 out of range" in r for r in report.rejected)
    # gapFill without citations is fine
    assert by_key["hashing"]["conditions"][0]["gapFill"] is True

    # low confidence without a note is logged, not rejected
    assert any("hashing" in line and "confidence" in line for line in report.logged)


def test_checks_groups_and_uncontrasted_pairs_logged_not_flagged():
    out, report = checks.check_output(load("checks"), n_pages=5)
    groups = {g["name"]: g for g in out["contrastGroups"]}

    # unknown member key dropped, and the contrast that used it
    lin = groups["Linear structures"]
    assert lin["memberKeys"] == ["stack", "queue"]
    assert [(c["a"], c["b"]) for c in lin["contrasts"]] == [("stack", "queue")]
    assert any("ghost" in r for r in report.rejected)

    # a group left with fewer than 2 members is dropped
    assert "Lost member" not in groups

    # gap-fill contrasts are held back (#19); uncontrasted pairs are logged, never flagged
    three = groups["Three way"]
    assert [(c["a"], c["b"]) for c in three["contrasts"]] == [("stack", "queue")]
    assert any("gapFill contrast" in r for r in report.rejected)
    pairs = [line for line in report.logged if "without a contrast" in line]
    assert len(pairs) == 2
    assert report.uncontrasted_pairs == 2


def test_checks_clean_output_has_no_problems():
    out, report = checks.check_output(load("part1"), n_pages=27)
    assert report.rejected == [r for r in report.rejected if "heap-sort" in r]
    assert len(out["concepts"]) == 3


# ---------------------------------------------------------------- split and merge (2.20)


@pytest.mark.parametrize(
    ("n", "ranges"),
    [
        (5, [(1, 5)]),
        (40, [(1, 40)]),
        (41, [(1, 21), (22, 41)]),
        (46, [(1, 23), (24, 46)]),
        (54, [(1, 27), (28, 54)]),
        (61, [(1, 21), (22, 41), (42, 61)]),
    ],
)
def test_plan_ranges(n, ranges):
    assert split.plan_ranges(n) == ranges


def test_halve():
    assert split.halve((28, 54)) == [(28, 41), (42, 54)]
    assert split.halve((1, 5)) == [(1, 3), (4, 5)]


def test_range_note():
    note = split.range_note(28, 54, 54)
    assert "pages 28–54 of a 54-page deck" in note
    assert "within this PDF" in note


def test_sub_pdf(tmp_path):
    src = make_pdf(tmp_path / "deck.pdf", 54)
    dst = split.sub_pdf(src, 28, 54, tmp_path / "part.pdf")
    assert page_count(dst) == 27


def test_remap_moves_every_page():
    out = split.remap(load("part2"), first_page=28)
    c = out["concepts"][0]
    assert c["explanation"]["citations"][0]["page"] == 29
    assert c["useWhen"][0]["citations"][0]["page"] == 30
    g = out["contrastGroups"][0]
    assert g["contrasts"][0]["decidingFeature"]["citations"][0]["page"] == 31


def test_merge_same_title_renames_and_dedupes_groups():
    p1, _ = checks.check_output(load("part1"), n_pages=27)
    p2, _ = checks.check_output(load("part2"), n_pages=27)
    merged, stats = split.merge([split.remap(p1, 1), split.remap(p2, 28)])
    titles = [c["title"] for c in merged["concepts"]]
    keys = [c["key"] for c in merged["concepts"]]

    # same normalised title merges: claims and aliases appended, the first explanation kept
    assert titles.count("Merge Sort") == 1 and "Merge sort" not in titles
    ms = next(c for c in merged["concepts"] if c["key"] == "merge-sort")
    assert [u["text"] for u in ms["useWhen"]] == [
        "A stable sort is required.",
        "Input is a linked list.",
    ]
    assert ms["aliases"] == ["top-down merge"]
    assert ms["explanation"]["text"] == "Split, sort halves, merge."
    assert stats["merged"] == 2

    # a key reused by a different concept in part 2 is renamed
    assert len(keys) == len(set(keys))
    bst = next(c for c in merged["concepts"] if c["title"] == "Binary Search Tree")
    assert bst["key"] != "binary-search"
    assert stats["renamed"] == 1

    # groups dedupe by member set after renames; contrasts of the duplicate join the kept group
    groups = merged["contrastGroups"]
    sorting = [g for g in groups if set(g["memberKeys"]) == {"merge-sort", "quick-sort"}]
    assert len(sorting) == 1
    assert len(sorting[0]["contrasts"]) == 1  # same pair, kept once
    assert stats["groups_deduped"] == 1

    # no group spans the cut: part 1's group naming part 2's heap-sort never survives
    assert not any(g["name"] == "Across the cut" for g in groups)
    trees = next(g for g in groups if g["name"] == "Trees vs heaps")
    assert set(trees["memberKeys"]) == {bst["key"], "heap-sort"}


# ---------------------------------------------------------------- the call(s)


def test_short_deck_is_one_call_without_note(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", 5)
    runner = FakeRunner([load("part1")])
    result = extract_deck(pdf, models=runner, work=tmp_path / "w", material=Privacy.CLASS)
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["step"] == "extract" and call["pages"] == 5 and call["schema"] == SCHEMA
    assert "of a 5-page deck" not in call["text"]
    assert "Named laws" in call["text"]
    assert result.calls == 1 and result.ranges == [(1, 5)]


def test_54_page_deck_splits_remaps_and_merges(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", 54)
    runner = FakeRunner([load("part1"), load("part2")])
    result = extract_deck(pdf, models=runner, work=tmp_path / "w", material=Privacy.CLASS)
    assert [c["pages"] for c in runner.calls] == [27, 27]
    assert "pages 1–27 of a 54-page deck" in runner.calls[0]["text"]
    assert "pages 28–54 of a 54-page deck" in runner.calls[1]["text"]
    assert result.ranges == [(1, 27), (28, 54)]
    heap = next(c for c in result.output["concepts"] if c["key"] == "heap-sort")
    assert heap["explanation"]["citations"][0]["page"] == 33
    assert result.stats["merged"] == 2


def test_max_tokens_splits_a_range_again_once(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", 54)
    runner = FakeRunner([load("part1"), "MAX_TOKENS", EMPTY, load("part2")])
    result = extract_deck(pdf, models=runner, work=tmp_path / "w", material=Privacy.CLASS)
    assert [c["pages"] for c in runner.calls] == [27, 27, 14, 13]
    assert "pages 28–41 of a 54-page deck" in runner.calls[2]["text"]
    assert "pages 42–54 of a 54-page deck" in runner.calls[3]["text"]
    assert result.ranges == [(1, 27), (28, 41), (42, 54)]
    heap = next(c for c in result.output["concepts"] if c["key"] == "heap-sort")
    assert heap["explanation"]["citations"][0]["page"] == 47  # local p6 of the 42–54 range
    assert any("re-split" in line for line in result.logged)


def test_schema_400_on_a_short_deck_splits_once(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", 10)
    runner = FakeRunner(["schema_rejected", EMPTY, EMPTY])
    result = extract_deck(pdf, models=runner, work=tmp_path / "w", material=Privacy.CLASS)
    assert [c["pages"] for c in runner.calls] == [10, 5, 5]
    assert result.ranges == [(1, 5), (6, 10)]


def test_second_max_tokens_fails(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", 10)
    runner = FakeRunner(["MAX_TOKENS", "MAX_TOKENS"])
    with pytest.raises(ExtractFailed, match="MAX_TOKENS"):
        extract_deck(pdf, models=runner, work=tmp_path / "w", material=Privacy.CLASS)
    assert len(runner.calls) == 2


def test_held_propagates(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", 5)
    with pytest.raises(Held):
        extract_deck(
            pdf, models=FakeRunner([Held("quota")]), work=tmp_path / "w", material=Privacy.CLASS
        )


# ---------------------------------------------------------------- raw tab → next model (#36)


def test_raw_tab_from_tau_goes_to_next_model(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.sqlite3")
    db.migrate()
    raw_tab = json.loads((GEMINI_FIX / "raw-tab.json").read_text())
    good = {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(load("part1"))}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {},
    }
    seen: list[str] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def post(self, url, headers=None, json=None):
            model = url.rsplit("/", 1)[1].split(":")[0]
            seen.append(model)
            body = raw_tab if model == "gemini-3.8-flash" else good
            return httpx.Response(200, json=body)

        def close(self):
            pass

    import articulate.core.models.gemini as gem

    monkeypatch.setattr(gem.httpx, "Client", FakeClient)
    monkeypatch.setattr(gem, "_get_gemini_api_key", lambda config=None: "k")
    runner = DefaultModelRunner(
        db, Config(courses=None, models=ModelsConfig(), articulate=None), pace=False
    )
    pdf = make_pdf(tmp_path / "deck.pdf", 5)
    result = extract_deck(pdf, models=runner, work=tmp_path / "w", material=Privacy.CLASS)
    assert seen == ["gemini-3.8-flash", "gemini-3.6-flash"]
    assert result.models == ["gemini-3.6-flash"]
    assert len(result.output["concepts"]) == 3


# ---------------------------------------------------------------- the stage


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    db = Database(tmp_path / "t.sqlite3")
    db.migrate()
    return RunContext(
        run_id="01TESTRUN0000000000000000",
        job="nightly",
        now=datetime(2026, 11, 20, 2, 0, tzinfo=UTC),
        db=db,
        config=Config(courses=None, models=ModelsConfig(), articulate=None),
    )


def _seed_file(ctx, tmp_path, n_pages: int, file_id: str = "a" * 64, layer: dict | None = None):
    pdf = make_pdf(tmp_path / f"{file_id}.pdf", n_pages)
    with ctx.db.connect() as conn:
        conn.execute(
            "INSERT INTO source_files (file_id, course_key, origin, slot, role, page_count, archive_path) "
            "VALUES (?, 'csc101', 'canvas', 'slot-1', 'slides', ?, ?)",
            (file_id, n_pages, str(pdf)),
        )
        for p in range(1, n_pages + 1):
            conn.execute(
                "INSERT INTO pages (file_id, page, page_hash, layer_text) VALUES (?, ?, '', ?)",
                (file_id, p, (layer or {}).get(p, "")),
            )
        conn.execute(
            "INSERT INTO items (item_id, source, kind, native_id, version, course_key, privacy, stage, status, "
            "created_at, updated_at) VALUES ('canvas:slot-1:x', 'canvas', 'slides', 'slot-1', ?, 'csc101', "
            "'class', 'extract', 'pending', 't', 't')",
            (file_id,),
        )
        conn.commit()
    return Item(
        item_id="canvas:slot-1:x",
        source="canvas",
        kind="slides",
        native_id="slot-1",
        version=file_id,
        course_key="csc101",
        privacy=Privacy.CLASS,
    )


def test_extract_item_writes_held_candidates(ctx, tmp_path):
    item = _seed_file(
        ctx,
        tmp_path,
        54,
        layer={3: "We halve the interval each step.", 10: "Then merge the sorted halves."},
    )
    with ctx.db.connect() as conn:  # an older concept already holds the plain slug
        conn.execute(
            "INSERT INTO concepts (concept_id, course_key, title, status) "
            "VALUES ('csc101.binary-search', 'csc101', 'Binary Search', 'active')"
        )
        conn.commit()
    ctx.models = FakeRunner([load("part1"), load("part2")])

    res = extract_item(item, ctx)
    assert res.outcome == "advance" and res.next_stage == "library"

    with ctx.db.connect() as conn:
        rows = {
            r["title"]: dict(r)
            for r in conn.execute("SELECT * FROM concepts WHERE status = 'held'")
        }
        groups = [dict(r) for r in conn.execute("SELECT * FROM contrast_groups")]
    assert set(rows) == {
        "Binary Search",
        "Merge Sort",
        "Quick Sort",
        "Binary Search Tree",
        "Heap Sort",
    }
    assert all(r["hold_reason"] == "library" and r["course_key"] == "csc101" for r in rows.values())
    assert rows["Binary Search"]["concept_id"] == "csc101.binary-search-2"
    assert rows["Merge Sort"]["concept_id"] == "csc101.merge-sort"
    assert rows["Merge Sort"]["kind"] == "method"
    assert rows["Merge Sort"]["confidence"] == "high"

    details = json.loads(rows["Binary Search"]["details_json"])
    cit = details["explanationCitations"][0]
    assert cit == {
        "sourceFileId": "f." + "a" * 16,
        "page": 3,
        "startSec": None,
        "endSec": None,
        "quote": "halve the interval",
        "status": "verified",
        "verifiedBy": "text",
    }
    assert details["explanation"] == "Search a sorted list by halving it."

    ms = json.loads(rows["Merge Sort"]["details_json"])
    assert ms["useWhen"][0]["provenance"] == "cited"
    assert ms["useWhen"][0]["citations"][0]["status"] == "unverified"  # page 11 has no such text
    assert ms["slideHints"][0]["kind"] == "examHint"
    assert json.loads(rows["Quick Sort"]["aliases_json"]) == ["partition-exchange sort"]

    sorting = next(g for g in groups if g["name"] == "Sorting choice")
    assert sorting["group_id"] == "csc101.g.sorting-choice"
    assert sorting["status"] == "held"
    assert json.loads(sorting["member_ids_json"]) == ["csc101.merge-sort", "csc101.quick-sort"]
    pair = json.loads(sorting["contrasts_json"])[0]
    assert pair["conceptA"] == "csc101.merge-sort" and pair["conceptB"] == "csc101.quick-sort"
    assert pair["decidingFeature"]["provenance"] == "cited"


def test_extract_item_holds_on_quota(ctx, tmp_path):
    item = _seed_file(ctx, tmp_path, 5)
    ctx.models = FakeRunner([Held("quota")])
    res = extract_item(item, ctx)
    assert res.outcome == "hold" and res.hold_reason == "quota"
    with ctx.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 0


def test_extract_item_fails_without_pdf(ctx, tmp_path):
    item = _seed_file(ctx, tmp_path, 5)
    with ctx.db.connect() as conn:
        conn.execute("UPDATE source_files SET archive_path = '/nonexistent/x.pptx'")
        conn.commit()
    ctx.models = FakeRunner([])
    res = extract_item(item, ctx)
    assert res.outcome == "fail" and "PDF" in res.detail


def test_step_advances_items_to_library(ctx, tmp_path):
    _seed_file(ctx, tmp_path, 5)
    ctx.models = FakeRunner([load("part1")])
    summary = step(ctx)
    assert summary.done == 1
    with ctx.db.connect() as conn:
        row = conn.execute("SELECT stage, status FROM items").fetchone()
        events = [r["event"] for r in conn.execute("SELECT event FROM item_events")]
    assert (row["stage"], row["status"]) == ("library", "pending")
    assert events == ["started", "advance"]


def test_step_rerun_does_not_duplicate(ctx, tmp_path):
    item = _seed_file(ctx, tmp_path, 5)
    ctx.models = FakeRunner([load("part1"), load("part1")])
    extract_item(item, ctx)
    extract_item(item, ctx)
    with ctx.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM contrast_groups").fetchone()[0] == 1


def test_registry_line():
    from articulate.registry import STEPS

    s = next(s for s in STEPS if s.name == "intake.extract")
    assert s.entry == "articulate.intake.extract:step"
    assert s.jobs == {"nightly", "pass2"}
    assert s.order == 260


# ---------------------------------------------------------------- real acceptance check (2.26)


@pytest.mark.real
def test_real_extract_acceptance(tmp_path):
    """Acceptance check 2.26: PHY-203 Ch. 9 core recall >= 11/15 against gold list."""
    now = datetime.now()
    current_time = now.time()
    t_start = datetime.strptime("01:45", "%H:%M").time()
    t_end = datetime.strptime("04:00", "%H:%M").time()
    if t_start <= current_time <= t_end:
        pytest.skip("Real checks blocked between 01:45 and 04:00")

    real_dir = Path.home() / "Library/Application Support/articulate/fixtures/real"
    deck = real_dir / "phy203-ch09.pdf"
    if not deck.exists():
        pytest.skip(
            "Real fixture files not present in ~/Library/Application Support/articulate/fixtures/real/"
        )

    gold_path = Path(__file__).parent / "fixtures" / "gold" / "phy203-ch09.json"
    if not gold_path.exists():
        pytest.skip("Gold file not found")

    gold = json.loads(gold_path.read_text(encoding="utf-8"))["items"]

    import re
    import time

    lock_path = Path("/tmp/articulate-real.lock")
    acquired = False
    for _ in range(60):
        try:
            lock_path.mkdir(exist_ok=False)
            acquired = True
            break
        except FileExistsError:
            time.sleep(2)

    if not acquired:
        pytest.fail("Could not acquire /tmp/articulate-real.lock")

    try:
        db = Database(tmp_path / "t.sqlite3")
        db.migrate()
        models_toml = Path(__file__).parent / "fixtures" / "core" / "models_valid.toml"
        from articulate.core.config import Config, load_models_config

        models_cfg = load_models_config(models_toml)
        models_cfg.chains["extract"] = ["gemini-3.8-flash", "gemini-3.6-flash"]
        cfg = Config(models=models_cfg)
        runner = DefaultModelRunner(db, cfg, pace=True)
        res = extract_deck(deck, models=runner, work=tmp_path / "w", material=Privacy.CLASS)

        def text(c):
            return [c["title"], c["key"].replace("-", " "), *c.get("aliases", [])]

        core_items = [g for g in gold if g["tier"] == "core"]
        hits = 0
        for g in core_items:
            if any(
                any(re.search(g["match"], t, re.IGNORECASE) for t in text(c))
                for c in res.output["concepts"]
            ):
                hits += 1

        assert hits >= 11, f"Core recall was {hits}/15, expected >= 11"
    finally:
        try:
            lock_path.rmdir()
        except OSError:
            pass
