"""Due dates only when spoken, resolved against the recording date (1.19)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from articulate.tasks.extract import resolve_due

ET = ZoneInfo("America/New_York")
THU_EVENING = datetime(2026, 11, 19, 18, 0, tzinfo=ET)  # a Thursday


@pytest.mark.parametrize(
    ("spoken", "due_at", "has_time"),
    [
        ("Thursday", "2026-11-26T00:00:00-05:00", False),  # next Thursday *after* the recording
        ("by Friday", "2026-11-20T00:00:00-05:00", False),
        ("tomorrow at 3pm", "2026-11-20T15:00:00-05:00", True),
        ("tonight", "2026-11-19T00:00:00-05:00", False),
        ("today at 9:30 pm", "2026-11-19T21:30:00-05:00", True),
        ("Monday at noon", "2026-11-23T12:00:00-05:00", True),
        ("December 2", "2026-12-02T00:00:00-05:00", False),
        ("Dec 2nd at 10am", "2026-12-02T10:00:00-05:00", True),
        ("12/3", "2026-12-03T00:00:00-05:00", False),
        ("January 5", "2027-01-05T00:00:00-05:00", False),  # a past month-day rolls to next year
    ],
)
def test_spoken_dates_resolve_against_recording(spoken, due_at, has_time):
    assert resolve_due(spoken, THU_EVENING) == (due_at, has_time)


@pytest.mark.parametrize("spoken", [None, "", "soon", "next week", "at some point", "later"])
def test_vague_or_missing_dates_give_no_due(spoken):
    assert resolve_due(spoken, THU_EVENING) == (None, False)


def test_dst_offset_follows_the_due_date():
    march = datetime(2027, 3, 10, 9, 0, tzinfo=ET)
    assert resolve_due("Monday at 9am", march) == ("2027-03-15T09:00:00-04:00", True)
