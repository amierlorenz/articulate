# Articulate: Build Plan

**Articulate** is two things at once:
1. **A better speaker.** Amier talks through what he's learning and creating every day, and the system coaches him to say it more clearly, coherently, and confidently (for videos, recordings, and everyday conversation).
2. **A second brain.** Everything he says lands in an automatically updating Obsidian vault, fed by his voice (Pocket), life (Google Calendar), and digital world (GitHub, YouTube, Medium), maintained by AI agents, and built around his existing Zettelkasten.

Speaking is the input. Clear thinking, a growing knowledge base, and better communication are the outputs.

> **For Claude Code:** Build this one phase at a time (see "Build Phases"). Stop at the end of each phase, show what changed, and wait for approval before starting the next. Resolve the "Open Questions" with Amier before Phase 1. Never edit or delete an existing note in the vault without asking. Never touch anything marked private.

---

## 1. Vision

Amier talks through what he's learning, building, and thinking. Within minutes, that talk lands in Obsidian, filed in the right place, tagged, linked to related Zettelkasten notes, with tasks sent to his to-do app, and with a short speaking report on how clearly he said it. Each night the system finds connections. Each week it quizzes him on his own notes, gives him a speaking drill, and suggests content ideas. A small dashboard shows that everything is running, how the knowledge base is growing, and how his speaking is improving.

**One-line summary:** *The AI files, links, quizzes, coaches, and suggests. Amier speaks, thinks, writes permanent notes, and decides.*

---

## 2. Principles

1. **AI files, Amier thinks.** Agents write sources, fleeting notes, literature notes, suggestions, quizzes, and drafts. Only Amier promotes ideas into permanent Zettelkasten notes (this is where the learning happens, per *Make It Stick*: generation and elaboration).
2. **Raw is sacred.** Source transcripts are stored once and never edited. Everything else is derived and can be regenerated.
3. **$0 ongoing cost by default.** Local models (Ollama) and free APIs. Paid AI is opt-in only (see Open Question 3).
4. **Private by default for sensitive recordings.** Anything in Pocket's Private folder or tagged "Private" is skipped.
5. **Small skills, one job each.** Easy to test, debug, and swap.
6. **Rules live in the vault, procedures live in skills.** Amier edits rules in Obsidian (plain Markdown). Skills read those rules.
7. **Idempotent and safe.** Running any job twice produces the same result. Agents only create new files or write to their own folders. Never overwrite Amier's notes.
8. **Deterministic first, AI second.** Parse voice tags with code. Use an LLM only when code can't decide.
9. **Every recording is practice.** Speaking feedback is short, specific, and encouraging: one thing done well, one thing to try next time. Never a wall of criticism.

---

## 3. Open Questions (resolve before Phase 1)

Defaults are in brackets. Claude Code: confirm each with Amier.

1. **Zettelkasten conventions.** How are permanent notes named, linked, tagged, and foldered? Which plugins are installed? *(Amier will share the vault folder or a private GitHub repo. Until then, use the defaults in Section 6.)*
2. **Master to-do app.** Which app? *(Use Pocket's built-in integration if the app is supported; otherwise push via the app's API.)*
3. **Heavy AI work.** Ollama only ($0), or Claude Code headless for nightly linking and weekly synthesis (uses existing Claude plan)? *[Ollama only, with a config switch to turn on Claude later.]*
4. **Mac setup.** Laptop or desktop? Plugged in overnight? Lid open? *(Affects Section 14.)*
5. **Book highlights source.** Kindle, Readwise, physical books, or none? *(Affects recall comparison in Section 10.)*
6. **Private recordings.** Skip entirely, or store transcript locally in an excluded folder with no AI processing? *[Skip entirely.]*
7. **Accounts.** GitHub username and repos to track, YouTube channel, Medium handle.
8. **Vault sync.** Obsidian Sync, iCloud, Git, or none? *(iCloud plus agent writes can cause conflict copies. Agents create new files only to reduce risk.)*
9. **Quiz cadence.** *[Weekly full quiz on Sunday, plus a 3-question pop quiz in every third Daily Review.]*
10. **Daily Review time.** *[Generated at 5:00 AM local, ready when Amier wakes.]*
11. **Speaking focus.** Which matters most right now: videos and recordings, everyday conversation, or presentations? Should the coach analyze only solo recordings, or also Amier's parts of conversations (Pocket labels him "me", but speaker labels on Free are unreliable)? *[Solo recordings only at first; conversations later as an opt-in.]*

