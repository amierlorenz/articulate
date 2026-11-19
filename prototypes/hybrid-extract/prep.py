# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2", "ocrmac", "pillow"]
# ///
"""PROTOTYPE — throwaway. Per-page text + routing signals for hybrid Extract (articulate#26).

Usage: uv run prep.py <deck.pdf>
Writes out/<stem>.pages.json: per page the text layer, Vision OCR lines (accurate, 2x render, as in
MieracleStudy#90), and the routing signals the ticket names:
- ocr_conf_mean / ocr_low_frac: Vision confidence (mean, share of lines below 0.5)
- image_frac: share of the page covered by image objects (capped at 1)
- math_density: math glyphs + equation-like tokens per 100 characters of page text
- ocr_only_frac: share of OCR words missing from the text layer (text that lives in images)
"""
import json, re, sys, time, unicodedata
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from ocrmac import ocrmac

here = Path(__file__).parent
deck = Path(sys.argv[1])
out = here / "out"
out.mkdir(exist_ok=True)

MATH_CHARS = set("∑∫√≤≥≠±×÷∞∂∆Δπθωαβγδεσμλφρτ∝≈→⇒∈∉⊂⊆∪∩¬∀∃²³⁻¹ⁿ₀₁₂ᵢ·∙⋅")
MATH_TOKENS = re.compile(r"(?:\b[A-Za-z]\s*[=<>]\s*[-\w(]|\b[A-Za-z]\([A-Za-z0-9 ,<>=|]+\)|\^|_\{|\\[a-z]+|\b(?:sin|cos|tan|ln|log|lim|exp)\b|\d\s*/\s*\d)")


def words(t: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", t).lower())


pdf = pdfium.PdfDocument(deck)
pages, t0 = [], time.time()
for i in range(len(pdf)):
    page = pdf[i]
    w, h = page.get_size()
    text = page.get_textpage().get_text_range().replace("\r\n", "\n").replace("\r", "\n").strip()

    img_area = 0.0
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=4):
        l, b, r, t = obj.get_bounds()
        img_area += max(0, min(r, w) - max(l, 0)) * max(0, min(t, h) - max(b, 0))
    image_frac = min(1.0, img_area / (w * h))

    pil = page.render(scale=2).to_pil()
    lines = ocrmac.OCR(pil, recognition_level="accurate", language_preference=["en-US"]).recognize()
    ocr = [{"text": t, "conf": round(float(c), 3)} for t, c, _ in lines]
    ocr_text = "\n".join(l["text"] for l in ocr)

    layer_words = set(words(text))
    ocr_words = words(ocr_text)
    ocr_only = [x for x in ocr_words if x not in layer_words]
    both = text + "\n" + ocr_text
    n = max(1, len(both))
    math = sum(ch in MATH_CHARS for ch in both) + len(MATH_TOKENS.findall(both))
    confs = [l["conf"] for l in ocr]
    pages.append({
        "page": i + 1,
        "text": text,
        "ocr": ocr,
        "chars_layer": len(text),
        "chars_ocr": len(ocr_text),
        "ocr_conf_mean": round(sum(confs) / len(confs), 3) if confs else None,
        "ocr_low_frac": round(sum(c < 0.5 for c in confs) / len(confs), 3) if confs else 0.0,
        "image_frac": round(image_frac, 3),
        "math_density": round(100 * math / n, 2),
        "ocr_only_frac": round(len(ocr_only) / len(ocr_words), 3) if ocr_words else 0.0,
    })

secs = round(time.time() - t0, 1)
(out / f"{deck.stem}.pages.json").write_text(json.dumps({"deck": deck.name, "prep_seconds": secs, "pages": pages}, indent=1, ensure_ascii=False))
print(f"{deck.name}: {len(pages)} pages, prep {secs} s")
print(" pg  layer   ocr  conf  low%  img%  math  ocrOnly%")
for p in pages:
    print(f"{p['page']:3d} {p['chars_layer']:6d} {p['chars_ocr']:5d} {p['ocr_conf_mean'] or 0:5.2f} {100*p['ocr_low_frac']:5.0f} {100*p['image_frac']:5.0f} {p['math_density']:5.1f} {100*p['ocr_only_frac']:6.0f}")
