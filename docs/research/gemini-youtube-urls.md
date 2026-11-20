# Gemini reading watched YouTube videos by link

Question: can the Gemini API read a public YouTube URL directly (passed as `fileData.fileUri`) and
replace the yt-dlp caption download plus the audio + mlx-whisper fallback, with zero ban risk?

Researched 2026-11-19 from the official docs. `ai.google.dev/gemini-api/docs/*` and
`ai.google.dev/api/*` now 301-redirect to `aistudio.google.com/docs/*` and
`aistudio.google.com/api-reference/*`; the citations below use the new URLs.

## Short answer

Probably yes, for public videos. Google documents YouTube URLs as a video input, fetches the video
itself, and allows 8 hours of YouTube video per day on the free tier. This replaces yt-dlp
for every video that is public. It does not work for unlisted or private videos. The feature is
still labelled preview, and the docs don't say whether the model reads YouTube's captions, so
transcript quality and timestamp accuracy need the measurements below.

## Findings

### Supported models
- The video page lists no per-model support table for YouTube URLs; its YouTube examples use
  `gemini-4-hydrogen`. Static processing (the normal mode) works on "all Gemini models"; the
  newer agentic mode lists Gemini 4 Carbon, Argon, Neon, Hydrogen, 3.8 Flash, 3.6 Flash and
  3.5 Flash Lite. [video-understanding]
- Model codes: `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite` (all "Stable"),
  `gemini-4-hydrogen` (no badge; 1M context; modalities include Video). [models]
- Before Gemini 2.5, 1 video per request; 2.5 and later, up to 10 videos per request. [video-understanding]

### Public, unlisted, private
- "You can only upload public videos (not private or unlisted videos)." [video-understanding]
- Consequence: the "Articulate" playlist itself can stay private (it is read through the Data API),
  but any unlisted or private video in it needs another route or gets a metadata-only note.

### Free-tier limits
- "For the free tier, you can't upload more than 8 hours of YouTube video per day." Paid tier:
  "no limit based on video length." [video-understanding]
- "The YouTube URL feature is in preview and is available at no charge." "Pricing and rate limits
  are likely to change." [video-understanding]
- Normal request limits still apply on top (project facts: 3.6/3.8 Flash and 4 Hydrogen 5 RPM /
  20 RPD, 3.5 Flash Lite 15 RPM / 500 RPD). Limits are per project, not per key; RPD resets at
  midnight Pacific. [rate-limits] The docs page gives no free-tier TPM for these models (it points
  to AI Studio); a 22-minute video is about 130K input tokens, so check TPM there.

### Max length per request
- No per-video cap on YouTube URLs beyond the 8 h/day free-tier quota. The context window is the
  real cap: "Models with a 1M context window can process videos up to 3 hours long by default (at
  low media resolution), or up to 1 hour long at high media resolution." [video-understanding]

### Tokens per second, media resolution
- Video page: per second, frames cost 66 tokens at low resolution or 258 otherwise, audio 32 tokens
  per second, plus metadata; "approximately 100 tokens per second of video at default (low) media
  resolution", about 300 at high. [video-understanding]
