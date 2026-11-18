# Pocket REST API, checked with the real key

Task for ticket amierlorenz/articulate#15, done 2026-11-18. It verifies the "Not verified" list in
[Pocket for class lectures](pocket-class-lectures.md) against Amier's account, with a personal `pk_` key stored
in the Keychain (service `articulate`, account `pocket_api_key`). Only shapes, counts and ids were recorded,
never titles or what anyone said.

Calls made: 10 REST requests (list, folders, tags, filters, 4 detail calls, one 404), 7 guessed action-item
REST paths (all 404), and the MCP server (`initialize`, `tools/list`, `search_pocket_actionitems`).

## Summary

1. **The key is read-only on Free.** Pocket wouldn't give Amier a read & write key on the Free plan. Reading
   works everywhere Articulate needs it. Every write fails: marking action items done, uploading, editing.
2. **Action items aren't in REST.** The detail call returns `summarizations.v2` with `summary` and `mindMap`
   only, even though `v2ActionItemsStatus` is `completed`. There's no action-item REST endpoint. They are read
   through **Pocket's MCP server** (`https://public.heypocketai.com/mcp`, Streamable HTTP, same `Bearer pk_...`
   key) with `search_pocket_actionitems`, which works with the read-only key.
3. **Times are seconds (floats); `duration` is whole seconds.** A normal recording's last segment ends within
   about 5 s of `duration`.
4. **Merged recordings carry a flag but no link.** `transcript.metadata = {merged: true, sourceRecordingCount}`.
   No ids of the originals.
5. **Speaker labels are mostly missing.** In REST, `speaker` is absent on most segments. Present values are
   `SPEAKER_<n>` or `USER_SPEAKER_<uuid>` (a named person, resolved through `data.speakers`).
6. **`state` values seen: `completed`, `pending`.**
7. **Rate limit: 50 per window**, headers `X-Ratelimit-Limit`, `X-Ratelimit-Remaining`, `X-Ratelimit-Reset`
   (Unix epoch seconds).
8. **The list endpoint ignores `folder_id`.** `tag_ids` filters correctly.
9. **Private:** a folder (created as `kind: space`) with id `5194b8b6-ec40-4d99-91a9-059fb610141e`, and a tag
   `private` with id `7dbebf3f-418b-4ea1-936d-61c67089d829`.
10. **Hidden recordings: not verified.** Amier couldn't find Hide in the app.

## REST details

### List: `GET /public/recordings`

- Envelope: `{success, data: [...], pagination: {page, limit, total, total_pages, has_more}}`.
- Item fields: `id`, `title`, `folder_id`, `duration`, `state`, `language`, `recording_at`, `created_at`,
  `updated_at`, `tags[]`. No `recorded_by` and no `transcript` key.
- **`duration` can be `null`** on a `completed` recording (seen once). Code must handle it, for example by
  using the last segment's `end`.
- A 78-second recording was still `pending` about 26 hours after it was recorded. Don't wait forever on
  `pending`; retry on later polls and give up after a few days with a health note.
- `folder_id=<id>` as a query parameter is accepted (200) but **ignored**: all 14 recordings came back. Filter
  by folder on the client side.
- `tag_ids=<id>` returned exactly the one tagged recording.

### Detail: `GET /public/recordings/{id}?include_transcript=true&include_summarizations=true`

```
data:
  id, title, folder_id, duration, state, language, recording_at, created_at, updated_at, tags[]
  transcript: { metadata: {merged, sourceRecordingCount}?, segments[], text }
  speakers: { "<label>": {name, speakerId} }   # only named speakers
  summarizations: { "<id>": { id, summarizationId, processingStatus, autoInitiated, createdAt, updatedAt,
                              settings{language, modelId, themeId},
                              v2{ summary{markdown, version}, mindMap{nodes, type} },
                              v2SummaryStatus{status}, v2MindMapStatus{status}, v2ActionItemsStatus{status} } }
```

- Not returned although the docs list them: `recorded_by`, `transcript_error`, `summarizations_errors`,
  `translation`. Don't depend on them.
- `transcript.text` is the flattened transcript, which is handy for hashing and dedupe.
- Segment keys: `start`, `end`, `text`, `originalText`, and `speaker` **only when known**.

**Time units:**

| Recording | `duration` | first `start` | last `end` |
| --- | --- | --- | --- |
| Lecture (original) | 3880 | 0.32 | 3876.71 |
| Conversation | 2928 | 16.56 | 2927.195 |
| Conversation | 2598 | 1.84 | 2573.545 |
| Lecture (merged copy) | 3935 | 0.32 | **7756.71** |

So segment times are seconds from the start of the recording. In a merged copy they run past `duration`, which
confirms that a merged timeline isn't wall-clock time.

**Speakers:**

| Recording | Segments | Without `speaker` | `SPEAKER_<n>` | `USER_SPEAKER_<uuid>` |
| --- | --- | --- | --- | --- |
| Lecture | 902 | 864 | 38 | 0 |
| Conversation | 639 | 489 | 149 | 1 (named through `speakers`) |
| Conversation | 379 | 359 | 20 | 0 |

