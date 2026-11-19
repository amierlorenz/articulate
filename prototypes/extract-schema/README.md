# Extract output schema (prototype, articulate#22)

Throwaway harness for the one validation call the ticket asks for: the schema decided in the grilling session, sent to Gemini 3.8 Flash with one real deck.

- `schema.py`: builds the `responseJsonSchema` (`python3 schema.py > schema.json` refreshes the reference copy)
- `prompt.md`: the Extract instructions
- `run.py`: one call, retries 503/429/500 on the same model, writes `out/` (gitignored)
- `check.py`: deterministic checks (control chars, stray `\\`, unbalanced `$`, citation/gap-fill rules, method fields only on methods, confidence notes, group keys) and quote verification against the PDF text layer (no OCR here)

```sh
GEMINI_API_KEY=… python3 run.py ~/path/to/deck.pdf
uv run check.py ~/path/to/deck.pdf out/<deck-stem>.json
```

## Study guide and past exam output (articulate#32)

Same harness, second schema: one call per guide / exam / practice test / answer key returns `{ document, items }`.

- `schema_exam.py`: `document { kind, examHint, unitHints }` + `items [{ number, kind, page, quote, answer }]`
- `prompt_exam.md`: copy-and-label instructions (printed text only, one item per lettered part)
- `courses/<key>.json`: the exam and unit lists the prompt gets (stands in for `courses.toml` + Canvas modules)
- `run_exam.py`, `check_exam.py`: like `run.py` / `check.py` (quotes on the text layer, hints against the lists, mix counts, >30% unverified → weekly list)

```sh
GEMINI_API_KEY=$(security find-generic-password -s articulate-gemini -w) python3 run_exam.py doc.pdf courses/phy-203.json
uv run check_exam.py doc.pdf out/<stem>.json
```
