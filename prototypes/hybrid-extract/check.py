"""PROTOTYPE — throwaway. Deterministic checks + citation pass rate on one Extract output (articulate#26).

Adapted from articulate#22's check.py; the quote check now uses the pipeline's order (text layer, then
Vision OCR of the cited page, MieracleStudy#76/#90) from out/<stem>.pages.json.

Usage: python3 check.py out/<stem>.<leg>.json [--quiet]
"""
import json, re, sys, unicodedata
from collections import Counter
from pathlib import Path

path = Path(sys.argv[1])
quiet = "--quiet" in sys.argv
r7 = "--rule=r7" in sys.argv  # articulate#35: one rule (vl_match R7) for text layer, OCR and VL
if r7:
    from vl_match import bounded
    R7 = bounded("near")
result = json.loads(path.read_text())
out, meta = result["output"], result["meta"]
stem = path.name.split(".")[0]
prep = json.loads((path.parent / f"{stem}.pages.json").read_text())["pages"]


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", t).lower())


layer = [norm(p["text"]) for p in prep]
ocr = [norm(" ".join(l["text"] for l in p["ocr"])) for p in prep]


def latex_plain(t: str) -> str:
    """Rough LaTeX -> text so a VL transcript can match a quote written either way."""
    t = re.sub(r"\\(?:text|textbf|mathrm|operatorname)\{([^}]*)\}", r"\1", t)
    rep = {"\\leq": "≤", "\\le": "≤", "\\geq": "≥", "\\ge": "≥", "\\infty": "∞", "\\sum": "∑", "\\int": "∫",
           "\\in": "∈", "\\neq": "≠", "\\pm": "±", "\\cdot": "·", "\\quad": " "}
    for k in sorted(rep, key=len, reverse=True):
        t = t.replace(k, rep[k])
    t = re.sub(r"\\[a-zA-Z]+", " ", t)  # remaining commands (\cap, \phi, \frac, \cdots…): norm() drops symbols anyway
    return re.sub(r"[\\{}$_^]", "", t)


vl_path = path.parent / f"{stem}.vl.json"
vl_raw = json.loads(vl_path.read_text()) if vl_path.exists() else {}
vl = {int(k): norm(latex_plain(v["text"])) for k, v in vl_raw.items() if v.get("text")}
vl_is_input = (meta.get("leg") == "hybrid-vl")  # quotes came from the transcript: VL match is not independent
problems: list[str] = []
stats = Counter()


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            if not k.startswith("_"):
                yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


for t in strings(out):
    if re.search(r"[\t\x08\x0c]", t):
        problems.append(f"control char in: {t[:60]!r}")
    if re.search(r"\\\\(?=[)_$]|$)", t):
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
    for ct in cites:
        stats["citations"] += 1
        p, q = ct["page"], norm(ct["quote"])
        if not 1 <= p <= len(prep):
            stats["cite: bad page"] += 1
            continue
        if r7:
            pg = prep[p - 1]
            vt = vl_raw.get(str(p), {}).get("text")
            if R7(ct["quote"], pg["text"]):
                stats["cite: text layer"] += 1
            elif R7(ct["quote"], "\n".join(l["text"] for l in pg["ocr"])):
                stats["cite: OCR"] += 1
            elif vt and R7(ct["quote"], vt):
                stats["cite: VL transcript"] += 1
            else:
                stats["cite: unverified"] += 1
                if not quiet:
                    print(f"    unverified p{p} [{where}]: {ct['quote'][:90]!r}")
            continue
        if q and q in layer[p - 1]:
            stats["cite: text layer"] += 1
        elif q and q in ocr[p - 1]:
            stats["cite: OCR"] += 1
        elif q and (qv := norm(latex_plain(ct["quote"]))) and qv in vl.get(p, ""):
            stats["cite: VL transcript"] += 1
        elif q and any(q in a or q in b for a, b in zip(layer, ocr)):
            stats["cite: other page"] += 1
        else:
            stats["cite: unverified"] += 1
            if not quiet:
                print(f"    unverified p{p} [{where}]: {ct['quote'][:90]!r}")


keys = [c["key"] for c in out["concepts"]]
for k, n in Counter(keys).items():
    if n > 1:
        problems.append(f"duplicate key {k}")
for c in out["concepts"]:
    stats[f"kind {c['kind']}"] += 1
    w = c["key"]
    check_claim(c["explanation"], f"{w}.explanation")
    for f in ("useWhen", "conditions", "workedExamples"):
        for i, cl in enumerate(c[f]):
            check_claim(cl, f"{w}.{f}[{i}]")
    if c["kind"] != "method" and (c["aliases"] or c["useWhen"] or c["conditions"] or c["workedExamples"]):
        problems.append(f"{w}: method fields on a {c['kind']}")
    if c["confidence"] != "high" and not c["confidenceNote"].strip():
        problems.append(f"{w}: {c['confidence']} confidence without a note")
for g in out["contrastGroups"]:
    stats["groups"] += 1
    for k in g["memberKeys"]:
        if k not in keys:
            problems.append(f"group {g['name']}: unknown member {k}")
    for ct in g["contrasts"]:
        check_claim(ct["decidingFeature"], f"group {g['name']}")

n = stats["citations"]
ok = stats["cite: text layer"] + stats["cite: OCR"]
row = {"file": path.name, "concepts": len(keys), "groups": stats["groups"], "claims": stats["claims"],
       "gapFill": stats["gapFill claims"], "citations": n, "verified": ok,
       "pass_rate": round(ok / n, 3) if n else None,
       "vl_verified": stats["cite: VL transcript"],
       "pass_rate_with_vl": (round((ok + stats["cite: VL transcript"]) / n, 3) if n and not vl_is_input else None),
       "other_page": stats["cite: other page"],
       "bad_page": stats["cite: bad page"], "problems": len(problems),
       "seconds": meta.get("seconds"), "gemini_calls": (meta.get("calls") or {}).get("gemini")}
if not quiet:
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    for p in problems:
        print("  -", p)
print(json.dumps(row))