- Media-resolution page (Gemini 3 and 4): video frames are 70 tokens at `low` and `medium` ("treated
  identically") and 280 at `high`; the default (`unspecified`) is 70. Audio there is 25 tokens/s.
  [media-resolution]
- The two pages disagree slightly (66 vs 70, 32 vs 25), but both say the default for video is
  already the low tier. So `--low-res` should change little or nothing on 3.x/4 models; the run
  measures it. Frame rate is the bigger lever: default 1 fps, settable in (0, 24] via
  `videoMetadata.fps` (generateContent) or `processing.fps` (Interactions). A transcript needs audio,
  not frames, so a low fps should cut cost without hurting the transcript. [video-understanding],
  [generate-content]
- Enum on `generationConfig.mediaResolution`: `MEDIA_RESOLUTION_LOW` (64 tokens),
  `MEDIA_RESOLUTION_MEDIUM` (256), `MEDIA_RESOLUTION_HIGH`. [generate-content]

### Audio, frames, captions
- Static mode defaults to `content_layers: ["frames", "audio"]`; "Audio is processed at 1Kbps
  (single channel)." So the model hears the audio and sees 1 fps frames. [video-understanding]
- There is a third layer, `transcript`: "Timestamped text transcript, billed as text tokens." The
  docs don't say where it comes from (YouTube captions or Google ASR) or whether YouTube URLs
  support it; all its examples use File API uploads. [video-understanding]
- Nowhere do the docs say the default mode reads YouTube captions. Treat transcripts as the
  model's own speech recognition until measured. The optional `--layers transcript` call tests this.
- Timestamps in prompts use `MM:SS` (e.g. `01:15`). [video-understanding]

### API shape and clipping
- The docs now show only the Interactions API (`POST /v1beta/interactions`, body
  `{"model", "input": [{"type": "video", "uri": "<youtube url>"}, {"type": "text", ...}]}`),
  with clipping as `processing: {"type": "static", "start_offset": "1200s", "end_offset": "1500s"}`.
  An end past the video is clamped; a start past it returns 400. [video-understanding]
- `generateContent` is still in the API reference with no deprecation notice seen; `FileData`
  (`fileUri`, `mimeType`) and `VideoMetadata` (`startOffset`, `endOffset`, `fps`, default 1.0,
  range (0, 24]) are still there. [generate-content] The prototype uses this route (matching the
  existing urllib + `x-goog-api-key` code) and can switch to Interactions with `--api interactions`.
- The YouTube section doesn't say clipping applies to YouTube URLs; the clipped call checks it,
  and whether returned timestamps are absolute or relative to the clip.

### Preview status
- Still preview: "The YouTube URL feature is in preview and is available at no charge."
  [video-understanding] Expect pricing, limits or shape to change; keep the fetch behind one function.

### Google's terms (free tier)
- Free-tier data use: "Used to improve our products": Yes on free, No on paid, for 3.6 Flash,
  3.8 Flash and 4 Hydrogen. [pricing]
- Unpaid Services: "human reviewers may read, annotate, and process your API input and output."
  "Do not submit sensitive, confidential, or personal information to the Unpaid Services."
  [terms] Public YouTube videos plus a transcription prompt are low risk here; the prompt should
  not include private vault content on the free tier.
- The Gemini API terms (effective March 23, 2026) contain no clause about YouTube or restricting
  video input; the closest rules are the content licence and "You may not use the Services to
  develop models that compete with the Services". [terms]

### Is it sanctioned on YouTube's side?
- YouTube's Terms forbid downloading content except "(a) as expressly authorized by the Service; or
  (b) with prior written permission from YouTube", and forbid accessing the Service "using any
  automated means (such as robots, botnets or scrapers)" except public search engines or with
  YouTube's prior written permission. [youtube-terms] yt-dlp and youtube-transcript-api do exactly that.
- With a YouTube URL in a Gemini request, Articulate sends only a string to
  `generativelanguage.googleapis.com`. Google, which owns YouTube, fetches the video on its own
  servers, as a documented feature of its own API. Articulate's Mac makes no request to YouTube, so
  there is no IP to block, and the access is Google's first-party feature rather than a scraper.
  Neither set of terms says this in so many words, but it is the documented, intended use.
- Remaining risk is product risk, not ban risk: preview status, the public-only rule, and the
  8 h/day cap.

## Design notes for the pipeline (tentative)

- Route: Data API for playlist + metadata (Channel, Title, URL, Duration, Published); Gemini with
  the URL for transcript and TL;DW; skip non-public videos (Data API `status.privacyStatus`) with a
  metadata-only note.
- Budget: 20 RPD on Flash is the binding limit, not 8 h of video. One call per video can return both
  the timestamped segments and the TL;DW if the schema includes both.
- Long videos: past about 3 h the context window is full; split with clip offsets.
- Timestamps feed `[m:ss](https://youtu.be/<id>?t=<sec>)` links, so drift matters more than WER.

## Results

Run 2026-11-19 by the main session from the home Mac (the Mac never contacts YouTube: Google fetches the
video). Video `QLgH5_mqX28` (21:49, public, Therapy in a Nutshell); reference = the creator's English captions
(4,124 words, 239 lines). `temperature 0`, JSON schema on, `generateContent`.

| Call | Model | Res / options | HTTP | Wall s | Video tokens | Output + thinking | WER | Median drift s | p90 abs drift s | Within 2 s |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | gemini-3.6-flash | default | 200 | 98.6 | 119,125 | 9,157 + 14,739 | 2.5 % | −0.24* | 1.4* | 97 %* |
| 2 | gemini-3.6-flash | low | 200 | 57.8 | 119,125 | 8,130 + 7,053 | 2.1 % | −0.16 | 3.45 | 87 % (max 8.3 s) |
| 3 | gemini-3.5-flash-lite | low | 503 ×2 | — | — | — | — | — | — | — |
| 4 | gemini-3.6-flash | low, clip 300–420 | 200 | 8.7 | 10,921 | 888 + 889 | 1.1 % | 0.08 | 0.5 | 100 % (max 0.6 s) |

\* Call 1 asked for `start_sec` as a number and Gemini wrote clock digits: 21:25 came back as `2125`
(no value had seconds ≥ 60). Re-read as M:SS, drift is as shown; raw it was 360 s median. `run.py` now asks for a
`"start": "M:SS"` string and converts in code; calls 2–4 used that.

Answers:
- **Low resolution changes nothing on 3.6 Flash:** identical 119,125 video tokens (~91 tokens/s). The default is
  already low; `fps` is the only lever left (not tested).
- **Clipped timestamps are absolute** (video time, not clip time), so they map straight onto `?t=` links.
- **Captions probably not read:** WER 1.1–2.5 % against the creator track, versus 0.8 % for YouTube's own auto
  captions and 1.5 % for local mlx-whisper (#46). Differences are dropped fillers and small rewordings, i.e. the
  model transcribes the audio. Good enough for timestamped notes; not a verbatim quote source without a check.
- **Shorter windows are more precise:** the 2-minute clip was exact; full 22-minute calls drift up to 4–8 s on a
  few segments. Clipping long videos into ≤20–30 min windows costs no extra tokens (same video seconds) but one
  request per window.
- **3.5 Flash Lite is untested:** two 503 "high demand" responses in a row. Its 500 RPD would matter (see below).

## Recommendation

- **Gemini with the URL replaces yt-dlp** for public videos: Google's documented feature, Google fetches the video,
  nothing on the Mac touches YouTube, so no IP or account to block. Drop yt-dlp and the mlx-whisper fallback
  (without a download there is nothing to transcribe locally).
- **Non-public videos** (unlisted, private, members-only, removed; Data API `status.privacyStatus`) and refusals:
  metadata-only stub note + weekly flag line. No fallback.
- **Budget is the catch:** each window is one request on a 20-RPD model (3.6 Flash), the same pool class Extract
  uses (#11). Videos should only spend what's left after class work (the 03:05 leftover pass), not the :15/:45
  pass, unless 3.5 Flash Lite (500 RPD) proves good enough: retest it once demand drops.
- **Ask for `M:SS` strings, never numbers**, and clip videos over ~30 min into windows.
- **Decision needed:** this sends watched videos to the Gemini free tier, which [What GitHub, YouTube and Medium
  feed into](https://github.com/amierlorenz/articulate/issues/44) ruled out (personal route only). Inputs here are
  a public URL plus a fixed prompt, but free-tier review means Google can see which videos Amier watches.

## Sources

- [video-understanding] https://aistudio.google.com/docs/video-understanding (redirect from https://ai.google.dev/gemini-api/docs/video-understanding)
- [media-resolution] https://aistudio.google.com/docs/media-resolution
- [generate-content] https://aistudio.google.com/api-reference/generate-content (redirect from https://ai.google.dev/api/generate-content)
- [models] https://aistudio.google.com/docs/models
- [rate-limits] https://aistudio.google.com/docs/rate-limits
- [pricing] https://aistudio.google.com/docs/pricing
- [terms] https://aistudio.google.com/docs/terms (redirect from https://ai.google.dev/gemini-api/terms)
- [youtube-terms] https://www.youtube.com/t/terms (effective December 15, 2023)