---

## 4. Architecture

```
SOURCES (raw, never edited)
  Pocket recordings  -> transcript, summary, action items
  Google Calendar    -> events (read-only)
  GitHub             -> commits, PRs, issues, READMEs
  YouTube            -> Amier's published videos + transcripts
  Medium             -> Amier's published posts (RSS)
        |
        v
INGEST (every 30 min, deterministic, no LLM)
  pull new items -> write to Sources/ -> record in state DB
        |
        v
ROUTE (per item)
  parse voice tag -> look up ROUTING.md -> pick processors
  no tag / low confidence -> Inbox/Unsorted (flagged in Daily Review)
        |
        v
PROCESS (per item, local LLM)
  learning | idea | content | project | meeting | journal | quiz-answer | practice
  + extract tasks -> to-do app
  + speaking report (solo recordings)
        |
        v
LINK (nightly)
  embeddings -> similar ZK notes -> LLM judges -> suggestions (not edits)
        |
        v
REVIEW (Amier)
  Daily Review note: what came in, suggested links, tasks, unsorted, pop quiz
  Amier promotes ideas into permanent notes, accepts or rejects links
        |
        v
SYNTHESIZE (weekly)
  Weekly Synthesis, ZK Quiz, Speaking Drill, Content Ideas, metrics
        |
        v
ACCESS
  Agents read the vault (except Private) and write only to Inbox/Agent/
  Dashboard shows health, growth, learning, speaking, content
```

---

## 5. Vault Structure

Merge with Amier's existing ZK layout once known. Existing ZK folders stay exactly where they are.

```
Vault/
  _system/                  # Rules and config Amier edits (agents read)
    README.md               # How the system works, for Amier
    AGENTS.md               # Map + rules for any agent reading the vault
    VOICE-TAGS.md           # Spoken tag cheat sheet (Section 7)
    ROUTING.md              # Tag -> folder -> template -> processors (Section 8)
    NAMING.md               # Naming and dating rules (Section 6)
    REGISTRY.md             # Known books, projects, people, aliases
    PROMPTS/                # Prompt files used by processors (editable)
    TEMPLATES/              # Note templates for each type
    Dashboard.md            # Auto-generated nightly
  Inbox/
    Fleeting/               # Agent-created fleeting notes
    Unsorted/               # Could not route confidently
    Agent/                  # The ONLY place outside agents may write
  Sources/
    Pocket/YYYY/MM/
    Calendar/YYYY/MM/
    GitHub/<repo>/YYYY/MM/
    YouTube/
    Medium/
  Literature/
    Books/<Book Title>/
    Articles/
    Videos/
    Courses/
  Zettelkasten/             # Amier's existing permanent notes (name TBD)
  Projects/<Project>/
  Meetings/YYYY/MM/
  Journal/YYYY/MM/
  Content/
    Ideas/
    Drafts/
    Published/
  Reviews/
    Daily/
    Weekly/
    Quizzes/
  Speaking/
    Reports/YYYY/MM/        # One short report per solo recording
    Drills/                 # Weekly drills and re-takes
    Speaking Progress.md    # Auto-updated trends + current focus skill
  Private/                  # Excluded from all agents and git (if used)
```

---

## 6. Naming and Dating

**Rules**
- Use the **recording time**, not the ingest time. Pocket gives `recording_at` in UTC; convert to local time.
- **Time zone:** `America/New_York`.
- **Filenames:** local time, sortable, readable.
- **Frontmatter:** ISO 8601 with offset (for example `2026-11-17T09:17:57-05:00`).
- **Weeks:** ISO weeks (`2026-W47`).
- Slugs: Title Case words from the title, max ~60 characters, no characters Obsidian dislikes (`# ^ [ ] | \ / : ? * " < >`).

