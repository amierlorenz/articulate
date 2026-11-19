"""PROTOTYPE — throwaway (articulate#40). Replay #35's exact rule (canon with operators kept) on
Ch. 2's Gemini citations, with flagged pages' text layers skipped. Shows which citations change verdict.

Usage: python3 replay.py   (needs out/chapter2.{pages,vl,signals}.json and #26's chapter2.gemini.json)
"""
import json
from pathlib import Path

from vl_match35 import canon, latex_plain, quote_dialect

DATA = Path.home() / "Library/Application Support/articulate/prototypes"
OUT = DATA / "40-damaged-layer/out"
gem = json.loads((DATA / "26-hybrid-extract/out/chapter2.gemini.json").read_text())
pages = {p["page"]: p for p in json.loads((OUT / "chapter2.pages.json").read_text())["pages"]}
vl = json.loads((OUT / "chapter2.vl.json").read_text())
sig = {r["page"]: r for r in json.loads((OUT / "chapter2.signals.json").read_text())}
untrusted = {p for p, r in sig.items() if r["glyph_img"] + r["eq_img"] + r["mathfont_odd"] > 0}


def walk(x):
    if isinstance(x, dict):
        if "page" in x and "quote" in x:
            yield x
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)


def hit(q, src):
    n = canon(quote_dialect(latex_plain(q)), True)
    return bool(n) and n in canon(latex_plain(src), True)


print("untrusted pages:", sorted(untrusted))
for c in walk(gem):
    p, q = c["page"], c["quote"]
    src = {"layer": pages[p]["text"], "ocr": "\n".join(l["text"] for l in pages[p]["ocr"]),
           "vl": vl.get(str(p), {}).get("text", "")}
    before = next((k for k in ("layer", "ocr", "vl") if hit(q, src[k])), "FAIL")
    after = next((k for k in ("ocr", "vl") if hit(q, src[k])), "FAIL") if p in untrusted else before
    if before != after:
        print(f"p{p}: {before} -> {after}  {q[:90]!r}")
