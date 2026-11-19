# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2"]
# ///
"""PROTOTYPE — throwaway. Deterministic checks on one guide/exam output (articulate#32).

Usage: uv run check_exam.py <doc.pdf> out/<stem>.json
Quote check is text layer only (the pipeline adds OCR when a page has no text layer). Matching is
loose by design: NFKC, lowercase, letters and digits only, so line breaks and symbol fonts don't count.
"""
import json, re, sys, unicodedata
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium

from schema_exam import ITEM_KINDS, NOT_COUNTED, RECOGNITION

doc, result = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
out, meta = result["output"], result["meta"]
course = meta["course"]
pdf = pdfium.PdfDocument(doc)


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", t).lower())


pages = [norm(pdf[i].get_textpage().get_text_range()) for i in range(len(pdf))]
problems: list[str] = []
stats = Counter()
unverified: list[str] = []


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
    if t.count("$") % 2:
        problems.append(f"unbalanced $ in: {t[:60]!r}")


def located(page: int, quote: str, where: str, span: int = 1) -> str:
    """verified | elsewhere | unverified | empty. `span` lets a quote run onto the next page."""
    if not quote.strip():
        if page:
            problems.append(f"{where}: page {page} with empty quote")
        return "empty"
    if not 1 <= page <= len(pages):
        problems.append(f"{where}: page {page} out of range")
        return "unverified"
    q = norm(quote)
    here = "".join(pages[page - 1:page - 1 + 1 + span])
    if q in here:
        return "verified"
    if q in "".join(pages):
        return "elsewhere"
    # long items: count as verified if every 40-char chunk is on the page(s), i.e. only line-joins differ
    chunks = [q[i:i + 40] for i in range(0, len(q), 40)]
    if len(chunks) > 1 and sum(c in here for c in chunks) / len(chunks) >= 0.9:
        return "verified (≥90% of chunks)"
    # math the model rewrote as LaTeX can't match a symbol-font text layer: check the words outside $…$
    words = [norm(w) for w in re.sub(r"\$[^$]*\$", " ", quote).split()]
    words = [w for w in words if len(w) >= 3]
    if words and sum(w in here for w in words) / len(words) >= 0.9:
        return "verified (words outside math)"
    return "unverified"


d = out["document"]
print(f"document: kind={d['kind']}, examHint={d['examHint']}, unitHints={[u['name'] for u in d['unitHints']]}")
for h, where in [(d["examHint"], "examHint")] + [(u, f"unitHint {u['name']}") for u in d["unitHints"]]:
    allowed = course["exams"] if where == "examHint" else course["units"]
    if h["name"] and h["name"] not in allowed:
        problems.append(f"{where}: '{h['name']}' not in list (→ weekly list)")
    stats[f"{where.split()[0]} {located(h['page'], h['quote'], where)}"] += 1
if not d["examHint"]["name"]:
    stats["examHint empty (→ weekly list)"] += 1

items = out["items"]
nums = [i["number"] for i in items]
for n, c in Counter(nums).items():
    if c > 1:
        problems.append(f"duplicate number {n}")
for it in items:
    w = f"item {it['number']}"
    k = it["kind"]
    if k not in ITEM_KINDS:
        problems.append(f"{w}: kind {k}")
    stats[f"kind {k}"] += 1
    v = located(it["page"], it["quote"], w)
    stats[f"quote {v}"] += 1
    if v == "unverified":
        unverified.append(it["number"])
    if v == "empty":
        problems.append(f"{w}: empty quote")
    a = it["answer"]
    if a["quote"].strip():
        stats[f"answer {located(a['page'], a['quote'], w + '.answer')}"] += 1
    elif d["kind"] == "answerKey":
        problems.append(f"{w}: answer key item without answer")
    if re.match(r"^\d+[a-z]$", it["number"]):
        parent = re.match(r"^\d+", it["number"]).group()
        if parent not in nums:
            stats["part without context item (logged)"] += 1

counted = [i for i in items if i["kind"] not in NOT_COUNTED]
rec = sum(i["kind"] in RECOGNITION for i in counted)
u = meta.get("usage") or {}
print(f"{meta['model']} on {meta['doc']} ({len(pages)} pages): {meta['seconds']} s, finishReason {meta['finishReason']}")
print(f"tokens: prompt {u.get('promptTokenCount')}, output {u.get('candidatesTokenCount')}, thinking {u.get('thoughtsTokenCount')}")
print(f"items {len(items)} ({len(counted)} counted toward the mix, {rec} recognition)")
for k, v in sorted(stats.items()):
    print(f"  {k}: {v}")
share = len(unverified) / max(len(items), 1)
print(f"unverified share {share:.0%}{' (→ weekly list)' if share > 0.3 else ''}: {unverified}")
print(f"problems: {len(problems)}")
for p in problems:
    print("  -", p)
