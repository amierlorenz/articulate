"""PROTOTYPE — throwaway (articulate#37). Per file: W vs. the chosen rule, and the pages a nightly run sends to VL.

Usage: python3 summary.py <stem>.<model> [...]
"""
import json, sys
from pathlib import Path

from exam_match import PREP, build, sources

CHOSEN = "R7+VL label+joined"
for t in sys.argv[1:]:
    items, rules = build(t)
    stem = t.split(".")[0]
    text, _, _ = sources(stem)
    row = {}
    for name in ("W (#34)", CHOSEN):
        r = rules[name]
        tp = [bool(r(q, p, pt)) for tr, k, q, p, w, pt in items if tr]
        neg = [bool(r(q, p, pt)) for tr, k, q, p, w, pt in items if not tr]
        alt = [bool(r(q, p, pt)) for tr, k, q, p, w, pt in items if k.startswith("altered")]
        row[name] = f"true {sum(tp)}/{len(tp)}, altered passed {sum(alt)}/{len(alt)}, all negatives passed {sum(neg)}/{len(neg)}"
    r = rules[CHOSEN]
    vl_pages = sorted({p for tr, k, q, p, w, pt in items if tr and r(q, p, pt) in ("vl", None)})
    vlj = json.loads((PREP / f"{stem}.vl.json").read_text())
    secs = round(sum(vlj[str(p)]["seconds"] for p in vl_pages))
    print(f"{t} ({len(text)} pages)")
    for name, s in row.items():
        print(f"  {name:20} {s}")
    print(f"  VL needed on pages {vl_pages} ({secs} s)")
