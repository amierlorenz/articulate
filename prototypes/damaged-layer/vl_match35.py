"""PROTOTYPE — throwaway. Match rule for the VL transcript as a third citation source (articulate#35).

Usage: python3 vl_match.py <stem> [<stem> ...] [--show]
Needs out/<stem>.{gemini,pages,vl}.json. Builds a labelled set from the deck's own citations:
  positives  every Gemini citation whose page has a VL transcript (eyeballed: all true)
  other-page the same quote checked against each neighbouring page's transcript (p-1, p+1)
  altered    formula quotes with one edit: digit changed, operator flipped, subscript/variable swapped,
             term dropped (skipped when the edit happens to still be on the page's text layer)
and scores candidate rules: true-pass rate on positives, false-pass rate on each negative class.
"""
import json, re, sys, unicodedata
from difflib import SequenceMatcher
from pathlib import Path

OUT = Path(__file__).parent / "out"

# --- normalisation --------------------------------------------------------------------------------

# Symbol-font private-use code points (PowerPoint equations pasted as Symbol text): U+F0xx -> char.
_SYM_LETTERS = dict(zip("abgdezhqiklmnxprstufcywjvJ", "αβγδεζηθικλμνξπρστυφχψωϕϖϑ"))
_SYM_LETTERS.update(zip("ABGDEZHQIKLMNXPRSTUFCYW", "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ"))
SYMBOL_PUA = {0xF000 + ord(k): v for k, v in _SYM_LETTERS.items()}
SYMBOL_PUA.update({0xF0E5: "∑", 0xF0F2: "∫", 0xF0B4: "x", 0xF0B1: "±", 0xF0A3: "≤", 0xF0B3: "≥", 0xF0B9: "≠",
                   0xF0AE: "→", 0xF0D6: "√", 0xF0B6: "∂", 0xF0D7: "·", 0xF02D: "-", 0xF02B: "+", 0xF03D: "=",
                   0xF0BC: "…", 0xF0A5: "∞", 0xF0C8: "∪", 0xF0C7: "∩", 0xF0CE: "∈", 0xF0C6: "∅"})

GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "epsilon", "ζ": "zeta",
         "η": "eta", "θ": "theta", "ϑ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu",
         "ξ": "xi", "π": "pi", "ϖ": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "phi",
         "ϕ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega", "Γ": "gamma", "Δ": "delta", "Θ": "theta",
         "Λ": "lambda", "Ξ": "xi", "Π": "pi", "Σ": "sigma", "Φ": "phi", "Ψ": "psi", "Ω": "omega"}

# whole LaTeX commands only (the #26 version replaced prefixes: \cdot ate \cdots -> "·s")
LATEX = {"le": "≤", "leq": "≤", "leqslant": "≤", "ge": "≥", "geq": "≥", "geqslant": "≥", "neq": "≠", "ne": "≠",
         "cup": "∪", "cap": "∩", "bigcup": "∪", "bigcap": "∩", "sum": "∑", "int": "∫", "times": "x",
         "pm": "±", "infty": "∞", "in": "∈", "emptyset": "∅", "varnothing": "∅", "to": "→", "rightarrow": "→",
         "sqrt": "√", "partial": "∂", "mid": "|", "vert": "|", "prime": "'", "ell": "l"}
LATEX.update({n: n for n in set(GREEK.values())})
LATEX.update({"varphi": "phi", "varepsilon": "epsilon", "vartheta": "theta", "varpi": "pi"})
# everything else (\frac \binom \cdots \ldots \dots \quad \left \right \text \mathrm …) -> nothing

# operators kept in the "ops" variant, canonicalised to one char each
OPS = {"+": "+", "-": "-", "−": "-", "–": "-", "=": "=", "<": "<", ">": ">", "≤": "≤", "≥": "≥", "≠": "≠",
       "∪": "∪", "∩": "∩", "∑": "∑", "∫": "∫", "√": "√", "!": "!", "|": "|", "±": "±", "∈": "∈"}


