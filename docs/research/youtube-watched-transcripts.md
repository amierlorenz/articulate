# Transcripts for watched YouTube videos

Research for the ticket "Getting transcripts for watched YouTube videos". Date: 2026-11-19.
Machine for the experiments: Amier's Mac, M2 Pro, 16 GB RAM, macOS (Darwin 27), home connection, logged out of YouTube (no cookies).

Already decided (not reopened here): Amier adds videos to a private playlist named "Articulate". The pipeline checks it
in the daytime pass at :15/:45 past each hour and writes one Literature note per video. Only local models or
`claude -p` process transcripts. Use local transcription when there are no captions. No length cap. A SQLite ledger
tracks what's done. Google OAuth (read-only) is already planned for Calendar on the same account.

## TL;DR

- **The official captions API is out.** `captions.download` needs the caller to be able to edit the video. It works
  only for the owner's own videos, so it can't fetch transcripts for videos Amier watches.
- **Use the YouTube Data API for the playlist and metadata** (`playlistItems.list` + `videos.list`, 1 quota unit each,
  about 64 units/day at 32 checks/day). Use **yt-dlp** for captions (manual first, then auto). Fall back to **yt-dlp
  audio + local transcription** (mlx-whisper large-v3-turbo; whisper.cpp also works).
- **Local transcription is fast and accurate enough to use as a full alternative, not just a fallback.** On the M2 Pro,
  large-v3-turbo transcribed 21.8 minutes of audio in 48 s (mlx) and 69 s (whisper.cpp). That is about 2.2 to 3.2
  minutes per hour of audio. Word error rate (WER) against the creator's captions was about 1.5%.
- **OAuth:** use one Desktop OAuth client and one refresh token covering `calendar.readonly` and `youtube.readonly`.
  Do **not** leave an External app in "Testing" status, because its refresh tokens die after 7 days. Use the
  **Personal** user type for a new project if it is available, or publish an External app to "In production" and
  click through the unverified-app screen once.

## 1. Getting transcripts for videos Amier doesn't own

### 1a. Official API: `captions.download` is owner-only (confirmed)

