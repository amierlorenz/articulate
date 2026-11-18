# MieracleStudy's import and session surface

Research for amierlorenz/articulate#4. Question: what MieracleStudy accepts and does today, so
Articulate's hand-off (import JSON schema v2 + a daily-plan file in an iCloud folder) fits it.

**Sources.** MieracleStudy `main` at `799a685` (paths below are relative to
`mieracleStudyDev/mieracle-study/`), its local `.plan/70-map-in-app-canvas-intake/` tickets (git-ignored
mirror of the GitHub issues), and `mieracleStudyDev/docs/SPEC-requirements.md` /
`SPEC-implementation.md`. Every claim cites the file it comes from. Nothing in that repo was changed.

**Already decided (not re-argued here):** Articulate runs intake headlessly and writes import JSON
(schema v2) plus a daily-plan file into an iCloud folder MieracleStudy watches; MieracleStudy serves
the quizzes.

---

## 1. Summary

- MieracleStudy today accepts **only schema v1**, and only through a manual picker, paste or drag-drop
  (`MieracleStudy/Features/Import/ImportView.swift`). Nothing watches a folder, and the validator
  rejects any `schemaVersion` other than `MieracleKit.schemaVersion = 1`
  (`MieracleKit/Sources/MieracleKit/MieracleKit.swift:4`, `Import/ImportValidator.swift:59`).
- **Schema v2 is designed but not built.** Its shape was settled in MieracleStudy #77; the Extract
  output schema (#93) is still open with no answer. No branch contains v2 code.
- Import is **upsert by stable string id, never delete**, and preserves review history. That is
  exactly what a daily automated writer needs, with a few sharp edges (section 3.4).
- Sessions are built **inside the app** from live schedule state (`dueDate`, `introducedAt`, ease,
  lapses) that lives only in SwiftData/CloudKit. **Articulate cannot see that state**: export leaves
  out review history (`Import/Exporter.swift:6-8`). So a daily plan should state intent (scope, new
  concepts to introduce, exam focus, length) and let the app compute what is due, unless the app
  starts exporting schedule state.
- There is no plan-driven session, no "today's session" record, and the only flag is a per-prompt
  `isFlagged` with no reason and no way back to Articulate.

---

## 2. Import schema v1 (what is accepted today)

Source of truth: `Schema/import-v1.schema.json` and the Swift decoder `Import/ImportDocument.swift`
(the app and the `mieracle-validate` CLI use the decoder, not the JSON Schema;
`MieracleKit/Package.swift:10`).

```
{ schemaVersion: 1 (const),
  courses: [ { id, name, shortName?, coding?, units?: [
      { id, name, order?, concepts?: [
          { id, title, explanation?, tags?: [str], resources?: [{title, url http(s)}],
            prompts?: [ { id, type, ...type fields } ] } ] } ] } ],
  exams?: [ { id, courseId, name, date (YYYY-MM-DD | ISO 8601), unitIds?: [id], tags?: [str] } ] }
```

- **Ids**: `^[A-Za-z0-9][A-Za-z0-9._-]*$`; convention is kebab-case prefixed by the parent id
  (`cs201.heaps.top-k.pattern`) (`import-v1.schema.json` description; `Import/GeneratorPrompt.swift:69`).
  Ids must be unique per kind within a file (`ImportValidator.swift` `checkID`).
- **Prompt kinds** (`type`): `flashcard` (front, back), `shortAnswer` (question, modelAnswer,
  keyPoints?), `multipleChoice` (question, options ≥2, correctIndex, explanation?), and the coding kinds
  `codeImplement`, `codeComplexity`, `patternPick`, `fullProblem` (`Models/PromptContent.swift:5-24`).
  `codeImplement`/`fullProblem` need the Mac's code runner and are skipped on iPhone.
- **Text** is Markdown with `$inline$` / `$$display$$` LaTeX and fenced code; backslashes must be
  JSON-escaped (`GeneratorPrompt.swift:65`).
- **Lean import**: a concept with an `explanation` and no `prompts` is valid (warning only); the
  **Mac** app later writes a flashcard + short answer for it with on-device Foundation Models, after
  an import from the Import screen (`ImportValidator.swift` concept warning;
  `MieracleStudy/Services/VariantGenerationService.swift`, macOS-only).
- **Strictness gap**: the JSON Schema sets `additionalProperties: false` everywhere, but Swift's
  `JSONDecoder` ignores unknown keys. A file with extra fields passes `mieracle-validate` and the app
  but fails a strict JSON Schema validator. Articulate should validate against whichever schema v2
  ships, not rely on leniency.
- **Multi-file**: `ImportValidator.crossDocumentDuplicates` allows the same course/unit in several
  files but errors on a concept, prompt or exam id appearing in two files.

## 3. How import behaves

Pipeline: decode → validate → plan (dry run with new/updated/unchanged counts) → user taps Apply →
upsert and save (`Import/Importer.swift`; `MieracleStudy/Features/Import/ImportView.swift`).

### 3.1 Upsert rules (`Import/Importer.swift`)
- Matches on `externalID` for course, unit, concept, prompt, exam. **Never deletes**, never touches a
  concept's schedule or `ReviewLog`s.
- Course: overwrites `name`; `shortName` only if given; coding flag can only turn on.
- Unit: overwrites `name`, `sortIndex` (`order` or file position), and parent course.
- Concept: overwrites `title`, `explanation`, `resources`, **the full tag set**, and parent unit.
- Prompt: overwrites content; if content changed, **deletes that prompt's AI variants** (they copied
  the old answer).