def latex_plain(t: str) -> str:
    t = re.sub(r"\\(?:text|textbf|textit|mathrm|mathbf|mathit|operatorname)\s*\{([^}]*)\}", r" \1 ", t)
    t = re.sub(r"\\([a-zA-Z]+)", lambda m: f" {LATEX.get(m.group(1), '')} ", t)
    t = re.sub(r"\\[,;:! ]", " ", t)  # spacing commands
    return re.sub(r"[\\{}$_^]", " ", t)


def quote_dialect(t: str) -> str:
    """Gemini's linear math -> the same tokens LaTeX gives: ∑(i=1 to k) -> ∑ i=1 k; sqrt( -> √(."""
    t = re.sub(r"\(\s*(\w+)\s*=\s*(\w+)\s+to\s+(\w+)\s*\)", r" \1=\2 \3 ", t)
    t = re.sub(r"\bsqrt\b", "√", t)
    return t


# Symbol-font letters are kept as-is: the same code point draws as λ in Symbol but ℓ in other fonts,
# so in a match it stands for either the plain letter or the Greek name (see pua_regex)
PUA_LETTER = {0xF000 + ord(k): (k.lower(), GREEK[v]) for k, v in _SYM_LETTERS.items() if v in GREEK}


def canon(t: str, ops: bool) -> str:
    t = "".join(c if ord(c) in PUA_LETTER else SYMBOL_PUA.get(ord(c), c) for c in t).replace("×", "x")
    t = unicodedata.normalize("NFKC", t)
    t = "".join(f" {GREEK[c]} " if c in GREEK else c for c in t).lower()
    keep = ((lambda c: c.isascii() and c.isalnum() or c in OPS or ord(c) in PUA_LETTER) if ops
            else (lambda c: c.isascii() and c.isalnum() or ord(c) in PUA_LETTER))
    return "".join(OPS.get(c, c) for c in t if keep(c))


def old_norm(t: str) -> str:  # #26 check.py, for the R0 baseline
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", t).lower())


def old_latex_plain(t: str) -> str:
    t = re.sub(r"\\(?:text|textbf|mathrm|operatorname)\{([^}]*)\}", r"\1", t)
    rep = {"\\leq": "≤", "\\le": "≤", "\\geq": "≥", "\\ge": "≥", "\\infty": "∞", "\\sum": "∑", "\\int": "∫",
           "\\in": "∈", "\\neq": "≠", "\\pm": "±", "\\cdot": "·", "\\quad": " "}
    for k in sorted(rep, key=len, reverse=True):
        t = t.replace(k, rep[k])
    t = re.sub(r"\\[a-zA-Z]+", " ", t)
    return re.sub(r"[\\{}$_^]", "", t)


# --- candidate rules: (quote, vl_text) -> bool ------------------------------------------------------

def q_canon(q, ops):
    return canon(quote_dialect(latex_plain(q)), ops)


def v_canon(v, ops):
    return canon(latex_plain(v), ops)


def r0(q, v):
    n = old_norm(old_latex_plain(q))
    return bool(n) and n in old_norm(old_latex_plain(v))


def exact(ops):
    def f(q, v):
        n = q_canon(q, ops)
        return bool(n) and n in v_canon(v, ops)
    return f


def best_window_ratio(q: str, v: str) -> float:
    if not q:
        return 0.0
    if q in v:
        return 1.0
    sm = SequenceMatcher(None, v, q, autojunk=False)
    best = 0.0
    # anchor windows on matching blocks; score q against v[start:start+len(q)*1.15]
    for b in sm.get_matching_blocks():
        if b.size < 4:
            continue
        start = max(0, b.a - b.b)
        w = v[start:start + int(len(q) * 1.15) + 4]
        best = max(best, SequenceMatcher(None, w, q, autojunk=False).ratio())
    return best


def window(t, ops=True):
    def f(q, v):
        return best_window_ratio(q_canon(q, ops), v_canon(v, ops)) >= t
    return f


def tokens(s: str):
    return re.findall(r"[a-z]+|\d+|[^a-z0-9\s]", s)


def overlap(t, min_len=0):
    """Ticket's token-overlap: share of quote tokens found (as a multiset) in the page transcript."""
    def f(q, v):
        qn, vn = q_canon(q, True), v_canon(v, True)
        if qn and qn in vn:
            return True
        if len(qn) < min_len:
            return False
        qt, vt = tokens(qn), tokens(vn)
        pool = {}
        for x in vt:
            pool[x] = pool.get(x, 0) + 1
        hit = 0
        for x in qt:
            if pool.get(x, 0) > 0:
                pool[x] -= 1
                hit += 1
        return bool(qt) and hit / len(qt) >= t
    return f


