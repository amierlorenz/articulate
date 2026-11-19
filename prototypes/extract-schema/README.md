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
