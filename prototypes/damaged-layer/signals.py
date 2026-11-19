# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2"]
# ///
"""PROTOTYPE — throwaway (articulate#40). Cheap signals that a page's text layer lost symbols.

Usage: uv run signals.py <stem> [<stem> ...]
Needs decks/<stem>.pdf and out/<stem>.pages.json (text layer + Vision OCR from #26's prep.py);
out/<stem>.vl.json (VL transcript) is used only for the labelling shortlist (`vl_deficit`).
Writes out/<stem>.signals.json, one row per page.

Signals (all read from the PDF or the cached OCR, no model):
  glyph_img   image objects the size of a glyph sitting inside a text line (Word/PowerPoint pasted a
              symbol as a picture: the ∪ in Ch. 2's `P(A B)`)
  eq_img      wider images one-to-three lines tall inside or next to a text line (a pasted equation)
  gap_gen     pdfium-generated spaces whose gap holds a non-text object
  maperr      chars with a broken ToUnicode map, U+FFFD, or control chars
  mathfont_odd ASCII punctuation drawn in a math font (Word's Cambria Math subset: x² -> `𝑥!`)
  pua         private-use chars (Symbol font; #35 already maps the common ones)
  math_gap    layer text patterns where an operand pair has only a space between (`P(A B)`, `x  y`)
  ocr_ops     operators OCR read on the page that the layer lacks (page-level count deficit; noisy:
              also fires on symbols inside pictures, which have no layer text at all)
  ocr_ins     1-2 chars OCR has inside an otherwise matching stretch of the layer line (lost ∪ -> `U`)
  ocr_line    the same, line-aligned: an OCR line with a symbol whose matching layer line lacks it
"""
import ctypes, json, re, statistics, sys, unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as c

DATA = Path.home() / "Library/Application Support/articulate/prototypes/40-damaged-layer"
OUT = DATA / "out"

sys.path.insert(0, str(Path(__file__).parent))
from vl_match35 import latex_plain, SYMBOL_PUA  # noqa: E402

# symbols a damaged layer is likely to lose; ASCII + - = < > are counted too but reported apart
MATH_OPS = set("∪∩≤≥≠∑∫√±∈∞→∂∅÷≈∝∆Δπθωαβγδεσμλφρτ")  # × and · left out: #35 reads × as x | set("ΣΩΘΛΦΨΓΠ")
ASCII_OPS = set("+=<>−-")
# the operators #35's citation rule keeps; only these (plus letters and digits) can change a verdict
KEEP35 = set("+-=<>≤≥≠∪∩∑∫√!|±∈")


def ops(text: str, ascii_too=False) -> Counter:
    text = "".join(SYMBOL_PUA.get(ord(ch), ch) for ch in text)
    keep = MATH_OPS | (ASCII_OPS if ascii_too else set())
    cnt = Counter(ch for ch in text if ch in keep)
    if ascii_too:  # one minus
        cnt["-"] += cnt.pop("−", 0)
    return cnt


def deficit(want: Counter, have: Counter) -> dict:
    return {k: v - have.get(k, 0) for k, v in want.items() if v > have.get(k, 0)}


