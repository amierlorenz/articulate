# Articulate v1: requirements

What each phase delivers and how to check it. How it's built (packages, contracts, schemas) is in
[SPEC-implementation.md](SPEC-implementation.md); section numbers like §3.6 point there.

**A phase is done when** the 02:00 job has run on real data and Amier has used the result for 3 days
([v1 scope and phase plan](https://github.com/amierlorenz/articulate/issues/49)). The checks below are what must pass
before those 3 days start. Checks marked **F** run against committed fixtures with local `pytest` (no CI); **R** run
against real data (`@pytest.mark.real`, or a manual run noted in the PR); **U** need Amier.

Dates: Phase 0 buildable by about 2026-11-22; Phase 1 and 2 start together after it; Phase 2 in Encore by about
2026-12-01; finals 2026-12-07 to 2026-12-11.

Status: **Phases 0–5 written.** Open questions Q12–Q17 (SPEC-implementation) carry defaults.

---

## Phase 0: skeleton

**Delivers.** The `core` package with every contract in SPEC-implementation §3 as code, the fixtures folder, the
LaunchAgents, and a `nightly`/`pass2`/`watch` that run end to end with no slices registered: they read config,
write a `runs` row, a `Runs.md` line and `Dashboard.md`, and exit. One agent builds it serially.

| # | Check | How |
|---|---|---|
| 0.1 | `uv sync` and `uv run pytest` pass on a clean checkout; `ruff check` is clean | F |
| 0.2 | `courses.toml`, `models.toml` and `articulate.toml` from §3.2 load; a missing file or a bad value names the file and key, raises a health row and stops only the jobs that need it | F |
| 0.3 | `articulate setup secrets` stores and `core.secrets` reads each Keychain item in §3.3; no secret appears in logs, `Runs.md` or exceptions (test greps the log for the fixture secret) | F, U |
| 0.4 | Migrations apply on an empty DB and are discovered from a dummy slice's `migrations/` folder without touching core files | F |
| 0.5 | Pipeline: a dummy Source and Handler move an item through 3 stages; `hold` keeps the stage; 3 `fail`s in a row add one weekly flag; a new version marks an unfinished old one `superseded`; a run killed mid-stage resumes at that stage | F |
| 0.6 | Privacy: a `personal` or `private` request never reaches a `gemini-*` model even when `models.toml` puts one first; `private` never reaches `claude` | F |
| 0.7 | Quota ledger: every attempt is a `model_calls` row written before the request; 503 → one retry then next model; timeout → next model; `PerDay` 429 → model exhausted until next midnight Pacific; per-minute 429 → wait `retryDelay` once; 404 → skipped + health row; `max_tokens` and schema 400 return to the caller | F (`tests/fixtures/gemini/`) |
| 0.8 | Reserve: in `pass2`, Flash Lite stops at `rpd − 40` and embeddings at `rpd − 50`; in `nightly` and `watch` the reserve isn't held back. Pacific-date boundary correct at 02:59 and 03:01 Eastern, across both DST changes | F |
| 0.9 | Claude gate: `/usage` parsed by `kind`; each fail-closed case in §3.6 holds Claude; >15 calls a night holds; `claude@ramp` only with `exam_in_ramp`; `rate_limit_event` `allowed_warning` stops the run's Claude use | F (`tests/fixtures/claude/`) |
| 0.10 | One real call per provider: Gemini Flash Lite (tiny prompt), `claude -p "/usage"`, Ollama `qwen3:8b`; each lands in `model_calls` / `claude_usage`; `articulate quota` shows them | R |
| 0.11 | `articulate setup` confirms the Gemini model ids with ListModels and the Pocket REST host with one call, and writes the confirmed values | R, U |
| 0.12 | Hand-off schemas: `articulate schemas` writes all six to `schemas/`; every fixture in `tests/fixtures/handoff/` round-trips; an unknown version is refused; extra fields are ignored; writes are tmp + rename | F |
| 0.13 | Vault writer on a temp vault: create-only, title clash → " (2)", tombstoned id never recreated, regenerate refused once the file is edited or moved, `scan()` re-finds a moved note, `write_system` skips an unchanged hash | F |
| 0.14 | Weekly flags: `Inbox/Intake flags.md` renders open flags with ids; a ticked box calls the registered resolver once; unticked lines carry over; one `study:flags:<week>` task is enqueued when flags exist | F |
| 0.15 | Health: each interrupting kind in §3.10 enqueues exactly one `health:<kind>:<date>` task per incident; recovery logs once to `Runs.md` and never closes the task | F |
| 0.16 | `Dashboard.md` after a real `articulate run nightly`: status line, jobs table, model budget, held items, Encore hand-off, dependencies; a crashed run (killed) shows as "crashed" on the next run | R |
| 0.17 | LaunchAgents installed by `articulate setup`: `launchctl print` shows all three; `nightly` fires at 02:00 with the screen locked and `pmset repeat wake` set; the run lock makes `watch` exit while `nightly` runs | R, U |
| 0.18 | Fixtures: `tests/fixtures/` has every folder in §4; `real-manifest.toml` lists the real files with sha256; `pytest -m real` reports which are present | F, R |

**Done:** three nights of 02:00 and 03:05 runs with no slices, each leaving a `Runs.md` line and a fresh
dashboard.

---

## Phase 1: Pocket → Inbox notes and tasks (first in daily use)

**Delivers.** Every non-Private, non-class Pocket recording becomes at most one fleeting `Inbox/` note within an
hour of transcription, and the first-person tasks in it reach master-todo (or the fallback list until master-todo's
intents ship). Class recordings are recognised and archived for Phase 2+. Not in Phase 1: voice commands, Literature
notes, person notes, task eval gate, lecture-directive tasks (Phase 3, Q9).

Cross-repo: [feat: generalize task source and external ID](https://github.com/amierlorenz/master-todo/issues/32) →
[feat: App Intents for external task producers](https://github.com/amierlorenz/master-todo/issues/33), built in the
same phase.

### Pocket and calendar

From [Pocket for class lectures](https://github.com/amierlorenz/articulate/issues/2),
[Check Pocket's REST API with the real key](https://github.com/amierlorenz/articulate/issues/15),
[Model routing and budget](https://github.com/amierlorenz/articulate/issues/11),
[Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29).

| # | Check | How |
|---|---|---|
| 1.1 | `watch` polls `/public/recordings` for the last 3 days at :15 and :45 and fetches details only for new or changed recordings; a poll on a class day costs ≤10 requests; a 429 sleeps until `X-Ratelimit-Reset` | F, R |
| 1.2 | Private fails closed: a recording in the Private folder **or any folder in its subtree**, or tagged `private` (by id, or by name ignoring case), is never fetched in detail, never archived beyond id and time, never sent to any model. If the configured folder or tag id is missing from `/public/folders` or `/public/tags`, the whole Pocket ingest stops with a health row | F |
| 1.3 | `private_events`: a recording overlapping a matching event or calendar (≥50 % of the recording or ≥20 min) is Private | F |
| 1.4 | Sensitivity check: before any non-class recording goes to a cloud model, local `qwen3:8b` checks it (health, therapy, relationships, finances, others' private matters); a hit makes it Private and logs a `Runs.md` line. (Phase 1 sends nothing personal to the cloud, but the check runs so Phase 4 inherits it.) | F, R |
| 1.5 | Class match: overlap with a "Classes" event by the rule in 1.3, largest overlap wins, course key from the event description's first line; only class start → class end + 10 min is `class`, the rest becomes a `personal` span ("class tail") | F (`tests/fixtures/calendar/`) |
| 1.6 | Merged copies: a detail with `transcript.metadata.merged` and an original at the same `recording_at` is skipped; a merged copy with no original is kept and its timeline is not treated as wall-clock | F |
| 1.7 | `duration: null` falls back to the last segment's `end`; `pending` recordings retry each poll and give up after 3 days with a dashboard row | F |
| 1.8 | Every non-Private recording is archived once as `transcripts/YYYY/MM/<id>.json.gz` before any processing; re-polls don't rewrite it | F, R |
| 1.9 | Real check: one week of Amier's recordings (Nov 17–23) classified with no Private recording read past its list row, every lecture matched to its course | R, U |

### Inbox notes

From [Non-class recordings with no command](https://github.com/amierlorenz/articulate/issues/42),
[Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31),
[The new vault workflow](https://github.com/amierlorenz/articulate/issues/8).

| # | Check | How |
|---|---|---|
| 1.10 | Gate on `qwen3:8b`: no transcript, <~15 words, or mostly playback → no note; nothing beyond tasks → tasks only; otherwise one note. No minimum length and no daily cap | F, R |
| 1.11 | Note shape: `Inbox/`, tags `fleeting` + `agent`, frontmatter `agent: true`, `articulate_id`, `pocket_id`, `kind:` (`idea`/`meeting`/`journal`/`content`/`other`), `created`, `recorded`; a short paraphrase plus 1–3 `≈H:MM:SS` quotes | F |
| 1.12 | Every quote passes the mechanical quote check against the archived transcript (exact after whitespace normalisation, `…` cuts and `[bracketed]` fixes allowed); a failing quote is retried once with the error, then the note is held and flagged | F |
| 1.13 | Meetings: overlap with a non-class event → `kind: meeting`, the event title as note title, `event:` field, `Decisions` and `Open questions` bullets when present, attendees as plain names (no `People/` links) | F |
| 1.14 | Recordings over ~30 min are summarised in chunks and merged into one note with ≤~5 points | F, R |
| 1.15 | Class tails get the same path; the note says which recording it was cut from | F |
| 1.16 | The note is written before the same pass sends its tasks; one `Runs.md` line per recording; a deleted note is tombstoned and never recreated | F |
| 1.17 | Nothing in this phase writes outside `Inbox/` and `_system/`; nothing is sent to Encore | F |

### Tasks

From [Task funnel into master-todo](https://github.com/amierlorenz/articulate/issues/7) and
[Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20).

| # | Check | How |
|---|---|---|
| 1.18 | Extraction on `qwen3:8b` takes first-person commitments ("I'll…", "I need to…", "remind me to…") and requests Amier accepts; never things Amier asks others to do, never musings, never Private; unanswered requests → weekly flags | F |
| 1.19 | Task fields: imperative title ≤80 chars; `dueAt`/`hasDueTime` only when spoken, resolved against the recording date ("Thursday" = next Thursday after it); `notes` = quoted line, recording title and date, `@mm:ss`; `link` = `obsidian://` URI of the note when one exists; `source = articulate`; `externalID = task:<recordingId>:<startSec>` | F |
| 1.20 | Bands: high → sent; medium → a checkbox in `Inbox/Intake flags.md` (ticking sends it); low → logged only. Confidence never appears on the task | F |
| 1.21 | Dedupe: each recording extracted once (`prompt_version` recorded); merged copies skipped; a close embedding match (`embeddinggemma`) to an open task sent in the last 14 days is dropped and logged | F |
| 1.22 | Sending, once master-todo#33 exists: `shortcuts run "Add Task"` with JSON in, JSON out; `created`/`existing`/`skipped` all recorded; the intent wins when it disagrees with the ledger; a failed send stays `pending` and retries; 3 failed runs in a row → dashboard row | F (fake `shortcuts`), R |
| 1.23 | Fallback until master-todo#33 ships: high-band tasks go to `Inbox/Articulate tasks.md` as checkbox lines with the same fields; when the intent works, those still `fallback` are sent and their line is marked "sent ✓" (Q10) | F |
| 1.24 | Nightly state poll with `Get Task State` for open ledger rows: stops at done, removed or 90 days; done ticks the vault checkbox where one exists; a removed task is never re-sent | F, R |
| 1.25 | Health and study-chore tasks queued in Phase 0 drain through the same sender | F |

### Phase 1 done

The 02:00 and :15/:45 jobs run on real Pocket data; Amier has used the Inbox notes and the tasks for 3 days, and
no Private recording has reached a note, a task or a model.

---

## Phase 2: class quizzes for finals

**Delivers.** For every active course, the nightly run takes new Canvas files and dropped study guides and past
exams, turns them into cited concepts and prompts, ships them to Encore as v2 import batches, and writes a simple
`plan.json` (due reviews, then up to 10 new concepts, core first, nearest exam first). Encore imports on the Mac
and serves "Today's session" on the iPhone. Target: in Encore by about 2026-12-01.

Cross-repo, same phase: [Import JSON v2 only](https://github.com/amierlorenz/encore/issues/25),
[Watch the hand-off folder on the Mac](https://github.com/amierlorenz/encore/issues/26),
[Today's session from Articulate's daily plan](https://github.com/amierlorenz/encore/issues/27),
[Export schedule state and grading attempts](https://github.com/amierlorenz/encore/issues/29) (Q7). Encore's identity
PR (`feat/new-app-identity`, [Create the Encore repo from MieracleStudy](https://github.com/amierlorenz/articulate/issues/17))
must be merged and the CloudKit container registered first.

Not in Phase 2: the 10-day ramp and practice tests, post-class quiz, cross-deck contrasts, re-intake of changed
files (a changed file is queued at stage `reintake`, not re-extracted), transcript-only concepts, lecture alignment. Without lecture
alignment, tiers come from slide hints, guides and past exams only; concepts with no signal are `standard`
([Deciding what matters](https://github.com/amierlorenz/articulate/issues/10): slides-only fallback).

### Pick and exam dates

From [Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14),
[Files linked only from Canvas Pages](https://github.com/amierlorenz/articulate/issues/24),
[Study guide and past exam output](https://github.com/amierlorenz/articulate/issues/32) (routing), §3.14.

| # | Check | How |
|---|---|---|
| 2.1 | Discovery from four places, deduped by file id: module File items, `/files/:id` links in module Page bodies, the Files tab when readable (documents only; skip images, video, `course_image`/`course_video`/`icons`; folder name as role hint), and attachments of **posted** announcements (title as role hint) | F (`tests/fixtures/canvas/`) |
| 2.2 | Real check on Fall 2026: finds Whitman Ch02 (CSC-382, module Page link), the HCAI reading (CSC-430), all PHY-203 slides and 8 practice tests (Files tab), CSC-395's past final (Files tab), and CSC-382/CSC-301's study guides (announcements); skips the CSC Department shell | R |
| 2.3 | `courses.toml` skip rules: `default_skip` globs, a course's `skip` overrides them; skipped modules never download | F |
| 2.4 | `Drop/<course_key>/` files are copied into the archive by content hash; a re-drop or rename is a no-op; nothing in `Drop/` is moved or deleted | F |
| 2.5 | Slots per #23 recorded for every file; an identical re-upload does nothing; a changed file (known slot, new content hash) gets a `Runs.md` line and becomes an item at stage `reintake`, where it waits: no handler takes it until Phase 3 | F |
| 2.6 | Routing check (Flash Lite, first 2 pages, filename and folder or announcement title as hints) sorts each file to `slides`, `guideOrExam` or `other`; `other` → weekly flags; .xlsx, video, links skipped with a flag | F, R |
| 2.7 | Queue order: nearest dated exam first, undated after; the backlog rolls to the next night; ceilings set the volume | F |
| 2.8 | Exams per §3.14: Canvas assignments filtered by name and group; posted announcements read nightly and parsed with a verbatim-quote check; unposted announcements ignored; precedence `courses.toml` → newest announcement → `due_at` (window → `unlock_at`); a changed date logs a `Runs.md` line; an undated exam raises one flag line | F, R |
| 2.9 | Real check: CSC-301 final Mon Dec 7 19:40, PHY-203 final Thu Dec 10 08:00, CSC-382 final Fri Dec 11 13:00 from announcements; CSC-430 final Dec 11 from the assignment; MAT-305's stale "Tuesday, December 9" ignored; MAT-305 final Dec 7 from `courses.toml`; PHY-203 Test 4 history Nov 1 → Nov 2 | R |
| 2.10 | Canvas 401/403 on the token → health row + interrupting task; no network → run fails, same-day task | F |

### Convert

From [Test DeckCraft on real Canvas decks](https://github.com/amierlorenz/articulate/issues/28),
[Run the Keynote export once](https://github.com/amierlorenz/articulate/issues/21),
[Running class intake unattended](https://github.com/amierlorenz/articulate/issues/14),
[Spotting a damaged text layer](https://github.com/amierlorenz/articulate/issues/40).

| # | Check | How |
|---|---|---|
| 2.11 | PPTX via DeckCraft: `open -n -g -j` with `DECKCRAFT_NO_PREFS`/`DECKCRAFT_NO_RECOVERY` and a free port; `file.open` → `document.inspect` (notes, hidden, builds) → `file.export` PDF with `includeHidden:true` → `file.close` → `app.quit`; commands through `{"method":"command",…}` | F (`tests/fixtures/deckcraft/`) |
| 2.12 | Keynote fallback on each trigger: no port in 10 s or process exit; `ok:false` or a 60 s timeout; `lostSlides`/`damagedMedia` non-empty; pages ≠ slides; empty text layer on >20 % of titled slides. Not on `missingFonts` or PDF size. Keynote script with #21's fixes (no `date:false`; `launch`, wait for `count of documents`, then `open`; close on error); Keynote presenting or unconsented → hold until next night | F, R |
| 2.13 | Real check: all 8 decks from #28 give pages = slides with DeckCraft, 0 fallbacks, the hidden slide kept; from a LaunchAgent with the screen locked | R |
| 2.14 | .docx/.doc/.rtf via `wordcraft-cli`; PDFs via pypdfium2; slide text from the PDF text layer, notes from `document.inspect` | F, R |
| 2.15 | OCR with `ocrmac` (accurate, 2×) in a short-lived subprocess, only on pages with no text layer or mostly picture; cached by page hash | F, R |
| 2.16 | Damaged-layer signals (glyph-sized image in a text line, inline equation picture, ASCII punctuation in a math font) set `layer_untrusted` per page; real check: 5/5 silent pages flagged with 1 false flag on #40's 9 files, 0 on PowerPoint/Keynote decks | F (`tests/fixtures/pdf/`), R |
| 2.17 | `.pages.json` written into the archive; work PDFs and PNGs deleted at the end of the run | F |

### Extract, guides and the citation check

From [Extract output schema](https://github.com/amierlorenz/articulate/issues/22),
[Extract recall on long decks](https://github.com/amierlorenz/articulate/issues/36),
[Study guide and past exam output](https://github.com/amierlorenz/articulate/issues/32),
[Rerun guide and exam validation on 3.6 and 3.8 Flash](https://github.com/amierlorenz/articulate/issues/34),
[Vision transcript in the citation check](https://github.com/amierlorenz/articulate/issues/35),
[Formula check for guide and exam quotes](https://github.com/amierlorenz/articulate/issues/37).

| # | Check | How |
|---|---|---|
| 2.18 | Slides: one call per deck (≤40 pages) on the `extract` chain returns `{ concepts, contrastGroups }` in #22's flat all-required schema with the control-character pattern; the prompt includes the named-concepts line from #36 | F |
| 2.19 | Code checks reject or log as #22 says: control chars, stray `\\`, unbalanced `$`, 1–3 citations per cited claim (extras logged), `gapFill` ⇒ no citations, method fields only on methods, group keys exist; an uncontrasted pair is logged, not flagged | F |
| 2.20 | Long decks: >40 pages → ⌈n/30⌉ equal ranges, one call each with the "pages a–b of n" note, remapped; same-title concepts merged, groups deduped by member set, never across the cut; `MAX_TOKENS` or a schema 400 splits a range again once | F |
| 2.21 | Guides and exams: one call per file on the `guide_exam` chain with the 1200 s timeout returns `{ document, items }`; exam and unit names checked against the course lists; empty or invalid exam hint → weekly flag; lettered parts after a `context` stem; a non-`context` quote holding both `(a)` and `(b)` is logged; `number` is a label and identity is reading order | F |
| 2.22 | Answer keys are joined to their exam by exam hint, filename stem and question order; an ambiguous join → weekly flag | F, R |
| 2.23 | Citation rule for every quote (slides, guide questions, answers): text layer (skipped on untrusted pages) → OCR → VL, exact after canonicalising, operators kept, boundary near math, part labels ignored at the edges, joined retry of a question and answer cut from one line, label-only quotes fail; `verifiedBy` recorded; VL (`qwen3-vl:8b-instruct`, `keep_alive: 0`) only on pages with a still-failing quote, cached by page hash; Ollama down → flag + health row | F |
| 2.24 | Real check, citations: MAT-305 Ch. 3 100 %, Ch. 2 63/64 (the reject is the misplaced Theorem 5 line), PHY-203 Ch. 9 100 %; Ch. 2's four `P(A B)` citations fail on the untrusted pages | R |
| 2.25 | Real check, guides: PHY-203 Test 4 Practice on 3.6 Flash gives 26 items, 26/26 question quotes, ≥21/22 answers; MAT-305 T3 Review + solutions ≥316/318 true quotes and 0 altered-formula passes on #37's labelled set | R |
| 2.26 | Real check, recall: Whitman Ch. 3 core recall ≥42/52 against `gold/csc382-whitman-ch03.json`; PHY-203 Ch. 9 ≥11/15 against its gold list | R |
| 2.27 | An unverified guide item counts toward the question mix only; a file with >30 % unverified items → weekly flag | F |

### Library, tiers and prompts

From [Deciding what matters](https://github.com/amierlorenz/articulate/issues/10),
[Definitions in decision groups?](https://github.com/amierlorenz/articulate/issues/19),
[Daily rhythm](https://github.com/amierlorenz/articulate/issues/9),
[Model routing and budget](https://github.com/amierlorenz/articulate/issues/11).

| # | Check | How |
|---|---|---|
| 2.28 | Matching: Gemini Embedding 2 shortlist → Flash Lite judge against the course's concepts and the night's other candidates; the judge returns new / same (merge or enrich) / confusable; Extract never sees library titles | F |
| 2.29 | Held back ([Carrying MieracleStudy's intake map over](https://github.com/amierlorenz/articulate/issues/5): the blocking set): merge candidates, matches to an existing concept that need a choice, gap-fill; everything else ships. Held items get weekly flag lines with keep / merge / drop | F |
| 2.30 | Tiers with quoted reasons: an exam hint → core; a dismissal → background; a matched guide item → at least standard and `on_guide`; concepts in a guide's units not on it → background; a matched past-exam question → one tier up; admin, title and agenda slides and dismissed-not-on-guide → out; no signal → standard | F |
| 2.31 | Gap-fill: an unmatched guide item becomes a held gap-fill candidate (a printed answer can be its source); no source → weekly flag; nothing is quizzed without a cited source | F |
| 2.32 | Prompts per tier: core 3, standard 2, background 1 (only when the new cap has room), out none; ≥½ of a course's active prompts are free recall; definition prompts on Flash Lite + verify; method prompts on the Flash chain + verify on Flash Lite; deterministic checks (no alias leak, options resolve to real concepts, payload validates) | F |
| 2.33 | Style examples: ≤3 verified items of the same kind from the same course, newest exam first; any prompt sharing an 8+ word run with an example is rejected | F |
| 2.34 | Prompts ship only with their concept; a held concept holds its prompts | F |

### Hand-off and plan

From [Hand-off folder layout and file formats](https://github.com/amierlorenz/articulate/issues/18),
[Contract between Articulate and MieracleStudy](https://github.com/amierlorenz/articulate/issues/13),
[Daily rhythm](https://github.com/amierlorenz/articulate/issues/9).

| # | Check | How |
|---|---|---|
| 2.35 | Each night that has shippable content writes one `import-<batchId>.json` (v2, delta, validated against `schemas/encore.import.v2.json`) by tmp + rename; nothing else writes `to-encore/` | F |
| 2.36 | After writing, `open -g` Encore and wait ≤3 min for a `state.json` newer than the launch; on timeout plan from the last state and flag; state >48 h old → dashboard row; no ack or state for 2 days → interrupting task | F, R |
| 2.37 | Acks: `appliedBatches` marks `handoff_files.acked_at`; a file in `failed/` becomes a weekly flag with its `.error.txt` | F |
| 2.38 | Attempts and flag files are committed to SQLite (deduped on `attemptId`) and then deleted; never sent to Gemini. A flag withdraws its prompt and regenerates it against the cited source; it ships if it passes the citation check, else (or on a second flag) goes on the weekly list | F |
| 2.39 | `plan.json` written last in `pass2`, before 06:00: `kind: daily`, `date`, `validUntil` = next 02:00, `reviewScope` = all active courses, `new` = up to 10 concepts not yet introduced (per `state.json`), core → standard → background, nearest exam first, background only when room is left; `budgetMinutes {target: 20, planned: 20}`, `kindsMix {freeRecallMin: 0.5}`, `readingShare 0`, `intervalCaps` 30 days per active course | F |
| 2.40 | Real check: a batch from one real deck imports in Encore with no error, the concepts show citations, and the plan's new concepts appear in "Today's session" on the iPhone | R, U |
| 2.41 | Throughput: by 2026-12-01 every active course with a final has its slides and every dropped or announced guide through Shape, or `Dashboard.md` shows what's held and why | R |

### Phase 2 done

The 02:00/03:05 jobs run on real Canvas data and the Drop folder; Encore imports the batches and serves the plan;
Amier has studied from it for 3 days before the first final (2026-12-07).

---

## Phase 3: ramp, post-class quiz, confusable pairs, re-intake

**Delivers.** The daily plan follows exams (10-day ramps, 14 for finals, practice tests, a review quiz, no cramming),
recovers from missed days, and ships corrections first. A 3–5 item quiz is ready shortly after each class. Concepts
students mix up across decks get cited contrast prompts. A changed Canvas file updates what it taught without losing
review history. Lecture-directive tasks join the task funnel. SPEC-implementation §7.1.

Cross-repo: [Serve contrast prompts (kind: contrast)](https://github.com/amierlorenz/encore/issues/34), [Flag button with reason and suggested fix](https://github.com/amierlorenz/encore/issues/28),
[Banner when Articulate's daily plan is stale](https://github.com/amierlorenz/encore/issues/33), [Export the library](https://github.com/amierlorenz/encore/issues/30), and the Encore ramp and post-class
issues (Q14).

### Plan

From [Daily rhythm](https://github.com/amierlorenz/articulate/issues/9), [Deciding what matters](https://github.com/amierlorenz/articulate/issues/10), [Re-intake when Canvas files change](https://github.com/amierlorenz/articulate/issues/23).

| # | Check | How |
|---|---|---|
| 3.1 | An exam 10 days out (14 for a final) moves its unit concepts to the front of `new`, sets `examFocus.rampDay`, allows up to 35 planned minutes and sets `readingShare` 0 | F |
| 3.2 | Practice test on ramp days 9, 6 and 3 (15–20 items, exam scope, ≥½ free recall); review quiz the day before; no new concepts for that exam in its last 2 days | F |
| 3.3 | Finals: every unit in scope; a concept emphasised in 2+ units or on a midterm moves up one tier in the final's ordering | F |
| 3.4 | Missed days: a plan with no attempts expires; the next one limits reviews to what fits, puts ramp exams first, pauses new until the backlog fits; 3 idle days → exactly one `study:idle:<date>` task per streak | F |
| 3.5 | A corrected prompt (new id) goes first in `new`, outside the 10 cap; a skipped post-class quiz's concepts lead the next day's `new` | F |
| 3.6 | Interval cap 30 days while a course is active, dropped after its final | F |
| 3.7 | Real check: the plans for Dec 1–11 show CSC-301, MAT-305, PHY-203, CSC-382 and CSC-430 ramps with practice tests on the right days | R |

### Post-class quiz

From [Daily rhythm](https://github.com/amierlorenz/articulate/issues/9), [Model routing and budget](https://github.com/amierlorenz/articulate/issues/11).

| # | Check | How |
|---|---|---|
| 3.8 | `watch` exits without calling Pocket when no class ended in the last 18 h; one quiz per lecture at most | F |
| 3.9 | Once the class transcript is archived: one Flash Lite call on the class window only, 3–5 closed-book items, every quote checked exactly against the transcript | F |
| 3.10 | Items attach to existing concepts through the library matcher or create `standard` concepts; batch written before the `postclass-<lectureId>.json` that names it; Encore opened with `open -g -b` | F |
| 3.11 | Daytime calls stay inside the reserve (40 Flash Lite, 50 embeddings); the 02:00 run takes over waiting lectures | F |
| 3.12 | Real check: after a real class, the quiz is in Encore within one `watch` pass of the transcript arriving, and the first review is ≥1 day later, uncalibrated | R, U |

### Confusable pairs

From [Confusable pairs across decks](https://github.com/amierlorenz/articulate/issues/41).

| # | Check | How |
|---|---|---|
| 3.13 | The judge returns `group: <id> | new` per same-course confusable match in the same call; cross-course matches stay notes | F |
| 3.14 | One batched contrast-writer call a night; each deciding-feature citation carries its own `sourceFileId` and passes the citation check against its own file | F |
| 3.15 | No citable feature → uncontrasted and logged, never flagged; retried on new matched material, at most 3 tries | F |
| 3.16 | `kind: contrast` prompts with id `contrast:<a>:<b>`; plan gives at most 2 a day, only when both members are introduced and reviewed ≥1 day later, both core or standard, exam scope only when both are in it | F |
| 3.17 | A retired or `out` member suspends the contrasts; "split the pair" tombstones the membership and the judge never re-adds it | F |

### Re-intake

From [Re-intake when Canvas files change](https://github.com/amierlorenz/articulate/issues/23).

| # | Check | How |
|---|---|---|
| 3.18 | A changed file whose old version never finished Shape runs as a new file; otherwise only changed and added pages are re-extracted (whole file above 30 %) | F |
| 3.19 | Every citation is re-anchored by quote; each concept lands in exactly one bucket (unchanged, enriched, changed, new, unsupported) with the actions #23 lists | F |
| 3.20 | Same answer → same `prompt_id`; changed answer → old prompt retired, new id, first in the next plan | F |
| 3.21 | Old versions archived with `superseded_by`; no re-intake after the final; 2-day freeze ships corrections only; open card flags on changed concepts close as superseded with a weekly line | F |
| 3.22 | Real check: re-uploading an edited copy of a real deck changes only the edited slides' concepts | R |

### Lecture-directive tasks

| # | Check | How |
|---|---|---|
| 3.23 | Instructor directives with a concrete action become tasks; one that matches a Canvas assignment in the same course due within ±2 days is dropped; a date change becomes a weekly flag | F, R |

### Phase 3 done

Ramps, practice tests and post-class quizzes have run for 3 days on real classes (or on the finals ramp), and a
contrast prompt and a re-intake correction have each reached Encore.

---

## Phase 4: voice commands, Literature notes, people, transcript-only concepts

**Delivers.** "Articulate, …" in any recording does what it says, within the whitelist, with a receipt and undo.
Class and personal talks become one-claim Literature notes with checked quotes. Named speakers get person notes.
Lectures add the concepts and claims that never appeared on a slide. SPEC-implementation §7.2.

### Commands

From [Voice commands for acting on recordings](https://github.com/amierlorenz/articulate/issues/29), [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31).

| # | Check | How |
|---|---|---|
| 4.1 | Wake-phrase detection is fuzzy ("Articulate", "main agent", mis-hearings); class recordings only in their first and last 2 min; a local confirm step finds the command's end | F |
| 4.2 | Parse returns action, target, span, speakers and instructions; instructions never change the folder, action or target | F |
| 4.3 | Target rules (72 h for "last", 14 days for dates, course key or title, default same/previous); ambiguous or none → weekly flag with candidate checkboxes; ticking one re-runs it | F |
| 4.4 | Span found by embeddings with position hints and a trimmed confidence; >30 min or low confidence → flag; timestamps skip the search | F |
| 4.5 | Speakers named by the command; unclear attribution → no quote; first instructor written to `courses.toml` only when empty, a conflict flagged | F |
| 4.6 | Whitelist only; refused part → `Inbox/` note; >12 notes → whole command flagged; `task` only when first-person | F |
| 4.7 | Receipt per command with an undo box; undo (box or spoken) deletes only untouched notes and stubs it made, tombstones them, keeps tasks | F |
| 4.8 | A command-only recording and `hands_off` spans get no Inbox note and no extracted tasks | F |
| 4.9 | Private: local models only, notes `private: true`, nothing to Encore | F |
| 4.10 | Real check: the AI301 command example (`5b20a9f2…` → `9891829e…`) resolves to the ~1:37–1:50 span and the named instructor | R |

### Literature notes

From [Claim breakdown from a guest speaker](https://github.com/amierlorenz/articulate/issues/30), [The new vault workflow](https://github.com/amierlorenz/articulate/issues/8).

| # | Check | How |
|---|---|---|
| 4.11 | One note per claim, title = the claim; code writes the frontmatter; body has paraphrase, Citation, Speaker, Questions for Amier | F |
| 4.12 | Mechanical quote check gates each note (pieces outside `[…]`/`…` in order); `≈H:MM:SS` interpolated; failure → one retry, then held + flagged | F |
| 4.13 | Links only to titles from the provided list, minus Private in cloud runs | F |
| 4.14 | Class targets on the `lecture_literature` chain; personal targets on `claude -p` within ceilings (held otherwise); `total_cost_usd` logged | F, R |
| 4.15 | Real check: the AI301 talk gives ~6 notes with 25/25 quotes verified | R |

### People

From [Person notes for speakers and authors](https://github.com/amierlorenz/articulate/issues/33).

| # | Check | How |
|---|---|---|
| 4.16 | A person note only when something links to it; fuzzy match on titles and aliases scoped to the course or source; unsure → flag, no note | F |
| 4.17 | Stub shape (tags, aliases, role, affiliation, sources, Dataview incl. hub text matches); updated only while untouched, then suggestions go to flags | F |
| 4.18 | From a Private recording → `private: true`, absent from cloud title lists and cloud matching | F |

### Transcript-only concepts

From [Transcript-only concepts](https://github.com/amierlorenz/articulate/issues/39).

| # | Check | How |
|---|---|---|
| 4.19 | Runs at 02:00 once the deck is matched, or after 3 nights without one; windows ~180 s (cap 300, tail <120 joins) | F |
| 4.20 | Schema `{ newConcepts, spokenClaims, spokenHints, contrastGroups }`; citations checked exactly in the window ±1; failing quotes fail closed as #39 lists | F |
| 4.21 | `correctsSlide` makes the spoken claim primary with a weekly undo line; unit-level hints cross-check exam scope; time share from cited windows | F |
| 4.22 | Real check: a CSC-382 lecture with its real deck gives no re-invented deck concept and ≥22/23 verified quotes | R |

### Phase 4 done

Amier has used a spoken command, read its Literature notes, and studied transcript-only concepts for 3 days.

---

## Phase 5: reading, YouTube, LeetCode, evals and tuning

**Delivers.** Literature notes and watched videos become quiz material in the reading slot. LeetCode problems
dropped in `Drop/interview/` become pattern practice with tested code prompts. Prompt changes to tasks and commands
are gated by local evals. Cloud agents can add tasks through the Google Tasks list. SPEC-implementation §7.3.

Cross-repo: [Mac practice list from the daily plan](https://github.com/amierlorenz/encore/issues/32).

### Reading

From [Quizzes from reading and goals](https://github.com/amierlorenz/articulate/issues/43).

| # | Check | How |
|---|---|---|
| 5.1 | Quizzable: written or touched Literature notes, minus `quiz: false` and `private: true`; `quiz: true` Permanent notes; untouched agent notes per Q12/Q13 | F |
| 5.2 | One `book:<slug>` course per hub; videos and articles in `reading`; Permanent notes to their `from:` course | F |
| 5.3 | Claims cite the title, restatement or Citation bullets, quotes exact against the note; book pages display-only | F |
| 5.4 | `claude -p` on leftover budget, `qwen3:8b` after 7 days held, never Gemini | F |
| 5.5 | ≤2 prompts a note; title-only → recall only; section edits follow #23; delete or `quiz: false` retires | F |
| 5.6 | Plan: reading share ≤20 %, 0 in ramps, ~2 new a day round-robin, `#reading` first | F |
| 5.7 | Real check: the 87 existing Literature notes produce prompts with every quote verified | R |

### YouTube

From [What GitHub, YouTube and Medium feed into](https://github.com/amierlorenz/articulate/issues/44), [Getting transcripts for watched YouTube videos](https://github.com/amierlorenz/articulate/issues/46),
[Gemini reading watched YouTube videos by link](https://github.com/amierlorenz/articulate/issues/47), [Retest Flash Lite on YouTube links](https://github.com/amierlorenz/articulate/issues/48).

| # | Check | How |
|---|---|---|
| 5.8 | Playlist "Articulate" read with `youtube.readonly`; Watched/Published/Duration from the Data API; ledger tracks what's done | F |
| 5.9 | Video calls run last, on leftover quota only, never the daytime reserve | F |
| 5.10 | ≤30-min windows; ≤5-min videos on Flash Lite first, longer on 3.6 Flash; `"M:SS"` converted in code; absolute clip times | F |
| 5.11 | Non-public or refused → metadata-only stub + weekly flag; no yt-dlp | F |
| 5.12 | Note shape `📺 <title>` with TL;DW and timestamped `?t=` notes, Key Ideas empty; `quiz: false` retires | F |
| 5.13 | Real check: a public 20+ min video added to the playlist has a note by the next morning | R |

### LeetCode

From [LeetCode problems as a source](https://github.com/amierlorenz/articulate/issues/25).

| # | Check | How |
|---|---|---|
| 5.14 | `interview` course: `public = true` drop files use the Gemini chain, `new_per_day = 2`, an interview date turns on the ramp | F |
| 5.15 | Pattern = method concept in contrast groups; problem = cited worked example; no `pattern:` and no solution → gap-fill held | F |
| 5.16 | Prompts: `methodPick` and `setUp` always; `codeImplement` with `implement: true` or a solution; `codeComplexity` on your solution | F |
| 5.17 | Hidden tests from running reference and brute force in `sandbox-exec` (no network, temp writes, 5 s, memory cap); disagreements and timeouts flagged; works under the LaunchAgent | F, R |
| 5.18 | `macPractice` lists ≤3 `codeImplement` prompts untried for 14+ days | F |

### Evals and tuning

From [Pulling to-dos from transcripts and tuning them](https://github.com/amierlorenz/articulate/issues/20), [Recording agent's instructions and allowed actions](https://github.com/amierlorenz/articulate/issues/31),
[Non-class recordings with no command](https://github.com/amierlorenz/articulate/issues/42), [Task funnel into master-todo](https://github.com/amierlorenz/articulate/issues/7).

| # | Check | How |
|---|---|---|
| 5.19 | `articulate eval tasks` on the private labelled set: high-band precision ≥0.9 and not below the current version, recall drop ≤5 points; seeding writes `Inbox/Task labels.md` | F, U |
| 5.20 | `articulate eval commands`: parse accuracy (span overlap ≥80 %), 100 % quote check, gate decision ≥90 %, `kind:` ≥80 % | F |
| 5.21 | The `pre-push` hook runs the matching eval when task, command, inbox or literature prompts change, and refuses a failing push; nothing runs in CI | F |
| 5.22 | Feedback signals (done, removed in 3 days, unticked 2 weeks, edited title) feed the labelled set; old recordings are re-scored, never re-extracted | F |
| 5.23 | Google Tasks "Articulate" items become tasks with `gtasks:<id>` (due time from a `due:` line) and are deleted from Google | F, R |

### Phase 5 done

Amier has studied reading and video prompts and one LeetCode pattern for 3 days, and one prompt change has gone
through an eval gate.