- "This method requires the user to have permission to edit the video." The required scopes are `youtube.force-ssl` or
  `youtubepartner`. Without permission it returns a 403 `forbidden` error. Cost: 200 units. Source:
  [captions.download](https://developers.google.com/youtube/v3/docs/captions/download).
- `captions.list` (50 units) only lists the tracks; it doesn't return their text.
  ([quota table](https://developers.google.com/youtube/v3/determine_quota_cost))
- `videos.list` `contentDetails.caption` (`true`/`false`) tells you **whether** captions exist, at 1 unit
  ([videos resource](https://developers.google.com/youtube/v3/docs/videos)). Use it as a cheap hint. It doesn't count
  auto-generated (ASR) captions, so `false` doesn't mean "no transcript available".

Conclusion: no official, ToS-sanctioned way exists to get transcript text for third-party videos. Every route below is
unofficial.

### 1b. Unofficial caption fetchers

| | `youtube-transcript-api` 1.2.5 | `yt-dlp` 2026.10.26 subtitles |
|---|---|---|
| What it calls | Undocumented YouTube web-client API ([README](https://github.com/jdepoix/youtube-transcript-api)) | YouTube player/innertube clients; needs a JS runtime for challenges ([EJS wiki](https://github.com/yt-dlp/yt-dlp/wiki/EJS)) |
| Manual vs auto | Both; `find_manually_created_transcript` / `find_generated_transcript` | `--write-subs` (manual) / `--write-auto-subs` (ASR); auto track is `<lang>-orig`, others are machine translations |
| Languages | All tracks plus YouTube's translation | All tracks plus auto-translated ones (`xx-<src>`) |
| Timestamps | Per caption line: `start`, `duration` (seconds) | json3/srv3/vtt; the **auto** track in json3 has **word-level** offsets |
| Metadata too | No | Yes (`--print`/`-J`: title, channel, duration, upload date, language) |
| Blocking | README: "YouTube has started blocking most IPs that are known to belong to cloud providers"; recommends rotating residential proxies; cookie auth "currently not available" | PO-token / client matrix changes often ([PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)); without a PO token, requests "may return HTTP Error 403, or result in your account or IP address being blocked" |
| Breakage cadence | Breaks when YouTube changes the web client; one maintainer | Very active; fixes usually ship within days; nightly channel. Needs `yt-dlp[default]` (EJS scripts) and Deno ≥ 2.3 or Node ≥ 22 |

**Experiment** (public video `QLgH5_mqX28`, "How to Train Yourself to be Less Reactive or Impulsive", Therapy in a
Nutshell, 21:49, home IP, no cookies, run with `uvx`):

- `yt-dlp --list-subs`: 2.1 s. Found 7 **manual** tracks (ar, nl, en, de, hi, pt, es) and auto captions, including
  `en-orig`. It warned "No supported JavaScript runtime could be found… deprecated, and some formats may be missing".
  Subtitles and audio still downloaded. Node v26 is installed but needs `--js-runtimes node`; Deno isn't installed.
- `yt-dlp --write-subs --write-auto-subs --sub-langs en,en-orig --sub-format json3`: 2.5 s. Results: manual `en`
  (239 caption lines, 47 KB) and auto `en-orig` (1,241 events, 4,751 word segments with ms offsets, 346 KB).
- `youtube_transcript_api QLgH5_mqX28 --languages en --format json`: 3.0 s, 239 lines with `start`/`duration`. These
  are the same lines as yt-dlp's manual `en` track.
- No bot check, 403 or "Sign in to confirm you're not a bot" occurred for one video. This test doesn't show what
  happens at volume. At Amier's rate (a few videos a day from one residential IP) the risk is low but not zero.

**Choice between them:** yt-dlp. It's needed anyway for the audio fallback, it gives word-level timing on auto
captions, it can return metadata as a cross-check, and its maintenance is much faster. youtube-transcript-api adds a
second dependency that breaks independently for little gain. Keep it only as an optional third try.

### 1c. Audio + local transcription

- **Audio:** `yt-dlp -f bestaudio` downloaded 16.2 MB of Opus in about 1 s. Re-encoding to m4a took about 20 s of
  CPU, so skip it: keep the native file and let ffmpeg resample to 16 kHz mono WAV (takes about 1 s) for whisper.cpp.
  mlx-whisper reads the file directly.
- Same 1,309 s audio, timings include model load, models already downloaded:

| Engine (version) | Model | Wall time | ≈ per audio hour | Peak RSS | WER vs creator `en` captions | Timestamps |
|---|---|---|---|---|---|---|
| YouTube auto captions (`en-orig`) | — | 2.5 s (download) | — | — | 0.8%* | word |
| mlx-whisper 0.4.3 (MLX) | `mlx-community/whisper-large-v3-turbo` | 48 s | ~2.2 min | 1.8 GB | 1.5% | segment (word with `--word-timestamps True`) |
| whisper.cpp 1.8.4 (Homebrew, Metal) | `ggml-large-v3-turbo-q5_0` (574 MB) | 69 s | ~3.2 min | 1.0 GB | 1.6% | segment (~5–10 s); word via `-ml 1`/`-owts` |
| parakeet-mlx 0.7.0 | Parakeet TDT (default, mostly English) | 44 s | ~2.0 min | 0.7 GB | 2.7% | sentence + token, with confidence |

\* The creator's "manual" English track seems to be an edited version of YouTube's ASR (0.8% difference), so the
reference favors the auto captions. Treat all three local numbers as roughly 1.5–3% on clean, single-speaker speech.
Expect worse on music, crosstalk or jargon.

Notes:
- whisper.cpp prints `tensor API disabled for pre-M5 and pre-A19 devices`, so the M2 Pro runs it on the plain Metal
  path. It still runs at about 19x real time.
- A 3-hour podcast would take about 7–10 minutes and about 2 GB of RAM. That fits the "no length cap" rule. Run it
  outside the :15/:45 time budget (see recommendation) so a long video doesn't stall the pass.
- Whisper models are multilingual. Parakeet's default model is mostly English (a v3 model covers about 25 European
  languages). Whisper is the safer default; Parakeet is an option for a faster English-only setup.
- Model weights come from Hugging Face without credentials (`ggerganov/whisper.cpp`, `mlx-community/...`). Pin them
  and cache them once.

### 1d. ToS and breakage risk

- YouTube ToS forbids accessing the Service "using any automated means (such as robots, botnets or scrapers)" and
  downloading content except "as expressly authorized by the Service" or with written permission
  ([Terms](https://www.youtube.com/static?template=terms)). Both caption scraping and audio download break these terms.
  Audio download is the more clear-cut breach. The practical risk is IP or account blocking, not legal action, for
  personal low-volume use. **Never pass Amier's account cookies** to yt-dlp, so a block can't reach his Google account.
- Breakage risk: high over time for both unofficial tools (PO tokens, JS challenges, client deprecations). Mitigations:
  `uv` dependency on `yt-dlp[default]`, weekly `uv lock --upgrade-package yt-dlp`, install Deno (or pass
  `--js-runtimes node`), and alert on repeated failures.

## 2. Reading the private playlist

- **Call:** `playlistItems.list?part=snippet,contentDetails,status&playlistId=<id>&maxResults=50`. Cost: **1 unit**.
  Use `pageToken` for more than 50 items. ([list](https://developers.google.com/youtube/v3/docs/playlistItems/list))
  Find the playlist ID once with `playlists.list?mine=true` (1 unit) by title "Articulate", then store it in config.
  Private playlists need an authorized request (403 `playlistItemsNotAccessible` otherwise). The docs' samples use
  `youtube.readonly`. Watch Later and history can't be read through the API (`watchLaterNotAccessible`,
  `watchHistoryNotAccessible`), which is why a custom playlist is the right design.
- **Fields** ([playlistItems resource](https://developers.google.com/youtube/v3/docs/playlistItems)):
  - `snippet.publishedAt`: "The date and time that the item was added to the playlist." → **Watched**.
  - `contentDetails.videoPublishedAt`: "The date and time that the video was published to YouTube." → **Published**.
  - `snippet.videoOwnerChannelTitle` / `videoOwnerChannelId`: uploader → **Channel**.
  - `contentDetails.note`: user note (≤280 chars). Could be read as Amier's own comment if he ever sets one.
  - The docs don't say whether the owner reading their own private playlist sees different fields. Unverified,
    observed behavior: items whose video went private or was deleted show as "Private video"/"Deleted video" and
    lack `videoPublishedAt`/owner fields. Flag these and don't process them.
- **Quota:** default 10,000 units/day, reset at midnight Pacific
  ([quota](https://developers.google.com/youtube/v3/determine_quota_cost)). One check = 1 unit (+1 per extra page)
  + 1 unit for a batched `videos.list` on new IDs. 32 checks/day ≈ 32–64 units. That leaves a large margin.
- **OAuth client:** create a **Desktop app** client. Use the loopback redirect (`127.0.0.1:<random port>`) with PKCE.
  The secret isn't confidential for desktop apps ("Desktop apps are public clients and cannot keep secrets
  confidential"). ([native-app flow](https://developers.google.com/identity/protocols/oauth2/native-app))
  `google-auth-oauthlib`'s `InstalledAppFlow.run_local_server()` implements this flow. Store the refresh token in the
  macOS Keychain (the pipeline's code does that; this research didn't touch it).
- **Refresh-token lifetime** ([OAuth 2.0 overview](https://developers.google.com/identity/protocols/oauth2),
  [audience/publishing status](https://support.google.com/cloud/answer/15549945)):
  - External app in **Testing**: tokens expire **7 days** after consent (unless only basic profile scopes). Not usable
    for an unattended job.
  - **In production** (External, unverified): no fixed expiry. The user sees the "unverified app" warning once. The
    100-user cap is irrelevant here. Tokens still die after **6 months unused**, on revocation, or when more than 100
    tokens exist per account per client (oldest invalidated).
  - **Personal** user type (newer Google Auth Platform option): limits authorization to "the single Google Account
    that created the application". Such apps "do not require OAuth app verification", aren't subject to the user cap,
    and are not subject to the "7-day test user token expiration". It can only be chosen when the project is set up
    and can't be changed later. **Preferred**, if Amier creates the project with the same Google account the
    Calendar/YouTube data lives on.
- **One client, one token, both scopes: yes.** The `scope` parameter is a space-delimited list. Request
  `https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/youtube.readonly` in one consent.
  Incremental authorization "is not supported for installed apps", so adding YouTube later means **re-consenting with
  the full scope list** and replacing the stored token. Users can grant some scopes and not others, so check the
  granted scopes in the token response.
  Note: if Amier's videos live on a **Brand Account** channel, consent must pick that channel identity, and the
  Calendar scope then applies to that identity. If so, use two tokens. Check during setup.

## 3. Metadata for the note fields

| Note field | Source | Cost |
|---|---|---|
| Channel | `playlistItems.snippet.videoOwnerChannelTitle` (or `videos.snippet.channelTitle`) | in list |
| Title | `videos.snippet.title` (≤100 chars) | 1 unit per batch of IDs |
| URL | build `https://youtu.be/<videoId>`; timestamp links `https://youtu.be/<id>?t=<sec>` | — |
| Duration | `videos.contentDetails.duration`, ISO 8601 (`PT#M#S`, `PT#H#M#S`, `P#DT#H#M#S`) → `isodate`/manual parse | same call |
| Published | `playlistItems.contentDetails.videoPublishedAt` (= `videos.snippet.publishedAt`) | in list |
| Watched | `playlistItems.snippet.publishedAt` (time added to playlist) — a proxy, not real watch time | in list |
| Language hint | `videos.snippet.defaultAudioLanguage` → pick caption language / whisper `--language` | same call |
| Captions hint | `videos.contentDetails.caption` (manual captions only) | same call |
| Skip live | `videos.snippet.liveBroadcastContent` ∈ {`live`,`upcoming`} → defer | same call |

Call `videos.list?part=snippet,contentDetails,status&id=<comma-separated new IDs>` once per pass, for new IDs only
([videos](https://developers.google.com/youtube/v3/docs/videos)). Convert all times from UTC to local time when writing
the note.

## 4. Recommendation

**Pass at :15/:45 (fast, bounded):**
1. `playlistItems.list` → diff against the ledger → insert new rows `status=queued`.
2. `videos.list` for new IDs → fill metadata. Defer rows where `liveBroadcastContent != none`, and flag rows that are
   private or deleted.
3. **Captions first (yt-dlp):** manual track in `defaultAudioLanguage` (or `en`), else the auto `<lang>-orig` track,
   json3 format. On success → `transcript_source=manual|auto` → hand off to note writing.
4. No captions, or a caption fetch error → `status=needs_audio`.

**Transcription worker (same pass or a separate launchd job, one video at a time, lock file):**
5. `yt-dlp -f bestaudio` (no re-encode) → mlx-whisper `large-v3-turbo` with the language hint → segments with
   timestamps → `transcript_source=whisper`. Delete the audio once the transcript is stored.
   whisper.cpp (`ggml-large-v3-turbo-q5_0`) is the drop-in alternative if Python/MLX dependencies become a problem.
6. Notes: `claude -p` (or a local LLM) over the transcript → TL;DW + timestamped notes linking `youtu.be/<id>?t=<sec>`.

**Retry and flag rules:**
- Transient errors (network, 403, 429, "Sign in to confirm") → retry with backoff (next pass, then ×2, cap at 24 h).
  After **3 consecutive failures** for one video, or **any** bot-check across 2 passes in a row, flag to master-todo
  ("yt-dlp blocked/broken: run `uv lock --upgrade-package yt-dlp`") and pause downloads for that day.
- Permanent: private, deleted, members-only, age-restricted (no cookies by policy), or region-blocked → `status=flagged`
  with a reason. Write a stub note with the metadata only.
- Live or upcoming → `deferred` until `liveBroadcastContent=none`.
- OAuth `invalid_grant` → flag "re-auth Google" once. Don't retry until the token changes.
- Quality: if the transcript is empty, or has fewer words than about 20% of `duration_s × 2.5 words/s`, flag it
  (likely music or a non-speech video).

**Ledger table `youtube_videos`:**

| column | type | note |
|---|---|---|
| `video_id` | TEXT PK | |
| `playlist_item_id` | TEXT | lets a removal from the playlist be detected |
| `added_at` | TEXT (UTC ISO) | `snippet.publishedAt` → Watched |
| `published_at` | TEXT | `videoPublishedAt` |
| `channel_title`, `channel_id`, `title` | TEXT | |
| `duration_s` | INTEGER | parsed ISO 8601 |
| `language` | TEXT | `defaultAudioLanguage` or detected |
| `status` | TEXT | `queued` / `needs_audio` / `transcribed` / `noted` / `deferred` / `flagged` / `removed` |
| `transcript_source` | TEXT | `manual` / `auto` / `whisper` / null |
| `transcript_lang` | TEXT | track or detected language |
| `transcript_path` | TEXT | cached transcript JSON (keep, so notes can be rewritten without re-fetching) |
| `asr_model` | TEXT | e.g. `mlx-whisper large-v3-turbo`, for later re-runs |
| `note_path` | TEXT | vault path of the Literature note |
| `attempts` | INTEGER | consecutive failures |
| `last_error` | TEXT | |
| `next_attempt_at` | TEXT | backoff |
| `flag_reason` | TEXT | |
| `first_seen_at`, `updated_at`, `done_at` | TEXT | |
| `tool_versions` | TEXT | yt-dlp version at fetch time, to correlate breakage |

## Open items

- Install Deno (or set `--js-runtimes node` in the yt-dlp config). Without a runtime, yt-dlp already warns that
  extraction is deprecated.
- Check whether Google Cloud offers the Personal user type when creating the project. If not, publish to production.
- Confirm how private and deleted items appear in the private playlist on the first real run.

## Sources

- YouTube Data API: [captions.download](https://developers.google.com/youtube/v3/docs/captions/download),
  [playlistItems](https://developers.google.com/youtube/v3/docs/playlistItems),
  [playlistItems.list](https://developers.google.com/youtube/v3/docs/playlistItems/list),
  [videos](https://developers.google.com/youtube/v3/docs/videos),
  [quota costs](https://developers.google.com/youtube/v3/determine_quota_cost)
- Google OAuth: [overview / refresh-token expiry](https://developers.google.com/identity/protocols/oauth2),
  [installed apps](https://developers.google.com/identity/protocols/oauth2/native-app),
  [publishing status & Personal user type](https://support.google.com/cloud/answer/15549945)
- [YouTube Terms of Service](https://www.youtube.com/static?template=terms)
- yt-dlp: [EJS wiki](https://github.com/yt-dlp/yt-dlp/wiki/EJS), [PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
- [youtube-transcript-api README](https://github.com/jdepoix/youtube-transcript-api)
- Local experiments: yt-dlp 2026.10.26, youtube-transcript-api 1.2.5, mlx-whisper 0.4.3, whisper.cpp 1.8.4,
  parakeet-mlx 0.7.0 (mlx 0.32.4), all via `uvx`/Homebrew on 2026-11-19.
