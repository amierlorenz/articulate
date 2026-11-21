"""PDF page text layer extraction, damaged layer signals, and OCR integration (§2.14, §2.15, §2.16)."""

import logging
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as c

from articulate.intake.convert.ocr import compute_image_page_hash, get_page_ocr
from articulate.intake.convert.signals import compute_damaged_layer_signals

logger = logging.getLogger(__name__)


def extract_pdf_pages(
    pdf_path: Path,
    inspect_data: dict[str, Any] | None = None,
    work_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Extracts text layer, damaged-layer signals, and OCR (where needed) per page (§2.14–2.16).
    Attaches title, notes, hidden from inspect_data when available.
    """
    pdf_path = Path(pdf_path).resolve()
    doc = pdfium.PdfDocument(pdf_path)
    total_pages = len(doc)

    slides_meta = (inspect_data or {}).get("slides") or []

    pages = []
    for idx in range(1, total_pages + 1):
        page = doc[idx - 1]
        w, h = page.get_size()
        tp = page.get_textpage()
        layer_text = tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n").strip()

        # Damaged layer signals (§2.16, #40)
        signals = compute_damaged_layer_signals(page)
        layer_untrusted = signals.get("layer_untrusted", 0)

        # Image area fraction calculation (§2.15, #26, #35)
        img_area = 0.0
        for obj in page.get_objects(filter=[c.FPDF_PAGEOBJ_IMAGE], max_depth=6):
            l, b, r, t = obj.get_bounds()
            img_area += max(0.0, min(r, w) - max(l, 0.0)) * max(0.0, min(t, h) - max(b, 0.0))
        image_frac = min(1.0, img_area / (w * h)) if (w * h) > 0 else 0.0

        # Check if page needs OCR: empty text layer OR mostly picture with little text (§2.15, #14)
        needs_ocr = (len(layer_text) == 0) or (image_frac >= 0.5 and len(layer_text) < 30)

        ocr_text: str | None = None
        if needs_ocr:
            ocr_text, page_hash = get_page_ocr(page, work_dir=work_dir)
        else:
            # Fast page hash from 2x render without running OCR
            pil_img = page.render(scale=2.0).to_pil()
            page_hash = compute_image_page_hash(pil_img)

        # Attach slide title, notes, hidden if present in inspect_data
        title: str | None = None
        notes: str | None = None
        hidden: int = 0

        if idx - 1 < len(slides_meta):
            sm = slides_meta[idx - 1]
            title = sm.get("title") or None
            notes = sm.get("notes") or None
            hidden = 1 if sm.get("hidden") else 0

        pages.append(
            {
                "page": idx,
                "page_hash": page_hash,
                "text": layer_text,
                "layer_untrusted": layer_untrusted,
                "ocr_text": ocr_text,
                "title": title,
                "notes": notes,
                "hidden": hidden,
                "signals": signals,
            }
        )

    return pages
