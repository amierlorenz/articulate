"""Tests for core.clock."""
from datetime import datetime
from zoneinfo import ZoneInfo

from articulate.core.clock import (
    EASTERN,
    PACIFIC,
    local_night,
    next_pacific_midnight,
    pacific_date,
)


def test_pacific_date_boundary_eastern():
    # 02:59 Eastern on 2026-11-20 is 23:59 Pacific on 2026-11-19
    dt_before = datetime(2026, 11, 20, 2, 59, tzinfo=EASTERN)
    assert pacific_date(dt_before) == "2026-11-19"

    # 03:01 Eastern on 2026-11-20 is 00:01 Pacific on 2026-11-20
    dt_after = datetime(2026, 11, 20, 3, 1, tzinfo=EASTERN)
    assert pacific_date(dt_after) == "2026-11-20"


def test_pacific_date_dst_changes():
    # Spring forward: 2026-03-08 at 02:00 local transitions to 03:00 PDT
    # Before change: 2026-03-08 01:59 PST
    dt_spring_before = datetime(2026, 3, 8, 1, 59, tzinfo=PACIFIC)
    assert pacific_date(dt_spring_before) == "2026-03-08"

    # After change: 2026-03-08 03:01 PDT
    dt_spring_after = datetime(2026, 3, 8, 3, 1, tzinfo=PACIFIC)
    assert pacific_date(dt_spring_after) == "2026-03-08"

    # Fall back: 2026-11-01 at 02:00 PDT transitions back to 01:00 PST
    dt_fall_before = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=PACIFIC)
    assert pacific_date(dt_fall_before) == "2026-11-01"

    dt_fall_after = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=PACIFIC)
    assert pacific_date(dt_fall_after) == "2026-11-01"


def test_next_pacific_midnight():
    dt = datetime(2026, 11, 20, 14, 30, tzinfo=PACIFIC)
    midnight = next_pacific_midnight(dt)
    assert midnight.year == 2026
    assert midnight.month == 11
    assert midnight.day == 21
    assert midnight.hour == 0
    assert midnight.minute == 0
    assert midnight.second == 0
    assert midnight.tzinfo == PACIFIC


def test_local_night():
    # §3.13: a time before 12:00 belongs to the previous evening's night
    tz = ZoneInfo("America/New_York")

    # 02:00 on 2026-11-20 belongs to 2026-11-19 night
    dt_early = datetime(2026, 11, 20, 2, 0, tzinfo=tz)
    assert local_night(dt_early, tz) == "2026-11-19"

    # 11:59 on 2026-11-20 belongs to 2026-11-19 night
    dt_noon_before = datetime(2026, 11, 20, 11, 59, tzinfo=tz)
    assert local_night(dt_noon_before, tz) == "2026-11-19"

    # 12:00 on 2026-11-20 belongs to 2026-11-20 night
    dt_noon = datetime(2026, 11, 20, 12, 0, tzinfo=tz)
    assert local_night(dt_noon, tz) == "2026-11-20"

    # 23:00 on 2026-11-20 belongs to 2026-11-20 night
    dt_late = datetime(2026, 11, 20, 23, 0, tzinfo=tz)
    assert local_night(dt_late, tz) == "2026-11-20"