| Note type | Pattern | Example |
|---|---|---|
| Pocket source | `Sources/Pocket/YYYY/MM/YYYY-MM-DD HHmm <Title>.md` | `2026-11-17 0917 Guitar Pickup Reminder.md` |
| Fleeting note | `Inbox/Fleeting/YYYYMMDDHHmm <Title>.md` | `202611170917 Spaced recall beats rereading.md` |
| Book hub | `Literature/Books/<Book>/<Book>.md` | `Make It Stick/Make It Stick.md` |
| Book session | `Literature/Books/<Book>/<Book> Ch03 <Mode> YYYY-MM-DD.md` | `Make It Stick Ch03 Recall 2026-11-17.md` |
| Permanent note | Amier's ZK convention (TBD) [default `YYYYMMDDHHmm <Claim as title>.md`] | `202611171030 Effortful retrieval strengthens memory.md` |
| Meeting | `Meetings/YYYY/MM/YYYY-MM-DD HHmm <Event title>.md` | |
| Journal | `Journal/YYYY/MM/YYYY-MM-DD HHmm Journal.md` | |
| Content idea | `Content/Ideas/YYYYMMDD <Working title>.md` | |
| Daily Review | `Reviews/Daily/YYYY-MM-DD Review.md` | `2026-11-17 Review.md` |
| Weekly Synthesis | `Reviews/Weekly/YYYY-Www Weekly.md` | `2026-W47 Weekly.md` |
| Quiz | `Reviews/Quizzes/YYYY-Www Quiz.md` | `2026-W47 Quiz.md` |
| Speaking report | `Speaking/Reports/YYYY/MM/YYYY-MM-DD HHmm Speaking.md` | `2026-11-17 0917 Speaking.md` |
| Speaking drill | `Speaking/Drills/YYYY-Www Drill.md` | `2026-W47 Drill.md` |

**Frontmatter schema (all agent-created notes)**

```yaml
---
id: 202611170917            # ZK-style UID (YYYYMMDDHHmm), unique
type: learning              # learning | idea | content | project | meeting | journal | quiz | practice | speaking | source | review
created: 2026-11-17T09:20:03-05:00   # when the file was written
recorded: 2026-11-17T09:17:57-05:00  # when it was said / happened
source: pocket              # pocket | gcal | github | youtube | medium | manual
source_id: 9c5237c0-...     # e.g. Pocket recording id (used for dedupe)
source_note: "[[2026-11-17 0917 Guitar Pickup Reminder]]"
voice_tag: "book, make it stick, chapter 3, recall"
subject: "[[Make It Stick]]"
mode: recall                # recall | predict | explain | reflect | highlights | draft
status: new                 # new | reviewed | promoted | archived
tags: [learning, memory]
agent_generated: true
reviewed: false
last_quizzed:               # filled by quiz grader
quiz_score:                 # 0-1, rolling
---
```

---

## 7. Voice Tags (rules for Amier when recording)

Say the tag in the **first 15 seconds**. Format: **"Tag: TYPE, SUBJECT, DETAIL."** Then talk normally.

**Types**

