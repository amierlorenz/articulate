# Pocket for class lectures

Research for ticket amierlorenz/articulate#2, done 2026-11-17. It answers: how does Articulate find and read
class lectures in Pocket, and what can it rely on?

Sources:

- **[Docs]** Pocket docs at `docs.heypocketai.com` (`public.heypocketai.com/docs` redirects there). The full text
  is at `https://docs.heypocketai.com/llms-full.txt`. Pages used: API Reference, List recordings, Get recording
  details, Get audio download URL, Search recordings, List folders and spaces, List tags, Webhooks, Pocket MCP
  Server, Speaker Management, Tagging and Labels, Combining Recordings, Recycle Bin and Security, Calendar Hub.
- **[Pricing]** `heypocket.com` home page, Free vs Pro plan table.
- **[Observed]** Amier's own account through the Pocket MCP connector and the Google Calendar MCP, on 2026-11-17.
  The REST API was **not** called: no API key was read for this research. Only shapes, counts and field names are
  recorded here, never what anyone said.

## Summary

1. **Nothing in Pocket marks a recording as a class.** The account has no folders, no Private folder, and no
   "lecture" tag. The reliable signal is **time**: `recording_at` plus `duration`, overlapped with an event on
   the Google Calendar "Classes" calendar. The AI title ("... Lecture") is a weak hint you can use to break ties.
2. **Calendar matching works on real data.** Today's lecture recording started 12 minutes after its class event
   started and ended within about a minute of the event's end. It overlaps the class event for about 63 of its
   65 minutes and overlaps no other class.
3. **Lecture transcripts have usable text and timestamps, but no usable speakers.** One 65-minute lecture had
   902 segments and 866 distinct speaker labels. Treat a lecture as a single stream of timestamped text.
4. **Merged recordings duplicate lectures.** Pocket's Combine feature creates a new recording and keeps the
   originals, so the same lecture shows up twice with the same `recording_at`. Articulate has to dedupe.
5. **Free plan: summaries last 30 days, transcripts have no stated limit.** Summaries stay 30 days on Free and
   forever on Pro [Pricing]. Neither the docs nor the pricing page give a transcript retention period. The
   transcript is the source of truth; the summary is a bonus that expires.
6. **Rate limit: per key, 429 with `X-RateLimit-*` headers [Docs].** The 50 requests per minute figure comes
   from Pocket support (PLAN.md section 16); the public docs give no number. Polling every 30 minutes uses only a
   few requests.
7. **Webhooks exist and are signed (HMAC-SHA256), but they need a public URL.** Polling stays the default.

## 1. Telling class recordings apart

### What Pocket gives you

| Signal | Where | Use for classes |
| --- | --- | --- |
| `recording_at` (UTC start, from the device clock) + `duration` (seconds) | List and detail responses; webhook `recording.recordingAt` / `recording.duration` | **Primary.** The webhook docs say to add `duration` to `recordingAt` to get the end time, and to use `recordingAt` rather than the title to find when a conversation happened. [Docs: Webhooks, Recording Timestamps] |
| `title` | List and detail | Weak hint. See "Titles" below. |
| `folder_id` | List and detail responses | Only if Amier files lectures into a folder. `GET /public/recordings` has **no** folder filter (only `start_date`, `end_date`, `tag_ids`, `page`, `limit`), so filter on the client side. [Docs: List recordings] |
| `tags[]` (`id`, `name`, `color`) | List and detail; `tag_ids` filter on list | Only if Amier tags lectures. The docs say Pocket's AI "sometimes" suggests tags; they don't say whether suggested tags get applied automatically. [Docs: Tagging] |
| Calendar link inside Pocket | Pocket app's Calendar Hub | The app can link a recording to a Google Calendar event [Docs: Calendar Hub], but **no API field exposes the link**. Articulate has to do the matching itself. |

**Observed, 2026-11-17:**

- `list_pocket_folders` returned an empty list, so the account has no folders and no spaces.
- Searching with a `lecture` tag filter found nothing.
- All 6 recordings in the account are from today:
  - one AI-titled lecture
  - one merged copy of the lecture
  - two recordings still on the default title
  - two other AI-titled personal recordings