def per_line(ops=True):
    """Every line (and every sentence) of the quote matches exactly somewhere on the page, any order."""
    def f(q, v):
        vn = v_canon(v, ops)
        segs = [s for s in re.split(r"\n|(?<=[.:;])\s+(?=[A-Z])", q) if q_canon(s, ops)]
        return bool(segs) and all(q_canon(s, ops) in vn for s in segs)
    return f


BOUND = re.compile(r"\\q?quad\b|\$\$|\$|\n|[.,;:?](?=\s|$)|\\\\")
MATH_EDGE = re.compile(r"[0-9)!=+−\-<>≤≥≠∪∩∑∫√|/\]}^_']|[-]")


def chunks_canon(v: str, ops=True):
    """VL transcript canonicalised, plus the offsets where a formula/line/clause boundary sat."""
    parts = [canon(latex_plain(c), ops) for c in BOUND.split(v)]
    s, cuts, n = "", {0}, 0
    for p in parts:
        s += p
        n += len(p)
        cuts.add(n)
    return s, cuts


def pua_regex(n: str):
    return re.compile("".join(f"(?:{PUA_LETTER[ord(c)][1]}|{PUA_LETTER[ord(c)][0]})" if ord(c) in PUA_LETTER
                              else re.escape(c) for c in n))


def bounded(edges="math"):
    """R2 + boundary: a quote that starts/ends in math must start/end where a formula or clause does.
    edges='math': only the math ends of a quote; 'both': every quote, both ends."""
    def f(q, v):
        n = q_canon(q, True)
        if not n:
            return False
        vn, cuts = chunks_canon(v)
        qs = q.strip().rstrip(".,;: ")
        need_end = edges == "both" or bool(MATH_EDGE.search(qs[-12:] if edges == "near" else qs[-1:]))
        need_start = edges == "both" or bool(MATH_EDGE.search(qs[:12] if edges == "near" else qs[:1]))
        rx = pua_regex(n)
        pos = 0
        while (m := rx.search(vn, pos)):
            if (not need_start or m.start() in cuts) and (not need_end or m.end() in cuts):
                return True
            pos = m.start() + 1
        return False
    return f


RULES = {
    "R6 exact+boundary at math end": bounded("math"),
    "R6 exact+boundary both ends": bounded("both"),
    "R7 exact+boundary near math": bounded("near"),
    "R0 #26 rule": r0,
    "R1 exact, ops dropped": exact(False),
    "R2 exact, ops kept": exact(True),
    "R3 per-line exact, ops kept": per_line(True),
    "R4 window ≥0.90": window(0.90),
    "R4 window ≥0.95": window(0.95),
    "R4 window ≥0.98": window(0.98),
    "R5 token overlap ≥0.85": overlap(0.85),
    "R5 token overlap ≥0.95": overlap(0.95),
}

# --- labelled set -----------------------------------------------------------------------------------

MATHY = re.compile(r"[=+−\-<>≤≥≠∪∩∑∫√|/^]|\d|[\uf000-\uf0ff]")
OP_FLIP = [("∪", "∩"), ("∩", "∪"), ("+", "−"), ("−", "+"), ("≤", "≥"), ("≥", "≤"), (" = ", " ≠ "), ("|", ","),
           ("!", "")]


