"""PROTOTYPE — throwaway. Formula check for guide and exam quotes (articulate#37).

Usage: python3 exam_match.py <stem>.<model> [...] [--show=<rule>] [--list]
Needs out/<stem>.<model>.json (run_all.sh) and ../hybrid-extract/out/<stem>.{pages,vl}.json (prep.py,
vl_transcribe.py). Labelled set, built like vl_match.py (articulate#35) from the items' own quotes:
  positives  every item question quote and printed-answer quote (eyeballed, see --list)
  other page the quote against the next/previous page only
  altered    one-edit versions of quotes with math (digit, operator, subscript, dropped term),
             skipped when the edit is still on the page's text layer
Rules compared (each runs text layer -> OCR [-> VL], a quote may run onto the next page like check_exam.py):
  W    #32/#34 check: loose exact, ≥90% of 40-char chunks, else ≥90% of 3+-letter words outside math
  R7   #35 rule: exact after canonicalising, operators kept, boundary near math (text, OCR)
  R7+VL  the same with the VL transcript as the third source
  R7|W   R7(+VL) for quotes with math, W for prose-only quotes
"""
import json, re, sys, unicodedata
from collections import Counter
from pathlib import Path

here = Path(__file__).parent
sys.path.insert(0, str(here.parent / "hybrid-extract"))
from vl_match import MATH_EDGE, MATHY, bounded, canon, chunks_canon, mutations, pua_regex, q_canon  # noqa: E402

PREP = here.parent / "hybrid-extract" / "out"
R7 = bounded("near")


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", t).lower())


def w_rule(q: str, page: str) -> bool:
    """#32 check_exam.located(), widened per #34: words outside any math, LaTeX commands dropped."""
    n, here_ = norm(q), norm(page)
    if not n:
        return False
    if n in here_:
        return True
    chunks = [n[i:i + 40] for i in range(0, len(n), 40)]
    if len(chunks) > 1 and sum(c in here_ for c in chunks) / len(chunks) >= 0.9:
        return True
    words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", re.sub(r"\\[a-zA-Z]+", " ", q))]
    return bool(words) and sum(norm(w) in here_ for w in words) / len(words) >= 0.9


def sources(stem: str):
    pages = json.loads((PREP / f"{stem}.pages.json").read_text())["pages"]
    vlp = PREP / f"{stem}.vl.json"
    vl = {int(k): v["text"] for k, v in json.loads(vlp.read_text()).items() if v.get("text")} if vlp.exists() else {}
    text = {p["page"]: p["text"] for p in pages}
    ocr = {p["page"]: "\n".join(l["text"] for l in p["ocr"]) for p in pages}
    return text, ocr, vl


def span(d: dict, p) -> str:
    """p: a page (the quote may run onto p+1) or a tuple of exact pages (other-page negatives)."""
    return "\n".join(d.get(x, "") for x in (p if isinstance(p, tuple) else (p, p + 1)))


# a leading part/question label: "(a)", "a)", "3)", "3.", "Q. 5", "Q, 3", "Q.6." — its ")" or digit is not math
LABEL = re.compile(r"^\s*(?:\(?[a-z]\)|\(?\d{1,2}[a-z]?[).]|Q\s*[.,]?\s*\d+\s*[.:]?)\s*", re.I)


def r7_label(q: str, page: str) -> bool:
    """R7 (vl_match.bounded("near")) with one change: a leading part/question label is skipped when
    deciding whether the quote *starts* in math. The match is still the whole quote, label included.
    A quote that is only a label ("(c)") fails."""
    rest = LABEL.sub("", q, count=1)
    if not q_canon(rest, True):
        return False
    n = q_canon(q, True)
    vn, cuts = chunks_canon(page)
    qs = q.strip().rstrip(".,;: ")
    need_end = bool(MATH_EDGE.search(LABEL.sub('', qs, count=1)[-12:]))  # a short part: the label is no end either
    need_start = bool(MATH_EDGE.search(rest.strip()[:12])) and rest.strip() == qs  # label present: start is prose
    rx = pua_regex(n)
    pos = 0
    while (m := rx.search(vn, pos)):
        if (not need_start or m.start() in cuts) and (not need_end or m.end() in cuts):
            return True
        pos = m.start() + 1
    return False


