# Carrying MieracleStudy's intake map over

Research for [articulate#5](https://github.com/amierlorenz/articulate/issues/5).

MieracleStudy's in-app Canvas intake map (MieracleStudy#70) is moving to Articulate as a headless, nightly
Python pipeline on the Mac. This note goes through each of that map's closed decisions and open tickets and
marks it **carry over**, **adapt** or **drop**. For adapt, it gives the likely Python-side equivalent. It also
lists the prototype code Articulate can reuse.

**Sources.** Everything below comes from MieracleStudy's planning files and branches, read without checking out
or changing that repo:
- `mieracle-study/.plan/70-map-in-app-canvas-intake/map.md` and `tickets/*.md` (cited as `#NN`)
- `git archive` of `prototype/extraction-bakeoff`, `prototype/definition-prompts`, `prototype/library-matching`
  and `research/source-preparation`
- `docs/PLAN.md` in this repo, for Articulate's own conventions (launchd jobs, Keychain via `keyring`)
- PyPI JSON metadata (2026-11-17) for package names, versions and licences

Tags: **[R]** = read in one of those sources, **[I]** = my inference, not tested.

**Already decided for Articulate** (given, not re-litigated here):
- Review is "nothing up front". Flags hold items back, and they're cleared in a weekly list or with a "this card
  is wrong" button in MieracleStudy.
- Study guides and past exams are dropped into a folder.
- Hand-off is import JSON v2 plus a daily plan file, through an iCloud folder.
- Class material may go to the Gemini free tier, with `claude -p` as the fallback.

## Summary

- **The research mostly carries over unchanged.** Canvas API facts (#71), the pedagogy (#73), the billing paths
  (#74/#75), the Extract winner and fallback chain (#76), definition-prompt generation (#81) and library
  matching (#92) are vendor or HTTP facts. The language they were tested in doesn't change them. Three of
  them (#81, #92 and the Gemini half of #76) were already prototyped in **Python**.
- **What tied the map to the app** comes down to three things: PDFKit and Vision for page text and OCR, Keynote
  consent from an app, and the SwiftData/Keychain/iCloud plumbing. Each has a Mac-side Python route:
  `pypdfium2` for text and page splitting, Vision through `ocrmac`/`pyobjc` (or a small Swift helper the
  pipeline shells out to), and `osascript` for Keynote. Keynote is the risky one. It needs Automation consent,
  and a nightly launchd job can't answer a prompt (#90 already warned: "never in a background job").
- **All UI drops:** the Take In Material… window (#78/#95), the flag-review screen (#80), the Settings usage
  and storage panels (#91/#94), and "ask before the Claude hop" (#94). Their *rules* survive as pipeline policy.
  The #80 split between flags that block Approve and flags accepted as is maps directly onto "flags hold items
  back".
- **The SwiftData model (#77) becomes the JSON v2 contract.** Articulate writes JSON. MieracleStudy still owns
  `Concept`, `MethodGroup`, `SourceFile`, `ReviewLog.pickedConceptID` and session ordering (#82).
- **The biggest new gap:** two steps need data that only MieracleStudy has. Pick needs the user's marked
  `Exam`s and their `CourseUnit`s (#78), and library matching needs the approved concept library (#92).
  Articulate has to get them from MieracleStudy somehow (see the open questions).

## Decision table: the map's own "settled while charting" notes

| Map decision | Verdict | Why / Python-side equivalent |
|---|---|---|
| Intake lives in the app, macOS only | **Drop** | Replaced by the headless pipeline. It's still macOS-only: Keynote, Vision and `claude -p` sign-in all live on the Mac [R map.md]. |
| Sources: Canvas module files + dropped-in files | **Carry** | Drop-ins become the watched folder (already decided). |
| Three checkpoints Pick → Extract → Shape practice | **Adapt** | They stay as pipeline *stages* but lose their checkpoints. Pick runs on deterministic defaults, and nothing waits for a click. |
| Strict grounding (file + slide/page citation; gap-fill labelled) | **Carry** | Language-neutral. Grounding is checked by code (quote match), not by UI. |
| Review is accept-by-default, flag exceptions | **Carry, tightened** | It matches "nothing up front". The flag taxonomy carries; the screen doesn't (see #80). |
| "Which method applies" practice is in scope | **Carry** | Pedagogy (#73) plus the prompt kinds (#79). |
| JSON import/export stays; generator-prompt flow retired | **Adapt** | JSON v2 becomes the *main* hand-off, written to the iCloud folder, not a backup path. |
| Swappable vendors: `claude -p` + Gemini API, Gemini free tier | **Carry** | `LLMBackend` becomes a Python `Protocol` (see #74). |
| Show cost before each step; live usage | **Adapt** | No one is there to see it before a step runs. Log estimates and actual usage per run instead (Dashboard / weekly list). |
| On-device first (Foundation Models / Core AI) | **Drop** (models) / **Carry** (Vision OCR) | The bake-offs already ruled on-device LLMs out of every intake step (#76, #81), and Foundation Models has no Python API [I]. Vision OCR stays on-device. |
| Keys live only in the Keychain; gitleaks before going public | **Carry** | Articulate's plan already says "macOS Keychain via `keyring`" [R PLAN.md]. |

## Decision table: closed tickets

| Ticket | Verdict | Why | Python-side equivalent (for adapt) |
|---|---|---|---|
| **#71 Canvas modules and files API** | **Carry** | It's all REST facts: one `modules?include[]=items&include[]=content_details` call; no exam type; discover files only through modules (the course Files list 403s for students); module item id as the stable key; `content_id`/`updated_at`/`size` as the change signal; strip `Authorization` on cross-host redirects; 429 backoff [R #71]. | `httpx` or `requests`, with pagination over `Link` headers. The token goes in the Keychain via `keyring`. Port `CanvasClient.getAll` and its `.forbidden`/`.throttled` split. |
| **#72 Reading slides with Claude and Gemini** | **Carry** (vendor facts) / **Adapt** (PDFKit) | Self-reported `slide` + verbatim quote, checked by code. PPTX → PDF via Keynote first. `claude -p` gaps (no Citations, schema checked afterwards, ≤20 pages per Read). One flat, all-required schema [R #72]. | Page text: `pypdfium2` (`PdfPage.get_textpage().get_text_range()`), or `pdfplumber` when the layout matters. Gemini: REST via `urllib`, as in the prototypes, or `google-genai`. |
| **#73 Practicing which method applies** | **Carry** | Research and the field list (use-when cues, conditions, decision group, look-alikes + deciding feature, a cited worked example). Cloud model + verify, never reworded on-device [R #73]. | — |
| **#74 Claude Code subscription vs Gemini API** | **Adapt** | The `LLMBackend` design (estimate/run/usage) and the hardened `claude -p` flags carry. The app-only parts (Swift protocol, Settings → Keychain) don't [R #74]. | A `typing.Protocol` with `ClaudeCodeBackend` (`subprocess.run` on the absolute `claude` path; `env` without `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`; `--system-prompt --tools … --setting-sources "" --strict-mcp-config --output-format json --json-schema`; run from a temp dir) and `GeminiBackend`. `/usage` parsing carries as is. |
| **#75 Set up intake accounts** | **Carry** | Accounts already exist: Claude Pro with `claude -p`, and a Gemini free-tier key. The model defaults stand: 3.8 → 3.7 → 3.6 Flash, Flash Lite for cheap steps, Sonnet opt-in [R #75]. | Recheck the free-tier table (it was read 2026-10-03). It's now **shared with Articulate's other jobs** if they use Gemini too [I]. |
| **#76 Extraction bake-off** | **Carry** (winner, chain) / **Adapt** (OCR check) | Gemini 3.8 Flash extracts, Claude Sonnet is the fallback, Foundation Models is out. Chain: retry 503/429/500 → older Flash → Claude. Quote check: text layer, then OCR [R #76]. | Text check: `pypdfium2`. OCR: Vision through `ocrmac` or `pyobjc-framework-Vision`, or a Swift CLI (see #90). |
| **#77 Concept model for intake** | **Adapt** | The model itself (kind, `ConceptDetails` claims with provenance and per-claim citations, `MethodGroup` with pair contrasts, `SourceFile`, JSON `schemaVersion: 2`) becomes the **output contract** Articulate writes [R #77]. Two parts change. "Candidates stay in a local draft until approved" becomes: unflagged candidates are emitted as approved (`approvedAt` = run time), and flagged ones stay in Articulate's state. `ReviewLog.pickedConceptID` and the editor changes stay in MieracleStudy. | Pydantic (or dataclasses + `jsonschema`) models that mirror JSON v2. They're validated before anything is written to the iCloud folder. |
| **#78 Pick step: files to exams** | **Adapt** | The rules carry: module = unit (name-match once, then `canvasModuleID`), exam coverage only from exams the user has marked, defaults only for new units, "after the exam" files flagged, only an explicit Skip sticks. The window, tree, checkboxes and "Take In Material…" drop [R #78]. | Deterministic Python over the Canvas tree. `IntakeSource` becomes a table in Articulate's local SQLite state DB (not synced). Skip-module lives in a config file. Non-importable items go on the weekly list. **Needs MieracleStudy's exams and units as input** (open question). |
| **#79 Practice shaping** | **Carry**, small adapt | Styles as recipes (course default, exam override). `methodPick`/`setUp` kinds. Generation from approved records only. Deterministic checks + Gemini verify, with failures flagged. Reshape adds only missing kinds [R #79]. "Definition prompts on-device" is superseded by #81. Reshape needs to know which prompts already exist, so Articulate keeps its own record of what it emitted [I]. | Recipes in a config file. Generation and verify follow `prototype/definition-prompts/run-lite.py`. |
| **#80 Flag-review screen** | **Drop** (screen) / **Carry** (flag rules) | The layout, OCR highlights, merge modal and summary sheet are all UI. Two pieces carry. The **blocking set** (merge candidate, matches an existing concept, gap-fill claim) is exactly what "flags hold items back" should hold. The **as-is set** (citation unverified, low confidence, missing worked example, missing contrast) ships without waiting [R #80]. Per-flag actions become the weekly-list options. | Weekly list = a Markdown note or a file in the hand-off folder. Its actions (Keep / Merge into X / Enrich X / Drop) get applied on the next run. Keep OCR boxes in `.pages.json` in case MieracleStudy wants highlights later. |
| **#81 Definition prompts: Flash Lite + verify** | **Carry** | Gemini 3.5 Flash Lite + a verify call. A control-character `pattern` `^[^\t\x08\x0c]*$` on every string. Checks: control chars, unbalanced `$`, math drift, duplicate options, answer leak, stray `\\` [R #81]. | Already Python: `run-lite.py`. |
| **#82 Session ordering for method items** | **Carry, stays in MieracleStudy** | `SessionBuilder` behaviour, not intake. Articulate only has to emit `MethodGroup` membership and contrast `pairID`s correctly [R #82]. | — |
| **#89 Core AI candidate models** | **Drop** | It only mattered for on-device Shape, which #81 ruled out. The Core AI runtime is Swift (`llm-server` is OpenAI-compatible, so Python could call it, but there's no reason to) [R #89, #81]. | — |
| **#90 Preparing sources before Extract** | **Adapt** | The pipeline shape carries: one PDF with page N = slide N, notes from PPTX XML, text + OCR on every page at 2×, whole PDF to Gemini, ≤10-page chunks to `claude -p`, quote check text → OCR → same chunk, 50 MB check [R #90]. The app-specific parts change: `AEDeterminePermissionToAutomateTarget`, the entitlements and `NSAppleEventsUsageDescription`, and PDFKit/Vision calls. | Keynote: `subprocess.run(["osascript", "keynote-export.applescript", src, dst])` with the destination pre-created. Notes: `zipfile` + regex (as in `pptx-text.py`) or `python-pptx`. Chunks: `pypdfium2` `PdfDocument.new()` + `import_pages`. Render at 2×: `pypdfium2` `page.render(scale=2)`. OCR: see the note below. |
| **#91 Keeping source files** | **Adapt** | What's kept carries: the converted PDF + `.pages.json`, not the PPTX, no automatic cleanup, citations survive deletion. The *where* changes. The decision relied on the app's CloudDocuments entitlement and the hidden `Documents/Sources/…` folder of `iCloud.com.mieracle.MieracleStudy` [R #91]. Articulate has no entitlements. | Write to the hand-off iCloud folder under `Sources/<course>/<sourceFileID>.pdf`. `SourceFile.archivePath` is relative to it. The run draft goes to Articulate's local state dir. Settings → Storage stays a MieracleStudy feature if wanted. |
| **#92 Matching against the library** | **Carry** (method) / **Adapt** (inputs) | A separate step: `gemini-embedding-2` shortlist (cosine ≥ 0.70, top 5, cached library vectors) → one Flash Lite judge per deck, across all courses, with the "same = already teaches the main claim" bar. Enrichment adds kind and cited claims and never rewrites [R #92]. The library has to come from MieracleStudy. | Already Python: `embed-gemini.py`, `run-judge.py`. The vector cache goes in the state DB. A "same" match is a blocking flag → weekly list. |
| **#94 Cost and usage guardrails** | **Adapt** | Carries: Gemini budget = free quota with own counts per model (midnight PT reset), Claude 70% weekly / 90% session ceiling, 3 min per-model deadline, 120 s request timeout, partial steps resume, Flash Lite never spills to Flash [R #94]. Drops: "last hop to Claude **asks first**", the per-deck opt-in at Pick, run-button estimates and the Settings panel. The already-decided `claude -p` fallback means the hop is automatic, inside the ceiling. When the ceiling is hit or usage can't be read, the step is deferred to the next night. | Counts and ceilings in the SQLite state DB and a config file. Usage is reported in Dashboard.md and the weekly list. |

### OCR from Python

#90 measured the **new** `RecognizeTextRequest` API (Swift async): 9–13 s and 97–189 MB per 60 pages. The legacy
`VNRecognizeTextRequest` peaked around **600 MB** [R `research/source-preparation.md`, #90]. `ocrmac` (PyPI 1.0.1,
MIT, "Uses the vision framework from Apple") and `pyobjc-framework-Vision` (13.0) both reach Vision from
Python. I haven't checked which request API they call. If it's the legacy one, expect the higher memory figure
[I]. Two options:
1. `ocrmac` / pyobjc in-process. Simplest, and the whole pipeline stays Python.
2. Keep `ocrbench.swift` as a ~60-line compiled helper (`swiftc`) that prints JSON (text, confidence, boxes)
   per page, and call it with `subprocess`. It reuses measured code on the measured API.

Either works for a nightly batch, and the 42 s first-call warm-up doesn't matter there.

### Keynote from a launchd job

[I, untested] Automation consent (TCC) is granted to the *responsible* process. Under a LaunchAgent, that's the
job's executable (the Python interpreter or `osascript`), not Terminal. A first run from launchd would raise a
prompt nobody answers, and it blocks until the AppleScript timeout (−1712) or fails with −1743 [R #90]. That
argues for:
- a one-time interactive `articulate doctor --keynote` run, using the same interpreter path the plist uses, to
  grant consent;
- a LaunchAgent, not a LaunchDaemon, so it runs in the logged-in GUI session that Keynote needs;
- a rule that a PPTX found with consent missing is held and listed, never prompted.

PowerPoint `save as PDF` remains #102's fallback, under the same consent rules. LibreOffice `soffice --headless
--convert-to pdf` would avoid TCC entirely but isn't installed and hasn't been tested for fidelity [R
`source-preparation.md`: "LibreOffice not installed"].

## Decision table: open tickets

| Ticket | Verdict | Why | Python-side note |
|---|---|---|---|
| **#93 Extract output schema** | **Carry** (still open) | Still needed, and still language-neutral. It must now line up exactly with the JSON v2 Articulate emits, with per-claim quotes for cues, conditions and worked examples, plus the control-character `pattern` [R #93, #80, #81]. | Validate with one real Gemini 3.8 Flash call from Python. |
| **#95 Intake window flow** | **Drop** | It's about a window: navigation, several runs, closing the window or quitting the app. What's left (resuming a half-finished run, and what survives an interruption) becomes the pipeline's idempotent run state, which Articulate needs anyway [R #95; PLAN.md principle 7]. | Per-run rows in SQLite. Every stage resumes from its last finished request. |
| **#96 Re-intake when Canvas files change** | **Carry, adapt** | Still open and more pressing: a nightly job sees changes every night. Detection is settled (module item id; `content_id`/`updated_at`/`size`). Still open: how a changed concept reaches MieracleStudy through JSON v2 (an update to an existing `externalID`?) and what happens to review history. That's an importer question as much as an intake one [R #96, #71]. | Diff against the archived `.pages.json`. |
| **#97 Files linked only from Canvas Pages** | **Carry** | Still the same research (count page-only file links on real courses). Run from the main session, since it needs the Canvas token [R #97]. | `GET /courses/:id/pages/:url` → parse `body` for `/files/:id`. |
| **#98 LeetCode problems as a source** | **Adapt** | The input form is now settled: the drop folder. Still open: what Extract makes of a single problem, and grounding for the pattern name [R #98]. Lower priority. | — |
| **#99 Decision groups for definitions** | **Carry** | A content-model question that shapes JSON v2 (`ConceptTag` vs groups for definitions) [R #99]. | — |
| **#100 Mix-up signals from method picks** | **Drop** (from Articulate) | It reads `ReviewLog.pickedConceptID` inside MieracleStudy, so it's a study feature, not intake. Its own ticket already asks whether it belongs to the intake map at all [R #100]. Leave it with MieracleStudy. It comes back only if the daily plan file should react to mix-ups, which would need MieracleStudy to export them. | — |
| **#101 Hybrid Extract: OCR + local text model** | **Drop** (park) | It came from the "on-device first" preference and a Swift Core AI model. Articulate's route is decided (Gemini free tier → `claude -p`), and #75 puts the free tier at ~20 decks/day [R #101, #75]. Reopen it only if the quota starts to bind. Then the local model would be Ollama (Articulate's $0 default [R PLAN.md]), not Core AI. | — |
| **#102 Run the Keynote export once** | **Carry, widened** | Still needed: the export has never run end to end [R #102]. It should now also run **from the launchd context** (see "Keynote from a launchd job"). | `osascript keynote-export.applescript in.pptx out.pdf` |

## Prototype code Articulate can reuse

All of it is marked "PROTOTYPE — throwaway". Reuse means port it and clean it up, not import it as is. Paths are
relative to the MieracleStudy repo root on each branch.

| Branch | Path | Lang | What it does | Reuse for Articulate |
|---|---|---|---|---|
| `prototype/extraction-bakeoff` | `prototypes/extraction-bakeoff/run-gemini.py` | Python (stdlib) | Inline-PDF `generateContent` with `responseJsonSchema`; 0/15/45 s retry on 429/500/503; Flash model fallback | **High**: the core of the Gemini Extract call and the fallback chain |
| | `…/run-claude.sh` | zsh + Python | Hardened `claude -p` (`env -u` keys, `--setting-sources "" --strict-mcp-config`, `--json-schema`, `--max-budget-usd`), `/usage` before and after, temp dir | **High**: port to `subprocess` for `ClaudeCodeBackend` |
| | `…/schema.json`, `…/prompt.md` | JSON, Markdown | Flat bake-off Extract schema + prompt | **Medium**: the starting point for #93 (needs per-claim quotes) |
| | `…/ocr-check.swift` | Swift | Text-layer then Vision OCR quote check (legacy `VNRecognizeTextRequest`) | **Medium**: the logic (`norm`, text → OCR) ports to Python; superseded by `ocrbench.swift` for OCR |
| | `…/build-review.py` | Python (+ poppler) | Review HTML with `pdftoppm`/`pdftotext` quote flags | **Low**: the `flags()` logic is a template for weekly-list flags |
| | `…/run-fm.swift` | Swift | Foundation Models leg | None (ruled out) |
| `prototype/definition-prompts` | `prototypes/definition-prompts/run-lite.py` | Python (stdlib) | Flash Lite generate + verify per concept; `add_pattern` control-char regex; 4.2 s pacing; token stats | **High**: the Shape step for definitions/facts as decided in #81 |
| | `…/schema.json`, `…/prompt.md`, `…/verify.md` | JSON, Markdown | Generation schema (flashcard / shortAnswer / MC), generation + verify prompts | **High** |
| | `…/prepare.py` | Python | Approved concepts → record text (claims, use-when, look-alikes) | **Medium**: the record shape for Shape input |
| | `…/judge.py` | Python | Reuses the verify call as a judge | Low |
| | `…/repair.py` | Python | Repairs eaten LaTeX escapes | **Medium**: as a deterministic check, not a fix |
| | `…/build-review.py` | Python | Side-by-side review HTML | Low |
| | `…/run-coreai.py`, `…/run-fm.swift`, `…/run-fm-pattern.swift` | Python / Swift | Core AI and Foundation Models legs | None (ruled out) |
| `prototype/library-matching` | `prototypes/library-matching/gemini.py` | Python (stdlib) | Shared `generate(models, system, parts, schema)` with retry + fallback | **High**: becomes the `GeminiBackend` core |
| | `…/embed-gemini.py` | Python | `gemini-embedding-2` `embedContent` (`SEMANTIC_SIMILARITY`), normalised cosine | **High**: the #92 shortlist |
| | `…/run-judge.py` | Python | One Flash Lite judge per deck with shortlist, `same`/`confusable` + `adds` | **High**: the #92 judge (add the "main claim" bar to its prompt) |
| | `…/dump-library.py` | Python (`sqlite3`) | Reads MieracleStudy's SwiftData store **read-only** (`~/Library/Application Support/default.store`) | **Medium**: one way to get the library and exams (see open questions) |
| | `…/pptx-text.py` | Python (`zipfile`) | Slide text from PPTX XML, no Keynote | **Medium**: extend to `ppt/notesSlides/` for speaker notes (#90 step 4) |
| | `…/run-extract.py` | Python | Extract with library titles inside (option B, rejected) | Low, except its `.txt` input path |
| | `…/extract-prompt.md`, `…/extract-schema.json` | Markdown, JSON | Same as the bake-off | Same as above |
| | `…/slice.swift` | Swift | PDFKit page-range slice | Port to `pypdfium2` `import_pages` |
| | `…/embed.swift` | Swift | `NLEmbedding` legs | None (ruled out) |
| | `…/truth.json`, `…/out/*.json` | JSON | Labelled match/miss fixtures on 4 real decks + library dump | **Medium**: regression fixtures for matching tests. Check them for private content before copying into this repo |
| `research/source-preparation` | `research/source-preparation/keynote-export.applescript` | AppleScript | Keynote PPTX → PDF with the #90 export properties | **High**: call it via `osascript` (untested, #102) |
| | `research/source-preparation/ocrbench.swift` | Swift | PDF render at a set scale + new `RecognizeTextRequest`, text layer, timings | **High** if Articulate uses a Swift OCR helper (option 2 above) |
| | `research/source-preparation/compare-ocr.py` | Python | OCR recall vs the text layer | Low (has a hard-coded temp path) |
| | `research/source-preparation.md` | Markdown | The full #90 findings | Reference |

Credentials note: `run-lite.py` and `gemini.py` read the key from `$GEMINI_API_KEY` or the login-keychain item
`mieracle-gemini-prototype` through `security find-generic-password`. Articulate should use its own `keyring`
entry instead [R PLAN.md].

## Open questions for the user

1. **Exams, units and the library come from MieracleStudy. How does Articulate read them?** (a) Read the
   SwiftData store read-only, as `dump-library.py` did. That's fragile across schema changes and CloudKit
   mirroring. (b) Have MieracleStudy export a small `library.json` / `exams.json` into the iCloud hand-off folder.
   It's a reverse channel, but a clean one. (c) Articulate keeps its own course/exam config. I'd lean (b).
2. **Which new files does the nightly Pick take?** #78 pre-checked only the *next* exam's new files and left
   later ones unchecked for a person to choose. With no one choosing: everything new and not skipped, or only
   up to the next exam?
3. **Where does Skip module live now?** A config file in Articulate, or a toggle in MieracleStudy that it
   exports?
4. **Keynote under launchd:** OK to require a one-time interactive consent run (`articulate doctor`), and to hold
   PPTX files until it's done? Or try PowerPoint/LibreOffice first?
5. **OCR:** in-process `ocrmac`/pyobjc, or a compiled Swift helper reusing `ocrbench.swift`?
6. **Claude hop without asking:** is it fine for the nightly run to fall back to `claude -p` on its own, as long
   as weekly usage stays under the 70% / 90% ceilings? That budget is shared with Articulate's other jobs and
   your own Claude Code use.
7. **Flagged items vs Shape:** should a concept held back by a blocking flag also hold back its prompts until
   it's cleared? (Assumed yes.) Should as-is flags (unverified citation, low confidence) still produce prompts
   the same night?
8. **Source archive location:** inside the shared hand-off folder (`Sources/…`, simple), or still MieracleStudy's
   private iCloud Documents container? The second needs MieracleStudy to copy files over on import.
9. **#96 and #99 change the JSON v2 contract.** Settle them here in Articulate, or back in MieracleStudy's map?
   That map also owns the importer.
