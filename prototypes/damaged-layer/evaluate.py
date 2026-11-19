# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2"]
# ///
"""PROTOTYPE — throwaway (articulate#40). Score damaged-layer rules against hand labels.

Usage: uv run evaluate.py   (after signals.py has written out/<stem>.signals.json for every stem below)

Labels were set by looking at every flagged or VL-deficit page next to its layer (see README):
  silent  the layer keeps the line but silently lacks or alters something #35's canonical form keeps,
          so a quote copied from the layer passes while differing from the page
  lowrisk a symbol drawn as a path inside a PowerPoint equation whose layer is already scrambled
          (a quote can't line up with it, so it fails to OCR/VL anyway)
Everything else is clean for this question (whole formulas as pictures have no layer text at all, and
Symbol-font PUA letters are mapped by #35).
"""
import json, time
from pathlib import Path

import pypdfium2 as pdfium

from signals import DATA, OUT, page_signals

SILENT = {("chapter2", 7), ("chapter2", 8), ("mat305-ch3", 6), ("phy203-test4", 4), ("test1-practice", 2)}
LOWRISK = {("phy203-ch09", 40)}
STEMS = ["chapter2", "mat305-ch3", "mat305-t2-review", "mat305-t3-review", "mat305-t3-solutions",
         "test1-practice", "phy203-test4", "phy203-ch09", "csc382-whitman-ch03"]

RULES = {
    "glyph_img": lambda r: r["glyph_img"] > 0,
    "eq_img": lambda r: r["eq_img"] > 0,
    "mathfont_odd": lambda r: r["mathfont_odd"] > 0,
    "gap_gen": lambda r: r["gap_gen"] > 0,
    "maperr": lambda r: r["maperr"] > 0,
    "math_gap": lambda r: bool(r["math_gap"]),
    "ocr_line": lambda r: bool(r["ocr_line"]),
    "ocr_ins": lambda r: bool(r["ocr_ins"]),
    "ocr_ops (page)": lambda r: bool(r["ocr_ops"]),
    "PDF: glyph|eq|mathfont": lambda r: r["glyph_img"] + r["eq_img"] + r["mathfont_odd"] > 0,
    "PDF + ocr_ins": lambda r: r["glyph_img"] + r["eq_img"] + r["mathfont_odd"] > 0 or bool(r["ocr_ins"]),
}

rows = {s: json.loads((OUT / f"{s}.signals.json").read_text()) for s in STEMS}
n_pages = sum(len(v) for v in rows.values())
print(f"{len(STEMS)} files, {n_pages} pages, {len(SILENT)} silent-damage pages, {len(LOWRISK)} low-risk\n")
print(f"{'rule':26} {'recall':>7} {'lowrisk':>7} {'flagged':>7} {'false':>5}  flagged pages by file")
for name, rule in RULES.items():
    flagged = {(s, r["page"]) for s, rs in rows.items() for r in rs if rule(r)}
    tp = len(flagged & SILENT)
    false = flagged - SILENT - LOWRISK
    by = {}
    for s, p in sorted(flagged):
        by.setdefault(s, []).append(p)
    print(f"{name:26} {tp}/{len(SILENT):<5} {len(flagged & LOWRISK):>7} {len(flagged):>7} {len(false):>5}  "
          + "; ".join(f"{s} {ps}" for s, ps in by.items()))

print("\nPDF-signal time (no OCR, no model):")
for s in STEMS:
    pdf = pdfium.PdfDocument(DATA / "decks" / f"{s}.pdf")
    t0 = time.time()
    for i in range(len(pdf)):
        page_signals(pdf[i])
    dt = time.time() - t0
    print(f"  {s:22} {len(pdf):>3} pages  {dt:5.2f} s  ({1000 * dt / len(pdf):.0f} ms/page)")