- Exam: overwrites `name`, `date`, course, **the full unit and tag sets**. Does not touch
  `canvasItemID`.

### 3.2 Validation errors that block
Wrong `schemaVersion`; empty or duplicate ids; empty required text; non-http resource URL; prompt
kind problems (MC index out of range, no tests, Swift without `signature`); exam with unknown
`courseId`/`unitIds` (checked against the file **and** the store) or unparseable date
(`ImportValidator.swift`).

### 3.3 Duplicates across devices
CloudKit can't enforce unique ids, so importing the same file on Mac and iPhone before they sync
creates duplicates; `DuplicateMerger.mergeAll` folds them at launch, keeping the most-reviewed concept
(`Models/DuplicateMerger.swift`, called from `MieracleStudy/App/RootView.swift:44`).

### 3.4 Sharp edges for an automated writer
1. **Course ids must match.** Courses loaded from Canvas get `externalID = "canvas-<canvasCourseID>"`
   (`Canvas/CanvasSync.swift` `importCourses`). If Articulate uses any other id it creates a second
   course. Articulate needs the existing course and unit ids (from an export, or by convention).
2. **Import overwrites `Course.name`.** The local alias is `shortName`, which survives if omitted, but
   Articulate must send the same `name` it found or it renames the class.
3. **Edits in the app get overwritten.** A re-sent concept replaces title, explanation, resources and
   tags; a re-sent exam replaces the user's unit/tag mapping. Articulate should re-send only what it
   owns, or v2 needs per-field ownership.
4. **Exams made from Canvas have UUID ids** (`CanvasSync.makeExam` uses the default
   `UUID().uuidString`), so Articulate can't reference them in `exams[]` or a plan unless it reads them
   back from somewhere.
5. **Concepts can't be removed or retired** by import. Dropped material stays studyable.

## 4. Schema v2 as designed (MieracleStudy #77, #79, #82; #93 open)

From `.plan/70-map-in-app-canvas-intake/tickets/77-concept-model-for-intake.md` unless noted. All
fields optional; v1 files import unchanged; review history stays out of export.

- **`schemaVersion: 2`.**
- **Course** gains `methodGroups[]` (with pair contrasts) and `sourceFiles[]`.
- **Concept** gains `kind` (`definition` | `fact` | `method`; absent = unclassified), `details`
  (the `ConceptDetails` blob), `origin` (`manual` | `imported` | `intake`), `approvedAt`, `groupIds`.
- **`ConceptDetails`**: `explanationCitations: [Citation]`; for methods: `useWhen`, `conditions`,
  `aliases`, `workedExamples`. Each entry is a **claim** `{ text, provenance: cited | gapFill |
  userWritten, citations }`.
- **`Citation`** `{ sourceFileID, page, quote, status: verified | unverified }`; `page` is the slide
  number after PPTX→PDF.
