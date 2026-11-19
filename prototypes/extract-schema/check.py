# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2"]
# ///
"""PROTOTYPE — throwaway. Deterministic checks on one Extract output (articulate#22).

Usage: uv run check.py <deck.pdf> out/<deck-stem>.json
Quote check here is text layer only; the pipeline adds Vision OCR (MieracleStudy#76), so
"unverified" below overstates misses on image-only slides.
"""
import json, re, sys, unicodedata
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium

deck, result = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
out, meta = result["output"], result["meta"]
pdf = pdfium.PdfDocument(deck)


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower()
    return re.sub(r"[^a-z0-9]+", "", t)


pages = [norm(pdf[i].get_textpage().get_text_range()) for i in range(len(pdf))]
problems: list[str] = []
stats = Counter()


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


for t in strings(out):
    if re.search(r"[\t\x08\x0c]", t):
        problems.append(f"control char in: {t[:60]!r}")
    if re.search(r"\\\\(?=[)_$]|$)", t):  # qwen-style garbage: `\\)`, `\\_`, `\\$`, trailing `\\`
        problems.append(f"stray \\\\ in: {t[:60]!r}")
    if t.count("$") % 2:
        problems.append(f"unbalanced $ in: {t[:60]!r}")


def check_claim(c: dict, where: str):
    stats["claims"] += 1
    cites = c["citations"]
    if c["gapFill"]:
        stats["gapFill claims"] += 1
        if cites:
            problems.append(f"{where}: gapFill claim has citations")
        return
    if not cites:
        problems.append(f"{where}: cited claim with no citation")
    if len(cites) > 3:
        problems.append(f"{where}: {len(cites)} citations (cap 3, extras logged)")
    for ct in cites:
        stats["citations"] += 1
        p = ct["page"]
        if not 1 <= p <= len(pages):
            problems.append(f"{where}: page {p} out of range")
            continue
        q = norm(ct["quote"])
        if q and q in pages[p - 1]:
            stats["quotes verified (text layer)"] += 1
        elif q and any(q in pg for pg in pages):
            stats["quotes on another page (off-by-n)"] += 1
        else:
            stats["quotes unverified (text layer)"] += 1


keys = [c["key"] for c in out["concepts"]]
for k, n in Counter(keys).items():
    if n > 1:
        problems.append(f"duplicate key {k}")
for c in out["concepts"]:
    stats[f"kind {c['kind']}"] += 1
    stats[f"confidence {c['confidence']}"] += 1
    w = c["key"]
    check_claim(c["explanation"], f"{w}.explanation")
    for f in ("useWhen", "conditions", "workedExamples"):
        for i, cl in enumerate(c[f]):
            stats[f] += 1
            check_claim(cl, f"{w}.{f}[{i}]")
    if c["kind"] != "method" and (c["aliases"] or c["useWhen"] or c["conditions"] or c["workedExamples"]):
        problems.append(f"{w}: method fields on a {c['kind']}")
    if c["kind"] == "method" and not c["workedExamples"]:
        stats["methods without worked example (flag)"] += 1
    if c["confidence"] != "high" and not c["confidenceNote"].strip():
        problems.append(f"{w}: {c['confidence']} confidence without a note")
    stats["slideHints"] += len(c["slideHints"])

for g in out["contrastGroups"]:
    stats["groups"] += 1
    m = g["memberKeys"]
    if len(m) < 2:
        problems.append(f"group {g['name']}: fewer than 2 members")
    for k in m:
        if k not in keys:
            problems.append(f"group {g['name']}: unknown member {k}")
    covered = set()
    for ct in g["contrasts"]:
        stats["contrasts"] += 1
        if ct["a"] not in m or ct["b"] not in m:
            problems.append(f"group {g['name']}: contrast {ct['a']}/{ct['b']} not both members")
        covered.add(frozenset((ct["a"], ct["b"])))
        check_claim(ct["decidingFeature"], f"group {g['name']} {ct['a']}/{ct['b']}")
    pairs = {frozenset((a, b)) for i, a in enumerate(m) for b in m[i + 1:]}
    stats["member pairs without contrast (logged)"] += len(pairs - covered)

u = meta.get("usage") or {}
print(f"{meta['model']} on {meta['deck']} ({len(pages)} pages): {meta['seconds']} s, finishReason {meta['finishReason']}")
print(f"tokens: prompt {u.get('promptTokenCount')}, output {u.get('candidatesTokenCount')}, thinking {u.get('thoughtsTokenCount')}")
print(f"concepts {len(keys)}")
for k, v in sorted(stats.items()):
    print(f"  {k}: {v}")
print(f"problems: {len(problems)}")
for p in problems:
    print("  -", p)