def page_signals(page) -> dict:
    tp = page.get_textpage()
    n = c.FPDFText_CountChars(tp)
    boxes, heights = [], []
    maperr = pua = mathfont_odd = 0
    gens = []
    buf = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    for i in range(n):
        u = c.FPDFText_GetUnicode(tp, i)
        if 0x21 <= u <= 0x7E and chr(u) in ODD_IN_MATH:
            c.FPDFText_GetFontInfo(tp, i, buf, 256, ctypes.byref(flags))
            if MATH_FONT.search(buf.value.decode(errors="replace")):
                mathfont_odd += 1
        if c.FPDFText_IsGenerated(tp, i):
            gens.append(i)
            continue
        if u not in (0xFFFE, 0xAD, 0x2) and (c.FPDFText_HasUnicodeMapError(tp, i) or u == 0xFFFD
                                            or (u < 0x20 and u not in (9, 10, 13))):
            maperr += 1
        if 0xE000 <= u <= 0xF8FF:
            pua += 1
        if u > 0x20 and u not in (0xA0,):
            b = tp.get_charbox(i)
            boxes.append((i, *b))
            heights.append(b[3] - b[1])
    ch = statistics.median(heights) if heights else 10.0

    def on_text_line(l, b, r, t, reach):
        """True if some char shares the object's line band and sits within `reach` pt horizontally."""
        mid = (b + t) / 2
        for _, cl, cb, cr, ct in boxes:
            if cb - 0.3 * ch <= mid <= ct + 0.3 * ch and (cl - reach <= r and cr + reach >= l):
                return True
        return False

    glyph_img = eq_img = 0
    img_rects = []
    for obj in page.get_objects(filter=[c.FPDF_PAGEOBJ_IMAGE], max_depth=6):
        l, b, r, t = obj.get_bounds()
        w, h = r - l, t - b
        if h <= 0 or w <= 0:
            continue
        img_rects.append((l, b, r, t))
        if h <= 1.8 * ch and w <= 3 * ch and on_text_line(l, b, r, t, 1.5 * ch):
            glyph_img += 1
        elif h <= 3.5 * ch and w <= 40 * ch and on_text_line(l, b, r, t, 1.0 * ch):
            eq_img += 1

    # generated spaces whose gap holds an image or path (a symbol drawn as non-text)
    gap_gen = 0
    paths = [obj.get_bounds() for obj in page.get_objects(filter=[c.FPDF_PAGEOBJ_PATH], max_depth=6)]
    # drop hairlines (table rules, underlines, fraction bars): a drawn symbol has some width AND height
    others = img_rects + [b for b in paths if (b[2] - b[0]) > 1.2 and (b[3] - b[1]) > 1.2]
    for i in gens:
        if i == 0 or i + 1 >= n:
            continue
        a, z = tp.get_charbox(i - 1), tp.get_charbox(i + 1)
        if abs(a[1] - z[1]) > ch:  # line break, not an in-line gap
            continue
        gx0, gx1 = a[2], z[0]
        if gx1 - gx0 < 0.2 * ch:
            continue
        y0, y1 = min(a[1], z[1]), max(a[3], z[3])
        for (l, b, r, t) in others:
            ow, oh = r - l, t - b
            if (l >= gx0 - 1 and r <= gx1 + 1 and t >= y0 and b <= y1 and 0.3 * ch <= ow < 3 * ch
                    and 0.3 * ch <= oh <= 1.8 * ch):
                gap_gen += 1
                break
    return {"glyph_img": glyph_img, "eq_img": eq_img, "gap_gen": gap_gen, "maperr": maperr, "pua": pua,
            "mathfont_odd": mathfont_odd,
            "char_h": round(ch, 1)}


# Word's Cambria Math subsets can map glyphs onto ASCII punctuation (x² comes out as `𝑥!`)
MATH_FONT = re.compile(r"math|stix|symbol|mt ?extra|euclid|cmsy|cmmi|cmex", re.I)
ODD_IN_MATH = set('!"#$%&*;?@^_`~')

MATH_GAP = re.compile(r"\b[A-Za-z]\(\s*\w{1,3} \w{1,3}\s*\)|(?<=\w)  +(?=\w)(?![^\n]*\n\n)")


