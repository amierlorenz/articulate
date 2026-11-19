# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2", "ocrmac", "pillow"]
# ///
"""PROTOTYPE — throwaway (articulate#40). Text layer + Vision OCR per page, the subset of #26's prep.py
that signals.py reads. Times the OCR, since the cross-check's cost is the question.

Usage: uv run prep.py <stem> [<stem> ...]   (decks/<stem>.pdf -> out/<stem>.pages.json)
"""
import json, sys, time
from pathlib import Path

import pypdfium2 as pdfium
from ocrmac import ocrmac

DATA = Path.home() / "Library/Application Support/articulate/prototypes/40-damaged-layer"

for stem in sys.argv[1:]:
    pdf = pdfium.PdfDocument(DATA / "decks" / f"{stem}.pdf")
    pages, t0 = [], time.time()
    for i in range(len(pdf)):
        page = pdf[i]
        text = page.get_textpage().get_text_range().replace("\r\n", "\n").replace("\r", "\n").strip()
        lines = ocrmac.OCR(page.render(scale=2).to_pil(), recognition_level="accurate",
                           language_preference=["en-US"]).recognize()
        pages.append({"page": i + 1, "text": text, "ocr": [{"text": t, "conf": round(float(c), 3)} for t, c, _ in lines]})
    secs = round(time.time() - t0, 1)
    (DATA / "out" / f"{stem}.pages.json").write_text(json.dumps({"deck": f"{stem}.pdf", "prep_seconds": secs,
                                                                 "pages": pages}, indent=1, ensure_ascii=False))
    print(f"{stem}: {len(pages)} pages, {secs} s ({secs / len(pages):.2f} s/page)")
