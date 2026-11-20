"""PROTOTYPE — throwaway. Checks for one transcript-pass output (articulate#39).

Usage: python3 check.py <out/stem.json> <recording.json> [--quiet]

Quote check (decided in grilling Q4): exact match after canonicalising case, whitespace and punctuation,
against Pocket's segment `text`, inside the cited window ±1. Code (not the model) records recordingId,
startSec and endSec from the segments the quote spans.
Also: structural rules (citation count, gapFill, method-only fields, keys, contrast members, hint keys).
"""
import json, re, sys, unicodedata

from windows import build, load


def canon(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).lower().replace("’", "'")
    t = re.sub(r"[^\w\s']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def locate(quote: str, wins: list[dict], w: int):
    """Return (window, startSec, endSec) where the canonical quote occurs in window w±1, else None."""
    q = canon(quote)
    if not q:
        return None
    for cand in (w, w - 1, w + 1):
        if not 1 <= cand <= len(wins):
            continue
        segs = wins[cand - 1]["segments"]
        # canonical text with a map from char offset to segment index
        buf, owner = "", []
        for i, s in enumerate(segs):
            c = canon(s["text"]) + " "
            buf += c
            owner += [i] * len(c)
        k = buf.find(q)
        if k >= 0:
            a, b = owner[k], owner[min(k + len(q) - 1, len(owner) - 1)]
            return cand, segs[a]["start"], segs[b]["end"]
    return None


def main():
    res = json.load(open(sys.argv[1]))
    rec = load(sys.argv[2])
    quiet = "--quiet" in sys.argv
    wins = build(rec["transcript"]["segments"])
    out, deck = res["output"], {c["key"] for c in res["deck"]}
    problems, quotes = [], []
    new_keys = [c["key"] for c in out["newConcepts"]]
    if len(set(new_keys)) != len(new_keys):
        problems.append("duplicate newConcept keys")
    if set(new_keys) & deck:
        problems.append("newConcept key collides with deck key")
    allk = set(new_keys) | deck

    def claim(c, where):
        n = len(c["citations"])
        if c["gapFill"] and n:
            problems.append(f"{where}: gapFill with citations")
        if not c["gapFill"] and not 1 <= n <= 3:
            problems.append(f"{where}: {n} citations")
        for ct in c["citations"]:
            hit = locate(ct["quote"], wins, ct["window"])
            quotes.append((where, ct["window"], ct["quote"], hit))

    for c in out["newConcepts"]:
        w = f"new:{c['key']}"
        claim(c["explanation"], w + ".explanation")
        for f in ("useWhen", "conditions", "workedExamples"):
            for i, cl in enumerate(c[f]):
                claim(cl, f"{w}.{f}[{i}]")
        if c["kind"] != "method" and (c["aliases"] or c["useWhen"] or c["conditions"]):
            problems.append(f"{w}: method fields on a {c['kind']}")
    for i, sc in enumerate(out["spokenClaims"]):
        if sc["conceptKey"] not in deck:
            problems.append(f"spokenClaims[{i}]: unknown deck key {sc['conceptKey']!r}")
        claim(sc["claim"], f"spoken[{i}]:{sc['conceptKey']}.{sc['field']}")
    for i, h in enumerate(out["spokenHints"]):
        if h["conceptKey"] and h["conceptKey"] not in allk:
            problems.append(f"hint[{i}]: unknown key {h['conceptKey']!r}")
        if not h["conceptKey"] and not h["scope"]:
            problems.append(f"hint[{i}]: neither conceptKey nor scope")
        hit = locate(h["quote"], wins, h["window"])
        quotes.append((f"hint[{i}]:{h['kind']}", h["window"], h["quote"], hit))
    for g in out["contrastGroups"]:
        if len(g["memberKeys"]) < 2 or not set(g["memberKeys"]) <= allk:
            problems.append(f"group {g['name']!r}: bad members {g['memberKeys']}")
        for ct in g["contrasts"]:
            claim(ct["decidingFeature"], f"group:{g['name']}:{ct['a']}|{ct['b']}")

    ok = [q for q in quotes if q[3]]
    shifted = [q for q in ok if q[3][0] != q[1]]
    print(f"concepts {len(out['newConcepts'])}, spokenClaims {len(out['spokenClaims'])}, "
          f"hints {len(out['spokenHints'])}, groups {len(out['contrastGroups'])}")
    print(f"quotes verified {len(ok)}/{len(quotes)} ({len(shifted)} found in a neighbouring window)")
    print(f"structural problems: {len(problems)}")
    for p in problems:
        print("  -", p)
    if not quiet:
        for where, w, q, hit in quotes:
            if not hit:
                print(f"  MISS {where} w{w}: {q!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
