# Articulate v1: implementation spec

How Articulate v1 is built: the module map, the contracts every slice codes against, and the build order.
What each phase must deliver, and how it's checked, is in [SPEC-requirements.md](SPEC-requirements.md).

Authority: the map ([Map: Articulate](https://github.com/amierlorenz/articulate/issues/1)) and the resolution
comments on its closed tickets. Where this spec and a ticket disagree, the ticket wins and this file is wrong.
Questions the tickets left open, and Amier's answers, are under [Decisions made while writing this spec](#decisions-made-while-writing-this-spec).

Status: **Phases 0–5 written.** Phase 0 contracts are in §3; Phases 3–5 add theirs in §7.

---

## 1. Shape of the system

A headless Python 3.12 job on the Mac, managed with `uv`, started by launchd
([Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14)). It has no server and no
UI. It writes to four places:

| Where | What | Who else writes there |
|---|---|---|
| `~/Library/Application Support/articulate/` ("data dir") | SQLite, transcripts, source archive, caches, logs, private eval sets | nobody |
| `~/Library/Application Support/Encore/Handoff/to-encore/` | import batches, `plan.json`, post-class plans | nobody (Encore only reads and deletes) |
| the iCloud Obsidian vault | agent notes, `_system/Runs.md`, `_system/Dashboard.md`, `Inbox/Intake flags.md` | Amier |
| master-todo (through App Intents) | tasks | Amier, master-todo's own Canvas sync |

It reads from Pocket, Canvas, Google Calendar, `iCloud Drive/Articulate/` (`courses.toml`, `models.toml`,
`Drop/`), and `Handoff/from-encore/`.

### Jobs

| Job | When | Does |
|---|---|---|
| `nightly` | 02:00 (LaunchAgent) | everything: intake, notes, tasks, export, plan, using the rest of the previous Pacific day's Gemini quota |
| `pass2` | 03:05 | a second pass on fresh Gemini quota (after midnight Pacific = 03:00 Eastern), then writes `plan.json` last, before 06:00 |
| `watch` | :15 and :45 every hour | Pocket poll for non-class recordings, Inbox notes, tasks; post-class quiz from Phase 3 |

Sources: [Model routing and budget](https://github.com/amierlorenz/articulate/issues/11),
[How Gemini free-tier daily limits are counted](https://github.com/amierlorenz/articulate/issues/38),
[Daily rhythm](https://github.com/amierlorenz/articulate/issues/9),
[Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20).

---

## 2. Module map

One package per slice. A slice owns its package outright, including its tests and its SQL migrations. **No two
slices edit the same file.** The one shared file is the job registry (`src/articulate/registry.py`), where each
slice adds one line. A slice may import another slice's public functions (what its `__init__.py` exports), but never
edits them; if it needs a change, that's an issue on the owning slice.

```
articulate/
  pyproject.toml            Phase 0
  schemas/                  Phase 0: generated JSON Schema for every hand-off file (Encore pins copies)
  prompts/                  one model-agnostic prompt file per step; each slice adds its own files
  launchd/                  Phase 0: plist templates
  tests/fixtures/           Phase 0 seeds it; slices add files in their own subfolder
  src/articulate/
    registry.py             THE shared file: one line per step
    core/                   Phase 0
    pocket/                 Phase 1
    calendar/               Phase 1
    inbox/                  Phase 1
    tasks/                  Phase 1
    intake/canvas/          Phase 2
    intake/convert/         Phase 2
    intake/cite/            Phase 2
    intake/extract/         Phase 2
    intake/guides/          Phase 2
    intake/library/         Phase 2
    intake/shape/           Phase 2
    handoff/                Phase 2 (writes batches, reads Encore's files)
    plan/                   Phase 2 (simple plan), Phase 3 (ramp)
    postclass/              Phase 3
    intake/contrast/        Phase 3
    intake/reintake/        Phase 3
    commands/               Phase 4
    literature/             Phase 4
    people/                 Phase 4
    intake/transcript/      Phase 4
    reading/                Phase 5
    youtube/                Phase 5
    leetcode/               Phase 5
    evals/                  Phase 5
```

| Package | Phase | Slice | Deciding tickets |
|---|---|---|---|
| `core` | 0 | Skeleton: paths, config, secrets, SQLite, pipeline, `ModelRunner`, hand-off schemas, vault writer, flags, health, Runs.md, Dashboard.md, CLI, launchd | [v1 scope and phase plan](https://github.com/amierlorenz/articulate/issues/49) and the tickets cited per section below |
| `calendar` | 1 | Google Calendar client, class match, `private_events` rule, non-class event match | [Pocket for class lectures](https://github.com/amierlorenz/articulate/issues/2), [Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29), [Non-class recordings with no command](https://github.com/amierlorenz/articulate/issues/42) |
| `pocket` | 1 | Poll, dedupe merged copies, Private (fail closed), sensitivity check, class window split, transcript archive | [Pocket for class lectures](https://github.com/amierlorenz/articulate/issues/2), [Check Pocket's REST API with the real key](https://github.com/amierlorenz/articulate/issues/15), [Model routing and budget](https://github.com/amierlorenz/articulate/issues/11), [Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29) |
| `inbox` | 1 | One fleeting `Inbox/` note per uncommanded non-class recording or stretch, on `qwen3:8b` | [Non-class recordings with no command](https://github.com/amierlorenz/articulate/issues/42), [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31), [The new vault workflow](https://github.com/amierlorenz/articulate/issues/8) |
| `tasks` | 1 | Extract tasks, ledger, master-todo intents, Inbox fallback list, state poll | [Task funnel into master-todo](https://github.com/amierlorenz/articulate/issues/7), [Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20) |
| `intake/canvas` | 2 | Canvas client, Pick (module items, module-Page links, Files tab, posted-announcement attachments), slots, `courses.toml` skip rules, Drop folder, download, archive, Flash Lite routing check, exam list and dates ([§3.14](#314-exams-and-exam-dates)) | [Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14), [Files linked only from Canvas Pages](https://github.com/amierlorenz/articulate/issues/24), [Re-intake when Canvas files change](https://github.com/amierlorenz/articulate/issues/23) (slots only), [Study guide and past exam output](https://github.com/amierlorenz/articulate/issues/32) (routing) |
| `intake/convert` | 2 | DeckCraft → Keynote fallback, `wordcraft-cli`, pypdfium2 page text, `ocrmac`, damaged-layer signals, `.pages.json` | [Test DeckCraft on real Canvas decks](https://github.com/amierlorenz/articulate/issues/28), [Run the Keynote export once](https://github.com/amierlorenz/articulate/issues/21), [Spotting a damaged text layer](https://github.com/amierlorenz/articulate/issues/40) |
| `intake/cite` | 2 | Quote check: text layer → OCR → VL, canonicaliser, boundary near math, label-aware, joined retry | [Vision transcript in the citation check](https://github.com/amierlorenz/articulate/issues/35), [Formula check for guide and exam quotes](https://github.com/amierlorenz/articulate/issues/37), [Spotting a damaged text layer](https://github.com/amierlorenz/articulate/issues/40) |
| `intake/extract` | 2 | Slide/PDF Extract, long-deck split and merge, code checks | [Extract output schema](https://github.com/amierlorenz/articulate/issues/22), [Extract recall on long decks](https://github.com/amierlorenz/articulate/issues/36) |
| `intake/guides` | 2 | Guide/exam Extract, answer-key join, question mix | [Study guide and past exam output](https://github.com/amierlorenz/articulate/issues/32), [Rerun guide and exam validation on 3.6 and 3.8 Flash](https://github.com/amierlorenz/articulate/issues/34) |
| `intake/library` | 2 | Embedding shortlist → Flash Lite judge, merge/enrich, tiers, guide and exam effects, gap-fill hold | [Deciding what matters](https://github.com/amierlorenz/articulate/issues/10), [Definitions in decision groups?](https://github.com/amierlorenz/articulate/issues/19) |
| `intake/shape` | 2 | Prompt generation per tier, verify, style examples, 8-word overlap rejection | [Daily rhythm](https://github.com/amierlorenz/articulate/issues/9) (cards per tier), [Model routing and budget](https://github.com/amierlorenz/articulate/issues/11), [Contract between Articulate and MieracleStudy](https://github.com/amierlorenz/articulate/issues/13) |
| `handoff` | 2 | Build and write v2 batches, `open -g` Encore, wait for state, read acks, `failed/`, attempts, flags | [Hand-off folder layout and file formats](https://github.com/amierlorenz/articulate/issues/18), [Contract between Articulate and MieracleStudy](https://github.com/amierlorenz/articulate/issues/13) |
| `plan` | 2, 3 | Phase 2: due reviews + ≤10 new, core first. Phase 3: ramp, practice tests, missed days | [Daily rhythm](https://github.com/amierlorenz/articulate/issues/9) |
| `postclass` | 3 | :15/:45 post-class quiz | [Daily rhythm](https://github.com/amierlorenz/articulate/issues/9) |
| `intake/contrast` | 3 | Cross-deck confusable pairs, contrast writer, `kind: contrast` | [Confusable pairs across decks](https://github.com/amierlorenz/articulate/issues/41) |
| `intake/reintake` | 3 | Changed Canvas files | [Re-intake when Canvas files change](https://github.com/amierlorenz/articulate/issues/23) |
| `commands` | 4 | Voice commands, receipts, undo | [Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29), [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31) |
| `literature` | 4 | Claim-breakdown Literature notes | [Claim breakdown from a guest speaker](https://github.com/amierlorenz/articulate/issues/30) |
| `people` | 4 | `People/` notes | [Person notes for speakers and authors](https://github.com/amierlorenz/articulate/issues/33) |
| `intake/transcript` | 4 | Transcript-only concepts | [Transcript-only concepts](https://github.com/amierlorenz/articulate/issues/39) |
| `reading` | 5 | Quizzes from Literature notes | [Quizzes from reading and goals](https://github.com/amierlorenz/articulate/issues/43) |
| `youtube` | 5 | Watched-videos playlist → video Literature notes | [What GitHub, YouTube and Medium feed into](https://github.com/amierlorenz/articulate/issues/44), [Getting transcripts for watched YouTube videos](https://github.com/amierlorenz/articulate/issues/46), [Gemini reading watched YouTube videos by link](https://github.com/amierlorenz/articulate/issues/47), [Retest Flash Lite on YouTube links](https://github.com/amierlorenz/articulate/issues/48) |
| `leetcode` | 5 | `interview` course from `Drop/interview/` | [LeetCode problems as a source](https://github.com/amierlorenz/articulate/issues/25) |
| `evals` | 5 | `articulate eval tasks`, `articulate eval commands`, tuning | [Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20), [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31) |

### Inside `core`

| Module | Contract section |
|---|---|
| `core/paths.py` | [§3.1 Paths](#31-paths) |
| `core/config.py` | [§3.2 Config files](#32-config-files) |
| `core/secrets.py` | [§3.3 Keychain secrets](#33-keychain-secrets) |
| `core/db.py`, `core/migrations/` | [§3.4 SQLite](#34-sqlite) |
| `core/pipeline.py` | [§3.5 Source → Item → Handler](#35-source--item--handler) |
| `core/models/` (`runner.py`, `gemini.py`, `claude.py`, `ollama.py`, `quota.py`, `usage.py`) | [§3.6 ModelRunner](#36-modelrunner) |
| `core/handoff_schemas/` | [§3.7 Hand-off schemas](#37-hand-off-schemas) |
| `core/vault.py` | [§3.8 Vault writer](#38-vault-writer) |
| `core/flags.py` | [§3.9 Weekly flags](#39-weekly-flags) |
| `core/health.py`, `core/taskqueue.py` | [§3.10 Health and the task queue](#310-health-and-the-task-queue) |
| `core/runlog.py`, `core/dashboard.py` | [§3.11 Runs.md and Dashboard.md](#311-runsmd-and-dashboardmd) |
| `core/jobs.py`, `core/cli.py` | [§3.12 Job registry, CLI and launchd](#312-job-registry-cli-and-launchd) |
| `core/atomic.py`, `core/clock.py`, `core/ids.py` | [§3.13 Small shared rules](#313-small-shared-rules) |

---

## 3. Phase 0 contracts

Everything here is committed as code in Phase 0, with tests, before any Phase 1 or 2 slice starts. Field names
below are the code names. Pydantic v2 models are the source of truth, and `uv run articulate schemas` regenerates
`schemas/*.json` from them.

### 3.1 Paths

| Name | Path |
|---|---|
| `DATA` | `~/Library/Application Support/articulate/` |
| `DB` | `DATA/articulate.sqlite3` |
| `TRANSCRIPTS` | `DATA/transcripts/YYYY/MM/<recording_id>.json.gz` (the Pocket detail payload as fetched, plus `fetched_at`) |
| `ARCHIVE` | `DATA/archive/<course_key>/<sha256>.<ext>` plus `<sha256>.pages.json` |
| `WORK` | `DATA/work/<run_id>/` (PDF exports, PNGs; deleted at the end of a run) |
| `CACHE` | `DATA/cache/` (`ocr/<page_hash>.json`, `vl/<page_hash>.json`, `embeddings/`) |
| `PRIVATE_EVALS` | `DATA/evals/` (labelled sets that quote personal recordings, never in the repo) |
| `REAL_FIXTURES` | `DATA/fixtures/real/` (see [§4](#4-fixtures)) |
| `LOGS` | `DATA/logs/<job>.log` (launchd stdout/stderr) |
| `HUMAN` | `~/Library/Mobile Documents/com~apple~CloudDocs/Articulate/` (`courses.toml`, `models.toml`, `Drop/<course_key>/`) |
| `HANDOFF` | `~/Library/Application Support/Encore/Handoff/` (`to-encore/`, `to-encore/failed/`, `from-encore/`) |
| `VAULT` | from `articulate.toml` (`vault.path`) |

Articulate never moves or deletes anything in `HUMAN`
([Hand-off folder layout and file formats](https://github.com/amierlorenz/articulate/issues/18)). Everything under
`DATA` stays local (Time Machine), never iCloud
([The new vault workflow](https://github.com/amierlorenz/articulate/issues/8),
[Contract between Articulate and MieracleStudy](https://github.com/amierlorenz/articulate/issues/13)).

### 3.2 Config files

All TOML, read with `tomllib`, validated into Pydantic models. A missing or invalid file stops the jobs that need
it and raises a health row ([§3.10](#310-health-and-the-task-queue)). Readers ignore keys they don't know.

#### `HUMAN/courses.toml` (Amier edits it, often from the phone)

From [Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14),
[Hand-off folder layout and file formats](https://github.com/amierlorenz/articulate/issues/18),
[Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29) (`instructor`),
[LeetCode problems as a source](https://github.com/amierlorenz/articulate/issues/25) (`interview`).

```toml
default_skip = ["Syllabus", "Start Here*", "Course Information*"]   # module-name globs

[courses.mat305]                 # key = first line of the "Classes" event description, normalised (below)
canvas_id = 12345
name = "MAT-305 Prob & Stats"
skip = ["Week 0*"]               # overrides default_skip when set
instructor = ""                  # written once by the commands slice when absent, never overwritten
active = true                    # false after the final: no re-intake, interval cap lifted

  # Exams found on Canvas need no entry here. An entry adds an exam Canvas lacks (PHY-203's final),
  # pins a date (it beats every Canvas source) or lists units. See §3.14.
  [[courses.mat305.exams]]
  key = "final"
  name = "Final Exam"            # matched to the Canvas assignment by name, case and spacing ignored
  kind = "final"                 # test | final
  date = "2026-12-07"            # optional; local date, optional time "2026-12-07T08:00"
  units = ["9.4 Single sample: estimating the mean", "9.5 Standard error of a point estimate"]
```

The course key is the id Articulate uses everywhere (`course_key`). **Normalising:** take the event description,
strip HTML (`<br>` and block tags end a line), take the first non-empty line, lowercase it, and drop everything
but `a–z` and `0–9`. The Classes calendar today has `MAT 305` → `mat305`, `CSC 301` → `csc301`, `CSC 430<br>…` →
`csc430`. An event whose key isn't in `courses.toml` doesn't count as a class (fail closed, dashboard row). The unit
and exam names are the lists the
guide/exam prompt copies from (`prototype/37-formula-check:prototypes/extract-schema/courses/mat-305.json` has the
current shape: `{"exams": [...], "units": [...]}`).

#### `HUMAN/models.toml`

From [Model routing and budget](https://github.com/amierlorenz/articulate/issues/11) ("chains and ceilings live in
`models.toml` next to `courses.toml`"; Q2), limits from
[Check model accounts and limits](https://github.com/amierlorenz/articulate/issues/16).

```toml
[gemini]
keychain_service = "articulate-gemini"
timeout_s = 600                         # per call
long_timeout_s = 1200                   # guide/exam files (#34)

[gemini.models."gemini-3.8-flash"]      # ids are confirmed with ListModels in setup; a 404 skips + health row
rpm = 5
rpd = 20
[gemini.models."gemini-3.6-flash"]
rpm = 5
rpd = 20
[gemini.models."gemini-4-hydrogen"]
rpm = 5
rpd = 20
[gemini.models."gemini-3.5-flash-lite"]
rpm = 15
rpd = 500
reserve = 40                            # kept for daytime post-class quizzes
[gemini.models."gemini-embedding-2"]
rpm = 100
rpd = 1000
reserve = 50

[claude]
model = "sonnet"                        # `claude -p --model sonnet`
weekly_ceiling_pct = 50
session_ceiling_pct = 70
max_calls_per_night = 15
usage_timeout_s = 30

[ollama]
url = "http://127.0.0.1:11434"
text_model = "qwen3:8b"
embed_model = "embeddinggemma"
vl_model = "qwen3-vl:8b-instruct"

# Chains: a step name → ordered model list. "claude@ramp" = claude -p, only for items whose exam is inside the
# 10-day ramp; otherwise the step holds. "hold" is implicit at the end of every chain.
[chains]
extract            = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen", "claude@ramp"]
guide_exam         = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen", "claude@ramp"]
route_file         = ["gemini-3.5-flash-lite"]
embed_class        = ["gemini-embedding-2"]
rank_lecture       = ["gemini-3.5-flash-lite"]
library_judge      = ["gemini-3.5-flash-lite"]
definition_prompts = ["gemini-3.5-flash-lite"]          # never spills to Flash
method_prompts     = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen"]
prompt_verify      = ["gemini-3.5-flash-lite"]
postclass_quiz     = ["gemini-3.5-flash-lite"]
lecture_literature = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
transcript_concepts = ["gemini-3.6-flash", "gemini-4-hydrogen", "gemini-3.8-flash"]   # #39
contrast_writer    = ["gemini-3.5-flash-lite"]
youtube_long       = ["gemini-3.6-flash"]                # #47/#48, lowest priority
youtube_short      = ["gemini-3.5-flash-lite", "gemini-3.6-flash"]
personal_literature = ["claude"]                         # within ceilings, else hold
personal_text      = ["ollama:qwen3:8b"]                 # sensitivity, gate, kind, inbox note, tasks
embed_personal     = ["ollama:embeddinggemma"]
vl_transcribe      = ["ollama:qwen3-vl:8b-instruct"]
```

The `[chains]` table is the routing table from #11 in config form. The **privacy rule is in code, not config**: a
request with `material="personal"` or `"private"` refuses any `gemini-*` entry, whatever `models.toml` says
([§3.6](#36-modelrunner)).

#### `DATA/articulate.toml` (machine settings, local)

Spec choice: these values identify accounts and folders on this Mac, so they stay local.

```toml
[vault]
path = "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/<Vault>"
name = "<Vault>"                          # for obsidian:// links

[pocket]
api_base = "https://public.heypocketai.com"   # REST paths are /public/recordings, /public/folders, /public/tags;
                                              # host not recorded in the research notes, confirm in setup
mcp_url = "https://public.heypocketai.com/mcp"
private_folder_id = "5194b8b6-ec40-4d99-91a9-059fb610141e"   # #15
private_tag_id = "7dbebf3f-418b-4ea1-936d-61c67089d829"      # #15
lookback_days = 3
give_up_pending_days = 3

[calendar]
classes_calendar = "Classes"
private_events = ["Therapy"]              # event titles (substring) or calendar names → Private (#29)
private_calendars = []

[canvas]
base_url = "https://<school>.instructure.com"

[encore]
bundle_id = "com.amierlorenz.Encore"   # launched with `open -g -b <bundle_id>`, wherever Xcode installed it
state_wait_s = 180                        # #18: wait ≤3 min for fresh state.json
```

### 3.3 Keychain secrets

Read with `security find-generic-password -s <service> [-a <account>] -w` from `core/secrets.py`. Secrets are
never logged, never put in config files, and never read by subagents. A missing secret raises a health row and
stops only the jobs that need it.

| Secret | Service | Account | Source |
|---|---|---|---|
| Gemini API key (`articulate` free-tier project) | `articulate-gemini` | any | [Extract output schema](https://github.com/amierlorenz/articulate/issues/22) |
| Pocket API key (read-only `pk_…`) | `articulate` | `pocket_api_key` | [Check Pocket's REST API with the real key](https://github.com/amierlorenz/articulate/issues/15) |
| Canvas token | `articulate` | `canvas_token` | moved from `~/.config/canvas-cli/config.yaml` ([Files linked only from Canvas Pages](https://github.com/amierlorenz/articulate/issues/24)) |
| Google OAuth (Desktop client; Calendar now, YouTube in Phase 5) | `articulate` | `google_oauth` (JSON: client id, secret, refresh token) | [Getting transcripts for watched YouTube videos](https://github.com/amierlorenz/articulate/issues/46): one token, app "In production", never "Testing" |
| Claude | none | none | `claude` uses its own `/login` credential ([Reading Claude usage from a script](https://github.com/amierlorenz/articulate/issues/27)) |

`articulate setup secrets` prompts for each one once (interactive, run by Amier) and writes it with
`security add-generic-password -U`.

### 3.4 SQLite

One file, `DB`, WAL mode, `foreign_keys=ON`. Migrations are plain `.sql` files. `core/migrations/` holds Phase 0's;
each slice keeps its own in `src/articulate/<slice>/migrations/NNN_<name>.sql` and `core/db.py` discovers them, so a
slice never edits core's files. Applied migrations are recorded in `schema_migrations(package, name, applied_at)`.
Every timestamp is ISO 8601 with offset; every `pacific_date` is `YYYY-MM-DD` in `America/Los_Angeles`.

#### Runs and items (resumable state, [Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14))

```sql
CREATE TABLE runs (
  run_id       TEXT PRIMARY KEY,          -- ULID
  job          TEXT NOT NULL,             -- nightly | pass2 | watch | manual
  started_at   TEXT NOT NULL,
  ended_at     TEXT,                      -- NULL + a later run = "crashed"
  status       TEXT NOT NULL,             -- running | ok | failed | crashed
  error        TEXT,
  summary_json TEXT                       -- counts the Runs.md line is built from
);

CREATE TABLE items (
  item_id      TEXT PRIMARY KEY,          -- see core/ids.py
  source       TEXT NOT NULL,             -- pocket | canvas | drop | handoff | vault | youtube | ...
  kind         TEXT NOT NULL,             -- recording | span | slides | guide_or_exam | other_file | flag | ...
  native_id    TEXT NOT NULL,             -- id at the source
  version      TEXT NOT NULL DEFAULT '',  -- content hash for files, '' when the source has no versions
  course_key   TEXT,
  privacy      TEXT NOT NULL,             -- class | personal | private
  exam_key     TEXT,                      -- nearest exam this item serves (Pick order, ramp check)
  priority     REAL NOT NULL DEFAULT 0,   -- lower runs first
  stage        TEXT NOT NULL,             -- handler-defined, e.g. pick → convert → extract → cite → library → shape → export
  status       TEXT NOT NULL,             -- pending | running | held | failed | done | skipped | superseded
  hold_reason  TEXT,                      -- see HoldReason below
  hold_since   TEXT,
  attempts     INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error   TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  UNIQUE (source, native_id, version)
);

CREATE TABLE item_events (
  id INTEGER PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items, run_id TEXT,
  at TEXT NOT NULL, stage TEXT, event TEXT NOT NULL,     -- started | done | held | failed | skipped | superseded
  detail TEXT
);
```

`HoldReason` values (shown on the dashboard, [Run health and dashboard](https://github.com/amierlorenz/articulate/issues/45)):
`quota`, `claude_over_budget`, `ollama_down`, `unconsented_deck`, `keynote_presenting`, `quote_check_failed`,
`flagged_card`, `waiting_transcript`, `waiting_encore`, `gap_fill`, `weekly_review`.

#### Model ledger and quota ([How Gemini free-tier daily limits are counted](https://github.com/amierlorenz/articulate/issues/38))

```sql
CREATE TABLE model_calls (                -- one row per HTTP attempt, failures included
  id INTEGER PRIMARY KEY,
  run_id TEXT, item_id TEXT, step TEXT NOT NULL,
  provider TEXT NOT NULL,                 -- gemini | claude | ollama
  model TEXT NOT NULL,
  pacific_date TEXT NOT NULL,             -- the Gemini quota day the call counts toward
  local_night TEXT,                       -- local date of the 02:00 night it belongs to (claude call cap)
  started_at TEXT NOT NULL, ms INTEGER,
  outcome TEXT NOT NULL,                  -- ok | max_tokens | schema_rejected | invalid_json | http_400 | http_404 |
                                          -- http_429_minute | http_429_day | http_500 | http_503 | timeout | refused
  counts_quota INTEGER NOT NULL,          -- 1 for every attempt that reached Gemini (400/500/503/timeout included)
  tokens_in INTEGER, tokens_out INTEGER, tokens_thinking INTEGER,
  finish_reason TEXT
);
CREATE INDEX model_calls_quota ON model_calls (provider, model, pacific_date);

CREATE TABLE model_state (
  model TEXT PRIMARY KEY,
  exhausted_until TEXT,                   -- set by a PerDay 429: next midnight Pacific
  missing_since TEXT                      -- set by a 404; cleared when a call succeeds
);

CREATE TABLE claude_usage (
  checked_at TEXT PRIMARY KEY, ok INTEGER NOT NULL,
  session_pct REAL, weekly_pct REAL, session_resets_at TEXT, weekly_resets_at TEXT,
  credential_expires_at TEXT, cli_version TEXT, error TEXT
);
```

#### Tasks ([Task funnel into master-todo](https://github.com/amierlorenz/articulate/issues/7), [Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20))

```sql
CREATE TABLE tasks (
  external_id   TEXT PRIMARY KEY,         -- task:<recordingId>:<startSec> | study:<course>:<slug> |
                                          -- health:<kind>:<YYYY-MM-DD> | gtasks:<id>
  source        TEXT NOT NULL DEFAULT 'articulate',
  title         TEXT NOT NULL,            -- imperative, ≤80 chars
  due_at        TEXT, has_due_time INTEGER NOT NULL DEFAULT 0,
  notes         TEXT, link TEXT,          -- link = obsidian:// URI
  origin_item_id TEXT,
  band          TEXT NOT NULL,            -- high | medium | low | system (health/study chores)
  prompt_version TEXT,
  state         TEXT NOT NULL,            -- pending | fallback | sent | skipped | flagged | dropped |
                                          -- open | done | removed | unknown
  master_todo_id TEXT,
  send_failures INTEGER NOT NULL DEFAULT 0,  -- consecutive failed runs
  created_at TEXT NOT NULL, sent_at TEXT, last_polled_at TEXT, closed_at TEXT
);
```

`core/taskqueue.py` owns `enqueue_task()`; the `tasks` slice owns sending, polling and the fallback list.

#### Vault ownership ([The new vault workflow](https://github.com/amierlorenz/articulate/issues/8), [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31))

```sql
CREATE TABLE vault_notes (
  articulate_id TEXT PRIMARY KEY,         -- ULID, also in the note's frontmatter
  path TEXT NOT NULL,                     -- vault-relative, updated when a scan re-finds a moved note
  kind TEXT NOT NULL,                     -- inbox | literature | people | receipt | system
  written_hash TEXT NOT NULL,             -- sha256 of what Articulate last wrote
  touched INTEGER NOT NULL DEFAULT 0,     -- 1 once the file's hash differs, or it moved: Amier owns it for good
  private INTEGER NOT NULL DEFAULT 0,
  source_item_id TEXT, command_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE tombstones (
  kind TEXT NOT NULL,                     -- note | task | pair | ...
  key TEXT NOT NULL,                      -- articulate_id, external_id, "a|b", ...
  reason TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY (kind, key)
);
```

#### Flags, health, hand-off

```sql
CREATE TABLE flags (                      -- rows of Inbox/Intake flags.md
  flag_id TEXT PRIMARY KEY,               -- ULID
  kind TEXT NOT NULL,                     -- resolver key, e.g. tasks.medium, intake.gap_fill, intake.unsupported
  item_id TEXT, subject TEXT NOT NULL,    -- one-line text shown in the note
  choices_json TEXT NOT NULL,             -- [{"key": "keep", "label": "keep"}, ...]
  status TEXT NOT NULL,                   -- open | resolved | expired
  chosen TEXT, created_at TEXT NOT NULL, resolved_at TEXT
);

CREATE TABLE health (                     -- the dashboard's ⚠️ rows
  kind TEXT NOT NULL, key TEXT NOT NULL DEFAULT '',
  detail TEXT, interrupt INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, recovered_at TEXT,
  task_external_id TEXT,
  PRIMARY KEY (kind, key, first_seen)
);

CREATE TABLE handoff_files (              -- what Articulate wrote to to-encore/ and what Encore acknowledged
  file_id TEXT PRIMARY KEY,               -- batch id or plan id
  kind TEXT NOT NULL,                     -- import | plan | postclass
  written_at TEXT NOT NULL, acked_at TEXT, failed_at TEXT, error TEXT
);

CREATE TABLE encore_snapshots (           -- last state.json / library.json read
  schema TEXT PRIMARY KEY, exported_at TEXT NOT NULL, read_at TEXT NOT NULL, body_json TEXT NOT NULL
);
```

#### Content (the library Articulate owns, mirrored into JSON v2)

Phase 0 creates these tables so the Phase 2 slices can write them in parallel. Each Phase 2 slice owns the columns
it fills (named in its build issue). Claims and prompt payloads are stored as JSON in the shape of
[§3.7](#37-hand-off-schemas).

```sql
CREATE TABLE courses (course_key TEXT PRIMARY KEY, name TEXT, canvas_id INTEGER, coding INTEGER DEFAULT 0,
                      active INTEGER DEFAULT 1);
CREATE TABLE units   (unit_id TEXT PRIMARY KEY, course_key TEXT, name TEXT, position INTEGER);
CREATE TABLE exams   (exam_id TEXT PRIMARY KEY, course_key TEXT, name TEXT, kind TEXT, date TEXT,
                      unit_ids_json TEXT);
CREATE TABLE source_files (
  file_id TEXT PRIMARY KEY,               -- sha256 of the content
  course_key TEXT, origin TEXT,           -- canvas | drop
  slot TEXT NOT NULL,                     -- #23 slot: module item id | page id + filename | Files-tab path | drop path
  canvas_file_id INTEGER, canvas_updated_at TEXT, display_name TEXT, folder_hint TEXT, mime TEXT,
  role TEXT,                              -- slides | guideOrExam | other (Flash Lite routing check, #32)
  page_count INTEGER, archive_path TEXT, superseded_by TEXT, created_at TEXT
);
CREATE TABLE pages (
  file_id TEXT, page INTEGER, page_hash TEXT,
  layer_text TEXT, layer_untrusted INTEGER DEFAULT 0,   -- #40
  ocr_text TEXT, vl_text TEXT, title TEXT, notes TEXT, hidden INTEGER DEFAULT 0,
  PRIMARY KEY (file_id, page)
);
CREATE TABLE concepts (
  concept_id TEXT PRIMARY KEY, course_key TEXT, unit_id TEXT,
  kind TEXT,                              -- definition | fact | method
  title TEXT, aliases_json TEXT,
  details_json TEXT,                      -- ConceptDetails (claims)
  confidence TEXT, confidence_note TEXT,
  tier TEXT,                              -- core | standard | background | out
  tier_reasons_json TEXT,                 -- [{signal, quote, sourceFileId, page}]
  on_guide INTEGER DEFAULT 0,
  status TEXT,                            -- held | active | retired (held = not shipped yet)
  hold_reason TEXT, created_at TEXT, updated_at TEXT, shipped_in TEXT
);
CREATE TABLE contrast_groups (group_id TEXT PRIMARY KEY, course_key TEXT, name TEXT, question TEXT,
                              member_ids_json TEXT, contrasts_json TEXT, status TEXT);
CREATE TABLE prompts (
  prompt_id TEXT PRIMARY KEY, concept_id TEXT, kind TEXT, content_json TEXT,
  status TEXT,                            -- held | active | retired
  generated_by TEXT, prompt_version TEXT, created_at TEXT, shipped_in TEXT
);
CREATE TABLE guide_items (
  file_id TEXT, position INTEGER,         -- identity = reading order (#32)
  number TEXT, kind TEXT, page INTEGER, quote TEXT, answer_page INTEGER, answer_quote TEXT,
  verified INTEGER, verified_by TEXT, answer_verified INTEGER,
  matched_concept_ids_json TEXT,
  PRIMARY KEY (file_id, position)
);
```

### 3.5 Source → Item → Handler

`core/pipeline.py`. Every slice that processes things plugs in through these three types. The pipeline runner,
not the slice, writes `items`, `item_events` and the failure counters.

```python
class Privacy(StrEnum): CLASS = "class"; PERSONAL = "personal"; PRIVATE = "private"

class Item(BaseModel):
    item_id: str                 # core.ids.item_id(source, native_id, version)
    source: str
    kind: str
    native_id: str
    version: str = ""
    course_key: str | None = None
    privacy: Privacy
    exam_key: str | None = None
    priority: float = 0.0
    payload: dict = {}

class Source(Protocol):
    name: str
    def discover(self, ctx: "RunContext") -> Iterable[Item]: ...
    # Upserts by (source, native_id, version). A new version of a known native_id marks the old item
    # `superseded` if it has not reached its handler's final stage (#23 rule 7).

class StageResult(BaseModel):
    outcome: Literal["advance", "done", "hold", "fail", "skip"]
    next_stage: str | None = None
    hold_reason: HoldReason | None = None
    detail: str = ""
    new_items: list[Item] = []   # e.g. a recording splits into class window + personal tail spans

class Handler(Protocol):
    name: str
    kinds: set[str]              # Item kinds it takes
    stages: list[str]            # ordered; the first is the entry stage
    def run(self, item: Item, stage: str, ctx: "RunContext") -> StageResult: ...

class RunContext:
    run_id: str; job: str; now: datetime
    db: Database; config: Config; models: ModelRunner; vault: Vault
    flags: Flags; health: Health; tasks: TaskQueue; runlog: RunLog
    def budget_left(self, step: str) -> int: ...   # calls the ModelRunner could still make for this step
```

Runner rules:
- Items run in `priority` order (Pick sets it: nearest exam first, [Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14)).
- `hold` keeps the stage; the item is retried next run. Two held nights for `claude_over_budget` or `quota` add a
  flag line ([Model routing and budget](https://github.com/amierlorenz/articulate/issues/11)).
- `fail` increments `consecutive_failures`; at 3 the item goes on the weekly flags
  ([Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14)). `advance` or `done`
  resets it.
- An exception that stops the whole job (Canvas auth, no network) fails the run, raises a health row, and the
  runner enqueues a same-day task.
- `privacy` is set by the Source and can only move toward `private`, never away from it, inside a run.

#### Phase 2 intake stages

The intake slices don't call each other's handlers. Each one registers a step that takes the items sitting at its
stage, does its work, and advances them. Stage names are the contract:

| Stage | Item kinds | Owner | Writes | Advances to |
|---|---|---|---|---|
| `pick` | `slides`, `guide_or_exam`, `other_file` | `intake/canvas` | `source_files` (all but `page_count`), `exams`, `units`, archive copy | `convert` (`other_file` → skipped + flag) |
| `pick` (changed file) | same | `intake/canvas` | a new `source_files` row for the new version | `reintake` (no handler until Phase 3: the item waits) |
| `convert` | `slides`, `guide_or_exam` | `intake/convert` | `pages`, `source_files.page_count`, `.pages.json` | `extract` (slides) or `guide_extract` |
| `extract` | `slides` | `intake/extract` | candidate concepts and groups in `concepts`/`contrast_groups` with `status = held`, `hold_reason = 'library'` | `library` |
| `guide_extract` | `guide_or_exam` | `intake/guides` | `guide_items` | `library` |
| `library` | both | `intake/library` | merges, `tier`, `tier_reasons_json`, `on_guide`, `status` (`held` with a reason, or ready) | `shape` |
| `shape` | both | `intake/shape` | `prompts` | `export` |
| `export` | both | `handoff` | `concepts.shipped_in`, `prompts.shipped_in`, `handoff_files` | `done` |

`intake/cite` has no stage: `extract` and `guides` call `intake.cite.check(quote, file_id, page, kind)` before they
advance, and it fills `status`/`verifiedBy` on each citation and `verified` on guide items. `plan` reads the content
tables and `encore_snapshots`; it has no items.

### 3.6 ModelRunner

`core/models/`. Every model call in Articulate goes through it, including prototypes run during the day, because the
Gemini quota is per project and per model
([How Gemini free-tier daily limits are counted](https://github.com/amierlorenz/articulate/issues/38), second comment).

```python
class Part(BaseModel):
    text: str | None = None
    pdf: Path | None = None          # sent as inlineData application/pdf
    image: Path | None = None
    youtube_url: str | None = None   # Phase 5

class ModelRequest(BaseModel):
    step: str                        # a key of [chains] in models.toml
    material: Privacy                # class | personal | private
    parts: list[Part]
    json_schema: dict | None = None  # Gemini responseJsonSchema / Ollama format / claude prompt-embedded
    item_id: str | None = None
    exam_in_ramp: bool = False       # allows "claude@ramp"
    timeout_s: int | None = None     # default from models.toml; guide/exam uses long_timeout_s
    embed: bool = False              # embedding call: parts are texts, result.vectors

class ModelResult(BaseModel):
    text: str
    parsed: Any | None               # JSON-decoded when json_schema was given
    vectors: list[list[float]] | None
    model: str; provider: str
    finish_reason: str | None
    usage: dict
    attempts: list[str]              # outcomes of every attempt, in order

class Held(Exception):               # nothing in the chain could run now → the item holds
    reason: HoldReason

class ModelRunner(Protocol):
    def run(self, req: ModelRequest) -> ModelResult: ...   # raises Held
    def remaining(self, model: str) -> int: ...            # rpd − counted calls today − reserve (if it applies)
```

**Chain walk.** For each model in the step's chain, in order:

1. **Privacy.** `material` personal or private → skip every `gemini-*` model. Private also skips `claude`
   ([Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29): Private runs
   local-only). This check runs before config is read and has its own test.
2. **Availability.** Skip a model with `exhausted_until` in the future, or `missing_since` set (404 → skip + health
   row "unknown or retired model").
3. **Quota (Gemini).** Count `model_calls` rows with `counts_quota=1` for that model and the current
   `pacific_date`. Skip when `count ≥ rpd − reserve_applies`. The reserve applies only in the `pass2` job (the
   first job on a fresh Pacific day); `nightly` at 02:00 is still on the previous Pacific day and spends what's
   left; `watch` spends the reserve.
4. **Pace.** Keep at least `60/rpm` seconds between attempts on one model.
5. **Claude gate** (`claude`, `claude@ramp`). `claude@ramp` only when `exam_in_ramp`. Then read usage (below);
   proceed only when `session_pct < 70`, `weekly_pct < 50`, and fewer than 15 `claude` rows exist for this
   `local_night`. Unreadable usage = over the ceiling
   ([Model routing and budget](https://github.com/amierlorenz/articulate/issues/11)).
6. **Call.** Write the `model_calls` row before the request (`outcome` updated after), so a crash still counts.

**Outcomes.**

| Outcome | Action |
|---|---|
| `ok` | validate (below); return |
| `http_503`, `http_500` | one retry on the same model, then next model (both attempts count) |
| `timeout` | next model (counts) |
| `http_429` with a `quotaId` containing `PerDay` | set `exhausted_until` = next midnight Pacific; next model |
| `http_429` per minute | sleep `RetryInfo.retryDelay` once, retry; then next model |
| `http_400` schema rejected, `max_tokens` | return to the caller with `finish_reason`, so it can split (#22, #36); not retried here |
| `invalid_json`, control-character hit | next model ([Extract recall on long decks](https://github.com/amierlorenz/articulate/issues/36): 3.6 Flash's raw tab from `\tau`) |
| `http_404` | `missing_since`; health row; next model |
| `rate_limit_event` `allowed_warning`/`rejected` during `claude -p` | stop the call and hold every further Claude call this run ([Reading Claude usage from a script](https://github.com/amierlorenz/articulate/issues/27)) |
| nothing left | raise `Held(quota | claude_over_budget | ollama_down)` |

**Validation.** When `json_schema` is given, the runner decodes the JSON and rejects strings containing tab,
backspace or form feed (`^[^\t\x08\x0c]*$`, [Extract output schema](https://github.com/amierlorenz/articulate/issues/22)).
Domain checks (citations, method fields, group keys) belong to the calling slice.

**Gemini client** (`gemini.py`): plain REST with `httpx`, `POST
https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, header `x-goog-api-key`,
`generationConfig.responseMimeType = "application/json"`, `responseJsonSchema = schema`. No SDK, so there are no
hidden retries ([How Gemini free-tier daily limits are counted](https://github.com/amierlorenz/articulate/issues/38)).
Thought parts are dropped from `text`. Embeddings use `:embedContent` / `:batchEmbedContents`; one batch request
counts as one request in the ledger. Reuse: `prototype/22-extract-schema:prototypes/extract-schema/run.py` (request
body, response parsing), minus its 3× same-model retry.

**Claude client** (`claude.py`): `claude -p --model sonnet --tools "" --output-format stream-json --verbose`, prompt
on stdin, no `--bare` ([Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31),
[Reading Claude usage from a script](https://github.com/amierlorenz/articulate/issues/27)).

**Claude usage** (`usage.py`): `claude -p "/usage" --output-format stream-json --verbose`, killed after 30 s. Read
the `type: "assistant"` line's `usage_report.rate_limits.limits[]`, picking rows by `kind` (`session`, `weekly_all`),
never by label or order. Read `credential_expires_at` from the `result` line and `claude_code_version` from `init`.
Fail closed on: non-zero exit, timeout, no `usage_report`, null `rate_limits`, empty `limits`, a missing row, or a
`resets_at` in the past. Retry once after 3 minutes, then hold Claude for the run. Store every check in
`claude_usage`. Source: `research/claude-usage-from-script:docs/research/claude-usage-from-script.md`.

**Ollama client** (`ollama.py`): REST `/api/chat` and `/api/embed`, `think: false`, `temperature: 0`. The VL model
runs with `keep_alive: 0` and before any other local model on 16 GB
([Vision transcript in the citation check](https://github.com/amierlorenz/articulate/issues/35)). Ollama drops the
grammar when a schema has `pattern` ([Hybrid Extract: OCR + local text model](https://github.com/amierlorenz/articulate/issues/26)),
so for Ollama the runner strips `pattern` from the schema and relies on the code check. Connection refused → health
row "Ollama down" and `Held(ollama_down)`.

### 3.7 Hand-off schemas

`core/handoff_schemas/`, from [Hand-off folder layout and file formats](https://github.com/amierlorenz/articulate/issues/18)
and [Contract between Articulate and MieracleStudy](https://github.com/amierlorenz/articulate/issues/13). Articulate owns
all six schemas, including the ones Encore writes. `uv run articulate schemas` writes `schemas/<schema>.v<N>.json`;
Encore pins those copies.

Rules (all from #18):
- Every file starts with the header. A JSONL file's first line is the header.
- Adding a field doesn't bump the version; readers ignore unknown fields. A breaking change bumps it; a reader that
  gets an unknown version refuses the file (Articulate flags it, Encore moves it to `failed/`).
- Imports start at version 2, every other kind at version 1.
- Times: ISO 8601 with offset. Plan dates: local `YYYY-MM-DD`. Ids: strings Articulate chooses; Encore's attempt and
  flag ids are UUIDs.
- A file is complete when renamed: write `.<name>.tmp` in the same folder, `fsync`, `rename`. Readers skip dotfiles.

```python
class Header(BaseModel):
    schema_: Literal["encore.import", "encore.plan", "encore.state", "encore.library",
                     "encore.attempt", "encore.flag"] = Field(alias="schema")
    version: int
    id: str                      # batch id, plan id, export id, flag id
    created_at: datetime
    producer: str                # "articulate 0.1.0 (abc1234)" / "encore 1.0 (42)"
```

Id pattern for every Articulate id: `^[A-Za-z0-9][A-Za-z0-9._:-]*$` (v1's pattern plus `:`, which
`contrast:<a>:<b>` needs, [Confusable pairs across decks](https://github.com/amierlorenz/articulate/issues/41)).

#### `encore.import` v2 → `to-encore/import-<batchId>.json`

The shape is MieracleStudy's v1 nesting plus what [Concept model for intake](https://github.com/amierlorenz/mieracle-study/issues/77)
designed for v2 (course-level `sourceFiles` and groups, concept `kind` and `details`), with
[Definitions in decision groups?](https://github.com/amierlorenz/articulate/issues/19)'s rename and #13's `status`.
JSON keys are camelCase.

```python
class Citation(BaseModel):
    sourceFileId: str
    page: int | None = None              # slides/PDF
    startSec: float | None = None        # transcripts (Phase 4)
    endSec: float | None = None
    quote: str
    status: Literal["verified", "unverified"]
    verifiedBy: Literal["text", "ocr", "vl"] | None = None   # #35

class Claim(BaseModel):
    text: str
    provenance: Literal["cited", "gapFill"]
    citations: list[Citation]            # 1–3 when cited, [] when gapFill

class ConceptDetails(BaseModel):
    explanationCitations: list[Citation] = []
    aliases: list[str] = []
    useWhen: list[Claim] = []
    conditions: list[Claim] = []
    workedExamples: list[Claim] = []

class PromptContent:  # discriminated by Prompt.kind
    flashcard:      {front, back}
    shortAnswer:    {question, modelAnswer, keyPoints: [str]}
    multipleChoice: {question, options: [str], correctIndex, explanation}
    methodPick:     {statement, targetConceptId, explanation, verifiedSiblingIds: [str]}     # MS#79
    setUp:          {statement, targetConceptId, modelSetUp, keyPoints: [str]}              # MS#79
    contrast:       {conceptIds: [a, b], groupId}                                           # Phase 3, #41
    codeImplement, codeComplexity: as Encore's v1 payloads                                   # Phase 5, #25

class Prompt(BaseModel):
    id: str
    kind: str
    status: Literal["active", "retired"] = "active"
    content: dict

class Concept(BaseModel):
    id: str
    title: str
    explanation: str
    kind: Literal["definition", "fact", "method"]
    status: Literal["active", "retired"] = "active"     # "out" ships as retired (Q3)
    tier: Literal["core", "standard", "background", "out"]   # informational for Encore
    details: ConceptDetails
    groupIds: list[str] = []
    tags: list[str] = []
    prompts: list[Prompt] = []

class Unit(BaseModel):
    id: str; name: str; order: int; concepts: list[Concept] = []

class ContrastPair(BaseModel):
    conceptA: str; conceptB: str; decidingFeature: Claim

class ContrastGroup(BaseModel):
    id: str; name: str; question: str | None = None
    memberIds: list[str]; contrasts: list[ContrastPair] = []

class SourceFile(BaseModel):
    id: str; origin: Literal["canvas", "dropped", "pocket"]
    displayName: str; pageCount: int | None = None; contentHash: str
    canvasFileId: int | None = None

class Course(BaseModel):
    id: str                              # course_key
    name: str; shortName: str | None = None; coding: bool = False
    units: list[Unit] = []
    contrastGroups: list[ContrastGroup] = []
    sourceFiles: list[SourceFile] = []

class Exam(BaseModel):
    id: str; courseId: str; name: str; date: date
    kind: Literal["test", "final"]; unitIds: list[str]

class ImportBatch(BaseModel):
    header: Header                       # schema "encore.import", version 2
    courses: list[Course]
    exams: list[Exam] = []
```

Ids (`core/ids.py`): course = `course_key`; unit = `<course>.u.<slug>`; exam = `<course>.x.<slug>`; concept =
`<course>.<slug>` (slug from the title, `-2`, `-3` on collision, never reused); prompt = `<concept_id>.<kind>.<n>`;
group = `<course>.g.<slug>`; source file = `f.<sha256[:16]>`; batch = ULID. A batch is a delta: it carries only what
changed, and Encore upserts by id, never deletes ([Contract between Articulate and MieracleStudy](https://github.com/amierlorenz/articulate/issues/13)).
Prompts ship only with their concept ([Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14)).

#### `encore.plan` v1 → `to-encore/plan.json` and `to-encore/postclass-<lectureId>.json`

```python
class PlanNew(BaseModel):
    conceptId: str
    promptIds: list[str] = []            # empty = all active prompts of the concept
    reason: Literal["core", "standard", "background", "correction", "postclassSkipped", "reading"]

class ExamFocus(BaseModel):
    examId: str; rampDay: int            # days until the exam (Phase 3)

class Plan(BaseModel):
    header: Header                       # schema "encore.plan", version 1; header.id = plan id
    kind: Literal["daily", "postclass"]
    date: date                           # local
    validUntil: datetime                 # next 02:00 local
    reviewScope: list[str]               # course ids whose due reviews are in scope ("all courses" = every active course)
    new: list[PlanNew]                   # in order
    examFocus: list[ExamFocus] = []
    budgetMinutes: dict                  # {"target": 20, "planned": 20}
    kindsMix: dict                       # {"freeRecallMin": 0.5}
    readingShare: float = 0.0            # ≤0.2; 0 inside any ramp
    intervalCaps: list[dict] = []        # [{"courseId": "mat305", "maxDays": 30}]
    importBatch: str | None = None       # postclass: the batch Encore must apply first
    macPractice: list[str] = []          # Phase 5, encore#32
```

`plan.json` is overwritten every night and never deleted. Post-class files are deleted by Encore when done or at the
02:00 takeover.

#### Files Encore writes (`from-encore/`)

```python
class StatePrompt(BaseModel):
    promptId: str; conceptId: str
    due: datetime | None; ease: float | None; interval: float | None; lapses: int
    introducedAt: datetime | None; suspended: bool

class StateConcept(BaseModel):
    conceptId: str; introducedAt: datetime | None; suspended: bool

class State(BaseModel):                  # state.json, overwritten
    header: Header                       # "encore.state" v1
    exportedAt: datetime
    appliedBatches: list[str]
    planIdSeen: str | None
    concepts: list[StateConcept]
    prompts: list[StatePrompt]

class Library(BaseModel):                # library.json, overwritten
    header: Header                       # "encore.library" v1
    exportedAt: datetime
    courses: list[dict]                  # {id, name, units: [{id, name}], status}
    exams: list[dict]                    # {id, courseId, name, date, unitIds}
    concepts: list[dict]                 # {id, courseId, unitId, title, status}

class Attempt(BaseModel):                # one line of attempts-<exportId>.jsonl (after the header line)
    attemptId: UUID; promptId: str; conceptId: str
    answeredAt: datetime; answerText: str; grade: int; msTaken: int
    sessionKind: Literal["plan", "postclass", "practice"]
    calibrated: bool

class Flag(BaseModel):                   # flag-<uuid>.json
    header: Header                       # "encore.flag" v1
    flagId: UUID; promptId: str; conceptId: str
    reason: Literal["wrong", "unclear", "duplicate", "other"]
    note: str = ""; suggestedFix: str | None = None
    flaggedAt: datetime
```

Clean-up ([Hand-off folder layout and file formats](https://github.com/amierlorenz/articulate/issues/18)): Encore
deletes an import after applying it and lists it in `appliedBatches`; Articulate deletes attempts and flag files after
committing them to SQLite; `state.json`, `library.json` and `plan.json` are only overwritten. Attempts are personal
data: only local models or `claude -p` may read them, never Gemini
([Contract between Articulate and MieracleStudy](https://github.com/amierlorenz/articulate/issues/13)).

### 3.8 Vault writer

`core/vault.py`, from [The new vault workflow](https://github.com/amierlorenz/articulate/issues/8) and
[Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31).

```python
class Vault:
    def create_note(self, folder: Literal["Inbox", "Literature Notes", "People", "_system"],
                    title: str, frontmatter: dict, body: str, *, kind: str,
                    source_item_id: str | None, private: bool = False) -> NoteRef: ...
        # create-only: never overwrites; a title clash gets " (2)"; skips (returns None) when the
        # articulate_id or source key is tombstoned
    def regenerate(self, articulate_id: str, frontmatter: dict, body: str) -> bool: ...
        # only while untouched (file hash == written_hash and path unchanged); else returns False
    def write_system(self, name: Literal["Runs.md", "Dashboard.md", "Intake flags.md", ...], text: str) -> bool: ...
        # whole-file rewrite of Articulate-owned files, only when the content hash changed
    def append_system(self, name: Literal["Runs.md"], line: str) -> None: ...
    def scan(self) -> None: ...
        # re-finds moved notes by articulate_id in frontmatter; missing → tombstone (deleted = tombstone);
        # moved or edited → touched = 1
    def obsidian_uri(self, ref: NoteRef) -> str: ...
```

Every agent note has `agent: true` and `articulate_id` in its frontmatter. Never written: Permanent notes,
`Concepts/`, `_Daily Notes/`, existing source hubs. Writes go through tmp + rename. Notes from Private recordings get
`private: true`, and readers that feed cloud calls skip them.

### 3.9 Weekly flags

`core/flags.py`, from [Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14) (see
also Q5). One note, `Inbox/Intake flags.md`, rewritten from the `flags` table. Each open flag is one line:

```markdown
- [ ] <subject> — keep · [ ] merge into X · [ ] drop   <!-- flag:01JD… -->
```

The next run parses ticked boxes by the HTML comment id and calls the resolver registered for the flag's `kind`
(each slice registers its resolvers through its registry line). Unticked flags carry over. When open flags exist,
one task `study:flags:<YYYY-WW>` ("Clear N intake flags", linking the note) is enqueued once a week.

```python
class Flags:
    def add(self, kind: str, subject: str, choices: list[tuple[str, str]], item_id: str | None = None) -> str: ...
    def register(self, kind: str, resolver: Callable[[str, str, RunContext], None]) -> None: ...
```

### 3.10 Health and the task queue

From [Run health and dashboard](https://github.com/amierlorenz/articulate/issues/45).

```python
class Health:
    def raise_(self, kind: str, key: str = "", detail: str = "", interrupt: bool = False) -> None: ...
    def recover(self, kind: str, key: str = "") -> None: ...
```

A row is logged once to `Runs.md` when it appears and once when it recovers. `interrupt=True` enqueues one task
`health:<kind>:<YYYY-MM-DD>` per incident; Articulate never closes it, and the dashboard marks "recovered at …".
Interrupting kinds (and only these):

| Kind | Raised when |
|---|---|
| `no_nightly_success` | no successful `nightly` in more than 26 h |
| `job_failing` | the same job failed 2 nights in a row |
| `claude_credential` | `credential_expires_at` within 7 days, or past |
| `encore_stale` | no import ack or `state.json` for 2 days |
| `token_rejected` | Canvas or Pocket returns 401/403 for the token |

Everything else (Ollama down, unknown model, task sends failing 3 runs, missing config, pending Pocket recording given
up) is a dashboard row only.

```python
class TaskQueue:
    def enqueue(self, t: TaskRequest) -> None: ...   # inserts into tasks with state pending; idempotent by external_id
```

### 3.11 Runs.md and Dashboard.md

**`_system/Runs.md`** is append-only, one line per run plus health appear/recover lines
([Model routing and budget](https://github.com/amierlorenz/articulate/issues/11)):

```
- 2026-11-23 02:00→02:41 nightly ✅ items 14 done · 3 held (quota 2, ollama_down 1) · gemini 3.8f 4, 3.6f 2, hyd 0, lite 37, emb 120 · claude 2 calls, weekly 31%→36% · run 01JD…
- 2026-11-23 02:41 ⚠️ appeared: ollama_down
- 2026-11-23 10:15 ✅ recovered: ollama_down
```

Slices add their own lines through `RunLog.line(text)` (one per command or recording where a ticket asks for it).

**`_system/Dashboard.md`** is a snapshot, rewritten at the end of every run in a `finally` block, only when its hash
changed, through tmp + rename. Frontmatter `agent: true`, `generated: true`, then "Regenerated by Articulate, don't
edit." Plain Markdown tables, no Mermaid. Sections, top to bottom
([Run health and dashboard](https://github.com/amierlorenz/articulate/issues/45)):

1. Status: `✅ all good` or `⚠️ N problems`, each a row from `health` with a link.
2. Jobs: one row per job (`nightly`, `pass2`, `watch`, Encore import) with last start, last success, lateness and
   failure streak. `nightly`/`pass2` are late after 30 min and stale after 26 h without success; `watch` is stale after
   2 h of awake time; Encore import stale after 2 days. A start with no end is "crashed".
3. Model budget today: Gemini requests per model against `rpd` (from `model_calls`), Claude session and weekly %,
   Claude credential expiry.
4. Items held, counted by `hold_reason`, linked to `Inbox/Intake flags.md`, plus its open count.
5. Encore hand-off: last `state.json` `exportedAt`, last acknowledged batch, `plan.json` age.
6. Dependencies: Ollama, DeckCraft/Keynote, Canvas token, Pocket API, Google token.

Phase 0 ships sections 1–3 and 6 filled, and 4–5 with whatever rows exist (empty tables are fine).

### 3.12 Job registry, CLI and launchd

**`src/articulate/registry.py`** is the only file more than one slice edits. Each slice adds exactly one line, in
its reserved `order` range, so merges are one-line conflicts at worst:

```python
from articulate.core.jobs import Step

STEPS: list[Step] = [
    Step("core.encore_read",  "articulate.core.steps:read_encore",   jobs={"nightly", "pass2", "watch"}, order=10),
    Step("core.flags",        "articulate.core.steps:apply_flags",   jobs={"nightly"},                   order=20),
    # Phase 1
    Step("pocket.poll",       "articulate.pocket:step",              jobs={"watch", "nightly"},          order=100),
    Step("inbox.notes",       "articulate.inbox:step",               jobs={"watch", "nightly"},          order=120),
    Step("tasks.extract_send","articulate.tasks:step",               jobs={"watch", "nightly"},          order=130),
    # Phase 2: 200–399, Phase 3: 400–499, Phase 4: 500–599, Phase 5: 600–699
    Step("core.plan_hook",    "articulate.core.steps:noop",          jobs={"pass2"},                     order=900),
]
```

`Step(name, entry, jobs, order)`: `entry` is a `module:function` string, imported lazily, so a broken slice fails its
own step (logged, health row) without stopping the others. Each entry function has the signature
`def step(ctx: RunContext) -> StepSummary` and may also call `ctx.flags.register(...)` on import. Reserved order
numbers per slice are listed in each build issue.

**CLI** (`articulate`, argparse, `uv run articulate …`):

| Command | Does |
|---|---|
| `run <job>` | run `nightly`, `pass2` or `watch` (writes `runs`, Runs.md, Dashboard.md) |
| `run <job> --only <step>` | one step, for debugging |
| `setup` | interactive one-time setup: secrets, `models.toml` model ids via ListModels, `ollama pull`, LaunchAgents, `pmset` line to run with sudo, Keynote consent run, iCloud Drive access check |
| `doctor` | read-only checks of every dependency, prints the dashboard's section 6 |
| `schemas` | write `schemas/*.json` from the Pydantic models |
| `quota` | today's ledger per model |

**launchd** (`launchd/*.plist`, installed into `~/Library/LaunchAgents/` by `setup`; LaunchAgents, not daemons,
because DeckCraft, Keynote and `claude`'s login need the user GUI session,
[Test DeckCraft on real Canvas decks](https://github.com/amierlorenz/articulate/issues/28),
[Reading Claude usage from a script](https://github.com/amierlorenz/articulate/issues/27)):

| Label | Schedule | Program |
|---|---|---|
| `com.amierlorenz.articulate.nightly` | `StartCalendarInterval` Hour 2 Minute 0 | `uv run --project <repo> articulate run nightly` |
| `com.amierlorenz.articulate.pass2` | Hour 3 Minute 5 | `… articulate run pass2` |
| `com.amierlorenz.articulate.watch` | Minute 15 and Minute 45 | `… articulate run watch` |

Plus `sudo pmset repeat wake MTWRFSU 01:55:00`. launchd runs missed calendar jobs on the next wake; a run more than
24 h late gets a flag ([Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14)). A
lock file (`DATA/run.lock`, `fcntl.flock`) keeps two jobs from running at once; `watch` exits quietly if `nightly` or
`pass2` holds it.

### 3.13 Small shared rules

- `core/atomic.py`: `write_atomic(path, bytes)`: `.<name>.tmp` in the same folder, fsync, rename. Used for every
  hand-off file, vault file and archive file.
- `core/clock.py`: `pacific_date(dt)`, `next_pacific_midnight(dt)`, `local_night(dt)` (a time before 12:00 belongs to
  the previous evening's night). Tests cover both DST changes.
- `core/ids.py`: ULIDs; `item_id(source, native_id, version)`; slugs; the v2 id helpers in
  [§3.7](#37-hand-off-schemas).
- Logging: `logging` to `LOGS/<job>.log` with run id; never log secrets, transcript text or note bodies.
- Dependencies: `pydantic>=2`, `httpx`, `pypdfium2`, `ocrmac`, `python-ulid`, `google-auth-oauthlib`; dev: `pytest`,
  `ruff`. Slices add their own dependencies in their issue's PR (a one-line `pyproject.toml` change is allowed, like
  the registry).

### 3.14 Exams and exam dates

From a read-only Canvas survey on 2026-11-19 (all six Fall 2026 courses), agreed with Amier in this session as the
answer to Q4. Owned by `intake/canvas`; the result lands in the `exams` table and feeds Pick order, `plan.json` and
(Phase 3) the ramp.

What the survey found:
- Exams are Canvas **assignments**, in a group named Exams / Test / Final Exam or named `Test N`, `Exam N`,
  `Final Exam`. Canvas's Quizzes list and calendar events add nothing.
- Dates are often missing: MAT-305 and PHY-203 have no exam dated; PHY-203 has no final assignment. An online test's
  `due_at` can be the close of a window (CSC-301's final: Nov 30 → Dec 7), not the sitting.
- **Announcements carry the real dates and every reschedule** (6 this term: two midterms moved by a power outage,
  PHY-203 Test 3 merged into the midterm, Test 4 moved then corrected, Test 5 moved), and often the units ("Chapters
  are Work & Energy and Impulse & momentum"). Canvas due dates were never updated for any of them.
- 14 announcements in MAT-305 and CSC-301 have `posted_at: null`: leftovers from last year's course copy, including
  a "Final Exam: Tuesday, December 9" whose weekday only fits 2025.
- **Study guides arrive as announcement attachments** in CSC-382 and CSC-301 (9 files, 8 in no module, Files tab
  hidden).

Rules:
1. **Exam list:** Canvas assignments whose group or name matches `exam|test|midterm|final` and not
   `project|presentation|quiz|report|proposal|peer`, plus `courses.toml` entries. Matched by normalised name.
2. **Announcements:** each night, the course's **posted** announcements (`posted_at` set) since the term start.
   New or edited ones (by id + `updated_at`) go through one Flash Lite call per course (step `exam_dates`, class
   material) returning `[{ examName, date, time, units, quote }]`, every string with the control-character pattern.
   `quote` must be found verbatim in the announcement text (HTML stripped), or the row is dropped and logged.
   `examName` must match an exam from rule 1 (a new name such as "Test 3+Midterm" becomes a flag line, not an exam).
3. **Which date wins:** `courses.toml` `date` → the newest posted announcement that dates the exam → the assignment's
   `due_at` (for a window longer than a day, `unlock_at`'s date) → none.
4. **Units:** `courses.toml` `units` → announcement `units`, kept only when each one is in the course's unit list.
5. **No date from any source:** no ramp, sorted after dated exams in Pick, and one weekly flag line
   ("<exam> has no date — add it to `courses.toml`").
6. Every date change is one `Runs.md` line ("PHY-203 Test 4: Nov 1 → Nov 2, from announcement 2026-10-28") and
   rebuilds the plan ([Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14): exam
   dates re-read nightly).
7. **Pick also reads posted-announcement attachments** (`attachments[]` and `/files/:id` links in the message), a
   fourth discovery place next to the three in [Files linked only from Canvas Pages](https://github.com/amierlorenz/articulate/issues/24).
   Slot = announcement id + filename; the title is the role hint ("Test 3 Study Guide" → guide).

`models.toml` gains `exam_dates = ["gemini-3.5-flash-lite"]` under `[chains]`.

---

## 4. Fixtures

Two kinds, because personal recordings and course files stay out of git even though the repo is private
([Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20): labelled sets
never in the repo; every prototype kept decks and outputs gitignored). See Q6.

**`tests/fixtures/` (committed).** Real shapes with personal text replaced. Phase 0 seeds:

| Folder | Contents | From |
|---|---|---|
| `pocket/` | `recordings-list.json`, `recording-detail-lecture.json`, `recording-detail-merged.json` (`transcript.metadata.merged`), `recording-detail-null-duration.json`, `recording-pending.json`, `folders.json` (Private as `kind: space` with children), `tags.json`, `error-404.json`, rate-limit headers | `research/pocket-rest-api-check` field tables; text replaced with lorem |
| `calendar/` | a Classes event with the course key on the first description line; overlapping and non-overlapping events; a `private_events` hit | [Pocket for class lectures](https://github.com/amierlorenz/articulate/issues/2) |
| `canvas/` | modules + module items (File and Page), a Page body with `/files/:id` links, Files tab 200 and 403, folders `Practice test questions`, `course_image`; assignments with and without `due_at` and a window; announcements: a reschedule, a correction, a final with time and room, an unposted course-copy leftover, one with a study-guide attachment | [Files linked only from Canvas Pages](https://github.com/amierlorenz/articulate/issues/24) |
| `gemini/` | `ok-extract.json`, `max-tokens.json`, `503.json`, `429-perday.json` (`QuotaFailure.violations[].quotaId` with `PerDay`), `429-perminute.json` (`RetryInfo.retryDelay`), `404.json`, `raw-tab.json` | [How Gemini free-tier daily limits are counted](https://github.com/amierlorenz/articulate/issues/38) |
| `claude/` | `/usage` stream-json: ok, missing row, past `resets_at`, non-zero exit; a `rate_limit_event` `allowed_warning` | `research/claude-usage-from-script` |
| `deckcraft/` | control-port replies for `file.open`, `document.inspect` (hidden slide, notes), `file.export` ok and `ok:false`, `lostSlides` non-empty | `research/deckcraft-real-decks:docs/research/deckcraft-real-decks/dc.py` |
| `handoff/` | one valid file per schema, one with an unknown version, one with extra fields | [§3.7](#37-hand-off-schemas) |
| `pdf/` | small generated PDFs: text layer; no text layer; a glyph image in a line; ASCII `!` in a math font | [Spotting a damaged text layer](https://github.com/amierlorenz/articulate/issues/40) |
| `gold/` | `csc382-whitman-ch03.json`, `phy203-ch09.json` | `prototype/36-long-deck-recall:prototypes/hybrid-extract/gold/` |

**`DATA/fixtures/real/` (local, not committed)** with a committed manifest, `tests/fixtures/real-manifest.toml`:
file name, sha256, where Amier has it, what it tests, and the number the deciding ticket measured. Tests that need
them are marked `@pytest.mark.real` and skip when the file is missing. Phase 0 collects:

| File | Tests | Expected (from the ticket) |
|---|---|---|
| MAT-305 *Chapter 10-3,4 notes* (PDF, 5 p) | Extract, cite | schema accepted; 14/15 quotes on the text layer ([Extract output schema](https://github.com/amierlorenz/articulate/issues/22)) |
| MAT-305 Ch. 2 and Ch. 3 (PDF) | cite, damaged layer | Ch. 3 100 %, Ch. 2 63/64 verified; Ch. 2 p7/p8 and Ch. 3 p6 untrusted ([#35](https://github.com/amierlorenz/articulate/issues/35), [#40](https://github.com/amierlorenz/articulate/issues/40)) |
| PHY-203 Ch. 9 (PDF) | Extract split, cite | 100 % verified with VL; split neutral to positive ([#35](https://github.com/amierlorenz/articulate/issues/35), [#36](https://github.com/amierlorenz/articulate/issues/36)) |
| CSC-382 Whitman Ch. 3 (54 p) | long-deck split | core recall 42/52 with split + prompt line ([#36](https://github.com/amierlorenz/articulate/issues/36)) |
| PHY-203 *203 TEST 4 Practice-2026* | guides, cite | 26 items, 26/26 question quotes, 21/22 answers on 3.6 Flash ([#32](https://github.com/amierlorenz/articulate/issues/32), [#34](https://github.com/amierlorenz/articulate/issues/34)) |
| MAT-305 *T3 Review* + *detailed solutions* | guides, answer-key join, formula check | 316/318 true quotes, 0 altered pass ([#37](https://github.com/amierlorenz/articulate/issues/37)) |
| PHY-203 Test 1 Practice | damaged layer | p2 untrusted, p3 the one false flag ([#40](https://github.com/amierlorenz/articulate/issues/40)) |
| `chapter25_4e.pptx`, `Cryptography.pptx`, `ch10.pptx`, risk assessment, Piracy final, Ethics (+ hidden-slide copy), CVpresentation | convert | pages = slides on all 8; 0 fallbacks; hidden slide kept with `includeHidden` ([#28](https://github.com/amierlorenz/articulate/issues/28)) |
| 2 Pocket lectures, 3 conversations, 3 short voice notes, 1 merged copy (detail JSON) | pocket, inbox, tasks | from the Nov 17–19 tally ([#42](https://github.com/amierlorenz/articulate/issues/42)) |

---

## 5. Reusable prototype code

Read with `git show <branch>:<path>`. Copy the logic, not the harness: prototypes read keys from env vars, retry on
the same model, and write to `out/`.

| Use in | Branch : path | What to take |
|---|---|---|
| `core/models/gemini.py` | `prototype/22-extract-schema:prototypes/extract-schema/run.py` | REST body, thought-part filtering, `finishReason` |
| `intake/extract` | `prototype/22-extract-schema:prototypes/extract-schema/schema.py`, `prompt.md`, `check.py` | schema builder (`s`/`obj`/`arr`, `PATTERN`), prompt, code checks |
| `intake/extract` | `prototype/36-long-deck-recall:prototypes/hybrid-extract/long_deck.py`, `prompt.md`, `recall.py` | page-range split, remap, merge, named-concepts line, recall scoring |
| `intake/guides` | `prototype/37-formula-check:prototypes/extract-schema/schema_exam.py`, `prompt_exam.md`, `run_exam.py`, `check_exam.py`, `courses/*.json` | guide/exam schema and prompt, item checks |
| `intake/cite` | `prototype/35-vl-citation:prototypes/hybrid-extract/vl_match.py`, `check.py --rule=r7` | canonicaliser, exact rule, boundary near math |
| `intake/cite` | `prototype/37-formula-check:prototypes/formula-check/exam_match.py` (rule `R7+VL label+joined`) | part labels, joined retry, label-only fail |
| `intake/cite`, `intake/convert` | `prototype/35-vl-citation:prototypes/hybrid-extract/vl_transcribe.py`, `prep.py` | VL page transcription, OCR with `ocrmac` (accurate, 2×) |
| `intake/convert` | `prototype/40-damaged-layer:prototypes/damaged-layer/signals.py` | the three layer-untrusted signals |
| `intake/convert` | `research/deckcraft-real-decks:docs/research/deckcraft-real-decks/dc.py`, `run.py`, `keynote-export.applescript` | control-port client, launch with `open -n`, fixed Keynote script |
| `intake/transcript` (Phase 4) | `prototype/39-transcript-concepts:prototypes/transcript-concepts/` | windows, schema, prompts |
| `literature` (Phase 4) | `prototype/30-claim-breakdown:prototypes/claim-breakdown/RECIPE.md`, `run.py` | the atomic-claim recipe |
| `youtube` (Phase 5) | `research/gemini-youtube-urls:prototypes/gemini-youtube/run.py` | URL request, `"M:SS"` timestamps |

---

## 6. Build order

Phase 0 is one agent, serial (~2 days; buildable by about 2026-11-22). Then Phases 1 and 2 start together; Phase 1
finishes first. Phase 2 must reach Encore by about 2026-12-01 (finals start 2026-12-07 with stats).

```
Phase 0 (serial)  core: config/paths/secrets → db + migrations → pipeline → ModelRunner + ledger + usage
                  → hand-off schemas → vault/flags/health/taskqueue → Runs.md/Dashboard.md → CLI/launchd → fixtures
        │
        ├── Phase 1   calendar ─┬─ pocket ─┬─ inbox
        │                       │          └─ tasks ── master-todo#32 → master-todo#33
        │
        └── Phase 2   intake/canvas ─ intake/convert ─ intake/cite ─┬─ intake/extract ─┐
                                                                    └─ intake/guides ──┤
                                                     intake/library ─ intake/shape ─ handoff ─ plan
                      encore#25 (import v2) · encore#26 (watch folder) · encore#27 (today's session) · encore#29 (state export)
```

Arrows are data dependencies, not build blockers: every slice builds against Phase 0's contracts and fixtures, so
all Phase 1 and 2 slices can start the day Phase 0 merges. Build issues carry the exact "blocked by" links.

---

## 7. Phases 3–5

The same rules hold: one package per slice, no two open issues edit the same file, contracts first. Two additions:
- **Taking over a package.** Once a phase has merged, a later issue may change a package from an earlier phase. Its
  issue names the files it touches, and no other open issue may touch them.
- **New contracts are additive.** New tables, columns, plan fields and prompt kinds are added, never renamed. The
  hand-off schemas stay at their versions (#18: additive fields don't bump the version).

### 7.1 Phase 3: ramp, post-class quiz, confusable pairs, re-intake

| Package | Change | Deciding tickets |
|---|---|---|
| `plan` (taken over) | ramp, practice tests, review quiz, missed days, corrections, contrasts, reading slot hook | [Daily rhythm](https://github.com/amierlorenz/articulate/issues/9), [Deciding what matters](https://github.com/amierlorenz/articulate/issues/10) (finals), [Re-intake when Canvas files change](https://github.com/amierlorenz/articulate/issues/23), [Confusable pairs across decks](https://github.com/amierlorenz/articulate/issues/41) |
| `postclass` | :15/:45 post-class quiz | [Daily rhythm](https://github.com/amierlorenz/articulate/issues/9), [Model routing and budget](https://github.com/amierlorenz/articulate/issues/11) |
| `intake/library` (taken over) | the judge also picks a contrast group | [Confusable pairs across decks](https://github.com/amierlorenz/articulate/issues/41) |
| `intake/contrast` | nightly contrast writer, `kind: contrast` prompts | [Confusable pairs across decks](https://github.com/amierlorenz/articulate/issues/41), [Definitions in decision groups?](https://github.com/amierlorenz/articulate/issues/19) |
| `intake/reintake` | stage `reintake`: diff, re-anchor, buckets | [Re-intake when Canvas files change](https://github.com/amierlorenz/articulate/issues/23) |
| `tasks` (taken over) | lecture-directive tasks (Q9) | [Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20) |

#### Plan, additive fields (`encore.plan` v1)

```python
class PracticeTest(BaseModel):
    examId: str
    itemCount: int                       # 15–20
    freeRecallMin: float = 0.5           # mix like past exams, never below half free recall

class ReviewQuiz(BaseModel):
    examId: str
    conceptIds: list[str]                # core concepts of the exam, short

class Plan(...):                         # new fields, all optional
    practiceTest: PracticeTest | None = None
    reviewQuiz: ReviewQuiz | None = None
    reviewLimit: int | None = None       # backlog spread: at most this many due reviews today
    reviewPriority: list[str] = []       # exam ids whose concepts jump the backlog
    newPaused: bool = False              # backlog doesn't fit the budget yet
    holdNewForExams: list[str] = []      # last 2 days before an exam: no new for it
```

Rules, all computed in plain code from `state.json`, the attempts ledger and the content tables
([Daily rhythm](https://github.com/amierlorenz/articulate/issues/9)):
1. **Ramp:** an exam inside 10 days (14 for a final) puts its unit concepts first in `new` and sets `examFocus` with
   `rampDay`. `budgetMinutes.planned` may rise to 35; `readingShare` is 0 while any ramp is on.
2. **Practice test** on ramp days 9, 6 and 3 (every ~3 days), exam scope, `itemCount` 15–20. **Review quiz** the day
   before. **No new** for that exam in its last 2 days (`holdNewForExams`).
3. **Finals:** scope is every unit; a concept emphasised in 2+ units or on a midterm counts one tier up in the final's
   ordering ([Deciding what matters](https://github.com/amierlorenz/articulate/issues/10)). A final study guide overrides like any guide.
4. **Missed days:** a day with no attempts expires its plan. The next plan sets `reviewLimit` to what fits the
   budget, most overdue and core first; `reviewPriority` lists ramp exams; `newPaused` stays on until the backlog
   fits. After 3 days in a row with no session, one task `study:idle:<YYYY-MM-DD>` (once per idle streak).
5. **Corrections** ([Re-intake when Canvas files change](https://github.com/amierlorenz/articulate/issues/23)): a prompt re-issued with a new id goes first in
   `new` with `reason: correction`, outside the ≤10 cap.
6. **Post-class skipped:** its concepts lead the next `new` with `reason: postclassSkipped`.
7. **Contrasts** ([Confusable pairs across decks](https://github.com/amierlorenz/articulate/issues/41)): eligible once both members are introduced and each has a
   review ≥1 day after introduction; both core or standard; ≤2 a day inside the cap; in an exam's scope only when
   both members are in its units.
8. **Interval cap:** 30 days while the course is `active`; dropped once its final has passed.

Encore serves these fields in [Serve ramp fields from the daily plan](https://github.com/amierlorenz/encore/issues/41) (Q14).

#### Post-class quiz (`postclass`)

Runs in `watch` (registry order 140), after `pocket.poll`.

1. For each Classes event that ended in the last 18 h, with an `active` course key and no row in `postclass_quizzes`
   (or `waiting`), look for its class recording (the `calendar` match). No ended class → exit without calling Pocket.
2. Once the transcript is archived: one `postclass_quiz` call (Flash Lite, class window only) returns 3–5 closed-book
   items, each `{ concept: { title, kind, explanation: Claim }, prompt: { kind, ...content } }`, with transcript
   citations `{ startSec, endSec, quote }` checked exactly against the archived segments ([Transcript-only concepts](https://github.com/amierlorenz/articulate/issues/39)'s
   rule). Mostly free recall.
3. The `library` public matcher (embedding shortlist → judge, from the daytime reserve) attaches each item to an
   existing concept or makes a new one at `tier: standard` (re-tiered at 02:00).
4. Write `import-<batchId>.json`, then `postclass-<lectureId>.json` (`kind: postclass`, `importBatch` = that batch),
   then `open -g -b com.amierlorenz.Encore`. Lecture id: `<course>.l.<YYYY-MM-DD>`.
5. The 02:00 run takes over any lecture still `waiting`: status `taken_over`, and the normal path runs (slides only if
   there's still no transcript, [Deciding what matters](https://github.com/amierlorenz/articulate/issues/10)).

```sql
CREATE TABLE postclass_quizzes (
  lecture_id TEXT PRIMARY KEY, course_key TEXT, event_id TEXT, recording_id TEXT,
  status TEXT,                  -- waiting | built | taken_over | failed
  batch_id TEXT, plan_id TEXT, created_at TEXT, built_at TEXT
);
```

Encore's side (introducing the concept on answer, first review ≥1 day later with `calibrated: false`, the "post-class
quiz ready" and mid-afternoon notifications) is [Post-class quiz from Articulate](https://github.com/amierlorenz/encore/issues/42) (Q14).

#### Confusable pairs (`intake/contrast`, `intake/library`)

- **Library change:** the per-deck judge call also gets each shortlisted concept's same-course groups (name,
  `question`, members) and returns `group: <id> | new` per confusable match. Membership is written to
  `contrast_groups`. Cross-course matches stay judge notes.
- **Contrast writer:** one batched `contrast_writer` call a night for every new pair or newly joined member. Input per
  pair: both concepts' claims with quotes and the cited pages (or transcript lines) from each source. Output: a
  `decidingFeature` claim whose citations each carry their own `sourceFileId`; `intake/cite` checks each against its
  own file.
- **No citable feature:** the pair stays in the group uncontrasted, logged, never flagged; retried when either member
  gets newly matched material, up to 3 tries.
- **Prompt:** `kind: contrast`, `content { conceptIds: [a, b], groupId }`, id `contrast:<lower id>:<higher id>`, a new
  id when the deciding feature changes. Tier = the lower member's.
- A retired or `out` member suspends its group's contrasts; a group with <2 active members goes dormant. "Split the
  pair" on the weekly list tombstones the membership (`tombstones.kind = 'pair'`).

```sql
CREATE TABLE contrast_attempts (
  pair TEXT PRIMARY KEY,        -- "<lower id>|<higher id>"
  group_id TEXT, tries INTEGER, last_try TEXT, status TEXT,   -- pending | written | given_up
  reason TEXT
);
```

#### Re-intake (`intake/reintake`)

Takes items at stage `reintake` ([§3.5](#35-source--item--handler)), from [Re-intake when Canvas files change](https://github.com/amierlorenz/articulate/issues/23):
1. Old version never finished Shape → mark it `superseded`; the new version goes to `convert` as a new file.
2. Otherwise convert the new version (the `convert` public function), diff page text against the archived
   `.pages.json` (whitespace and formatting ignored), and run the `extract` (or `guides`) public function on changed
   and added pages only, with the titles of concepts already cited from this file. More than 30 % of pages changed →
   the whole file.
3. Re-anchor every citation from the file by searching for its quote in the new version.
4. Bucket each concept: unchanged (nothing), enriched (auto), changed (auto if the new claim passes the citation
   check; weekly undo line), new (normal path), unsupported (weekly keep / retire; keeps shipping).
5. The judge says whether the expected answer changed: same → regenerate under the same `prompt_id`; changed → retire
   the old prompt, new id, first in the next plan ([§7.1](#plan-additive-fields-encoreplan-v1) rule 5).
6. Old versions stay archived with `superseded_by`. No re-intake after a course's final. Inside the 2-day freeze,
   corrections ship and new concepts wait. An open "card is wrong" flag on a changed concept closes as "superseded by
   source update" with a weekly line.

The `extract` and `guides` public functions must accept a page subset and context titles; if #61 or #62 didn't expose
that, the re-intake issue adds it (taking over the named file).

#### Lecture-directive tasks (`tasks`)

At 02:00, class windows of lectures go through `prompts/task.md`'s lecture variant on `qwen3:8b`: instructor
directives with a concrete action only. A directive that looks like a Canvas assignment (same course, an assignment
due within ±2 days, from the `intake/canvas` public list) is dropped. A date change ("quiz moved to Friday") becomes a
weekly flag line, not a task.

### 7.2 Phase 4: voice commands, Literature notes, people, transcript-only concepts

| Package | Change | Deciding tickets |
|---|---|---|
| `commands` | detect, parse, resolve, act, receipts, undo | [Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29), [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31) |
| `literature` | claim-breakdown Literature notes | [Claim breakdown from a guest speaker](https://github.com/amierlorenz/articulate/issues/30), [The new vault workflow](https://github.com/amierlorenz/articulate/issues/8) |
| `people` | `People/` stubs | [Person notes for speakers and authors](https://github.com/amierlorenz/articulate/issues/33) |
| `intake/transcript` | transcript-only concepts | [Transcript-only concepts](https://github.com/amierlorenz/articulate/issues/39), [Deciding what matters](https://github.com/amierlorenz/articulate/issues/10) |
| `inbox`, `tasks` (taken over) | skip spans a command used up or put hands-off | [Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29), [Non-class recordings with no command](https://github.com/amierlorenz/articulate/issues/42) |

#### Commands (`commands`, registry order 110: before `inbox` and `tasks`)

1. **Detect:** fuzzy wake-phrase search ("Articulate", "main agent", plus a growing mis-hearing list in
   `src/articulate/commands/wake_variants.txt`) over each new transcript; in class recordings only the first and last
   2 min. `command-confirm` (`qwen3:8b`) confirms an imperative addressed to the agent and finds where it ends.
2. **Parse:** `command-parse` (`qwen3:8b`) → `{ action, target, span, speakers, instructions }`, action one of
   `literature_notes`, `note`, `task`, `hands_off`, `cancel`.
3. **Target:** recordings ending before the command; "last/latest/earlier today" look back 72 h, dates and weekdays
   14 days; course key or title, people by title or transcript; no reference → the same recording if it has more than
   the command, else the previous one. Only a single clear match acts; otherwise a weekly flag with one checkbox per
   candidate (ticking re-runs it).
4. **Span:** embed the topic phrase against 2–5 min windows (class: Gemini Embedding 2; personal: `embeddinggemma`),
   apply position hints, grow while neighbours stay on topic, `span-trim` cuts to sentences with a confidence.
   Timestamps skip the search. >30 min or low confidence → flag.
5. **Speakers:** the command names them; spellings joined; `attribution` decides which sentences are theirs; unclear →
   no quote. The first instructor named for a course is written to `courses.toml` `instructor` (only when empty; a
   different later name → flag). `commands` is the only writer of that key.
6. **Act** (the whitelist, [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31)): `literature_notes` → `literature`;
   `note` → a dictated or summarised `Inbox/` note (`inbox-note` prompt); `task` → task queue, first-person only;
   `hands_off` → the span gets no automatic notes or tasks; `cancel`. Refused parts → an `Inbox/` note quoting them.
   More than 12 notes → flag the whole command.
7. **Report:** one `Runs.md` line per command (id, action, target, outputs, model, prompt hash, usage) and a receipt
   `Inbox/Articulate – <command title>.md` with a "☐ Undo this command" box. Undo (box or spoken) deletes only the
   untouched notes and person stubs it made and tombstones them; tasks stay.
8. **Timing:** `watch` detects and resolves; a target transcript not ready retries each pass until 02:00; after 24 h
   unresolved → weekly flag.
9. **Routing:** command recordings are personal (`qwen3:8b`). A class-recording target follows class routing (span on
   embeddings, notes on `lecture_literature`). A personal target's Literature notes use `personal_literature`
   (`claude -p`). Anything Private runs on local models only and never reaches Encore.

```sql
CREATE TABLE commands (
  command_id TEXT PRIMARY KEY, recording_id TEXT, start_sec REAL, end_sec REAL,
  action TEXT, target_recording_id TEXT, span_start REAL, span_end REAL,
  speakers_json TEXT, instructions TEXT,
  status TEXT,                  -- detected | waiting_target | resolved | done | flagged | undone | cancelled
  attempts INTEGER, receipt_id TEXT, prompt_hash TEXT, created_at TEXT, resolved_at TEXT
);
CREATE TABLE span_claims (       -- what inbox and tasks skip
  recording_id TEXT, start_sec REAL, end_sec REAL,
  kind TEXT,                    -- command | hands_off | used_by_command
  command_id TEXT
);
CREATE TABLE command_outputs (command_id TEXT, kind TEXT, ref TEXT);   -- note articulate_id, task external_id, ...
```

A command-only recording is claimed in full, so `inbox` and `tasks` skip it; that check is the `inbox`/`tasks`
take-over.

#### Literature notes (`literature`)

The recipe from `prototype/30-claim-breakdown:prototypes/claim-breakdown/RECIPE.md` becomes
`prompts/literature-notes.md`:
- One note per claim in `Literature Notes/`; title = the claim; restatements merged; anecdotes are evidence.
- Code writes the frontmatter (`tags: [literature]`, `source`, `created`, `recorded`, `agent: true`, `articulate_id`,
  `pocket_id`, `command_id`, `speaker`, and `course` for class targets); the model writes only title and body.
- Body: paraphrase, `## Citation` with verbatim quotes (`…` cuts, `[bracket]` fixes) at interpolated `≈H:MM:SS`,
  `## Speaker` (role at the time, context only), `## Questions for Amier` (1–3), the Derived Permanent Notes Dataview.
- The mechanical quote check (every piece outside `[…]` and `…` in the transcript, in order) gates each note; one
  retry with the error, then hold + flag.
- Links only to titles from the provided list (Literature, Permanent, `Concepts/`, hubs, `People/`, minus
  `private: true` in cloud runs).
- `Runs.md` logs `total_cost_usd` and usage per `claude -p` call.

#### People (`people`)

From [Person notes for speakers and authors](https://github.com/amierlorenz/articulate/issues/33): `People/<Full Name>.md` only when something links to it;
frontmatter `tags: [person]`, `aliases`, `role`, `affiliation`, `agent: true`, `articulate_id`, and the source of each
fact; a Dataview of notes whose `speaker`/`author` links here (and hubs whose `author`/`Channel` text contains the name
or an alias). Before creating: normalised fuzzy match on titles and aliases, scoped to the course or source; confident
→ reuse and add the alias (untouched notes only); unsure → weekly flag, no new note. Instructors come from
`courses.toml` first. From a Private recording → `private: true`, left out of cloud title lists and cloud matching.
`People.base` is created once if missing.

#### Transcript-only concepts (`intake/transcript`)

From [Transcript-only concepts](https://github.com/amierlorenz/articulate/issues/39):
- A Source over archived class windows emits one `lecture` item per class recording at 02:00, once its deck is
  matched, or after 3 nights without one (then the whole lecture is off-slide).
- Windows: cut at segment boundaries every ~180 s (cap 300 s; a tail under 120 s joins the previous one).
- One `transcript_concepts` call (text only, 20-min timeout on 3.8 Flash) with the deck concept list returns
  `{ newConcepts, spokenClaims, spokenHints, contrastGroups }` (schema in
  `prototype/39-transcript-concepts:prototypes/transcript-concepts/schema.py`).
- Citations `{ window, quote }` are checked exactly (case, whitespace and punctuation canonicalised) against Pocket
  text in the window ±1; code adds `recordingId`, `startSec`, `endSec`. Failing quotes fail closed as #39 lists.
- `correctsSlide: true` makes the spoken claim primary with a weekly undo line; unit-level hints with `scope`
  cross-check exam scope and dates; time share comes from cited windows.
- Items then go to stage `library`, like slides.
- A lecture with no transcript keeps the slides-only fallback (every concept standard) and a weekly line.

### 7.3 Phase 5: reading, YouTube, LeetCode, evals and tuning

| Package | Change | Deciding tickets |
|---|---|---|
| `reading` | Literature notes (and `quiz: true` Permanent notes) → prompts | [Quizzes from reading and goals](https://github.com/amierlorenz/articulate/issues/43) |
| `youtube` | "Articulate" playlist → `📺` Literature notes | [What GitHub, YouTube and Medium feed into](https://github.com/amierlorenz/articulate/issues/44), [Getting transcripts for watched YouTube videos](https://github.com/amierlorenz/articulate/issues/46), [Gemini reading watched YouTube videos by link](https://github.com/amierlorenz/articulate/issues/47), [Retest Flash Lite on YouTube links](https://github.com/amierlorenz/articulate/issues/48) |
| `leetcode` | `interview` course from `Drop/interview/` | [LeetCode problems as a source](https://github.com/amierlorenz/articulate/issues/25) |
| `evals` | `articulate eval tasks`, `articulate eval commands` | [Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20), [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31), [Non-class recordings with no command](https://github.com/amierlorenz/articulate/issues/42) |
| `tasks` (taken over) | Google Tasks "Articulate" list drain (Q11), feedback signals | [Task funnel into master-todo](https://github.com/amierlorenz/articulate/issues/7), [Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20) |
| `plan` (taken over) | reading slot, `macPractice` | [Quizzes from reading and goals](https://github.com/amierlorenz/articulate/issues/43), [LeetCode problems as a source](https://github.com/amierlorenz/articulate/issues/25) |

#### Reading (`reading`)

- **Which notes:** Literature notes Amier wrote or touched, minus `quiz: false` and `private: true`; untouched agent
  notes are not quizzed, except video notes (Q12) and notes a command makes from a class recording (Q13). Permanent notes only with `quiz: true`.
- **Courses:** one `book:<slug>` course per source hub, created when a quizzable note's `source:` points at it; videos
  and articles share `reading`. A Permanent note goes to its `from:` source's course, else `reading`.
- **Claims:** #22's claim shape with citations `{ locator, quote }` from the note's title, restatement and
  `## Citation` bullets; quotes checked exactly against the note. A book page in a bullet is display-only.
- **Model:** `claude -p` Sonnet on leftover budget after class work; held 7 days → `qwen3:8b`. Never Gemini.
- **Prompts:** every note counts as standard: a cue → claim recall prompt, plus an explain-why or apply prompt when the
  Citation or restatement supports it; title-only notes get the recall prompt only.
- **Edits:** per-section heading hash checked nightly; #23's rules (same id if the answer holds); a deleted note or
  `quiz: false` retires its prompts.
- **Plan:** one pooled reading slot inside ≤20 % (0 in ramps), ~2 new a day, round-robin across courses with unquizzed
  notes, `#reading` books first. A hub tag mismatch (e.g. `#finished` vs `#read`) → weekly flag.

#### YouTube (`youtube`)

- **OAuth:** `articulate setup` adds `youtube.readonly` to the same Google client (one re-consent).
- **Playlist:** a private playlist named "Articulate"; `playlistItems.list` + batched `videos.list` (~64 units a day).
  Watched = item `snippet.publishedAt`; Published = `contentDetails.videoPublishedAt`; Duration from `videos`.
  Ledger table `videos(video_id, playlist_item_id, status, watched_at, note_id, windows_done, error)`.
- **When:** last in `nightly` and `pass2`, **lowest priority of every Gemini call**: only leftover quota, never the
  daytime reserve. A note appears the morning after a video is added.
- **Calls:** one request per ≤30-min window. Videos ≤5 min → `youtube_short` (Flash Lite first); longer →
  `youtube_long` (3.6 Flash). Timestamps asked as `"M:SS"` strings, converted in code; clip times are absolute.
- **Non-public** (unlisted, private, removed) or refused → metadata-only stub note + weekly flag.
- **Note:** `Literature Notes/📺 <video title>.md`, tag `video`, `agent: true`, Channel, Title, URL, Duration,
  Published, Watched, TL;DW, timestamped notes with `?t=` links; `## Key Ideas` left empty for Amier. The Gemini URL call per
  window writes the timestamped notes; one Flash Lite text call writes the TL;DW (Q15).
- `quiz: false` on the note retires its cards; an untouched video note is quizzed by default (Q12).

#### LeetCode (`leetcode`)

- **Course:** `[courses.interview]` with no `canvas_id`, `public = true` (its drop files count as class material and
  use the Gemini chain; recordings still fail closed), `new_per_day = 2`, optional `[[courses.interview.exams]]` (an
  interview date) that turns on the ramp. Without a date it counts as a learning goal inside the ≤20 % slot.
- **Input:** one Markdown file per problem in `Drop/interview/`: statement with examples, optional URL (citation
  only, never fetched), `pattern:`, starter code, `implement: true`, your solution, notes. A pattern reference doc
  dropped once grounds use-when cues and contrasts.
- **Extract:** the pattern is a `method` concept in contrast groups; the problem is a cited worked example on it. With
  neither `pattern:` nor a solution, the model's label is gap-fill (held, weekly list).
- **Prompts:** `methodPick` and `setUp` always; `codeImplement` with `implement: true` or a solution;
  `codeComplexity` on your solution; no `fullProblem`.
- **Tests:** visible = the statement's examples. Hidden = 5–8 model-written inputs, expected values from **running** a
  reference solution (yours wins) and an independent brute force in `sandbox-exec` (no network, temp writes only, 5 s,
  memory cap, `uv` Python). Flag when the reference fails an example, the two disagree, or a run times out. Python only.
- **Plan:** `macPractice` lists ≤3 `codeImplement` prompts not tried in 14+ days ([Mac practice list from the daily plan](https://github.com/amierlorenz/encore/issues/32)).

#### Evals (`evals`)

- **`articulate eval tasks`:** labelled set in `DATA/evals/tasks/`, seeded once from ~20 non-Private recordings (8
  conversations or meetings, 6 voice notes, 6 lectures) by a high-recall pass that writes `Inbox/Task labels.md` for
  Amier to tick; grows from "wrong" signals (removed within 3 days, unticked 2 weeks) and 3 Pocket-caught misses a
  month. Gate: high-band precision ≥0.9 and not below the current version; high+medium recall drops ≤5 points.
- **`articulate eval commands`:** fixtures in `DATA/evals/commands/`, seeded with AI301 and ~10 uncommanded recordings
  ([Non-class recordings with no command](https://github.com/amierlorenz/articulate/issues/42)); grows from flagged or undone commands and deleted default notes.
  Gate: parse accuracy (action, target, span overlap ≥80 %), 100 % quote check, gate decision ≥90 %, `kind:` ≥80 %.
- Both run locally, never in CI. A `pre-push` hook (installed by `articulate setup`) runs the matching eval when
  `prompts/task*.md`, `prompts/command-*.md`, `prompts/inbox-*.md` or `prompts/literature-notes.md` changed, and
  refuses the push on a failed gate. Old recordings are re-scored, never re-extracted.

#### Google Tasks drain (`tasks`)

Each run drains the Google Tasks list "Articulate": each item goes through Add Task with `externalID = gtasks:<id>`
(a `due: YYYY-MM-DD HH:MM` line in its notes brings back the due time), then is deleted from Google. Needs the
`tasks` OAuth scope added to the Google client (one re-consent).

### 7.4 Cross-repo work per phase

| Phase | Repo | Issues |
|---|---|---|
| 3 | Encore | [Serve contrast prompts (kind: contrast)](https://github.com/amierlorenz/encore/issues/34), [Flag button with reason and suggested fix](https://github.com/amierlorenz/encore/issues/28), [Banner when Articulate's daily plan is stale](https://github.com/amierlorenz/encore/issues/33), [Export the library](https://github.com/amierlorenz/encore/issues/30), [Serve ramp fields from the daily plan](https://github.com/amierlorenz/encore/issues/41), [Post-class quiz from Articulate](https://github.com/amierlorenz/encore/issues/42) |
| 5 | Encore | [Mac practice list from the daily plan](https://github.com/amierlorenz/encore/issues/32) |

### 7.5 Registry orders for Phases 3–5

| Step | Order | Jobs |
|---|---|---|
| `commands.detect` | 110 | `watch`, `nightly` |
| `postclass.build` | 140 | `watch` |
| `tasks.lecture` (inside `tasks`'s line) | 130 | `nightly` |
| `intake.transcript` | 280 | `nightly`, `pass2` |
| `intake.reintake` | 290 | `nightly`, `pass2` |
| `intake.contrast` | 310 | `nightly`, `pass2` |
| `literature.write` | 500 | `watch`, `nightly` |
| `people.stubs` | 510 | `watch`, `nightly` |
| `reading.prompts` | 600 | `nightly`, `pass2` |
| `leetcode.tests` | 610 | `nightly`, `pass2` |
| `youtube.notes` | 690 | `nightly`, `pass2` (always last before `plan`) |

---

## Decisions made while writing this spec

These were open after the tickets. Amier answered them on 2026-11-19 (taking the recommendation each time). They
override the defaults above where they differ.

| # | Question | Answer |
|---|---|---|
| Q1 | Data dir: `Application Support/articulate` (#14) or `…/Articulate/transcripts` (#8)? | lowercase `articulate` for everything |
| Q2 | `models.toml` in iCloud (#11) or local (map)? | iCloud, next to `courses.toml` |
| Q3 | How does a shipped concept that becomes `out` reach Encore? | `status: retired` + `tier: out` (hidden, history kept, reversible by re-import) |
| Q4 | Where do exam dates and units come from? | Canvas assignments + posted announcements + `courses.toml` overrides ([§3.14](#314-exams-and-exam-dates)) |
| Q5 | Weekly list: `_system/Weekly Flags.md` (#8) or `Inbox/Intake flags.md` (#14)? | `Inbox/Intake flags.md` |
| Q6 | Real fixtures in git? | no: local `DATA/fixtures/real/` + committed manifest ([§4](#4-fixtures)) |
| Q7 | Does Phase 2 need Encore's state export? | yes: [Export schedule state and grading attempts](https://github.com/amierlorenz/encore/issues/29) joins Phase 2; [Export the library](https://github.com/amierlorenz/encore/issues/30) stays later |
| Q8 | encore#26 still says "iCloud hand-off folder" | comment posted on [encore#26](https://github.com/amierlorenz/encore/issues/26) pointing at #18's local folder |
| Q9 | Lecture-directive tasks in Phase 1? | no: Phase 1 sends tasks from non-class recordings; lecture directives join in Phase 3, once `intake/canvas` can drop Canvas look-alikes |
| Q10 | Fallback list tasks once master-todo#33 lands | sent through the intent; each fallback line is marked "sent ✓" |
| Q11 | Google Tasks "Articulate" list drain (#7) | Phase 5 |
| — | Spec-chosen names (launchd labels, Keychain accounts `canvas_token`/`google_oauth`, v2 id format) | kept as written |
| — | Pocket REST host | confirmed by `articulate setup` with one call |

No open questions remain for Phases 0–2.

### Decisions for Phases 3–5 (Q12–Q17)

Amier accepted every default on 2026-11-20.

| # | Question | Where it comes from | Decision |
|---|---|---|---|
| Q12 | Are untouched agent video notes quizzed? [What GitHub, YouTube and Medium feed into](https://github.com/amierlorenz/articulate/issues/44) says every video is quizzed by default; [Quizzes from reading and goals](https://github.com/amierlorenz/articulate/issues/43) says untouched agent notes are not. | #44 vs #43 | videos quizzed by default (#44 decided videos specifically); `quiz: false` opts out |
| Q13 | Literature notes a command makes from a **class** recording: [Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29) says they join the course's quiz pool as reading material; #43 says untouched agent notes aren't quizzed. | #29 vs #43 | quizzed in the course's pool at reading priority (their quotes are mechanically checked) |
| Q14 | Encore has no issue for the Phase 3 plan fields (`practiceTest`, `reviewQuiz`, `reviewLimit`, `reviewPriority`, `newPaused`, `holdNewForExams`) or for the post-class quiz (`kind: postclass` plans, introducing on answer, uncalibrated first review, the two notifications from [Daily rhythm](https://github.com/amierlorenz/articulate/issues/9)). | #9, #18 | filed as [Serve ramp fields from the daily plan](https://github.com/amierlorenz/encore/issues/41) (blocks #70) and [Post-class quiz from Articulate](https://github.com/amierlorenz/encore/issues/42) (blocks #71) |
| Q15 | Who writes a video note's TL;DW and timestamped notes? #44 first said "personal route only"; its amendment moves transcription to Gemini by URL but doesn't name the note writer. | #44, #47 | the same Gemini URL call per window returns timestamped notes; one Flash Lite text call writes the TL;DW (the video is public) |
| Q16 | When does `articulate eval commands` start gating prompt changes? [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31) says it gates every change to `prompts/`; [v1 scope and phase plan](https://github.com/amierlorenz/articulate/issues/49) puts evals in Phase 5. | #31 vs #49 | Phase 5; Phase 4 ships its prompts as version 1 with the fixtures seeded as it goes |
| Q17 | Is [Deciding what matters](https://github.com/amierlorenz/articulate/issues/10)'s separate lecture-to-slide embedding alignment, and #11's Flash Lite "rank lecture concepts" step, still built? [Transcript-only concepts](https://github.com/amierlorenz/articulate/issues/39) gets spoken claims on deck concepts, hints and time share from one call. | #10, #11 vs #39 | not built: #39's call covers them |
