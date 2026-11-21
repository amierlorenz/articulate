"""Pocket REST API client (§3.2, §3.3, Issue #15, Issue #55)."""
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from articulate.core.secrets import SecretNotFoundError, get_pocket_key

logger = logging.getLogger(__name__)


def _parse_timestamp(val: Any) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        if val.lstrip("-").isdigit():
            return float(val)
        try:
            clean = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.timestamp()
        except ValueError:
            pass
    return 0.0


class TokenRejectedError(Exception):
    """Raised when Pocket API key is rejected (HTTP 401 or 403)."""

    def __init__(self, message: str = "token_rejected"):
        super().__init__(message)


class PocketClient:
    """Read-only Pocket client for recordings, transcripts, folders, and tags."""

    def __init__(
        self,
        token: str | None = None,
        api_base: str = "https://public.heypocketai.com",
        health: Any = None,
        timeout_s: float = 15.0,
    ):
        if token is not None:
            self._token = token
        else:
            try:
                self._token = get_pocket_key()
            except SecretNotFoundError:
                self._token = ""

        self.api_base = api_base.rstrip("/")
        self.health = health
        self.timeout_s = timeout_s
        self.request_count = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": "articulate",
            "Accept": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _raise_token_rejected(self, detail: str) -> None:
        if self.health is not None:
            try:
                self.health.raise_("token_rejected", key="pocket_api_key", detail=detail, interrupt=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not record token_rejected health incident: %s", e)
        raise TokenRejectedError(f"token_rejected: {detail}")

    def _url(self, path: str) -> str:
        base = self.api_base.rstrip("/")
        if not base.endswith("/api/v1"):
            base = f"{base}/api/v1"
        clean_path = "/" + path.lstrip("/")
        return f"{base}{clean_path}"

    def _request_raw(self, method: str, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        url = self._url(path)
        with httpx.Client(timeout=self.timeout_s) as http:
            self.request_count += 1
            return http.request(method, url, headers=self._headers(), params=params)

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._request_raw(method, path, params=params)

        # Handle 429 Rate Limit
        if resp.status_code == 429:
            reset_header = resp.headers.get("X-Ratelimit-Reset")
            if reset_header:
                try:
                    reset_epoch = float(reset_header)
                    now_epoch = time.time()
                    sleep_sec = max(0.0, reset_epoch - now_epoch)
                    logger.warning("Pocket rate limited (429). Sleeping %0.2f s until reset.", sleep_sec)
                    time.sleep(sleep_sec)
                    resp = self._request_raw(method, path, params=params)
                except ValueError:
                    pass

        # Handle 401 / 403 Token Rejection
        if resp.status_code in (401, 403):
            self._raise_token_rejected(f"Pocket API returned {resp.status_code}: {resp.text}")

        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("success") is False:
            raise RuntimeError(f"Pocket API error: {data.get('error', 'unknown error')}")
        return data

    def get_folders(self) -> list[dict[str, Any]]:
        """GET /public/folders: returns list of folder/space nodes."""
        data = self._request("GET", "/public/folders")
        return data.get("data", [])

    def get_tags(self) -> list[dict[str, Any]]:
        """GET /public/tags: returns list of tag objects."""
        data = self._request("GET", "/public/tags")
        return data.get("data", [])

    def list_recordings(
        self,
        lookback_days: int = 3,
        limit: int = 20,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """
        GET /public/recordings: page through recordings until lookback window or has_more is exhausted.
        """
        cutoff_dt = datetime.now(UTC) - timedelta(days=lookback_days)
        cutoff_epoch = int(cutoff_dt.timestamp())

        recordings: list[dict[str, Any]] = []
        page = 1

        while page <= max_pages:
            data = self._request("GET", "/public/recordings", params={"page": page, "limit": limit})
            items = data.get("data", [])
            if not items:
                break

            recordings.extend(items)

            # Check if oldest item on this page is older than cutoff
            oldest_on_page = min(
                _parse_timestamp(it.get("recording_at", it.get("created_at", 0)))
                for it in items
            )
            pagination = data.get("pagination", {})
            has_more = pagination.get("has_more", False)

            if not has_more or oldest_on_page < cutoff_epoch:
                break

            page += 1

        # Filter recordings within lookback window
        return [
            rec for rec in recordings
            if _parse_timestamp(rec.get("recording_at", rec.get("created_at", 0))) >= cutoff_epoch
        ]

    def get_recording(self, recording_id: str) -> dict[str, Any]:
        """GET /public/recordings/{id}?include_transcript=true&include_summarizations=true."""
        return self._request(
            "GET",
            f"/public/recordings/{recording_id}",
            params={"include_transcript": "true", "include_summarizations": "true"},
        )
