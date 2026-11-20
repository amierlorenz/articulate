"""Tests for calendar client and recording classification (§3.2, §3.3, §3.5, Issue #54).

Acceptance checks:
- 1.3: private_events: recording overlapping a matching event or calendar (>=50% or >=20 min) is Private
- 1.5: Class match: overlap with a "Classes" event, largest overlap wins, course key from description first line,
       class window = start -> end + 10 min, rest returned as personal spans
- Non-class event match: returns title and time for meeting notes
- Token refresh: rejected token raises token_rejected
"""
import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from articulate.calendar.client import CalendarClient, CalendarEvent, TokenRejectedError
from articulate.calendar.match import (
    calculate_overlap,
    classify_recording,
    match_class_event,
    match_meeting_event,
    match_private_event,
    normalize_course_key,
    split_class_window,
)
from articulate.core.config import CalendarConfig, CoursesConfig
from articulate.core.pipeline import Privacy

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "calendar"


# --- Fixtures Loading Tests ---


def test_fixture_files_exist():
    assert (FIXTURES_DIR / "classes-event.json").exists()
    assert (FIXTURES_DIR / "overlapping-events.json").exists()
    assert (FIXTURES_DIR / "non-overlapping-events.json").exists()
    assert (FIXTURES_DIR / "private-events.json").exists()


# --- Unit Tests: Course Key Normalization ---


def test_normalize_course_key_plain():
    assert normalize_course_key("mat305\nRoom 238\nProbability and Statistics") == "mat305"
    assert normalize_course_key("MAT 305") == "mat305"
    assert normalize_course_key("CSC 301") == "csc301"
    assert normalize_course_key("CSC-382") == "csc382"


def test_normalize_course_key_html():
    assert normalize_course_key("CSC 430<br>Some details") == "csc430"
    assert normalize_course_key("<p>MAT 305</p>Room 101") == "mat305"
    assert normalize_course_key("<div>CSC 301</div>") == "csc301"
    assert normalize_course_key("<br><br>PHY 203<br>") == "phy203"


def test_normalize_course_key_empty_or_no_alpha():
    assert normalize_course_key("") is None
    assert normalize_course_key("   \n\n  ") is None
    assert normalize_course_key("--- @@@ !!!") is None


# --- Unit Tests: Overlap Calculation ---


def test_calculate_overlap_exact():
    # Recording: 14:00 to 15:00 (3600s)
    # Event: 14:00 to 15:00 (3600s)
    start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    overlap, pct = calculate_overlap(start, 3600, start, start + timedelta(hours=1))
    assert overlap == 3600.0
    assert pct == 1.0


def test_calculate_overlap_disjoint():
    start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    evt_start = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)
    overlap, pct = calculate_overlap(start, 3600, evt_start, evt_start + timedelta(hours=1))
    assert overlap == 0.0
    assert pct == 0.0


def test_calculate_overlap_50_percent():
    # Recording: 60 min (3600s)
    # Overlap: 30 min (1800s) -> 50%
    start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    evt_start = datetime(2026, 11, 20, 14, 30, tzinfo=UTC)
    overlap, pct = calculate_overlap(start, 3600, evt_start, evt_start + timedelta(hours=1))
    assert overlap == 1800.0
    assert pct == 0.5


def test_calculate_overlap_under_50_percent_under_20_min():
    # Recording: 60 min (3600s)
    # Overlap: 15 min (900s) -> 25%, 15 min < 20 min -> fails overlap rule
    start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    evt_start = datetime(2026, 11, 20, 14, 45, tzinfo=UTC)
    overlap, pct = calculate_overlap(start, 3600, evt_start, evt_start + timedelta(minutes=15))
    assert overlap == 900.0
    assert pct == 0.25


def test_calculate_overlap_under_50_percent_but_over_20_min():
    # Recording: 90 min (5400s)
    # Overlap: 25 min (1500s) -> 27.7%, but >= 20 min (1200s) -> passes overlap rule
    start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    evt_start = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    overlap, pct = calculate_overlap(start, 5400, evt_start, evt_start + timedelta(minutes=25))
    assert overlap == 1500.0
    assert pct < 0.5
    assert overlap >= 1200.0


# --- Acceptance Check 1.5: Class Match and Class Window ---


