"""Inbox's meeting match goes through the calendar slice's `match_meeting_event` (#54, 1.13)."""
from datetime import UTC, datetime

from articulate.calendar import CalendarEvent
from articulate.inbox.step import meeting_from_calendar


class FakeClient:
    def __init__(self, events):
        self.events = events
        self.asked = []

    def list_events(self, calendar_id, time_min, time_max):
        self.asked.append((calendar_id, time_min, time_max))
        return self.events


def _evt(eid, summary, start, end, calendar="primary"):
    return CalendarEvent(id=eid, summary=summary, start=start, end=end, calendar=calendar)


START = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)
END = datetime(2026, 11, 18, 16, 30, tzinfo=UTC)


def test_overlapping_non_class_event_is_the_meeting(env):
    client = FakeClient([
        _evt("a", "Showcase poster sync", START, END),
        _evt("b", "PHY 203", START, END, calendar="Classes"),
    ])
    m = meeting_from_calendar(START, END, env.ctx(), client=client)
    assert m["title"] == "Showcase poster sync"
    assert m["start"] == START and m["end"] == END
    assert client.asked and client.asked[0][1] <= START and client.asked[0][2] >= END


def test_no_overlap_or_no_calendar_means_no_meeting(env):
    far = datetime(2026, 11, 18, 20, 0, tzinfo=UTC)
    assert meeting_from_calendar(START, END, env.ctx(), client=FakeClient([_evt("c", "x", far, far)])) is None

    class Broken:
        def list_events(self, *a):
            raise RuntimeError("no token")

    assert meeting_from_calendar(START, END, env.ctx(), client=Broken()) is None