def run(stem: str):
    pdf = pdfium.PdfDocument(DATA / "decks" / f"{stem}.pdf")
    prep = json.loads((OUT / f"{stem}.pages.json").read_text())["pages"]
    vlp = OUT / f"{stem}.vl.json"
    vl = json.loads(vlp.read_text()) if vlp.exists() else {}
    rows = []
    for p in prep:
        i = p["page"]
        row = {"page": i, **page_signals(pdf[i - 1])}
        layer = p["text"]
        ocr = "\n".join(l["text"] for l in p["ocr"])
        row["math_gap"] = [m.group(0) for m in re.finditer(r"\b[A-Za-z]\(\s*\w{1,3} \w{1,3}\s*\)", layer)]
        row["ocr_ops"] = deficit(ops(ocr), ops(layer))
        row["ocr_line"] = ocr_line_diff(layer, [l["text"] for l in p["ocr"]])
        row["ocr_ins"] = ocr_insertions(layer, [l["text"] for l in p["ocr"]])
        if str(i) in vl:
            row["vl_deficit"] = deficit(ops(latex_plain_keep(vl[str(i)]["text"])), ops(layer))
        rows.append(row)
    (OUT / f"{stem}.signals.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    return rows


def words(t: str) -> list[str]:
    t = unicodedata.normalize("NFKC", "".join(SYMBOL_PUA.get(ord(ch), ch) for ch in t)).lower()
    return re.findall(r"[a-z0-9]+", t)


def ocr_line_diff(layer: str, ocr_lines: list[str]) -> list[str]:
    """OCR lines holding a math symbol whose matching layer line (most shared words, >= 60 % of the
    OCR line's words and >= 2 of them) lacks that symbol. Lines with no layer match are image text."""
    lines = [l for l in layer.split("\n") if l.strip()]
    lw = [Counter(words(l)) for l in lines]
    hits = []
    for o in ocr_lines:
        want = Counter(ch for ch in ops(o).elements() if ch in KEEP35)
        if not want:
            continue
        ow = Counter(words(o))
        if sum(ow.values()) < 2:
            continue
        best, score = None, 0.0
        for k, cnt in enumerate(lw):
            # join with the next layer line too: OCR and the layer break lines differently
            both = cnt + (lw[k + 1] if k + 1 < len(lw) else Counter())
            sc = sum((ow & both).values()) / sum(ow.values())
            if sc > score:
                best, score = k, sc
        if best is None or score < 0.6:
            continue
        span = lines[best] + (lines[best + 1] if best + 1 < len(lines) else "")
        if deficit(want, ops(span)):
            hits.append(o)
    return hits


def cstr(t: str) -> str:
    """Canonical chars for alignment: NFKC, Symbol PUA mapped, lower, alnum + operators, no spaces."""
    t = unicodedata.normalize("NFKC", "".join(SYMBOL_PUA.get(ord(ch), ch) for ch in t)).lower()
    t = re.sub("[–—−‒]", "-", t)
    return "".join(ch for ch in t if ch.isalnum() or ch in MATH_OPS or ch in "+=<>-()|")


MATHY = set("()") | KEEP35


def ocr_insertions(layer: str, ocr_lines: list[str], ctx: int = 2) -> list[str]:
    """OCR chars missing from the matching layer line: 1-2 char insertions flanked by >= ctx matched
    chars on both sides (OCR reads a lost ∪ as `U`: layer `P(A B)` vs OCR `P(AUB)`)."""
    lines = [l for l in layer.split("\n") if l.strip()]
    lw = [Counter(words(l)) for l in lines]
    hits = []
    for o in ocr_lines:
        ow = Counter(words(o))
        if sum(ow.values()) < 2:
            continue
        best, score = None, 0.0
        for k, cnt in enumerate(lw):
            both = cnt + (lw[k + 1] if k + 1 < len(lw) else Counter())
            sc = sum((ow & both).values()) / sum(ow.values())
            if sc > score:
                best, score = k, sc
        if best is None or score < 0.6:
            continue
        a = cstr(lines[best] + " " + (lines[best + 1] if best + 1 < len(lines) else ""))
        b = cstr(o)
        sm = SequenceMatcher(None, a, b, autojunk=False)
        opc = sm.get_opcodes()
        for j, (tag, i1, i2, j1, j2) in enumerate(opc):
            if tag != "insert" or j2 - j1 > 2 or j == 0 or j == len(opc) - 1:
                continue
            ins = b[j1:j2]
            # a lost symbol comes back from OCR as a letter or operator (∪ -> U, ∩ -> n, ∈ -> €/e),
            # never as a digit or bracket (those are fraction/ordering noise in scrambled layers)
            if not all(ch.isalpha() or ch in KEEP35 for ch in ins):
                continue
            if not (MATHY & set(b[max(0, j1 - 3):j1] + b[j2:j2 + 3])):
                continue
            if opc[j - 1][0] == "equal" and opc[j - 1][2] - opc[j - 1][1] >= ctx \
                    and opc[j + 1][0] == "equal" and opc[j + 1][2] - opc[j + 1][1] >= ctx:
                hits.append(f"{b[max(0, j1 - 6):j1]}[{b[j1:j2]}]{b[j2:j2 + 6]}")
    return hits


LATEX_SYM = {"cup": "∪", "cap": "∩", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "neq": "≠", "ne": "≠",
             "sum": "∑", "int": "∫", "sqrt": "√", "pm": "±", "in": "∈", "infty": "∞", "to": "→",
             "rightarrow": "→", "partial": "∂", "emptyset": "∅", "varnothing": "∅", "times": "×", "div": "÷",
             "approx": "≈", "propto": "∝", "cdot": "·", "Delta": "Δ", "pi": "π", "theta": "θ", "omega": "ω",
             "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
             "sigma": "σ", "mu": "μ", "lambda": "λ", "phi": "φ", "varphi": "φ", "rho": "ρ", "tau": "τ",
             "Sigma": "Σ", "Omega": "Ω", "Theta": "Θ", "Lambda": "Λ", "Phi": "Φ", "Psi": "Ψ", "Gamma": "Γ",
             "Pi": "Π"}


def latex_plain_keep(t: str) -> str:
    """VL LaTeX -> symbols, keeping Greek as glyphs (latex_plain spells them out)."""
    return re.sub(r"\\([a-zA-Z]+)", lambda m: LATEX_SYM.get(m.group(1), " "), t)


if __name__ == "__main__":
    for stem in sys.argv[1:]:
        rows = run(stem)
        print(f"== {stem}: {len(rows)} pages")
        for r in rows:
            flags = {k: r[k] for k in ("glyph_img", "eq_img", "gap_gen", "maperr", "pua", "mathfont_odd") if r[k]}
            for k in ("math_gap", "ocr_line", "ocr_ins", "vl_deficit"):
                if r.get(k):
                    flags[k] = r[k]
            if flags:
                print(f"p{r['page']:>2} ch={r['char_h']}", flags)