def test_class_match_from_fixture():
    classes_event_data = json.loads((FIXTURES_DIR / "classes-event.json").read_text())
    event = CalendarEvent.from_dict(classes_event_data, calendar_name="Classes")

    rec_start = datetime.fromisoformat("2026-11-20T17:35:00-05:00")
    rec_duration = 3600.0

    match = match_class_event(rec_start, rec_duration, [event], classes_calendar="Classes")
    assert match is not None
    matched_event, course_key, _overlap_sec = match
    assert course_key == "mat305"
    assert matched_event.id == "evt-class-01"


def test_class_match_largest_overlap_wins():
    rec_start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    rec_duration = 3600.0

    evt1 = CalendarEvent(
        id="evt-1",
        summary="Event 1",
        description="mat305\nFirst line",
        start=datetime(2026, 11, 20, 13, 30, tzinfo=UTC),
        end=datetime(2026, 11, 20, 14, 25, tzinfo=UTC),  # overlap 25 min
        calendar="Classes",
    )
    evt2 = CalendarEvent(
        id="evt-2",
        summary="Event 2",
        description="csc301\nFirst line",
        start=datetime(2026, 11, 20, 14, 15, tzinfo=UTC),
        end=datetime(2026, 11, 20, 15, 0, tzinfo=UTC),  # overlap 45 min
        calendar="Classes",
    )

    match = match_class_event(rec_start, rec_duration, [evt1, evt2], classes_calendar="Classes")
    assert match is not None
    matched_event, course_key, overlap_sec = match
    assert matched_event.id == "evt-2"
    assert course_key == "csc301"
    assert overlap_sec == 45 * 60.0


def test_class_window_split_inside_window():
    event = CalendarEvent(
        id="evt-class",
        summary="CSC 382 Lecture",
        description="csc382\nDetails",
        start=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 15, 15, tzinfo=UTC),
        calendar="Classes",
    )
    rec_start = datetime(2026, 11, 20, 14, 5, tzinfo=UTC)
    rec_duration = 70 * 60.0  # ends at 15:15

    spans = split_class_window(rec_start, rec_duration, event, course_key="csc382")
    assert len(spans) == 1
    assert spans[0].privacy == Privacy.CLASS
    assert spans[0].course_key == "csc382"
    assert spans[0].start == rec_start
    assert spans[0].end == rec_start + timedelta(seconds=rec_duration)


def test_class_window_split_with_lead_in_and_tail():
    event = CalendarEvent(
        id="evt-class",
        summary="CSC 382 Lecture",
        description="csc382\nDetails",
        start=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
        calendar="Classes",
    )
    rec_start = datetime(2026, 11, 20, 13, 50, tzinfo=UTC)
    rec_duration = 100 * 60.0

    spans = split_class_window(rec_start, rec_duration, event, course_key="csc382")
    assert len(spans) == 3

    # Lead-in
    assert spans[0].privacy == Privacy.PERSONAL
    assert spans[0].start == datetime(2026, 11, 20, 13, 50, tzinfo=UTC)
    assert spans[0].end == datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    assert spans[0].start_offset_seconds == 0.0
    assert spans[0].end_offset_seconds == 600.0

    # Class
    assert spans[1].privacy == Privacy.CLASS
    assert spans[1].course_key == "csc382"
    assert spans[1].start == datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    assert spans[1].end == datetime(2026, 11, 20, 15, 10, tzinfo=UTC)
    assert spans[1].start_offset_seconds == 600.0
    assert spans[1].end_offset_seconds == 4800.0

    # Tail
    assert spans[2].privacy == Privacy.PERSONAL
    assert spans[2].start == datetime(2026, 11, 20, 15, 10, tzinfo=UTC)
    assert spans[2].end == datetime(2026, 11, 20, 15, 30, tzinfo=UTC)
    assert spans[2].start_offset_seconds == 4800.0
    assert spans[2].end_offset_seconds == 6000.0


def test_class_window_split_tail_only():
    event = CalendarEvent(
        id="evt-class",
        summary="CSC 382 Lecture",
        description="csc382\nDetails",
        start=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
        calendar="Classes",
    )
    rec_start = datetime(2026, 11, 20, 14, 5, tzinfo=UTC)
    rec_duration = 75 * 60.0

    spans = split_class_window(rec_start, rec_duration, event, course_key="csc382")
    assert len(spans) == 2
    assert spans[0].privacy == Privacy.CLASS
    assert spans[0].start == datetime(2026, 11, 20, 14, 5, tzinfo=UTC)
    assert spans[0].end == datetime(2026, 11, 20, 15, 10, tzinfo=UTC)

    assert spans[1].privacy == Privacy.PERSONAL
    assert spans[1].start == datetime(2026, 11, 20, 15, 10, tzinfo=UTC)
    assert spans[1].end == datetime(2026, 11, 20, 15, 20, tzinfo=UTC)


