"""Vision-language page transcription using qwen3-vl:8b-instruct with caching (§3.5, §3.6, #35)."""
import base64
import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.models.runner import Held, ModelRequest, ModelRunner, Part
from articulate.core.paths import get_data_dir
from articulate.core.pipeline import Privacy

logger = logging.getLogger(__name__)

# Fallback cache directory if paths.py is overridden or patched
CACHE_DIR = get_data_dir() / "cache"


def vl_cache_path(page_hash: str) -> Path:
    """Path to cached VL transcript: CACHE/vl/<page_hash>.json (§3.1, §3.5)."""
    # Use CACHE_DIR so test monkeypatching works cleanly
    return CACHE_DIR / "vl" / f"{page_hash}.json"


def get_cached_vl_transcript(page_hash: str) -> str | None:
    """Retrieve cached VL transcript text if available."""
    cp = vl_cache_path(page_hash)
    if cp.exists():
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
            return data.get("text")
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed reading VL cache at %s: %s", cp, e)
    return None


def save_cached_vl_transcript(page_hash: str, text: str, model: str = "qwen3-vl:8b-instruct") -> None:
    """Save transcript text to cache."""
    cp = vl_cache_path(page_hash)
    cp.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "text": text,
        "model": model,
        "cached_at": datetime.now(UTC).isoformat(),
    }
    cp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_vl_prompt() -> str:
    """Read prompts/vl-transcribe.md or fallback to default prompt."""
    prompt_file = Path(__file__).resolve().parents[4] / "prompts" / "vl-transcribe.md"
    if prompt_file.exists():
        try:
            return prompt_file.read_text(encoding="utf-8").strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not read prompt file: %s", e)
    return (
        "Transcribe everything written on this slide, in reading order, exactly as written. "
        "Include text inside images, tables and diagrams. Write every formula and math symbol as LaTeX in $...$. "
        "Do not explain, summarise or add anything that is not on the slide."
    )


def record_ollama_down(db: Database, flags: Flags | None = None, subject: str = "Ollama down during VL transcription") -> None:
    """Record health incident and intake flag when Ollama is unreachable (§3.6, §3.9, #35)."""
    now_str = datetime.now(UTC).isoformat()
    try:
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO health (kind, key, detail, interrupt, first_seen, last_seen)
                VALUES ('ollama_down', 'ollama', 'Ollama connection refused: service is down', 0, ?, ?)
                ON CONFLICT(kind, key, first_seen) DO UPDATE SET last_seen = excluded.last_seen;
                """,
                (now_str, now_str),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not write health row for ollama_down: %s", e)

    if flags is not None:
        try:
            flags.add(
                kind="intake.ollama_down",
                subject=subject,
                choices=[("retry", "retry next run"), ("skip", "skip VL check")],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not add flag for ollama_down: %s", e)


def render_pdf_page_png(pdf_path: Path, page_num: int, scale: float = 2.0) -> bytes | None:
    """Render a PDF page to PNG bytes at 2x scale using pypdfium2."""
    if not pdf_path.exists():
        return None
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(pdf_path))
        if not (1 <= page_num <= len(doc)):
            return None
        page = doc[page_num - 1]
        buf = io.BytesIO()
        page.render(scale=scale).to_pil().save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed rendering PDF page %d of %s: %s", page_num, pdf_path, e)
        return None


def transcribe_page(
    file_path: Path | None,
    page_num: int,
    page_hash: str,
    db: Database | None = None,
    models: ModelRunner | None = None,
    flags: Flags | None = None,
    file_id: str | None = None,
) -> str | None:
    """
    Transcribe a page using local vision-language model (qwen3-vl:8b-instruct) with caching (§3.5, #35).
    Runs only on still-failing pages, cached by page_hash.
    """
    # 1. Check cache first
    cached = get_cached_vl_transcript(page_hash)
    if cached is not None:
        if db and file_id:
            try:
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE pages SET vl_text = ? WHERE file_id = ? AND page = ?;",
                        (cached, file_id, page_num),
                    )
                    conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.debug("Failed updating pages table from cache: %s", e)
        return cached

    # 2. If no models runner, cannot transcribe
    if not models:
        return None

    # 3. Render page to image if file_path exists
    img_bytes = render_pdf_page_png(file_path, page_num, scale=2.0) if file_path and file_path.exists() else None
    prompt_text = _get_vl_prompt()

    # 4. Call model runner step vl_transcribe
    try:
        parts = [Part(text=prompt_text)]
        if img_bytes:
            parts.append(Part(text=f"data:image/png;base64,{base64.b64encode(img_bytes).decode()}"))
        req = ModelRequest(
            step="vl_transcribe",
            material=Privacy.CLASS,
            parts=parts,
        )
        res = models.run(req)
        text = res.text.strip()
        if text:
            save_cached_vl_transcript(page_hash, text)
            if db and file_id:
                try:
                    with db.connect() as conn:
                        conn.execute(
                            "UPDATE pages SET vl_text = ? WHERE file_id = ? AND page = ?;",
                            (text, file_id, page_num),
                        )
                        conn.commit()
                except Exception as e:  # noqa: BLE001
                    logger.debug("Failed updating pages table after VL transcribe: %s", e)
            return text

    except Held as e:
        if e.reason == "ollama_down" and db:
            record_ollama_down(
                db,
                flags=flags,
                subject=f"Ollama down transcribing page {page_num} of {file_id or file_path.name}",
            )
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("VL transcription failed: %s", e)
        if db and "connection refused" in str(e).lower():
            record_ollama_down(
                db,
                flags=flags,
                subject=f"Ollama down transcribing page {page_num} of {file_id or file_path.name}",
            )
        return None

    return None
