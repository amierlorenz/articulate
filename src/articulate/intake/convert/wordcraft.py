"""Word document (.docx, .doc, .rtf) conversion using wordcraft-cli (§2.14)."""

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class WordCraftError(Exception):
    """Raised when wordcraft-cli conversion fails."""


def convert_wordcraft(input_path: Path, output_pdf_path: Path) -> None:
    """
    Converts .docx, .doc, or .rtf to PDF using wordcraft-cli (§2.14).
    """
    input_path = Path(input_path).resolve()
    output_pdf_path = Path(output_pdf_path).resolve()
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    wordcraft_bin = shutil.which("wordcraft-cli") or "/opt/homebrew/bin/wordcraft-cli"

    cmd = [
        wordcraft_bin,
        "convert",
        str(input_path),
        str(output_pdf_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except Exception as e:
        raise WordCraftError(f"Failed to invoke wordcraft-cli: {e}") from e

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        raise WordCraftError(f"wordcraft-cli convert failed with code {proc.returncode}: {err}")

    if not output_pdf_path.exists() or output_pdf_path.stat().st_size == 0:
        raise WordCraftError(f"wordcraft-cli did not produce an output PDF at {output_pdf_path}")
