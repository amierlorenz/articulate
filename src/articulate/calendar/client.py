"""Google Calendar read-only client (§3.2, §3.3, Issue #54)."""
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from articulate.core.config import CalendarConfig, load_articulate_config
from articulate.core.secrets import get_google_oauth

logger = logging.getLogger(__name__)


class TokenRejectedError(Exception):
    """Raised when Google OAuth token refresh fails or token is rejected (§3.3, Issue #54)."""

    def __init__(self, message: str = "token_rejected"):
        super().__init__(message)


class CalendarEvent(BaseModel):
    """Represents a calendar event (§3.2, Issue #54)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    summary: str = ""
    description: str = ""
    start: datetime
    end: datetime
    calendar: str = ""
    recurring_event_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], calendar_name: str = "") -> "CalendarEvent":
        """Parse from Google Calendar API format or flat dict."""
        evt_id = str(data.get("id", ""))
        summary = str(data.get("summary", data.get("title", "")))
        description = str(data.get("description", ""))

        start_val = data.get("start")
        if isinstance(start_val, dict):
            if "dateTime" in start_val:
                start_dt = datetime.fromisoformat(start_val["dateTime"])
            elif "date" in start_val:
                start_dt = datetime.fromisoformat(start_val["date"]).replace(tzinfo=UTC)
            else:
                start_dt = datetime.now(UTC)
        elif isinstance(start_val, str):
            start_dt = datetime.fromisoformat(start_val)
        elif isinstance(start_val, datetime):
            start_dt = start_val
        else:
            start_dt = datetime.now(UTC)

        end_val = data.get("end")
        if isinstance(end_val, dict):
            if "dateTime" in end_val:
                end_dt = datetime.fromisoformat(end_val["dateTime"])
            elif "date" in end_val:
                end_dt = datetime.fromisoformat(end_val["date"]).replace(tzinfo=UTC)
            else:
                end_dt = start_dt
        elif isinstance(end_val, str):
            end_dt = datetime.fromisoformat(end_val)
        elif isinstance(end_val, datetime):
            end_dt = end_val
        elif "duration" in data and isinstance(data["duration"], (int, float)):
            end_dt = start_dt + timedelta(seconds=float(data["duration"]))
        else:
            end_dt = start_dt

        cal = str(data.get("calendar", calendar_name))
        rec_id = data.get("recurringEventId")

        return cls(
            id=evt_id,
            summary=summary,
            description=description,
            start=start_dt,
            end=end_dt,
            calendar=cal,
            recurring_event_id=rec_id,
        )


class CalendarClient:
    """Read-only Google Calendar client using Google OAuth credentials (§3.3, Issue #54)."""

    def __init__(
        self,
        credentials: dict[str, Any] | None = None,
        config: CalendarConfig | None = None,
        health: Any = None,
    ):
        self._creds = credentials if credentials is not None else get_google_oauth()
        if config is not None:
            self.config = config
        else:
            try:
                self.config = load_articulate_config().calendar
            except Exception:  # noqa: BLE001
                self.config = CalendarConfig()
        self.health = health
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    def get_access_token(self) -> str:
        """Fetch or refresh the Google OAuth access token using refresh token."""
        now = datetime.now(UTC)
        if self._access_token and self._token_expires_at and now < self._token_expires_at:
            return self._access_token

        token_uri = self._creds.get("token_uri", "https://oauth2.googleapis.com/token")
        client_id = self._creds.get("client_id")
        client_secret = self._creds.get("client_secret")
        refresh_token = self._creds.get("refresh_token")

        if not refresh_token:
            self._raise_token_rejected("Missing refresh_token in credentials")

        try:
            with httpx.Client(timeout=15.0) as http:
                resp = http.post(
                    token_uri,
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
        except Exception as e:
            logger.error("Network error during token refresh: %s", e)
            raise

        if resp.status_code in (400, 401, 403):
            detail = resp.text
            with contextlib.suppress(Exception):
                err_json = resp.json()
                detail = err_json.get("error_description", err_json.get("error", detail))
            self._raise_token_rejected(f"Google token refresh failed ({resp.status_code}): {detail}")

        if resp.status_code != 200:
            raise RuntimeError(f"Unexpected token refresh response status {resp.status_code}: {resp.text}")

        data = resp.json()
        token = data.get("access_token")
        if not token:
            self._raise_token_rejected("No access_token returned in refresh response")

        expires_in = int(data.get("expires_in", 3600))
        self._access_token = token
        self._token_expires_at = now + timedelta(seconds=max(expires_in - 60, 60))
        return self._access_token

    def _raise_token_rejected(self, detail: str) -> None:
        if self.health:
            with contextlib.suppress(Exception):
                self.health.raise_("token_rejected", key="google_oauth", detail=detail, interrupt=True)
        raise TokenRejectedError(f"token_rejected: {detail}")

    def list_calendars(self) -> list[dict[str, Any]]:
        """List all calendars visible to the user."""
        token = self.get_access_token()
        url = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
        headers = {"Authorization": f"Bearer {token}"}

        items: list[dict[str, Any]] = []
        page_token: str | None = None

        with httpx.Client(timeout=15.0) as http:
            while True:
                params: dict[str, Any] = {}
                if page_token:
                    params["pageToken"] = page_token
                resp = http.get(url, headers=headers, params=params)
                if resp.status_code in (401, 403):
                    self._raise_token_rejected(f"Calendar list rejected: {resp.text}")
                resp.raise_for_status()

                data = resp.json()
                items.extend(data.get("items", []))
                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        return items

    def get_calendar_id(self, calendar_name_or_id: str) -> str:
        """Resolve a calendar name (e.g. 'Classes') to its Google Calendar ID."""
        if calendar_name_or_id.lower() == "primary":
            return "primary"

        calendars = self.list_calendars()
        for cal in calendars:
            if cal.get("summary") == calendar_name_or_id or cal.get("id") == calendar_name_or_id:
                return cal["id"]

        # If not found by summary, assume calendar_name_or_id is already the calendar ID
        return calendar_name_or_id

    def list_events(
        self,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[CalendarEvent]:
        """List events in the given calendar within the specified time range."""
        actual_cal_id = self.get_calendar_id(calendar_id)
        token = self.get_access_token()

        # Format ISO with Z if UTC
        t_min_str = time_min.isoformat()
        t_max_str = time_max.isoformat()

        import urllib.parse

        encoded_id = urllib.parse.quote(actual_cal_id, safe="")
        url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_id}/events"
        headers = {"Authorization": f"Bearer {token}"}

        events: list[CalendarEvent] = []
        page_token: str | None = None

        with httpx.Client(timeout=15.0) as http:
            while True:
                params: dict[str, Any] = {
                    "timeMin": t_min_str,
                    "timeMax": t_max_str,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                }
                if page_token:
                    params["pageToken"] = page_token

                resp = http.get(url, headers=headers, params=params)
                if resp.status_code in (401, 403):
                    self._raise_token_rejected(f"List events rejected: {resp.text}")
                resp.raise_for_status()

                data = resp.json()
                for item in data.get("items", []):
                    # Filter out cancelled events
                    if item.get("status") == "cancelled":
                        continue
                    events.append(CalendarEvent.from_dict(item, calendar_name=calendar_id))

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        return events
