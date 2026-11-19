# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2"]
# ///
"""PROTOTYPE — throwaway. Extract recall on long decks (articulate#36).

Gemini-only Extract (articulate#22) with two knobs:
  prompt  base   = prompt.md as #22 left it
          named  = prompt.md + one line: named laws, standards, organisations and frameworks are concepts
  split   0      = one call for the whole deck (#22)
          N      = above N pages, two page-range calls cut at the middle; contrast groups stay within a half,
                   concepts with the same title merge (extract.py's merge)

Usage: uv run long_deck.py <deck.pdf> <base|named> <split> [tag]
Writes out/<stem>.<prompt>-s<split>[-tag].json = {meta, output}, the shape check.py and recall.py read.
"""
import importlib.util, json, sys, time
from pathlib import Path

here = Path(__file__).parent
deck, variant, split = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
tag = f"-{sys.argv[4]}" if len(sys.argv) > 4 else ""

# extract.py runs its legs at import; borrow its helpers by importing with a dummy leg that exits early.
sys_argv = sys.argv
sys.argv = ["extract.py", "none", str(deck)]
spec = importlib.util.spec_from_file_location("ex", here / "extract.py")
ex = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(ex)
except SystemExit:
    pass
sys.argv = sys_argv

NAMED = """
- Named laws, acts, regulations, standards, organisations, agencies and frameworks that the document lists or describes are concepts (usually `fact`), one concept each, even when a slide gives only a line about each.
"""
base = (here / "prompt.md").read_text()
anchor = "- One concept per idea."
assert anchor in base
ex.PROMPT = base.replace(anchor, NAMED.strip() + "\n" + anchor) if variant == "named" else base
assert variant in ("base", "named")

n = len(ex.pdfium.PdfDocument(deck))
t0 = time.time()
if split and n > split:
    cut = (n + 1) // 2
    halves = [list(range(1, cut + 1)), list(range(cut + 1, n + 1))]
    parts = []
    for i, pages in enumerate(halves):
        note = (f"\n\nThis PDF holds pages {pages[0]}–{pages[-1]} of a {n}-page deck. Cite page numbers within "
                f"this PDF (1-based).")
        print(f"gemini pages {pages[0]}-{pages[-1]}", file=sys.stderr)
        g = ex.gemini(ex.sub_pdf(pages), note)
        parts.append((f"half{i + 1}", ex.remap(g, {k + 1: p for k, p in enumerate(pages)})))
    output, merge_stats = ex.merge(parts)
    plan = {"halves": [[h[0], h[-1]] for h in halves]}
else:
    output, merge_stats, plan = ex.gemini(deck.read_bytes()), {}, {"halves": [[1, n]]}

meta = {"leg": f"{variant}-s{split}", "deck": deck.name, "pages": n, "model": ex.GEMINI_MODEL,
        "seconds": round(time.time() - t0, 1), "calls": ex.calls, "plan": plan, "merge": merge_stats,
        "usage": ex.usage, "finishReason": None}
name = f"{deck.stem}.{variant}-s{split}{tag}"
(here / "out" / f"{name}.json").write_text(json.dumps({"meta": meta, "output": output}, indent=2, ensure_ascii=False))
print(json.dumps({k: meta[k] for k in ("leg", "seconds", "calls", "plan", "merge")}))
