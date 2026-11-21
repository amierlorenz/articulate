"""OCR extraction using ocrmac in a short-lived subprocess with page-hash caching (§2.15)."""

import hashlib
import io
import json
import logging
import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from articulate.core.paths import cache_ocr_path

logger = logging.getLogger(__name__)


def compute_image_page_hash(img: Image.Image) -> str:
    """Compute sha256 hash from raw image bytes for caching."""
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return hashlib.sha256(bio.getvalue()).hexdigest()


def run_ocr_subprocess(image_path: Path) -> str:
    """
    Run ocrmac in a short-lived python subprocess to isolate memory (§2.15).
    Uses accurate recognition level and en-US language preference.
    """
    code = """
import sys, json
from PIL import Image
from ocrmac import ocrmac

img_path = sys.argv[1]
lines = ocrmac.OCR(img_path, recognition_level="accurate", language_preference=["en-US"]).recognize()
text = "\\n".join(t for t, c, b in lines)
print(text)
"""
    proc = subprocess.run(
        [sys.executable, "-c", code, str(image_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning("ocrmac subprocess failed (%s): %s", proc.returncode, proc.stderr.strip())
        return ""
    return proc.stdout.strip()


def run_ocr(image: Image.Image, work_dir: Path | None = None) -> str:
    """Run OCR on a PIL image via short-lived subprocess."""
    temp_dir = work_dir or Path("/tmp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_png = temp_dir / f"ocr_{hashlib.sha256(image.tobytes()).hexdigest()[:16]}.png"
    try:
        image.save(temp_png, format="PNG")
        return run_ocr_subprocess(temp_png)
    finally:
        if temp_png.exists():
            try:
                temp_png.unlink()
            except OSError:
                pass


def get_page_ocr(
    page: pdfium.PdfPage,
    work_dir: Path | None = None,
    scale: float = 2.0,
) -> tuple[str, str]:
    """
    Get OCR text for a PDF page, cached by page_hash in CACHE/ocr/<page_hash>.json (§2.15).
    Returns (ocr_text, page_hash).
    """
    pil_image = page.render(scale=scale).to_pil()
    page_hash = compute_image_page_hash(pil_image)

    cache_file = cache_ocr_path(page_hash)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return cached.get("text", ""), page_hash
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Error reading OCR cache %s: %s", cache_file, e)

    # Cache miss: run ocrmac
    ocr_text = run_ocr(pil_image, work_dir=work_dir)

    # Save to cache
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_cache = cache_file.with_suffix(".tmp")
        tmp_cache.write_text(
            json.dumps({"page_hash": page_hash, "text": ocr_text}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_cache.replace(cache_file)
    except OSError as ce:
        logger.warning("Error writing OCR cache %s: %s", cache_file, ce)

    return ocr_text, page_hash
