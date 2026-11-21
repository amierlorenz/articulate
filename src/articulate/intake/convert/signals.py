"""Damaged text layer signals (articulate#40, Acceptance check 2.16)."""

import ctypes
import re
import statistics
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as c

# Word's Cambria Math subsets can map glyphs onto ASCII punctuation (x² -> x!)
MATH_FONT = re.compile(r"math|stix|symbol|mt ?extra|euclid|cmsy|cmmi|cmex", re.IGNORECASE)
ODD_IN_MATH = set('!"#$%&*;?@^_`~')


def compute_damaged_layer_signals(page: pdfium.PdfPage) -> dict[str, Any]:
    """
    Computes cheap PDF-only damaged-layer signals on a page (§2.16, #40).

    Signals:
      glyph_img: glyph-sized image in a text line
      eq_img: inline equation picture in or beside a text line
      mathfont_odd: ASCII punctuation drawn in a math font
    Returns dict with counts and layer_untrusted (1 if any signal > 0, else 0).
    """
    tp = page.get_textpage()
    n = c.FPDFText_CountChars(tp)
    boxes, heights = [], []
    mathfont_odd = 0
    buf = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()

    for i in range(n):
        u = c.FPDFText_GetUnicode(tp, i)
        if 0x21 <= u <= 0x7E and chr(u) in ODD_IN_MATH:
            c.FPDFText_GetFontInfo(tp, i, buf, 256, ctypes.byref(flags))
            fontname = buf.value.decode(errors="replace")
            if MATH_FONT.search(fontname):
                mathfont_odd += 1

        if u > 0x20 and u != 0xA0:
            b = tp.get_charbox(i)
            boxes.append((i, *b))
            heights.append(b[3] - b[1])

    ch = statistics.median(heights) if heights else 10.0

    def on_text_line(l: float, b: float, r: float, t: float, reach: float) -> bool:
        """True if some char shares the object's line band and sits within `reach` pt horizontally."""
        mid = (b + t) / 2
        for _, cl, cb, cr, ct in boxes:
            if cb - 0.3 * ch <= mid <= ct + 0.3 * ch and (cl - reach <= r and cr + reach >= l):
                return True
        return False

    glyph_img = 0
    eq_img = 0

    for obj in page.get_objects(filter=[c.FPDF_PAGEOBJ_IMAGE], max_depth=6):
        l, b, r, t = obj.get_bounds()
        w, h = r - l, t - b
        if h <= 0 or w <= 0:
            continue

        if h <= 1.8 * ch and w <= 3.0 * ch and on_text_line(l, b, r, t, 1.5 * ch):
            glyph_img += 1
        elif h <= 3.5 * ch and w <= 40.0 * ch and on_text_line(l, b, r, t, 1.0 * ch):
            eq_img += 1

    untrusted = 1 if (glyph_img > 0 or eq_img > 0 or mathfont_odd > 0) else 0

    return {
        "glyph_img": glyph_img,
        "eq_img": eq_img,
        "mathfont_odd": mathfont_odd,
        "char_h": round(ch, 1),
        "layer_untrusted": untrusted,
    }