| Say | Use for | Example |
|---|---|---|
| **Book** | Reading | "Tag: book, Make It Stick, chapter 3, recall." |
| **Article / Video / Podcast / Course** | Other learning | "Tag: video, Andrej Karpathy, tokenizers." |
| **Idea** | Raw ideas | "Tag: idea, study app." |
| **Content** | Something to publish | "Tag: content, Medium, why I record myself studying." |
| **Project** | Work on a project | "Tag: project, Articulate, design decisions." |
| **Meeting** | Meetings, calls | "Tag: meeting." (calendar fills in the rest) |
| **Journal** | Reflection | "Tag: journal." |
| **Quiz** | Answering a quiz out loud | "Tag: quiz, week 47." |
| **Practice** | Speaking drills and re-takes | "Tag: practice, week 47, take 2." |
| **Private** | Do not process | "Tag: private." (also use Pocket's Private folder) |

**Learning modes** (add after subject, optional)
- **predict**: before reading, guess what it covers (generation effect)
- **recall**: after reading, explain from memory with the book closed (retrieval practice)
- **explain**: teach it like to a friend (elaboration)
- **reflect**: how it connects to my life or other ideas
- **highlights**: reading quotes out loud word for word
- **draft**: talking through a piece of content

**Inline markers** (say anytime mid-recording)
- "**To do:** ..." -> task
- "**Content idea:** ..." -> content idea
- "**Question:** ..." -> open question (shows in Review, may become a quiz item)
- "**Link to** <note or topic> ..." -> forced link suggestion
- "**Quote:** ..." -> captured as a quote, verbatim

**No tag?** That's fine. The agent guesses. Low confidence goes to `Inbox/Unsorted` and shows up in the Daily Review.

Store this table in `_system/VOICE-TAGS.md` so Amier can print it or keep it on his phone.

---

## 8. Routing Rules (for the agent)

Stored in `_system/ROUTING.md` as a Markdown table plus a YAML block the code parses. Amier edits it in Obsidian.

```yaml
routes:
  book:     { folder: "Literature/Books/{subject}", template: book-session, processors: [learning, tasks, link] }
  article:  { folder: "Literature/Articles",        template: literature,   processors: [learning, tasks, link] }
  video:    { folder: "Literature/Videos",          template: literature,   processors: [learning, tasks, link] }
  podcast:  { folder: "Literature/Articles",        template: literature,   processors: [learning, tasks, link] }
  course:   { folder: "Literature/Courses/{subject}", template: literature, processors: [learning, tasks, link] }
  idea:     { folder: "Inbox/Fleeting",             template: fleeting,     processors: [idea, tasks, link] }
  content:  { folder: "Content/Drafts",             template: content-draft, processors: [content, tasks, link] }
  project:  { folder: "Projects/{subject}",         template: project-log,  processors: [project, tasks, link] }
  meeting:  { folder: "Meetings/{yyyy}/{mm}",       template: meeting,      processors: [meeting, tasks] }
  journal:  { folder: "Journal/{yyyy}/{mm}",        template: journal,      processors: [journal] }
  quiz:     { folder: "Reviews/Quizzes",            template: quiz-answer,  processors: [quiz-grade, speaking] }
  practice: { folder: "Speaking/Drills",            template: drill-take,   processors: [speaking, drill-compare] }
  private:  { skip: true }
  unknown:  { folder: "Inbox/Unsorted",             template: fleeting,     processors: [classify] }
```

**Routing algorithm**
1. Skip if the recording is in a Pocket folder listed in `config.private_folder_ids`.
2. Read the first ~15 seconds of transcript. Regex for `tag[:,]?\s*(\w+)`. Also accept the type word alone as the first word.
3. Fuzzy-match the subject against `_system/REGISTRY.md` aliases (rapidfuzz, threshold 85). Transcription errors are expected ("make a stick" -> Make It Stick).
4. If the type is found but the subject is new: create it with `status: needs-confirmation` and list it in the Daily Review.
5. If no tag: ask the local LLM to classify into a type with a confidence score. Below 0.7 -> `unknown`.
6. Scan the full transcript for inline markers.
7. Log every routing decision (input, rule used, confidence) to the state DB.

**Registry example** (`_system/REGISTRY.md`)

```yaml
books:
  - title: Make It Stick
    authors: [Peter C. Brown, Henry L. Roediger III, Mark A. McDaniel]
    aliases: [make it stick, makeitstick, make a stick]
projects:
  - name: Articulate
    aliases: [articulate, second brain, knowledge base, the pipeline]
    repo: <github repo>
```

---

## 9. Skills

Many small skills plus one orchestrator. Each skill is a folder in the pipeline repo under `.claude/skills/<name>/SKILL.md`. Each SKILL.md is short and points to the rule files in `_system/`. Python modules in `articulate/` do the actual work; skills tell Claude Code how and when to run them and how to judge the output.

| Skill | Job | Runs |
|---|---|---|
| `articulate-ingest-pocket` | Pull new and updated Pocket recordings into `Sources/Pocket` | Every 30 min |
| `articulate-route` | Voice tag parsing and routing | After ingest |
| `articulate-process-learning` | Book, article, video, course sessions (Section 10) | After route |
| `articulate-process-idea` | Turn idea recordings into fleeting notes | After route |
| `articulate-process-content` | Turn draft recordings into outlines and cleaned drafts in Amier's voice | After route |
| `articulate-process-meeting` | Match to calendar event, notes, decisions, tasks | After route |
| `articulate-extract-tasks` | Pocket action items + "To do:" markers -> to-do app | After route |
| `articulate-link-suggest` | Embeddings + LLM judging -> link suggestions | Nightly |
| `articulate-daily-review` | Build the Daily Review note | Nightly (5:00 AM) |
| `articulate-quiz-generate` | Build the weekly ZK quiz and pop quizzes | Weekly + every 3rd day |
| `articulate-quiz-grade` | Grade answers (written or spoken), update scores | After quiz answers arrive |
| `articulate-content-ideas` | Suggest content ideas from the ZK | Weekly |
| `articulate-weekly-synthesis` | Patterns, open loops, wins, what to revisit | Weekly |
| `articulate-speaking-report` | Speaking metrics + coaching for each solo recording (Section 13) | After route |
| `articulate-speaking-drill` | Weekly drill, compares re-takes, updates focus skill | Weekly + after practice recordings |
| `articulate-ingest-calendar` | Google Calendar events (read-only) | Every 30 min |
| `articulate-ingest-github` | Commits, PRs, issues per tracked repo | Nightly |
| `articulate-ingest-youtube` | Amier's new videos + transcripts | Daily |
| `articulate-ingest-medium` | Amier's new posts via RSS | Daily |
| `articulate-metrics` | Update metrics DB and `_system/Dashboard.md` | After every run |
| `articulate-ask` | Guide for agents querying the vault safely | On demand |
| `articulate-nightly` | Orchestrator: runs the nightly sequence in order | Nightly |

Rule for every skill: read `_system/AGENTS.md`, respect Private, write only new files, log what it did.

---

## 10. Learning Loop (Make It Stick)

| Technique | How the system supports it |
|---|---|
| **Retrieval practice** | "recall" sessions. Agent compares recall to highlights (if available) and lists what was missed, vague, or wrong. |
| **Generation** | "predict" sessions before reading. Agent later compares predictions to recall. |
| **Elaboration** | "explain" and "reflect" sessions. Agent adds 3 elaboration prompts to the Review ("How does this connect to X?"). |
| **Spaced repetition** | Agent drafts flashcards (Obsidian Spaced Repetition plugin format) in a `## Flashcards` section. Amier approves by keeping them. |
| **Interleaving** | Weekly quiz mixes topics and books. |
| **Calibration** | Quiz asks for confidence (1 to 5) before answering. Grader tracks confidence vs. score. |
| **Reflection** | Weekly Synthesis asks: what did I learn, what's still fuzzy, what will I try. |

**Book session output** (`articulate-process-learning`)
- Cleaned summary of what Amier said (his words, lightly edited)
- Key claims he made (bullet list)
- Gaps: missed or fuzzy compared to highlights or chapter outline
- 3 elaboration questions
- Draft flashcards (max 5)
- Suggested permanent note titles (claims), for Amier to write himself
- Links: book hub, chapter, related ZK notes

---

## 11. Quiz System

**Weekly ZK Quiz** (`articulate-quiz-generate`, Sunday)
- 8 questions from Amier's notes:
  - 4 from permanent notes not quizzed in 30+ days (spacing)
  - 2 from this week's new notes
  - 2 random across distant topics (interleaving)
- Question types: explain it, connect two notes, apply it to a scenario, spot the mistake.
- Each question: confidence 1-5 first, then answer.
- Answer in the note, or by voice: "Tag: quiz, week 47" then "Question 1: ...".

**Pop quiz:** 3 questions in every third Daily Review.

**Grading** (`articulate-quiz-grade`): compares answers to the source notes, gives short feedback, links the note, updates `last_quizzed` and `quiz_score`, logs calibration (confidence vs. correctness). Weak notes come back sooner.

---

## 12. Content Idea Engine

**Weekly** (`articulate-content-ideas`): 3 to 5 suggestions from:
1. **Dense clusters:** 5+ linked notes on one theme
2. **Bridges:** surprising links between distant topics (often the best ideas)
3. **Recurring themes:** things Amier keeps talking about across recordings
4. **Open questions:** "Question:" markers that keep coming up

Each suggestion: working title, hook (1 sentence), format (Medium, YouTube, short post), notes it draws from (links), why now.

**Pipeline:** `idea -> outlined -> drafting -> published`. Amier marks keep or skip. Skipped ideas are remembered so they aren't suggested again. Published Medium/YouTube items link back to their idea note automatically.

**Talk-to-draft:** "Tag: content, Medium, <title>, draft" produces an outline plus a cleaned draft in Amier's voice in `Content/Drafts/`. Never published automatically.

---

## 13. Speaking Coach (the "Articulate" part)

**Goal:** speak more clearly and coherently, on camera and in conversation. Every solo recording doubles as practice.

**Speaking report** (`articulate-speaking-report`, one per solo recording, ~10 lines)

*Measured with code from the transcript timestamps (free, exact):*
- **Pace:** words per minute (comfortable range is roughly 130 to 170)
- **Fillers per minute:** um, uh, like, you know, kind of, sort of, basically, I mean
- **False starts:** restarted or abandoned sentences ("I, I'm in a...", "it's like, it's...")
- **Sentence length:** average, and the longest run-on
- **Hedges:** "I don't know", "maybe", "I guess" (fine sometimes, a habit when constant)

*Judged by the local LLM:*
- **Point first?** Was the main point stated in the first 30 seconds?
- **Structure:** did it follow a shape (PREP: Point, Reason, Example, Point; or What, So what, Now what)?
- **Tangents:** where the thread got lost
- **The tight version:** "Here's how you could say this in 3 sentences," using Amier's own words
- **One win, one next step:** always exactly one of each

**Focus skill:** the coach picks **one** skill to work on for 2 weeks (for example "fewer fillers" or "point first"), based on the weakest trend. Every report highlights that skill. When it improves, the coach moves to the next one. One thing at a time.

**Weekly drill** (`articulate-speaking-drill`, in the Sunday batch)
- A 60-second prompt pulled from Amier's own notes ("Explain [[Effortful retrieval strengthens memory]] to a friend").
- Record take 1 ("Tag: practice, week 47, take 1"), read the report, record take 2.
- The coach compares takes side by side. Seeing the improvement is the point.
- Drill types rotate: explain a note, connect two notes, tell a story, argue a position, summarize a book chapter in 60 seconds.

**Ties to the rest of the system**
- Quiz answers spoken out loud get both a knowledge grade and a speaking report.
- Content drafts ("Tag: content, ..., draft") get extra structure feedback before Amier records the real video.
- Learning sessions (recall, explain) build the habit of explaining ideas clearly, which is the same skill.

**Later (optional, after Phase 11):** download audio and run local Whisper with word timestamps for pause analysis (long pauses vs. filler-filled pauses) and pace variation.

**Data note:** Pocket's transcripts keep fillers ("um", "like"), which makes this possible. If a future transcript cleans them out, the filler metric is marked unavailable rather than reported as zero.

---

## 14. Running While Asleep (macOS)

**Jobs** (launchd LaunchAgents in `~/Library/LaunchAgents/`)

| Job | Schedule | Does |
|---|---|---|
| `com.amier.articulate.ingest` | Every 30 min (`StartInterval 1800`) | Ingest Pocket + Calendar, route, process |
| `com.amier.articulate.nightly` | 3:00 AM (`StartCalendarInterval`) | GitHub, YouTube, Medium, linking, Daily Review, metrics |
| `com.amier.articulate.weekly` | Sunday 4:00 AM | Weekly Synthesis, Quiz, Speaking Drill, Content Ideas |

**Make sure the Mac is awake**
- Schedule a wake: `sudo pmset repeat wakeorpoweron MTWRFSU 02:55:00`
- Wrap each job in `caffeinate -i` so the Mac doesn't sleep mid-run.
- If the Mac was asleep anyway, `StartCalendarInterval` jobs run at the next wake.
- Laptop: plugged in, lid open (or clamshell with external display). Power Nap does not run these jobs.
- Ollama must be running: keep "Launch at login" on, and have each job check `ollama` health first and start it if needed.

**Reliability**
- Lock file per job so runs never overlap.
- Logs: `~/Library/Logs/articulate/<job>.log`, rotated.
- On failure: macOS notification (`osascript`), plus an error section at the top of the next Daily Review.
- Retries with backoff for network calls. Respect Pocket's 50 requests/min limit (read `X-RateLimit-*` headers).

---

## 15. Dashboard and Metrics

**Storage:** SQLite at `state/articulate.db` (outside the vault).

**Tables:** `recordings`, `notes`, `routes`, `runs`, `link_suggestions`, `tasks`, `quiz_items`, `quiz_results`, `content_ideas`, `speaking_reports`, `drill_takes`, `embeddings`.

**Two views**
1. **`_system/Dashboard.md`** (Phase 1+): regenerated after every run. Markdown tables + Mermaid charts. Works on phone through Obsidian.
2. **Local web app** (Phase 10): `http://localhost:8787`, FastAPI + one HTML page with Chart.js. Read-only.

**Metrics**
- **Health:** last successful run per job, failures this week, Pocket API calls vs. limit
- **Capture:** recordings per day, minutes, by type, voice-tag hit rate
- **Knowledge:** fleeting -> permanent promotion rate, links suggested vs. accepted, orphan notes, average links per note
- **Learning:** quiz score trend, calibration gap, notes due for review, books in progress
- **Content:** ideas -> outlined -> drafting -> published
- **Tasks:** extracted, sent, completed
- **Speaking:** fillers per minute, pace, false starts, point-first rate, structure score, current focus skill, drill take 1 vs. take 2 improvement (all as trends over time)
- **Streaks:** days with at least one recording, days with Daily Review opened

---

## 16. Connectors

**Pocket (primary)**
- Base URL `https://public.heypocketai.com/api/v1`, header `Authorization: Bearer pk_...`
- `GET /public/recordings` (page, limit max 100, `start_date`/`end_date` as UTC `YYYY-MM-DD`; no updated-after cursor, so poll the last 3 days and compare `updated_at`)
- `GET /public/recordings/{id}?include_transcript=true&include_summarizations=true` (transcript segments with start, end, speaker, text; summaries; action items)
- Audio download URL endpoint (optional backup for local Whisper)
- `POST /public/search`, tags, folders
- Free plan confirmed by Pocket support (Nov 2026): API keys, full transcripts and summaries, audio, personal webhooks, 50 req/min.
- Free plan keeps summaries 30 days, so ingest promptly.
- Speaker labels on Free are unreliable. Don't depend on them.
- Pocket can re-process a recording; store a content hash and re-sync if it changes.
- Webhooks (optional later): need a public URL (e.g., Cloudflare Tunnel). Polling is the default.

**Google Calendar (read-only)**
- Google Calendar API with OAuth desktop credentials, read-only scope.
- Match a recording to an event when their time windows overlap. Pull title, attendees, description into the meeting note.
- Daily Review lists today's events.

**GitHub**
- `gh` CLI or REST with a fine-grained, read-only token.
- Nightly: commits, PRs, issues for tracked repos -> `Sources/GitHub/` and a short devlog line in the matching project note.
- Optional: back up the vault (minus Private) to a private repo.

**YouTube**
- YouTube Data API for Amier's own uploads; captions for transcripts.
- Watch history isn't available through the API. Use "Tag: video, ..." while watching instead.

**Medium**
- RSS feed of Amier's profile -> `Content/Published/`, linked to the original idea note.

**To-do app:** TBD (Open Question 2).

---

## 17. Agent Access

- **`_system/AGENTS.md`**: the map. Folder meanings, naming, how to search, what not to touch.
- **Read:** whole vault except `Private/`.
- **Write:** only `Inbox/Agent/`. Amier moves things out.
- **How agents connect** (pick per agent, off unless needed):
  - Claude Code opened in the vault folder (reads `CLAUDE.md` + `AGENTS.md`)
  - A filesystem or Obsidian MCP server scoped to the vault, with `Private/` excluded
  - The Pocket MCP server for questions about raw recordings
- `articulate-ask` skill teaches agents to search by links and tags first, then full text.

---

## 18. Tech Stack and Repo Layout

- **Python 3.12** with `uv`
- `httpx`, `pydantic`, `python-frontmatter`, `rapidfuzz`, `keyring`, `feedparser`, `google-api-python-client`, `sqlite-vec`, `ollama`, `pytest`
- **Local models:** Ollama (`llama3.1:8b` or similar for text, `nomic-embed-text` for embeddings)
- **Secrets:** macOS Keychain via `keyring`. Never in the vault or the repo.

```
articulate/
  CLAUDE.md                 # Project context for Claude Code
  PLAN.md                   # This file
  pyproject.toml
  config.example.yaml       # vault path, timezone, private folder ids, schedules, llm settings
  articulate/
    cli.py                  # `articulate ingest`, `articulate nightly`, `articulate weekly`, `articulate review`, `articulate metrics`, `--dry-run`
    config.py  state.py  vault.py  llm.py  embeddings.py
    connectors/  pocket.py  gcal.py  github.py  youtube.py  medium.py  todo.py
    route/       voice_tags.py  router.py  registry.py
    process/     learning.py  idea.py  content.py  meeting.py  journal.py  tasks.py
    speaking/    metrics.py  coach.py  drill.py
    link/        suggest.py
    review/      daily.py  weekly.py
    quiz/        generate.py  grade.py
    content/     ideas.py
    metrics/     collect.py  dashboard_md.py
  .claude/skills/articulate-*/SKILL.md
  vault_seed/_system/       # Starter rule files copied into the vault in Phase 0
  launchd/*.plist
  dashboard/                # Phase 10 web app
  tests/fixtures/           # Fake Pocket responses, sample transcripts
```

**`--dry-run` everywhere:** writes to `_preview/` instead of the vault, so Amier can check output first.

---

## 19. Build Phases

Each phase ends with a demo and Amier's approval.

**Phase 0: Foundation**
- Repo, config, Keychain secrets, state DB, logging, `--dry-run`
- Seed `_system/` files (README, AGENTS, VOICE-TAGS, ROUTING, NAMING, REGISTRY, TEMPLATES)
- Done when: `articulate doctor` checks vault path, Pocket key, Ollama, and timezone, and all pass.

**Phase 1: Pocket -> Obsidian**
- `articulate-ingest-pocket`, raw source notes, dedupe, Private skip
- Simple Daily Review (what came in) and `_system/Dashboard.md`
- `com.amier.articulate.ingest` launchd job + wake schedule
- Done when: a new recording shows up in Obsidian within 30 min, running twice creates no duplicates, Private recordings never appear.

**Phase 2: Voice tags, routing, tasks**
- `articulate-route`, registry fuzzy match, processors for learning, idea, journal
- `articulate-extract-tasks` -> to-do app
- Basic `articulate-speaking-report`: code-measured metrics only (pace, fillers, false starts, sentence length) + `Speaking/Speaking Progress.md`
- Done when: 10 test recordings with different tags land in the right folders, tasks reach the to-do app with a link back, and every solo recording gets a speaking report with real numbers.

**Phase 3: Linking**
- Embeddings index of the ZK, `articulate-link-suggest`, accept/reject checkboxes in the Review
- Done when: suggestions appear with a one-line reason each, and accepted links are applied only to agent-created notes.

**Phase 4: Learning loop + quizzes**
- Full book session output, flashcards, `articulate-quiz-generate`, `articulate-quiz-grade`, pop quizzes
- Done when: a recall session produces gaps and flashcards, and a spoken quiz answer gets graded.

**Phase 5: Content ideas + weekly synthesis**
- `articulate-content-ideas`, `articulate-process-content`, `articulate-weekly-synthesis`, `com.amier.articulate.weekly`
- Done when: Sunday produces a Weekly note, a Quiz, and 3-5 content ideas linked to real notes.

**Phase 6: Speaking coach**
- LLM coaching (point first, structure, tangents, the tight version, one win and one next step)
- Focus skill selection, `articulate-speaking-drill`, take 1 vs. take 2 comparison, speaking section in Daily and Weekly Reviews
- Done when: a practice recording gets a report, a re-take gets a side-by-side comparison, and the focus skill shows in `Speaking Progress.md`.

**Phase 7: Google Calendar + meetings**
- Done when: a meeting recording gets the right event title and attendees.

**Phase 8: GitHub**
- Done when: yesterday's commits show up as a devlog line in the right project.

**Phase 9: YouTube + Medium**
- Done when: a new video or post appears in `Content/Published/` linked to its idea.

**Phase 10: Dashboard web app**
- Done when: `localhost:8787` shows all metric groups from Section 15.

**Phase 11: Agent access**
- `AGENTS.md`, `articulate-ask`, MCP config examples
- Done when: an outside agent can answer "what have I learned about memory?" from the vault without touching `Private/`.

---

## 20. Testing

- Unit tests for voice tag parsing (including transcription errors), routing, naming, dedupe.
- Fixture Pocket responses (never real private data in tests).
- Golden-file tests: given a fixture transcript, the generated note matches the expected Markdown.
- `articulate doctor` for health checks.
- Every LLM prompt lives in `_system/PROMPTS/` so Amier can tune it without touching code.

---

## 21. Privacy and Safety

- Private recordings are skipped before any processing.
- Local models by default. No transcript leaves the Mac except to services Amier explicitly enables.
- `Private/` excluded from git, MCP servers, and embeddings.
- Agents never delete. Agents never edit Amier's permanent notes.
- Secrets only in Keychain.
- Recording other people: get their consent first.