- One personal recording that is clearly sensitive sits outside any folder (it isn't a class recording, and its
  content isn't described here). So the "skip the Private folder" rule protects nothing yet.

### Titles

- The default title is a display string in the owner's local time, for example "Conversation on Nov 17th at
  12:07". The docs say it "usually changes after processing". [Docs: Webhooks]
- Observed: one recording's default title time was about 26 minutes after its `recording_at`. That matches its
  end time, not its start time. **Never parse a time out of the title.**
- After processing, the lecture got an AI title that ended in "Lecture" and named the topic. Two recordings
  still had their default titles hours later.
- A merged recording is titled `Merged - <title of one original> + N more`. The docs call the new entry
  "Merged Memory" while it processes. [Observed; Docs: Combining Recordings]

### Matching to Google Calendar class events

**What's on the calendar (observed):**

- Classes live on a calendar named **"Classes"** (timezone America/New_York). They are weekly recurring events
  with a `recurringEventId`.
- `summary` follows the pattern `<Course name> @ <Building room>`.
- The first line of `description` is the course code (`CSC 382`, `MAT 305`, and so on).
- The pattern isn't universal: one online class has an emoji-decorated summary and the course code in a longer
  description.
- There are also Google Classroom calendars (IDs start with `c_classroom...`, timezone UTC) and an office-hours
  calendar. Neither is needed for matching lectures.

**Real-data check:**

| | Start (UTC) | End (UTC) |
| --- | --- | --- |
| Lecture recording | 17:42:07 | about 18:46:44 (3,877 s) |
| Class event | 17:30 | 18:45 |

The recording overlaps the event for about 63 minutes, which is 97% of the recording. It doesn't overlap any
other class that day.

**Suggested rule:**

1. Match a recording to a Classes event when they overlap by at least 50% of the recording's duration, or by at
   least 20 minutes.
2. Pick the event with the largest overlap.
3. Ignore recordings shorter than about 5 minutes.
4. Take the course key from the description's course code, not from the room.
5. If a recording has no match but its title ends in "Lecture", send it to review instead of guessing.

Edge cases this rule has to handle:

- **Late start and running over.** Seen here: recording started 12 minutes late and ran a minute past the event.
- **A recording that spans two back-to-back classes.** The Monday/Wednesday/Friday classes start on the hour with
  10-minute gaps between them.
- **A merged recording.** Its `recording_at` is the earliest original's, but its `duration` is the combined
  length.

### Merged recordings (dedupe)

- **Combine never deletes the originals.** "Your original recordings are **not removed**." Pocket makes a new
  recording, re-summarizes it, and fires a `recording.merged` webhook. [Docs: Combining Recordings; Webhooks]
- **Observed: the lecture shows up twice.**
  - The original lecture: 902 segments, 0.32 to 3,876.71 s.
  - The merged copy: same `recording_at`, 917 segments ending at 7,756.71 s.
  - The merged copy has 15 segments from a short clip (0 to 53 s), then a jump of about 3,828 s, then the
    lecture's 902 segments shifted by +3,880 s.
- **Consequence 1:** the timeline of a merged recording isn't wall-clock time. Never compute "minute N of class"
  from a merged recording's offsets.
- **Consequence 2:** the API docs list no field that links a merged recording to its originals. Dedupe by
  content (for example, a hash of the shared segment texts) or by the "Merged - " title prefix plus a matching
  `recording_at`. Prefer the original.

## 2. Transcript shape

**REST** (`GET /public/recordings/{id}?include_transcript=true&include_summarizations=true`) [Docs: Get recording
details]:

- `data.transcript.segments[]`: `start`, `end`, `text`, `originalText`, `speaker`, `speakerName`.
  - The units of `start` and `end` aren't documented.
  - The webhook and MCP docs say **seconds**, and the MCP returned seconds as floats (for example `16.56`).
- `data.speakers`: an object that maps a speaker label to `{ name, speakerId }`.
- Other fields:
  - `id`, `title`, `state`, `created_at`, `updated_at`, `recording_at`, `duration`, `language`, `folder_id`
  - `recorded_by{user_id, display_name, email}`
  - `tags[]`
  - `summarizations`, `summarizations_errors[]`, `transcript_error`
  - `translation{...}`, `translation_error`
- `summarizations` shows as `null` in the REST example. Its real shape is documented only in the webhook payload:
  - keyed by summarization ID, with `processingStatus`
  - `v2.summary{title, emoji, markdown, bulletPoints[]}`
  - `v2.actionItems{version, actionItems[{id, title, dueDate, status, isCompleted, ...}]}`
  - `v2.mindMap`
  - `settings{modelId, themeId, language}`
  - `autoInitiated`, `createdAt`, `updatedAt` [Docs: Webhooks]
- The list endpoint returns `transcript: null`. Get the transcript from the detail call.
- Error bodies use the same envelope as success, plus `error` and `error_code`. The docs' error examples show
  `"success": true`, which looks like a docs bug, so branch on the HTTP status code instead.

**Speaker labels:**

| Source | Labels |
| --- | --- |
| Webhook docs | Raw labels like `SPEAKER_00` or `USER_SPEAKER_...`; `speakerName` only when the speaker has a name. |
| MCP (observed) | `Speaker 0`, `Speaker 1`, `Unknown Speaker <n>`, and once `me`. |

The REST format isn't verified yet. Check it on the first real API call.

**What a real lecture looks like (observed, original lecture recording):**

| Metric | Value |
| --- | --- |
| Duration | about 64.6 min (0.32 to 3,876.71 s) |
| Segments | 902 |
| Words | about 6,900 |
| Characters (flattened) | about 55k |
| Segment length | about 4 s on average, max about 38 s |
| Longest silence gap | about 38 s |
| Distinct speaker labels | 866 |
| Stable labels | only `Speaker 0` (10 segments) and `Speaker 1` (28 segments) |

**`Unknown Speaker <n>` is basically one label per line.** Other recordings looked the same: a 49-minute
two-person conversation had 492 labels across 639 segments.

The docs back this up:

- Pocket only auto-names speakers in recordings with **8 or fewer speakers**. A lecture hall is more than 8.
  [Docs: Webhooks, Speaker Names]
- Detection is unavailable for recordings of 3 hours or more. [Docs: Speaker Management]
- On Free, "Auto-detect speaker names" is listed as Pro-only, and transcription is "standard accuracy".
  [Pricing]

**Consequence:** ignore `speaker` for lectures. Chunk by time windows (for example 2 to 5 minutes, using
`start`), not by turns. Raw text has filler and some garbled words, so plan for errors when you triangulate
against slides and study guides.

**A recording that hasn't been transcribed yet:** one recording came back with `transcriptAvailable: false` and
no segments. In REST, check `state` and `transcript_error` and retry on a later poll. The doc examples don't list
the possible `state` values.

**Audio:** `GET /public/recordings/{id}/audio-url?expires_in=60..86400` (default 3600) returns a pre-signed S3
URL. It's a fallback if Articulate ever re-transcribes lectures locally. [Docs]

## 3. What the free plan keeps

| Item | Free | Source |
| --- | --- | --- |
| Summaries | "Unlimited summaries, saved for 30 days" (Pro: "saved forever") | Pricing |
| Transcripts | Retention not stated anywhere. "Unlimited transcription (standard accuracy)" | Pricing |
| Audio | "Text export and audio download" is on Free | Pricing |
| Deleted recordings | Recycle Bin keeps them 30 days, then deletes them permanently | Docs: Recycle Bin |
| API, personal webhooks, 50 requests/min | Confirmed on Free by Pocket support (Nov 2026) | PLAN.md section 16 (not in the public docs) |
| MCP tools | Search, folders, account info and action items work on all plans. `get_pocket_conversation` and `query_pocket_meetings` are Pro only | Docs: MCP |
| Account | Plan is `free` | Observed (`get_account_info`) |

**Consequences:**

- Pull lectures and their summaries within days, and store the full transcript and summary in the vault. That's
  already the PLAN.md section 16 approach.
- Don't build anything that re-reads a summary from Pocket after 30 days.
- Free-plan MCP still returns full transcripts: a search with no query runs in recency mode. That's handy for
  agents, but Articulate itself should use REST.

## 4. Rate limits and webhooks

**Rate limits:**

- The docs only say that `429` means "Per-key rate limit hit (see `X-RateLimit-*` response headers)". [Docs: MCP,
  Errors]
- `POST /public/search` takes `query` (required), `limit` (1 to 20, default 8) and `filters` (undocumented
  object).
- Polling the last 3 days at `limit=100` costs 1 request, plus 1 detail request for each new or changed
  recording. That's about 5 to 10 requests on a class day, well under 50 per minute.
- `start_date` and `end_date` are UTC days: `start_date` starts at midnight and `end_date` ends at 23:59:59. An
  evening class in Eastern time can land on the next UTC day, so the 3-day window handles it.
- The list endpoint has no updated-after filter, so compare `updated_at`, as PLAN.md already says.

**Webhooks:**

- Personal webhooks are set up in the app's integrations screen. [Docs: Webhooks]
- Delivery: POST with JSON, 30-second timeout, 3 retries with backoff from 1 to 30 seconds, delivered at least
  once, so expect duplicates.
- Signing: `X-HeyPocket-Signature` is the HMAC-SHA256 of `{timestamp}.{rawBody}`, and `X-HeyPocket-Timestamp`
  is in milliseconds.
- Events useful for lectures:
  - `transcription.completed`
  - `summary.completed` (all post-processing done)
  - `recording.merged`
  - `recording.deleted`
  - `transcript.edited` (also fires for every recording affected by a speaker rename)
- The payload carries the transcript and summarizations, so a webhook could save the detail call.
- **Decision unchanged:** a headless job on a Mac with no public URL keeps polling. Webhooks need a tunnel, so
  they stay an optional later step.

## 5. The Private folder

- **The account has no folders at all, so there's no Private folder.** Folders come back from `GET /public/folders`
  as a tree: `id`, `name`, `kind` (space or folder), `parent_folder_id`, `space_id`, `color`, `children[]`,
  `recording_count`, `total_recording_count`. [Docs; observed: empty]
- No Pocket doc mentions a "Private" folder or an `is_private` flag. A Private folder would be an ordinary folder
  that Amier creates and names. Articulate should match it by `folder_id` from config (as PLAN.md does), and
  check `parent_folder_id` too, so recordings in a subfolder of Private are also skipped.
- **Pocket's "Hide" is different.** It removes a recording from the main list without deleting it [Docs: Recycle
  Bin]. The docs don't say whether the API still returns hidden recordings, so don't treat Hide as Private.
- **Fail closed:** PLAN.md section 4 rule 4 also skips anything tagged "Private". Keep both checks, and treat a
  configured Private folder ID that no longer exists as a config error, not as "nothing is private".

## Not verified

These need a REST call with Amier's key:

- the units of `start`/`end` in REST (expected: seconds)
- the speaker label format in REST
- the `state` values
- the `X-RateLimit-*` header names and numbers
- whether hidden recordings appear in the list
- whether merged recordings carry any link to their originals
