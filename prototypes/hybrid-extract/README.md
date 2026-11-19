# Hybrid Extract: OCR + local text model (prototype, articulate#26)

Throwaway harness comparing Gemini-only Extract (articulate#22) with a hybrid (local model for text pages,
Gemini for formula/image pages), an all-local leg, and a local vision-language (VL) side test.
Decks and outputs stay local: `decks/` (course PDFs) and `out/` are gitignored.

| File | What it does |
|---|---|
| `survey.py` | text-layer stats for candidate decks |
| `keynote-export.applescript` | PPTX → PDF via Keynote (articulate#21 fixes) |
| `prep.py` | per page: text layer, Vision OCR (`ocrmac`, accurate, 2×), routing signals → `out/<stem>.pages.json` |
| `route.py` | routing rules (`default`, `ocr`, `all`) |
| `extract.py` | legs `gemini`, `hybrid`, `local`, `hybrid-vl` → `out/<stem>.<leg>.json` |
| `vl_transcribe.py` | local VL transcription of slides (math as LaTeX) → `out/<stem>.vl.json` |
| `check.py` | #22 checks + citation pass rate: text layer → OCR → VL transcript |
| `ollama_format_test.py` | shows Ollama dropping the grammar when a schema has `pattern` |
| `long_deck.py` | articulate#36: Gemini Extract with a prompt variant (`base`/`named`) and a page-range split above N pages → `out/<stem>.<variant>-s<N>.json`; `GEMINI_MODEL` overrides 3.8 Flash |
| `recall.py`, `gold/` | articulate#36: concept recall against a hand list of what each deck teaches |
| `schema.py`, `prompt.md`, `run_gemini.py` | copied from `prototype/22-extract-schema` |

```sh
uv run prep.py decks/<deck>.pdf
python3 route.py out/<deck>.pages.json [default|ocr]
uv run extract.py gemini decks/<deck>.pdf
NUM_PREDICT=4096 CHUNK=4 uv run extract.py local decks/<deck>.pdf      # LOCAL_MODEL=qwen3:8b default
uv run vl_transcribe.py decks/<deck>.pdf all qwen3-vl:8b-instruct
python3 check.py out/<deck>.<leg>.json [--quiet]
uv run long_deck.py decks/<deck>.pdf named 40      # articulate#36: prompt line + split above 40 pages
python3 recall.py out/<deck>.named-s40.json
```

Needs Ollama with `qwen3:8b` and `qwen3-vl:8b-instruct`, and the Gemini key in the login Keychain item
`articulate-gemini`. Results and the decision are on the issue.
