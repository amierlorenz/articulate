# Transcript-only concepts (prototype, articulate#39)

Throwaway harness for the transcript pass: one Gemini call per lecture over the whole Pocket transcript, split
into numbered ~3 min windows, returning `{ newConcepts, spokenClaims, spokenHints, contrastGroups }` with
citations `{ window, quote }`. Code checks every quote and records `startSec`/`endSec`.

Transcripts and outputs stay local in `~/Library/Application Support/articulate/prototypes/39-transcript-concepts/`
(`data/`, `out/`); nothing from a lecture is committed.

| File | What it does |
|---|---|
| `windows.py` | segments → windows: close at the first segment boundary past 180 s, cap 300 s, a tail under 120 s joins the previous window |
| `schema.py` / `schema.json` | output schema, same rules as Extract output schema (#22) |
| `prompt.md` | base prompt; `prompt-recall.md` adds the recall line (the one to keep) |
| `run.py` | one call; `PROMPT=prompt-recall` picks the variant, 4th arg picks the model (default `gemini-3.6-flash`) |
| `check.py` | quote check (exact after case/whitespace/punctuation canonicalisation, cited window ±1) + structural rules |

```sh
python3 run.py <data/recording.json> "<course>" [deck-concepts.json|-] [model]
python3 check.py <out/stem.json> <data/recording.json>
```

Gemini key from the login Keychain item `articulate-gemini`. Results and the decision are on the issue.
