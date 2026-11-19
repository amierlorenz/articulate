# /// script
# requires-python = ">=3.12"
# dependencies = ["pypdfium2"]
# ///
"""PROTOTYPE — throwaway. Text-layer survey of candidate decks (articulate#26).

Usage: uv run survey.py <deck.pdf>...
"""
import sys

import pypdfium2 as pdfium

for f in sys.argv[1:]:
    try:
        pdf = pdfium.PdfDocument(f)
    except Exception as e:
        print(f, "ERR", e)
        continue
    lens = [len(pdf[i].get_textpage().get_text_range().strip()) for i in range(len(pdf))]
    first = pdf[0].get_textpage().get_text_range().strip().replace("\n", " ").replace("\r", " ")[:80]
    med = sorted(lens)[len(lens) // 2]
    print(f"{len(pdf):3d}p med={med:5d} low(<80)={sum(l < 80 for l in lens):3d}  {f.split('/')[-1]}  | {first}")
