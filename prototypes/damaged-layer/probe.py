# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2"]
# ///
"""PROTOTYPE — throwaway (articulate#40). Dump per-char info for a page or a text snippet.

Usage: uv run probe.py <deck.pdf> <page> [needle]
Per char: index, unicode, repr, font name, font flags, has-unicode-mapping (FPDFText_HasUnicodeMapError).
"""
import ctypes, sys
import pypdfium2 as pdfium
import pypdfium2.raw as c

pdf = pdfium.PdfDocument(sys.argv[1])
page = pdf[int(sys.argv[2]) - 1]
tp = page.get_textpage()
n = c.FPDFText_CountChars(tp)
text = tp.get_text_range()
needle = sys.argv[3] if len(sys.argv) > 3 else None
lo, hi = 0, n
if needle:
    k = text.find(needle)
    print("found at", k)
    lo, hi = max(0, k - 5), min(n, k + len(needle) + 10)
buf = ctypes.create_string_buffer(256)
flags = ctypes.c_int()
for i in range(lo, hi):
    u = c.FPDFText_GetUnicode(tp, i)
    c.FPDFText_GetFontInfo(tp, i, buf, 256, ctypes.byref(flags))
    err = c.FPDFText_HasUnicodeMapError(tp, i)
    gen = c.FPDFText_IsGenerated(tp, i)
    print(i, f"U+{u:04X}", repr(chr(u)) if u else "''", buf.value.decode(errors="replace"), flags.value, "maperr" if err else "", "gen" if gen else "")
