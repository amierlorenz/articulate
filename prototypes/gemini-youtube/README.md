# Prototype: Gemini reading a YouTube URL

Throwaway test for the research note `docs/research/gemini-youtube-urls.md`. Can Gemini transcribe a
public YouTube video from its URL alone, so the pipeline never downloads anything from YouTube?

- `run.py` sends https://www.youtube.com/watch?v=QLgH5_mqX28 (21:49, public) to Gemini as
  `fileData.fileUri` on `generateContent` and asks for a verbatim transcript as
  `{"segments": [{"start_sec", "text"}]}`. It saves the request, the raw response, `usageMetadata`,
  wall time and the parsed segments to `out/<model>-<res>[...].json`. Stdlib only, plain REST,
  key from `GEMINI_API_KEY` (never printed or saved).
- `compare.py` scores an `out/*.json` against the creator's captions
  (`/Users/amierld/.claude/jobs/2abab32b/tmp/ref/QLgH5_mqX28.en.json3`): word error rate, and
  timestamp drift of each segment's first word. `python3 compare.py --self-test` checks it against
  the reference itself (WER 0, drift 0; verified).

## Commands for the main session

Run from this folder with `GEMINI_API_KEY` exported. Space the calls about a minute apart: each
full-length call is roughly 130K input tokens (about 100 tokens per second of video), which may hit
the free-tier tokens-per-minute limit if two run back to back.

```sh
cd prototypes/gemini-youtube

# 1. 3.6 Flash, default media resolution
python3 run.py gemini-3.6-flash
python3 compare.py out/gemini-3.6-flash-default.json

# 2. 3.6 Flash, low media resolution
python3 run.py gemini-3.6-flash --low-res
python3 compare.py out/gemini-3.6-flash-low.json

# 3. 3.5 Flash Lite, low media resolution
python3 run.py gemini-3.5-flash-lite --low-res
python3 compare.py out/gemini-3.5-flash-lite-low.json

# 4. 3.6 Flash, low res, clipped to 5:00-7:00 (are timestamps absolute or relative to the clip?)
python3 run.py gemini-3.6-flash --low-res --clip 300 420
python3 compare.py out/gemini-3.6-flash-low-clip300-420.json --clip 300 420
```

Budget: 3 calls on 3.6 Flash (of 20 RPD), 1 on 3.5 Flash Lite (of 500 RPD), about 67 minutes of the
free tier's 8 hours of YouTube video per day.

### If something fails

- `400` naming `responseJsonSchema`: rerun the same command with `--no-schema` (the prompt still asks for JSON).
- `finishReason: MAX_TOKENS`: rerun with `--max-output-tokens 65536`.
- `429`: wait a minute (RPM/TPM) or until midnight Pacific (RPD).

### Optional extra calls

```sh
# Fewer frames: speech needs audio, not pictures. Does usage drop with no loss in WER?
python3 run.py gemini-3.5-flash-lite --low-res --fps 0.2

# Interactions API with only the "transcript" content layer (docs don't say if YouTube URLs
# support it or where the transcript comes from; this shows whether it uses YouTube's captions).
python3 run.py gemini-3.5-flash-lite --api interactions --layers transcript
```

The output name gains `-fps0.2`, `-noschema` or `-interactions-transcript` to match the options.
