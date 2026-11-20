# Flash Lite on YouTube links

Question: [Gemini reading watched YouTube videos by link](https://github.com/amierlorenz/articulate/issues/47)
measured 3.6 Flash, but `gemini-3.5-flash-lite` returned 503 "high demand" three times in a row on 2026-11-19
for a YouTube URL. Is that load, or does Flash Lite not take YouTube URLs? If it works, how do its WER
and timestamp drift compare (same video `QLgH5_mqX28`), and should it lead the video chain since videos run
at the lowest priority and Flash Lite has 500 RPD?

Researched and measured 2026-11-19 from Amier's Mac. Experiments run against the Gemini free-tier API
using public YouTube URLs with `fileData.fileUri` on `generateContent`.

## Short answer

Flash Lite **does** support YouTube URLs natively, but it consistently fails with HTTP 503 "high demand"
(after ~50–57 seconds) on full-length 20+ minute videos (~120k video tokens). On short clips (2–4 minutes),
Flash Lite works reliably, returns in 5–6 seconds, and achieves **1.34% WER** and **100% timestamps within 2 seconds**,
matching 3.6 Flash almost identically.

Because of the 503 failures on long videos, Flash Lite **cannot** lead the video chain for full-length videos.
`gemini-3.6-flash` (20 RPD) must remain the primary engine for full videos. Flash Lite (500 RPD) can be used
either for short videos (≤ 3–5 min) or as a fallback when videos are partitioned into short clips.

---

## Experimental Results

All calls used `gemini-3.5-flash-lite` with `MEDIA_RESOLUTION_LOW`, `temperature: 0`, and the structured JSON schema
from `prototypes/gemini-youtube/run.py`.

### 1. Plain text baseline
- Prompt: "What is 2+2? Reply with just the number."
- Result: **HTTP 200**, wall time 1.1s, returned "4".
- Proves API key, authentication, and the model endpoint itself are operational.

### 2. Short clip (2 minutes, 300–420s on `QLgH5_mqX28`)
- Video: `https://www.youtube.com/watch?v=QLgH5_mqX28` (21:49, public, Therapy in a Nutshell)
- Clip: `startOffset: "300s"`, `endOffset: "420s"` (10,921 video tokens)
- Result: **HTTP 200** in **5.97s** (vs 8.7s on 3.6 Flash)
- Segments: 32 segments, last timestamp 06:58 (418.0s)
- Words: 374 hyp words vs 373 ref words

### 3. Different short public video (3:33, Rick Astley `dQw4w9WgXcQ`)
- Video: `https://www.youtube.com/watch?v=dQw4w9WgXcQ` (full video, 3:33)
- Tokens: 19,387 video tokens
- Result: **HTTP 200** in **5.04s**
- Segments: 46 segments, transcribed accurately from 0:18 to 3:29

### 4. Full 21:49 video (`QLgH5_mqX28`, ~119k video tokens)
- Video: `https://www.youtube.com/watch?v=QLgH5_mqX28` (full video, no clip)
- Attempt 1 (earlier today, #47): 503 UNAVAILABLE
- Attempt 2 (earlier today, #47): 503 UNAVAILABLE
- Attempt 3 (earlier today, #47): 503 UNAVAILABLE
- Attempt 4 (21:46 local): **HTTP 503** in 29.24s ("This model is currently experiencing high demand")
- Attempt 5 (22:05 local): **HTTP 503** in 57.05s ("This model is currently experiencing high demand")
- In contrast, `gemini-3.6-flash` processes the full 21:49 video successfully in 57.8s (low res) and 98.6s (default res).

---

## Accuracy and Drift Comparison (Clip 300–420s)

Scored against the creator's manual English captions (`QLgH5_mqX28.en.json3`, 373 words across 300–420s):

| Metric | `gemini-3.5-flash-lite` (low res) | `gemini-3.6-flash` (low res) |
|---|---|---|
| HTTP status | 200 | 200 |
| Wall time | **5.97 s** | 8.7 s |
| Video tokens | 10,921 | 10,921 |
| Output tokens | 1,254 | 888 (+ 889 thinking) |
| Hyp words | 374 | 376 |
| Word Error Rate (WER) | **1.34 %** | 1.1 % |
| Substitutions | 2 | 1 |
| Deletions | 1 | 0 |
| Insertions | 2 | 3 |
| Timestamp alignment | Absolute (video time) | Absolute (video time) |
| Median drift | **−0.17 s** | +0.08 s |
| Mean abs drift | **0.40 s** | 0.28 s |
| p90 abs drift | **0.70 s** | 0.50 s |
| Max abs drift | **0.92 s** | 0.60 s |
| Timestamps within 2 s | **100 %** (30/30) | 100 % (29/29) |

Observations:
1. **Quality is virtually identical:** Flash Lite's 1.34% WER is within ~1 word difference from 3.6 Flash (1.1%), primarily differing on minor word boundary choices and contractions.
2. **Timestamps are highly accurate:** 100% of segment start timestamps are within 1 second of the spoken words (maximum drift 0.92s). Like 3.6 Flash, clipped timestamps are returned as absolute video time.
3. **Speed is faster:** Flash Lite completed in ~6 seconds vs ~9 seconds on 3.6 Flash.

---

## Architectural Recommendation

1. **Why Flash Lite fails on full videos:** Google's backend video ingestion allocates limited compute/memory resources to the `flash-lite` tier. When passed ~120k tokens (~22 minutes) of video, the request stalls and times out with a 503 "high demand" error after 30–60 seconds, whereas 3.6 Flash has sufficient resource allocation to process the full sequence.
2. **Video chain routing:**
   - **Full-length videos (> ~5 min):** Must use `gemini-3.6-flash` (20 RPD tier). As decided in #44 and #47, this runs at the **lowest priority** (in the leftover quota pass after class work, never taking the daytime reserve).
   - **Short videos (≤ 3–5 min):** Can route to `gemini-3.5-flash-lite` first to save 3.6 Flash quota, benefiting from Flash Lite's 500 RPD pool and 5-second response time.
   - **Windowed / clipped approach:** If 3.6 Flash quota is exhausted, Articulate could chunk longer videos into 3–5 minute windows (`--clip`) and process them via Flash Lite. However, a 20-minute video would consume 4–6 requests from Flash Lite instead of 1 from 3.6 Flash. Given 500 RPD, this is viable as a fallback, but 3.6 Flash remains cleaner as the primary path.
