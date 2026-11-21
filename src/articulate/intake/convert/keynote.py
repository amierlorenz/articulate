"""Keynote fallback conversion for PPTX files (§2.12, #21)."""

import logging
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class KeynoteHoldError(Exception):
    """Raised when conversion must hold (keynote_presenting or unconsented_deck)."""

    def __init__(self, hold_reason: str, message: str = ""):
        super().__init__(f"{hold_reason}: {message}" if message else f"Keynote hold: {hold_reason}")
        self.hold_reason = hold_reason


class KeynoteExportError(Exception):
    """Raised when Keynote export fails."""


def is_keynote_presenting() -> bool:
    """Check if Keynote is running and currently presenting a slideshow (§2.12, #21)."""
    script = 'tell application "Keynote" to playing'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return proc.returncode == 0 and proc.stdout.strip().lower() == "true"
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("Error checking if Keynote is playing: %s", e)
        return False


def convert_pptx_keynote(pptx_path: Path, output_pdf_path: Path) -> int:
    """
    Export PPTX to PDF using Keynote via osascript (§2.12, #21).
    Returns slide count.
    Raises KeynoteHoldError on keynote_presenting or unconsented_deck.
    """
    pptx_path = Path(pptx_path).resolve()
    output_pdf_path = Path(output_pdf_path).resolve()
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Presenting check: hold until next night
    if is_keynote_presenting():
        raise KeynoteHoldError("keynote_presenting", "Keynote is currently presenting a slideshow")

    # 2. Output file must be created first (#21)
    output_pdf_path.touch(exist_ok=True)

    script_path = Path(__file__).parent / "keynote-export.applescript"

    try:
        proc = subprocess.run(
            ["osascript", str(script_path), str(pptx_path), str(output_pdf_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=660,
        )
    except subprocess.TimeoutExpired as te:
        raise KeynoteExportError(f"Keynote export timed out: {te}") from te
    except (zipfile.BadZipFile, OSError) as e:
        raise KeynoteExportError(f"Failed to invoke Keynote osascript: {e}") from e

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip()
        # Unconsented check: -1743 or "Not authorized to send Apple events"
        if "-1743" in err_msg or "Not authorized to send Apple events" in err_msg:
            raise KeynoteHoldError(
                "unconsented_deck", f"Keynote automation consent missing: {err_msg}"
            )
        raise KeynoteExportError(f"Keynote export failed ({proc.returncode}): {err_msg}")

    try:
        slide_count = int(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        slide_count = 0

    if not output_pdf_path.exists() or output_pdf_path.stat().st_size == 0:
        raise KeynoteExportError(f"Keynote export did not produce a valid PDF at {output_pdf_path}")

    return slide_count


def extract_pptx_notes_titles(pptx_path: Path) -> dict[str, Any]:
    """
    Extract slide titles and speaker notes directly from a PPTX zip file (§2.14, #5).
    Used as fallback metadata when DeckCraft inspect is unavailable.
    """
    slides_info = []
    try:
        with zipfile.ZipFile(pptx_path, "r") as zf:
            slide_names = sorted(
                [
                    n
                    for n in zf.namelist()
                    if n.startswith("ppt/slides/slide") and n.endswith(".xml")
                ],
                key=lambda x: int("".join(filter(str.isdigit, x)) or 0),
            )
            for idx, sname in enumerate(slide_names, start=1):
                title = ""
                notes = ""
                # Parse slide xml for title
                try:
                    sxml = zf.read(sname)
                    root = ET.fromstring(sxml)
                    # Look for title placeholder
                    ns = {
                        "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
                        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
                    }
                    for sp in root.findall(".//p:sp", ns):
                        ph = sp.find(".//p:ph", ns)
                        if ph is not None and ph.get("type") in ("title", "ctrTitle"):
                            text_parts = [t.text for t in sp.findall(".//a:t", ns) if t.text]
                            title = " ".join(text_parts).strip()
                            break
                    if not title:
                        all_t = [t.text for t in root.findall(".//a:t", ns) if t.text]
                        if all_t:
                            title = all_t[0].strip()
                except (ET.ParseError, KeyError, IndexError, OSError) as e:
                    logger.debug("Error parsing slide title: %s", e)

                # Parse notes slide xml
                note_name = f"ppt/notesSlides/notesSlide{idx}.xml"
                if note_name in zf.namelist():
                    try:
                        nxml = zf.read(note_name)
                        nroot = ET.fromstring(nxml)
                        ns = {
                            "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
                            "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
                        }
                        note_parts = [t.text for t in nroot.findall(".//a:t", ns) if t.text]
                        notes = " ".join(note_parts).strip()
                    except (ET.ParseError, KeyError, IndexError, OSError) as e:
                        logger.debug("Error parsing notes slide: %s", e)

                slides_info.append(
                    {
                        "index": idx,
                        "title": title,
                        "notes": notes,
                        "hidden": False,
                    }
                )
    except (zipfile.BadZipFile, OSError) as e:
        logger.debug("Could not inspect PPTX zip %s: %s", pptx_path, e)

    return {"slides": slides_info}
