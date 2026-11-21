"""File routing check (§3.6, Issue #32, SPEC-requirements 2.6)."""
import logging
import re
from pathlib import Path
from typing import Any

from articulate.core.models.runner import ModelRequest, Part
from articulate.core.pipeline import Privacy

logger = logging.getLogger(__name__)

OTHER_EXTENSIONS = {
    ".xlsx",
    ".xls",
    ".csv",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".mp3",
    ".wav",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
}

GUIDE_KEYWORDS = re.compile(r"\b(guide|review|exam|test|practice|sample|past|solution|answer)\b", re.IGNORECASE)
SLIDES_KEYWORDS = re.compile(r"\b(lecture|slide|deck|presentation|chapter|ch\d+|week|unit)\b", re.IGNORECASE)


def extract_sample_text(file_path: Path, max_pages: int = 2) -> str:
    """Extract sample text from first two pages of PDF, or initial text chunk."""
    if not file_path.exists():
        return ""

    if file_path.suffix.lower() == ".pdf":
        try:
            import pypdfium2 as pdfium

            doc = pdfium.PdfDocument(str(file_path))
            pages_text: list[str] = []
            for i in range(min(max_pages, len(doc))):
                page = doc[i]
                textpage = page.get_textpage()
                pages_text.append(textpage.get_text_range())
            return "\n--- Page Break ---\n".join(pages_text)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed reading PDF sample text with pypdfium2: %s", e)
            return ""

    try:
        return file_path.read_text(encoding="utf-8", errors="ignore")[:3000]
    except Exception:  # noqa: BLE001
        return ""


def route_file(
    file_path: Path,
    filename: str,
    folder_hint: str = "",
    models: Any = None,
) -> str:
    """
    Classify a course file into 'slides', 'guideOrExam', or 'other' (§3.6, SPEC 2.6).
    .xlsx, video, images, audio are automatically 'other'.
    """
    ext = Path(filename).suffix.lower()
    if ext in OTHER_EXTENSIONS:
        return "other"

    sample_text = extract_sample_text(file_path, max_pages=2)

    if models:
        req = ModelRequest(
            step="route_file",
            material=Privacy.CLASS,
            parts=[
                Part(
                    text=f"Filename: {filename}\nFolder hint: {folder_hint}\nSample text:\n{sample_text}"
                )
            ],
            json_schema={
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "pattern": "^(slides|guideOrExam|other)$",
                    },
                    "reason": {
                        "type": "string",
                        "pattern": "^[^\t\x08\x0c]*$",
                    },
                },
                "required": ["role"],
            },
        )
        try:
            res = models.run(req)
            if isinstance(res.parsed, dict) and "role" in res.parsed:
                role = res.parsed["role"]
                if role in ("slides", "guideOrExam", "other"):
                    return role
        except Exception as e:  # noqa: BLE001
            logger.warning("Flash Lite route_file call failed: %s; falling back to heuristic", e)

    # Heuristic fallback
    combined = f"{filename} {folder_hint} {sample_text[:500]}"
    if GUIDE_KEYWORDS.search(combined):
        return "guideOrExam"
    if SLIDES_KEYWORDS.search(combined):
        return "slides"

    return "other"
