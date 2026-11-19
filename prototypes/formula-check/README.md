# Formula check for guide and exam quotes (prototype, articulate#37)

Throwaway harness: does #35's citation rule (exact after canonicalising, operators kept, boundary near math,
text layer → OCR → VL) hold on study guide / exam items from #32, in place of the "words outside math" check?

| File | What it does |
|---|---|
| `run_all.sh <model>` | #32's `run_exam.py` on the three local docs → `out/<stem>.<model>.json` |
| `exam_match.py` | labelled set from the items' own question and answer quotes (true, altered formula, other page) and the rules compared |
| `summary.py` | per file: current check vs. the chosen rule, and the pages a night would send to VL |

Uses `../extract-schema` (`run_exam.py`, `courses/`) and `../hybrid-extract` (`prep.py`, `vl_transcribe.py`,
`vl_match.py`). Documents (`docs/`) and outputs (`out/`, `../hybrid-extract/out/`) stay local.

```sh
zsh run_all.sh gemini-3.6-flash
(cd ../hybrid-extract && uv run prep.py ../formula-check/docs/<doc>.pdf \
  && uv run vl_transcribe.py ../formula-check/docs/<doc>.pdf all qwen3-vl:8b-instruct)
python3 exam_match.py <stem>.<model> [...] ["--show=R7+VL label+joined"]
python3 summary.py <stem>.<model> [...]
```

Chosen rule: `R7+VL label+joined`. Results and the decision are on the issue.