The MCP's `Unknown Speaker <n>` labels (866 in one lecture) are made up by the MCP for unlabelled segments.
REST just leaves the key out. Either way, the earlier conclusion holds: ignore speakers for lectures.

### Merged recordings

- The merged copy has `transcript.metadata = {merged: true, sourceRecordingCount: N}`. Originals have no
  `metadata` (or `merged: false`).
- No field links the merged copy to its originals. **Dedupe rule:** skip any recording whose
  `transcript.metadata.merged` is true when an original with the same `recording_at` exists. Otherwise keep the
  merged copy, and still don't treat its timeline as wall-clock. The `Merged - ` title prefix is a fallback
  only.
- Since `merged` shows only in the detail call, a merged copy costs one detail request before it's skipped.

### Errors and rate limit

- 404: `{success: false, error: "<message>"}`. **No `error_code`.** Branch on the HTTP status.
- Every response has `X-Ratelimit-Limit: 50`, `X-Ratelimit-Remaining`, and `X-Ratelimit-Reset` (epoch seconds,
  probably the end of a one-minute window). On a 429, sleep until `Reset`.

### Folders and tags

- `GET /public/folders`: `{success, data: [{id, name, kind, parent_folder_id, space_id, color,
  recording_count, total_recording_count, created_at, updated_at, children[]}]}`.
- The Private folder Amier created came back as **`kind: space`** with `parent_folder_id: null`. The recording
  inside it has `folder_id` equal to the space's id. So the Private check is: `folder_id` is the configured id
  **or any id in its subtree** (`children`, recursively). It doesn't matter whether Private is a space or a
  folder.
- `GET /public/tags`: `{id, name, color, usage_count, created_at, updated_at}`. The tag is named `private`
  (lowercase). Match by configured id, and also by name with case ignored as a safety net.
- Config values:
  - `pocket.private_folder_id = "5194b8b6-ec40-4d99-91a9-059fb610141e"`
  - `pocket.private_tag_id = "7dbebf3f-418b-4ea1-936d-61c67089d829"`
- Fail closed, as already decided: if the configured folder or tag id is missing from `/public/folders` or
  `/public/tags`, stop the Pocket ingest and add a health flag.

## Action items: MCP, read-only

- REST: none of these exist (all 404): `/public/action-items`, `/public/action_items`, `/public/actionitems`,
  `/public/recordings/{id}/action-items`, `/public/recordings/{id}/action_items`. The docs list no action-item
  REST endpoint either. The only documented place is the webhook payload, which needs a public URL.
- MCP: `https://public.heypocketai.com/mcp`, Streamable HTTP. Send `initialize`, then use the
  `Mcp-Session-Id` header from its response. Auth is `Authorization: Bearer pk_...`. Tools for this key:
  `get_account_info`, `list_pocket_folders`, `search_pocket_actionitems`, `search_pocket_conversations`,
  `search_pocket_conversations_timerange`, `update_pocket_actionitem`.
- `search_pocket_actionitems` with no arguments returned `{success, data: {actions[], limit, total}}`, with 22
  items. Item fields: `actionItemId`, `actionType`, `assignee`, `context`, `dueDate`, `label`, `payload`,
  `priority`, `recordingDate`, `recordingDateLocal`, `recordingId`, `recordingTitle`, `status`. Filters per
  the docs: `query`, `status` (TODO, IN_PROGRESS, COMPLETED, CANCELLED), `priority`, `category`, `dueBefore`,
  `dueAfter`, `recordingDateFrom`, `recordingDateTo`.
- `recordingId` lets Articulate apply the Private check (folder and tag of that recording) before sending
  anything to master-todo. `assignee` is the field for the "first-person only" rule.
- `update_pocket_actionitem` needs `recordings:write` (docs), which a Free key doesn't have.

## What the read-only key costs

| Capability | Needs write | Used by Articulate? | Effect |
| --- | --- | --- | --- |
| List/read recordings, transcripts, summaries, folders, tags, audio URL | no | yes | Works |
| Read action items (MCP search) | no | yes | Works |
| Mark a Pocket action item done or edit it | yes | yes: the [task funnel](https://github.com/amierlorenz/articulate/issues/7)'s nightly poll was going to mark Pocket items done | **Lost.** Pocket's own action-item list goes stale; master-todo is the only place items get ticked. |
| Upload recordings | yes | no | none |
| Rename, retag, move or delete recordings | yes | no (and Articulate shouldn't) | none |

Nothing else in the decisions so far writes to Pocket.

## Still not verified

- Whether hidden recordings show up in `GET /public/recordings`. Amier couldn't find Hide in the app. This is
  low stakes, because privacy depends on the Private folder and tag, not on Hide.
- What a 429 response body looks like (not triggered).
- Whether `X-Ratelimit-Reset` marks a one-minute window.