def mutations(q: str):
    """One-edit wrong versions of a formula quote, each tagged with its kind."""
    out = []
    digits = [m for m in re.finditer(r"\d", q)]
    if digits:
        m = digits[len(digits) // 2]
        d = str((int(m.group()) + 1) % 10)
        out.append(("altered: digit", q[:m.start()] + d + q[m.end():]))
    for a, b in OP_FLIP:
        i = q.rfind(a)
        if i >= 0:
            out.append(("altered: operator", q[:i] + b + q[i + len(a):]))
            break
    # swap a subscripted variable: A2 -> A3 / Bi -> Bk / vf -> vi (last one in the quote, likely the formula)
    subs = list(re.finditer(r"\b([A-Za-z])(\d|[ikrfn])\b", q))
    if subs:
        m = subs[-1]
        s = m.group(2)
        ns = str((int(s) + 1) % 10) if s.isdigit() else {"i": "k", "k": "i", "r": "i", "f": "i", "n": "k"}[s]
        out.append(("altered: subscript", q[:m.start(2)] + ns + q[m.end(2):]))
    # drop a term: remove the last "+ X" / "− X" / "P(…)" factor
    m = list(re.finditer(r"\s[+−]\s[^\s+−=]+", q)) or list(re.finditer(r"P\([^()]*\)", q))
    if m:
        m = m[-1]
        out.append(("altered: dropped term", q[:m.start()] + q[m.end():]))
    return out


def citations(x):
    if isinstance(x, dict):
        if "citations" in x and not x.get("gapFill"):
            yield from x["citations"]
        for v in x.values():
            yield from citations(v)
    elif isinstance(x, list):
        for v in x:
            yield from citations(v)


# hand labels after looking at the slide / transcript (see the issue comment)
RELABEL = {
    # Gemini put Theorem 5's "nr" line on Theorem 4's page; p5 ends in nk and has no such line
    ("chapter2", 5, "where n1 + n2 + ··· + nr = n"): "wrong page (Gemini)",
}


def build(stem):
    g = json.loads((OUT / f"{stem}.gemini.json").read_text())["output"]
    pages = json.loads((OUT / f"{stem}.pages.json").read_text())["pages"]
    vl = {int(k): v["text"] for k, v in json.loads((OUT / f"{stem}.vl.json").read_text()).items() if v.get("text")}
    layer = {p["page"]: canon(p["text"] + " " + " ".join(l["text"] for l in p["ocr"]), True) for p in pages}
    items = []  # (label, kind, quote, page_text, where)
    seen = set()
    for ct in citations(g):
        p, q = ct["page"], ct["quote"]
        if p not in vl or (p, q) in seen:
            continue
        seen.add((p, q))
        on_layer = q_canon(q, True) in layer.get(p, "")
        src = "positive (text layer/OCR)" if on_layer else "positive (image-only)"
        if (k := RELABEL.get((stem, p, q))):
            items.append((False, k, q, vl[p], f"{stem} p{p}"))
            continue
        items.append((True, src, q, vl[p], f"{stem} p{p}"))
        for np_ in (p - 1, p + 1):
            if np_ in vl and q_canon(q, False) not in canon(layer.get(np_, ""), False):
                items.append((False, "other page", q, vl[np_], f"{stem} p{p}→p{np_}"))
        if MATHY.search(q):
            for kind, mq in mutations(q):
                if q_canon(mq, True) == q_canon(q, True) or q_canon(mq, True) in layer.get(p, ""):
                    continue
                items.append((False, kind, mq, vl[p], f"{stem} p{p}"))
    return items


def main():
    stems = [a for a in sys.argv[1:] if not a.startswith("--")]
    show = "--show" in sys.argv
    items = [it for s in stems for it in build(s)]
    kinds = sorted({k for _, k, *_ in items}, key=lambda k: (not k.startswith("positive"), k))
    counts = {k: sum(1 for _, kk, *_ in items if kk == k) for k in kinds}
    print("labelled set:", ", ".join(f"{k} {n}" for k, n in counts.items()))
    hdr = f"{'rule':30}" + "".join(f"{k.replace('positive ', 'pos ').replace('altered: ', 'alt ')[:16]:>17}" for k in kinds)
    print(hdr)
    for name, rule in RULES.items():
        row = f"{name:30}"
        for k in kinds:
            sel = [(q, v) for lab, kk, q, v, _ in items if kk == k]
            passed = sum(rule(q, v) for q, v in sel)
            row += f"{f'{passed}/{len(sel)}':>17}"
        print(row)
    if show:
        rule_names = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--rule=")] or ["R2 exact, ops kept"]
        for rn in rule_names:
            rule = RULES[rn]
            print(f"\n== {rn}: wrong calls")
            for lab, k, q, v, where in items:
                if rule(q, v) != lab:
                    print(f"  [{k}] {where}: {q[:110]!r}")


if __name__ == "__main__":
    main()