def make_rules(text, ocr, vl):
    def via(rule, srcs):
        def f(q, p, partner=None):
            for name, d in srcs:
                if rule(q, span(d, p)):
                    return name
            return None
        return f

    def joined(base):
        """A quote that fails is retried joined to its question/answer partner on the same page
        (a printed line the model cut between question and answer)."""
        def f(q, p, partner=None):
            r = base(q, p)
            if r or not partner or partner[2] != p:
                return r
            role, pq, _ = partner
            return base(q + " " + pq if role == "ans" else pq + " " + q, p) and "joined"
        return f
    w = via(w_rule, [("text", text), ("ocr", ocr)])
    r7 = via(R7, [("text", text), ("ocr", ocr)])
    r7vl = via(R7, [("text", text), ("ocr", ocr), ("vl", vl)])
    r7l = via(r7_label, [("text", text), ("ocr", ocr), ("vl", vl)])
    r7lj = joined(r7l)
    return {"W (#34)": w, "R7 text+OCR": r7, "R7+VL": r7vl,
            "R7+VL math | W prose": lambda q, p, pt=None: r7vl(q, p) if MATHY.search(q) else w(q, p),
            "R7+VL label": r7l, "R7+VL label+joined": r7lj,
            "R7+VL label text+OCR": via(r7_label, [("text", text), ("ocr", ocr)])}


def quotes(out):
    for i, it in enumerate(out["items"]):
        a = it["answer"]
        has_a = bool(a["quote"].strip())
        yield f"#{i} {it['number']} q", it["page"], it["quote"], (("ans", a["quote"], a["page"]) if has_a else None)
        if has_a:
            yield f"#{i} {it['number']} ans", a["page"], a["quote"], ("q", it["quote"], it["page"])


def build(tag: str):
    stem = tag.split(".")[0]
    out = json.loads((here / "out" / f"{tag}.json").read_text())["output"]
    text, ocr, vl = sources(stem)
    layer = {p: canon(text.get(p, "") + " " + ocr.get(p, ""), True) for p in text}
    items = []  # (truth, kind, quote, page, where)
    for where, p, q, pt in quotes(out):
        if not q.strip():
            continue
        math = bool(MATHY.search(q))
        items.append((True, "positive (math)" if math else "positive (prose)", q, p, f"{tag} {where} p{p}", pt))
        for np_ in (p - 1, p + 2):  # a quote may run onto p+1, so the other pages are p-1 and p+2, alone
            if np_ in text and q_canon(q, True) not in layer.get(np_, ""):
                items.append((False, "other page", q, (np_,), f"{tag} {where} p{p}→p{np_}", None))
        if math:
            for kind, mq in mutations(q):
                if q_canon(mq, True) == q_canon(q, True) or q_canon(mq, True) in layer.get(p, "") + layer.get(p + 1, ""):
                    continue
                items.append((False, kind, mq, p, f"{tag} {where} p{p}", pt))
    return items, make_rules(text, ocr, vl)


def main():
    tags = [a for a in sys.argv[1:] if not a.startswith("--")]
    show = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--show=")]
    sets = [build(t) for t in tags]
    kinds = sorted({k for items, _ in sets for _, k, *_ in items}, key=lambda k: (not k.startswith("positive"), k))
    names = list(sets[0][1])
    tot = {(n, k): [0, 0] for n in names for k in kinds}
    via = {n: Counter() for n in names}
    wrong = {n: [] for n in names}
    for items, rules in sets:
        for truth, k, q, p, where, pt in items:
            for n in names:
                r = rules[n](q, p, pt)
                tot[(n, k)][0] += bool(r)
                tot[(n, k)][1] += 1
                if truth and r:
                    via[n][r] += 1
                if bool(r) != truth:
                    wrong[n].append((k, where, q))
    print("set:", ", ".join(tags))
    print("labelled:", ", ".join(f"{k} {tot[(names[0], k)][1]}" for k in kinds))
    print(f"{'rule':22}" + "".join(f"{k.replace('positive ', 'pos ').replace('altered: ', 'alt ')[:15]:>16}" for k in kinds)
          + "   true passes by source")
    for n in names:
        print(f"{n:22}" + "".join(f"{f'{tot[(n, k)][0]}/{tot[(n, k)][1]}':>16}" for k in kinds)
              + "   " + ", ".join(f"{s} {c}" for s, c in sorted(via[n].items())))
    for n in show:
        print(f"\n== {n}: wrong calls")
        for k, where, q in wrong[n]:
            print(f"  [{k}] {where}: {q[:120]!r}")
    if "--list" in sys.argv:
        for items, _ in sets:
            for truth, k, q, p, where, _ in items:
                if truth:
                    print(f"{where}: {q[:140]!r}")


if __name__ == "__main__":
    main()
