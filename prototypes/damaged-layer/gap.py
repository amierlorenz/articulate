# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2"]
# ///
"""PROTOTYPE — throwaway (articulate#40). What sits in the gap between two chars?

Usage: uv run gap.py <deck.pdf> <page> <char_i> <char_j>
Lists page objects (any type, nested) whose bounds overlap the box between chars i and j.
"""
import sys
import pypdfium2 as pdfium
import pypdfium2.raw as c

pdf = pdfium.PdfDocument(sys.argv[1])
page = pdf[int(sys.argv[2]) - 1]
tp = page.get_textpage()
i, j = int(sys.argv[3]), int(sys.argv[4])
li, bi, ri, ti = tp.get_charbox(i)
lj, bj, rj, tj = tp.get_charbox(j)
gx0, gx1 = ri, lj
gy0, gy1 = min(bi, bj) - 2, max(ti, tj) + 2
print(f"gap x {gx0:.1f}..{gx1:.1f} y {gy0:.1f}..{gy1:.1f}")
names = {c.FPDF_PAGEOBJ_TEXT: "text", c.FPDF_PAGEOBJ_PATH: "path", c.FPDF_PAGEOBJ_IMAGE: "image",
         c.FPDF_PAGEOBJ_SHADING: "shading", c.FPDF_PAGEOBJ_FORM: "form"}
for obj in page.get_objects(max_depth=6):
    l, b, r, t = obj.get_bounds()
    if r < gx0 - 1 or l > gx1 + 1 or t < gy0 or b > gy1:
        continue
    kind = names.get(obj.type, obj.type)
    extra = ""
    if obj.type == c.FPDF_PAGEOBJ_TEXT:
        n = c.FPDFTextObj_GetText(obj.raw, tp.raw, None, 0)
        import ctypes
        buf = (ctypes.c_ushort * (n + 1))()
        c.FPDFTextObj_GetText(obj.raw, tp.raw, buf, n)
        txt = bytes(buf)[: max(0, n - 2)].decode("utf-16-le", errors="replace")
        font = c.FPDFTextObj_GetFont(obj.raw)
        fbuf = ctypes.create_string_buffer(256)
        c.FPDFFont_GetBaseFontName(font, fbuf, 256)
        extra = f"text={txt!r} font={fbuf.value.decode()}"
    print(f"{kind:6} [{l:.1f},{b:.1f},{r:.1f},{t:.1f}] {extra}")