# --- Acceptance Check 1.3: Private Events Rule ---


def test_private_events_by_title_substring():
    event = CalendarEvent(
        id="evt-priv-01",
        summary="Therapy Appointment",
        description="Confidential session",
        start=datetime(2026, 11, 20, 11, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
        calendar="Personal",
    )
    rec_start = datetime(2026, 11, 20, 11, 5, tzinfo=UTC)
    rec_duration = 50 * 60.0

    config = CalendarConfig(private_events=["Therapy"])
    priv_match = match_private_event(rec_start, rec_duration, [event], config)
    assert priv_match is not None
    assert priv_match.id == "evt-priv-01"


def test_private_events_by_calendar_name():
    event = CalendarEvent(
        id="evt-priv-02",
        summary="Health Checkup",
        description="Doctor visit",
        start=datetime(2026, 11, 20, 11, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
        calendar="private_events",
    )
    rec_start = datetime(2026, 11, 20, 11, 0, tzinfo=UTC)
    rec_duration = 30 * 60.0

    config = CalendarConfig(private_events=["Therapy"], private_calendars=["private_events"])
    priv_match = match_private_event(rec_start, rec_duration, [event], config)
    assert priv_match is not None
    assert priv_match.id == "evt-priv-02"


def test_private_events_no_overlap_does_not_trigger():
    event = CalendarEvent(
        id="evt-priv-01",
        summary="Therapy Appointment",
        description="Confidential session",
        start=datetime(2026, 11, 20, 11, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
        calendar="Personal",
    )
    rec_start = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    rec_duration = 30 * 60.0

    config = CalendarConfig(private_events=["Therapy"])
    priv_match = match_private_event(rec_start, rec_duration, [event], config)
    assert priv_match is None


# --- Non-Class Event Match for Meetings ---


def test_non_class_event_match_for_meeting():
    event = CalendarEvent(
        id="evt-meeting-01",
        summary="Sprint Planning with Team",
        description="Bi-weekly sync",
        start=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 11, 0, tzinfo=UTC),
        calendar="Work",
    )
    rec_start = datetime(2026, 11, 20, 10, 5, tzinfo=UTC)
    rec_duration = 50 * 60.0

    config = CalendarConfig(classes_calendar="Classes", private_events=["Therapy"])
    meeting_match = match_meeting_event(rec_start, rec_duration, [event], config)
    assert meeting_match is not None
    matched_event, _overlap_sec = meeting_match
    assert matched_event.summary == "Sprint Planning with Team"
    assert matched_event.start == datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    assert matched_event.end == datetime(2026, 11, 20, 11, 0, tzinfo=UTC)


# --- Full Recording Classification ---


def test_classify_recording_private_wins():
    class_evt = CalendarEvent(
        id="evt-class",
        summary="MAT 305",
        description="mat305\nMath",
        start=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
        calendar="Classes",
    )
    priv_evt = CalendarEvent(
        id="evt-priv",
        summary="Therapy",
        description="Session",
        start=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
        calendar="Personal",
    )
    rec_start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    rec_duration = 3600.0

    config = CalendarConfig(classes_calendar="Classes", private_events=["Therapy"])
    result = classify_recording(rec_start, rec_duration, [class_evt, priv_evt], config)
    assert result.privacy == Privacy.PRIVATE
    assert result.course_key is None


def test_classify_recording_class_match():
    class_evt = CalendarEvent(
        id="evt-class",
        summary="MAT 305",
        description="mat305\nMath",
        start=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
        calendar="Classes",
    )
    rec_start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    rec_duration = 3600.0

    config = CalendarConfig(classes_calendar="Classes", private_events=["Therapy"])
    result = classify_recording(rec_start, rec_duration, [class_evt], config)
    assert result.privacy == Privacy.CLASS
    assert result.course_key == "mat305"
    assert len(result.spans) == 1
    assert result.spans[0].privacy == Privacy.CLASS


def test_classify_recording_meeting_match():
    meeting_evt = CalendarEvent(
        id="evt-meet",
        summary="Project Sync",
        description="Agenda",
        start=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 11, 0, tzinfo=UTC),
        calendar="General",
    )
    rec_start = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    rec_duration = 3600.0

    config = CalendarConfig(classes_calendar="Classes", private_events=["Therapy"])
    result = classify_recording(rec_start, rec_duration, [meeting_evt], config)
    assert result.privacy == Privacy.PERSONAL
    assert result.is_meeting is True
    assert result.meeting_title == "Project Sync"
    assert result.meeting_time == (meeting_evt.start, meeting_evt.end)


def test_classify_recording_unmatched():
    rec_start = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)
    rec_duration = 1800.0

    config = CalendarConfig(classes_calendar="Classes", private_events=["Therapy"])
    result = classify_recording(rec_start, rec_duration, [], config)
    assert result.privacy == Privacy.PERSONAL
    assert result.is_meeting is False
    assert result.course_key is None