- **`MethodGroup`**: course-scoped, `name`, `question`, citations, many-to-many members, and
  `contrasts: [{ conceptA, conceptB, decidingFeature (claim) }]`. Methods only; confusable definitions
  use tags for now (#99 open).
- **`SourceFile`** (metadata only): `origin` (canvas | dropped), `canvasModuleItemID` (stable key),
  `canvasFileID` + `canvasUpdatedAt`, `canvasCourseID`, name, mime, byte size, page count, content
  hash, optional `archivePath`.
- **New prompt kinds** (`tickets/79-practice-shaping.md`):
  - `methodPick`: `statement`, `targetConceptID`, `explanation`, `verifiedSiblingIDs`. Options are
    **drawn live** at session time (target + verified siblings the learner has studied, max 4).
  - `setUp`: stage 1 method pick, stage 2 free-text set-up graded on key points.
  - Pick + why and non-coding full solve reuse `shortAnswer`. Contrast pairs (`pairID`) are "Next".
- **Grading signal**: `ReviewLog.pickedConceptID` records which sibling was wrongly picked
  (app-internal; not in export).
- **Session ordering** (`tickets/82-session-ordering-for-method-items.md`): in `interleaved`, a method
  group's due items form one shuffled run counted as one slot; `methodPick`/`setUp` ineligible below 2
  studied options.
- **Not decided:** #93 "Extract output schema" (per-claim fields, confidence, duplicate signals, the
  control-character `pattern` on strings from #81) has no resolution. #96 re-intake, #99, #100 also
  open.

**What changes with Articulate owning intake.** Map #70 assumed intake **in the app** with a local
unsynced draft, an in-app flag-review screen before Approve, and `IntakeSource` records
(`tickets/77`, `78`, `80`). If Articulate approves upstream, whatever reaches the folder is already
"approved": `origin = intake` and `approvedAt` are set by Articulate, and #80's review screen either
moves to Articulate or shrinks to in-app flagging after the fact. #78's `IntakeSource` /
`canvasModuleID` state would live in Articulate.

## 5. Exams, units and readiness

- **Model**: `Course → CourseUnit → Concept → Prompt`; the **concept** is the unit of scheduling
  (`Models/Concept.swift`). `Exam` has `name`, `date`, `course`, many-to-many `units` and `tags`, and
  optional `canvasItemID` (`Models/Exam.swift`).
- **Coverage**: an exam covers a concept if the concept's unit is among its units or shares a tag
  (`Exam.covers`).
- **Unit suggestions** (`Overview/ExamUnitSuggester.swift`): "final"/"cumulative" → all units;
  otherwise units whose earliest concept was added after the previous exam and before this one;
  fallback: units no earlier exam covers. This relies on `Concept.createdAt` tracking teaching order,
  which an automated importer keeps only if it imports material as it's taught.
- **Readiness** (`Overview/StudyOverview.swift`): per upcoming exam, days left, concepts covered,
  `studied` (not new), `weak` (studied and ease < 2.1 or lapses ≥ 2), and a session scope (exam units
  + tags).
- **Exam-aware scheduling** (`Scheduling/ReviewPolicy.swift`): after SM-2, a concept covered by a
  future exam gets `interval = max(1, floor(daysLeft/2))`, so it is due before the exam and at least
  twice in the final week.

## 6. How sessions are built

`SessionConfig` (Codable JSON, `Sessions/SessionConfig.swift`): `scope` {courseIDs, unitIDs, tags,
conceptIDs; empty = everything}, `mode`, `length` (`count(n)` | `minutes(n)` | `all`), `order`,
`promptKinds`.

- **Modes** (`Models/StudyEnums.swift`, `Sessions/SessionBuilder.swift`):
  - `review`: not new and `dueDate <= now`; truncated by earliest due date.
  - `learnNew`: `introducedAt == nil`, sorted by unit order, then `createdAt`, then id, and capped by
    `NewConceptBudget.remaining` (daily limit, default 15, counted from today's `ReviewLog.wasNew`;
    `Scheduling/NewConceptBudget.swift`). The limit is a synced setting
    (`MieracleStudy/Services/SettingsSync.swift`).
  - `practice`: everything in scope; truncated by lowest ease / most lapses. Passing a not-due concept
    leaves its schedule alone (`ReviewPolicy`).
  - **One mode per session.** There is no mixed "due + new" session.
- **Prompt choice**: one prompt per concept, allowed kind, not flagged, runnable on this device,
  least recently shown (ties random).
- **Order**: `interleaved` (greedy: largest remaining unit ≠ previous unit), `blocked` (by course,
  unit order; shuffled within), `hardestFirst` (ease asc, lapses desc).
- **Engine** (`Sessions/SessionEngine.swift`): an Again re-queues the concept once, 4 items later, with
  a different prompt if one exists; undo; time limit; saves a `StudySession` record with the config
  JSON and an optional reflection (`Models/StudySessionRecords.swift`).
- **Entry points** (`MieracleStudy/Features/Dashboard/DashboardView.swift`): "Review Due" =
  `SessionConfig(mode: .review)`; "Learn New" = `(mode: .learnNew)`; per class review; **exam prep** =
  `(scope: exam.scope, mode: .practice, length: .minutes(25), order: .hardestFirst)`. Other screens
  start sessions through `AppNavigation.startSession(config)`.
- `SessionPreset` stores named configs as JSON (synced).

## 7. Canvas exam-date sync (`Canvas/`)

- Token in the Keychain; `CanvasClient` reads active courses, assignments (with submission) and
  calendar events per linked course, following `Link: rel="next"` pagination.
- `CanvasSync.run` runs at launch / foreground when the last sync is ≥ 24 h old
  (`MieracleStudy/App/RootView.swift`, `Services/CanvasService.swift`). It upserts `CanvasItem`s
  (`assignment_<id>` / `event_<id>`), deletes items gone from Canvas, records changes
  (added / removed / rescheduled), and **moves each exam linked by `canvasItemID` to Canvas's date**.
- Exams come from the user marking a Canvas item (name heuristic `looksLikeExam` suggests them;
  `makeExam`). Unit/tag mappings are never touched by sync.
- **Overlap with Articulate**: Articulate also reads Canvas. If both write exam dates (Articulate via
  `exams[].date`, the app via sync) they can disagree until the next sync. One side should own exam
  dates; today the app does, and an imported exam has no `canvasItemID` unless the app links it.

## 8. What a daily-plan file needs to carry

Constraints from the code above, not a design:

- **Identity and freshness**: plan id, `schemaVersion`, the local date it is for, `generatedAt`. The
  app must ignore stale plans (a missed day) and not rerun a finished one; today there is nothing to
  record "plan X was done" except a `StudySession` row with the config JSON.
- **Imports first**: which import file(s) the plan depends on (name + hash), so the app applies them
  before building the session. Imports reference existing ids, so order matters.
- **Session intent, expressed in ids the app knows** (course `canvas-<id>`, unit, concept, exam
  external ids):
  - due review scope (usually "all" or a course set): the app must compute what's due, because
    Articulate can't see schedule state;
  - new concepts to introduce today, as an ordered list of concept ids, within the daily new limit
    (`learnNew` today orders by unit and `createdAt`, not by a list);
  - exam focus: exam id (or scope) with practice/hardest-first, like the dashboard's exam prep;
  - length (count or minutes), order, allowed prompt kinds.
- **Multi-part session**: one plan = one sitting but several modes. The engine runs one
  `SessionConfig` per session, so the plan either carries several configs run back to back, or the
  app gains a combined mode.
- **Feedback path**: a plan is only as good as Articulate's view of progress. Either the app writes
  back a small state file (per-concept due date / ease / lapses / introduced, flags, last session
  done), or the plan stays intent-only and the app fills in the rest.

## 9. What MieracleStudy would need to change (not designed here)

1. **Schema v2**: decoder, validator, `MieracleKit.schemaVersion = 2` while still accepting v1,
   new models (`MethodGroup`, `SourceFile`, Concept fields, `methodPick`/`setUp`), JSON Schema file,
   exporter. Needs #93 settled, or Articulate defines it.
2. **Folder watching**: the app has CloudKit only; no iCloud Documents entitlement
   (`MieracleStudy/MieracleStudy.entitlements`: container `iCloud.com.mieracle.MieracleStudy`, services `CloudKit`).
   #91 already planned adding CloudDocuments. Simplest shape: the **Mac** app watches the folder and
   imports; CloudKit then carries content to iPhone. If both devices import, `DuplicateMerger` cleans
   up, but one importer avoids it. The macOS app is unsandboxed (`docs/ARCHITECTURE.md`, Key Design
   Decisions), so it can read a folder path directly. Unattended import replaces the manual Apply
   step, so validation errors need a place to surface (and a file back for Articulate).
3. **Plan-driven session**: read the plan (and get it to iPhone: synced record or KVS), turn it into
   one or more `SessionConfig`s (scope `conceptIDs` already exists), a "Today's session" button on the
   dashboard, and mark the plan done.
4. **Flag button**: today `FlagPromptButton` sets `Prompt.isFlagged` (excluded from sessions, listed in
   the Library's Flagged card) with no reason, no concept-level flag, and nothing exported. Articulate
   needs flags with a reason and target (prompt / concept / citation) written to a file it reads.
5. **Ownership rules** for re-import, so Articulate doesn't overwrite in-app edits (section 3.4) and
   can retire concepts.

## 10. Open questions

1. Does Articulate need schedule state (due, ease, lapses, introduced) back from the app, or is an
   intent-only plan enough? This decides whether the app must write a state file.
2. Who owns exam dates and unit mappings: Canvas sync in the app (today) or Articulate?
3. Who defines the v2 wire format now that #93 is open and intake has moved out of the app: keep #77's
   shape and finish #93 in MieracleStudy, or let Articulate own the schema?
4. Does the in-app flag-review before approval (#80) survive, or does Articulate approve everything
   and the app only flags afterwards?
5. Should the Mac alone import (simpler, but the Mac must be running), or should the iPhone also read
   the folder?
6. How should Articulate learn existing ids (Canvas `canvas-<id>` courses, UUID exam ids, units the
   user created)? A periodic export into the same folder would cover it.
7. Is map #70 (in-app Pick/Extract/Shape, `LLMBackend`, intake window) now retired except for the data
   model?
