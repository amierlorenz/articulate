"""Canvas API REST Client (§3.3, §3.14, Issue #24)."""
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx

from articulate.core.secrets import SecretNotFoundError, get_canvas_token

logger = logging.getLogger(__name__)


class CanvasError(Exception):
    """Base exception for Canvas API errors."""


class CanvasAuthError(CanvasError):
    """Raised when Canvas returns 401 or 403 on authentication/token."""


class CanvasNetworkError(CanvasError):
    """Raised when network is unreachable or times out."""


class CanvasFilesHiddenError(CanvasError):
    """Raised when the Files tab returns 403 (hidden by instructor)."""


def _get_default_base_url() -> str:
    """Resolve Canvas base URL from env, canvas-cli config, or fallback."""
    env = os.environ.get("CANVAS_BASE_URL")
    if env:
        return env.rstrip("/")

    # Check ~/.config/canvas-cli/config.yaml
    cfg_path = Path("~/.config/canvas-cli/config.yaml").expanduser()
    if cfg_path.exists():
        try:
            import yaml  # noqa: F401
            # Simple text extraction to avoid dependency if yaml is absent
            text = cfg_path.read_text(encoding="utf-8")
            m = re.search(r'canvasdomain:\s*["\']?([^"\'\s\n]+)', text)
            if m:
                return m.group(1).rstrip("/")
        except Exception:  # noqa: BLE001, S110
            pass

    return "https://hamptonu.instructure.com"


class CanvasClient:
    """Read-only client for the Canvas REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or _get_default_base_url()).rstrip("/")
        if token:
            self._token = token
        else:
            try:
                self._token = get_canvas_token()
            except SecretNotFoundError:
                self._token = os.environ.get("CANVAS_TOKEN", "")

        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        allow_403: bool = False,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                if method.upper() == "GET":
                    resp = client.get(url, headers=self._headers(), params=params)
                else:
                    resp = client.request(method, url, headers=self._headers(), params=params)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            raise CanvasNetworkError(f"Network error connecting to Canvas: {e}") from e

        if resp.status_code in (401, 403):
            if allow_403 and resp.status_code == 403:
                return resp
            # 401 or unauthorized token error
            raise CanvasAuthError(f"Canvas authentication failed ({resp.status_code}): {resp.text[:200]}")

        if resp.status_code >= 400:
            raise CanvasError(f"Canvas API error ({resp.status_code}): {resp.text[:200]}")

        return resp

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Paginate through Canvas API results."""
        results: list[dict[str, Any]] = []
        req_params = dict(params or {})
        req_params.setdefault("per_page", 100)
        page = 1

        while True:
            req_params["page"] = page
            resp = self._request("GET", path, params=req_params)
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            results.extend(data)
            # Check Link header for 'rel="next"'
            link_hdr = resp.headers.get("Link", "")
            if 'rel="next"' not in link_hdr and len(data) < req_params["per_page"]:
                break
            page += 1

        return results

    def get_course(self, course_id: int) -> dict[str, Any]:
        """Fetch course details."""
        resp = self._request("GET", f"/api/v1/courses/{course_id}")
        return resp.json()

    def list_modules(self, course_id: int) -> list[dict[str, Any]]:
        """List course modules with items included."""
        return self._paginate(f"/api/v1/courses/{course_id}/modules", params={"include[]": "items"})

    def get_page(self, course_id: int, page_url: str) -> dict[str, Any]:
        """Fetch page details including HTML body."""
        resp = self._request("GET", f"/api/v1/courses/{course_id}/pages/{page_url}")
        return resp.json()

    def list_files(self, course_id: int) -> list[dict[str, Any]]:
        """
        List course files.
        If Files tab is hidden (403), returns empty list without raising.
        """
        try:
            resp = self._request("GET", f"/api/v1/courses/{course_id}/files", params={"per_page": 100}, allow_403=True)
            if resp.status_code == 403:
                logger.info("Files tab is hidden (403) for course %s", course_id)
                return []
            data = resp.json()
            return data if isinstance(data, list) else []
        except CanvasAuthError:
            # If allow_403 was not triggered or global token failed
            return []

    def list_folders(self, course_id: int) -> list[dict[str, Any]]:
        """List folders for folder path and hint resolution."""
        try:
            resp = self._request("GET", f"/api/v1/courses/{course_id}/folders", params={"per_page": 100}, allow_403=True)
            if resp.status_code == 403:
                return []
            data = resp.json()
            return data if isinstance(data, list) else []
        except CanvasAuthError:
            return []

    def list_assignments(self, course_id: int) -> list[dict[str, Any]]:
        """List course assignments with assignment_group included."""
        return self._paginate(f"/api/v1/courses/{course_id}/assignments", params={"include[]": "assignment_group"})

    def list_announcements(self, course_id: int, start_date: str | None = None) -> list[dict[str, Any]]:
        """List announcements for a course."""
        params: dict[str, Any] = {"context_codes[]": f"course_{course_id}", "per_page": 100}
        if start_date:
            params["start_date"] = start_date

        try:
            resp = self._request("GET", "/api/v1/announcements", params=params)
            data = resp.json()
            if isinstance(data, list):
                return data
        except CanvasError:
            pass

        # Fallback to discussion_topics?only_announcements=true
        try:
            return self._paginate(f"/api/v1/courses/{course_id}/discussion_topics", params={"only_announcements": "true"})
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not list announcements for course %s: %s", course_id, e)
            return []

    def download_file(self, download_url: str, dest_path: Path) -> Path:
        """Download a file from Canvas to dest_path."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with (
                httpx.Client(timeout=self.timeout, follow_redirects=True) as client,
                client.stream("GET", download_url, headers=self._headers()) as resp,
            ):
                if resp.status_code >= 400:
                    raise CanvasError(f"Failed to download file: HTTP {resp.status_code}")
                with open(dest_path, "wb") as f:
                    f.writelines(resp.iter_bytes(chunk_size=8192))
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            raise CanvasNetworkError(f"Network error downloading file: {e}") from e

        return dest_path
