"""DeckCraft PPTX converter and fallback detection (§2.11, §2.12, #28)."""

import json
import logging
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

logger = logging.getLogger(__name__)


class DeckCraftError(Exception):
    """General DeckCraft communication or process error."""


class DeckCraftFallbackError(Exception):
    """Raised when a DeckCraft conversion must fall back to Keynote (§2.12)."""


def get_free_port() -> int:
    """Find an available local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout_s: float = 10.0) -> bool:
    """Wait up to timeout_s for port to become reachable."""
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except (TimeoutError, OSError):
            time.sleep(0.05)
    return False


def launch_deckcraft(port: int) -> subprocess.Popen:
    """
    Launch DeckCraft with DECKCRAFT_NO_PREFS=1 and DECKCRAFT_NO_RECOVERY=1 (§2.11).
    Uses open -g -j -n --env ... --args --control <port>.
    """
    cmd = [
        "/usr/bin/open",
        "-g",
        "-j",
        "-n",
        "--env",
        "DECKCRAFT_NO_PREFS=1",
        "--env",
        "DECKCRAFT_NO_RECOVERY=1",
        "-a",
        "/Applications/DeckCraft.app",
        "--args",
        "--control",
        str(port),
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DeckCraftClient:
    """Communicates with DeckCraft over its JSON control socket (§2.11, #28)."""

    def __init__(self, port: int | None = None, host: str = "127.0.0.1"):
        self.port = port or get_free_port()
        self.host = host

    def _call(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        payload = json.dumps({"id": 1, "method": method, "params": params or {}}) + "\n"
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout) as s:
                f = s.makefile("rw")
                f.write(payload)
                f.flush()
                response = f.readline()
                if not response:
                    raise DeckCraftError("DeckCraft closed socket without response")
                return json.loads(response)
        except (TimeoutError, OSError) as e:
            raise DeckCraftError(f"Socket call failed: {e}") from e

    def cmd(self, command_id: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        """Issue a command method: {"method":"command", "params":{"id": cid, "params": {…}}}."""
        return self._call("command", {"id": command_id, "params": params or {}}, timeout=timeout)

    def quit(self) -> None:
        """Quit the DeckCraft instance."""
        try:
            self._call("app.quit", timeout=5.0)
        except (DeckCraftError, OSError) as e:
            logger.debug("Failed to quit DeckCraft: %s", e)

    def convert(self, pptx_path: Path, out_pdf_path: Path) -> tuple[dict[str, Any], int]:
        """
        Convert PPTX to PDF using DeckCraft, checking all 5 fallback triggers (§2.12).
        Returns (inspect_data, pages_exported).
        Raises DeckCraftFallbackError on any fallback trigger.
        """
        pptx_path = Path(pptx_path).resolve()
        out_pdf_path = Path(out_pdf_path).resolve()
        out_pdf_path.parent.mkdir(parents=True, exist_ok=True)

        # Trigger 1: Port not up in 10 s or process exit
        if not wait_for_port(self.port, timeout_s=10.0):
            self.quit()
            raise DeckCraftFallbackError("DeckCraft port not up within 10 s or process exited")

        try:
            # Step 1: file.open
            r_open = self.cmd("file.open", {"path": str(pptx_path)}, timeout=60.0)
            if not r_open.get("ok"):
                raise DeckCraftFallbackError(f"DeckCraft file.open failed: {r_open.get('error')}")

            res_open = r_open.get("result", {})
            lost_slides = res_open.get("lostSlides") or []
            damaged_media = res_open.get("damagedMedia") or []

            # Trigger 3: lostSlides or damagedMedia non-empty
            if lost_slides or damaged_media:
                raise DeckCraftFallbackError(
                    f"DeckCraft reported lostSlides or damagedMedia: lost={lost_slides}, damaged={damaged_media}"
                )

            # Step 2: document.inspect (title, notes, hidden, builds)
            r_inspect = self.cmd("document.inspect", timeout=60.0)
            inspect_data = r_inspect.get("result", {}) if r_inspect.get("ok") else {}
            slides_inspect = inspect_data.get("slides") or []
            expected_slides = len(slides_inspect) or res_open.get("slides") or 0

            # Step 3: file.export PDF with includeHidden:true
            r_export = self.cmd(
                "file.export",
                {"path": str(out_pdf_path), "includeHidden": True},
                timeout=60.0,
            )
            if not r_export.get("ok"):
                raise DeckCraftFallbackError(
                    f"DeckCraft file.export failed: {r_export.get('error')}"
                )

            # Step 4: file.close
            self.cmd("file.close", timeout=10.0)

        except (DeckCraftError, TimeoutError) as e:
            # Trigger 2: call times out (60 s) or error
            raise DeckCraftFallbackError(f"DeckCraft timeout or socket error: {e}") from e
        finally:
            self.quit()

        # Trigger 4: PDF page count != slide count from document.inspect
        if not out_pdf_path.exists() or out_pdf_path.stat().st_size == 0:
            raise DeckCraftFallbackError(f"DeckCraft did not produce output PDF at {out_pdf_path}")

        try:
            doc = pdfium.PdfDocument(out_pdf_path)
            actual_pages = len(doc)
        except Exception as pe:
            raise DeckCraftFallbackError(f"Failed to read exported PDF with pdfium: {pe}") from pe

        if expected_slides > 0 and actual_pages != expected_slides:
            raise DeckCraftFallbackError(
                f"PDF page count mismatch: expected {expected_slides} slides, got {actual_pages} pages"
            )

        # Trigger 5: empty text layer on >20 % of titled slides
        titled_slides = 0
        empty_text_on_titled = 0

        for i, s_meta in enumerate(slides_inspect):
            title = (s_meta.get("title") or "").strip()
            if title:
                titled_slides += 1
                if i < actual_pages:
                    page_text = doc[i].get_textpage().get_text_range().strip()
                    if not page_text:
                        empty_text_on_titled += 1

        if titled_slides > 0 and (empty_text_on_titled / titled_slides) > 0.20:
            raise DeckCraftFallbackError(
                f"DeckCraft produced empty text layer on >20% of titled slides "
                f"({empty_text_on_titled}/{titled_slides} = {empty_text_on_titled / titled_slides:.1%})"
            )

        return inspect_data, actual_pages


def convert_pptx_deckcraft(pptx_path: Path, out_pdf_path: Path) -> tuple[dict[str, Any], int]:
    """Convenience function: launches DeckCraft on a free port and converts PPTX."""
    port = get_free_port()
    launch_deckcraft(port)
    client = DeckCraftClient(port=port)
    return client.convert(pptx_path, out_pdf_path)