def test_classify_recording_invalid_course_key_fails_closed():
    class_evt = CalendarEvent(
        id="evt-class",
        summary="Some Study Session",
        description="unknown_subject\nNotes",
        start=datetime(2026, 11, 20, 14, 0, tzinfo=UTC),
        end=datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
        calendar="Classes",
    )
    rec_start = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)
    rec_duration = 3600.0

    config = CalendarConfig(classes_calendar="Classes", private_events=["Therapy"])
    courses_cfg = CoursesConfig(courses={})

    result = classify_recording(
        rec_start,
        rec_duration,
        [class_evt],
        config,
        courses_config=courses_cfg,
        valid_courses={"mat305", "csc301"},
    )
    # Fails closed: does NOT match as class
    assert result.privacy == Privacy.PERSONAL
    assert result.course_key is None


# --- Client & Token Refresh Tests ---


def test_client_refresh_rejected_token():
    creds = {
        "client_id": "test_id",
        "client_secret": "test_secret",
        "refresh_token": "bad_token",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    client = CalendarClient(credentials=creds)

    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Token has been expired or revoked.",
        }
        mock_post.return_value = mock_resp

        with pytest.raises(TokenRejectedError) as exc_info:
            client.get_access_token()
        assert "token_rejected" in str(exc_info.value)


def test_client_refresh_success():
    creds = {
        "client_id": "test_id",
        "client_secret": "test_secret",
        "refresh_token": "good_token",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    client = CalendarClient(credentials=creds)

    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "fresh_access_token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_post.return_value = mock_resp

        token = client.get_access_token()
        assert token == "fresh_access_token"


def test_client_list_events():
    creds = {
        "client_id": "test_id",
        "client_secret": "test_secret",
        "refresh_token": "good_token",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    client = CalendarClient(credentials=creds)

    with patch.object(client, "get_access_token", return_value="fake_token"), patch(
        "httpx.Client.get"
    ) as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {
                    "id": "evt-01",
                    "summary": "MAT 305 Lecture",
                    "description": "mat305\nRoom 238",
                    "start": {"dateTime": "2026-11-20T17:30:00-05:00"},
                    "end": {"dateTime": "2026-11-20T18:45:00-05:00"},
                }
            ]
        }
        mock_get.return_value = mock_resp

        t_min = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
        t_max = datetime(2026, 11, 20, 23, 59, tzinfo=UTC)
        events = client.list_events(calendar_id="Classes", time_min=t_min, time_max=t_max)
        assert len(events) == 1
        assert events[0].id == "evt-01"
        assert events[0].summary == "MAT 305 Lecture"
        assert events[0].description == "mat305\nRoom 238"


# --- Real Integration Test (@pytest.mark.real) ---


@pytest.mark.real
def test_real_classes_calendar():
    """Live check against real Google Calendar while holding the lock (§3.2, Issue #54)."""
    import os
    import time

    lock_path = "/tmp/articulate-real.lock"
    acquired = False
    for _ in range(60):
        try:
            os.mkdir(lock_path)
            acquired = True
            break
        except FileExistsError:
            time.sleep(2)

    assert acquired, "Could not acquire /tmp/articulate-real.lock"
    try:
        client = CalendarClient()
        calendars = client.list_calendars()
        cal_names = [c.get("summary") for c in calendars]
        assert "Classes" in cal_names, f"'Classes' calendar not found in: {cal_names}"

        now = datetime.now(UTC)
        time_min = now - timedelta(days=7)
        time_max = now + timedelta(days=7)
        events = client.list_events("Classes", time_min=time_min, time_max=time_max)
        assert isinstance(events, list)
    finally:
        with contextlib.suppress(OSError):
            os.rmdir(lock_path)
