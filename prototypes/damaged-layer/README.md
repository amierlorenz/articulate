# Spotting a damaged text layer (prototype, articulate#40)

Throwaway harness: which cheap signal tells that a PDF text layer silently lost a symbol (the ∪ in
MAT-305 Ch. 2's `P(A B)`), so a quote copied from it would pass #35's citation check?
Decks and outputs stay local in `~/Library/Application Support/articulate/prototypes/40-damaged-layer/`
(`decks/`, `out/`, `png/`), not in the repo.

| File | What it does |
|---|---|
| `prep.py` | text layer + Vision OCR per page (subset of #26's prep) → `out/<stem>.pages.json` |
| `vl_transcribe.py` | #35's VL transcriber, used here only to build the labelling shortlist |
| `signals.py` | per-page signals from the PDF (and cached OCR) → `out/<stem>.signals.json` |
| `evaluate.py` | scores each signal and rule against the hand labels; times the PDF signals |
| `replay.py` | #35's exact rule on Ch. 2's Gemini citations with flagged pages' layers skipped |
| `probe.py`, `gap.py` | char dump (unicode, font, map error, generated) and objects inside a char gap |
| `vl_match35.py` | #35's canonicaliser, copied from `prototype/35-vl-citation` |

```sh
uv run prep.py <stem> ...            # decks/<stem>.pdf
uv run signals.py <stem> ...
uv run evaluate.py
python3 replay.py
```

## Signals

- `glyph_img`: an image the size of a glyph inside a text line (Word pasted the symbol as a picture)
- `eq_img`: a wider, one-to-three-line-tall image inside or next to a text line (an inline equation picture)
- `mathfont_odd`: ASCII punctuation drawn in a math font (Word's Cambria Math subset: `x²` → `𝑥!`)
- `gap_gen`, `math_gap`, `maperr`, `pua`: generated spaces over a drawn object, `P(A B)` patterns,
  broken ToUnicode maps, private-use chars
- `ocr_ops`, `ocr_line`, `ocr_ins`: OCR cross-checks (page counts, line-aligned, char insertions)

Results and the decision are on the issue.
