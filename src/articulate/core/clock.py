"""Time and timezone helpers for Articulate."""
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
EASTERN = ZoneInfo("America/New_York")


def pacific_date(dt: datetime | None = None) -> str:
    """Return the date in America/Los_Angeles as YYYY-MM-DD."""
    if dt is None:
        dt = datetime.now(UTC)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    pacific_dt = dt.astimezone(PACIFIC)
    return pacific_dt.strftime("%Y-%m-%d")


def next_pacific_midnight(dt: datetime | None = None) -> datetime:
    """Return the datetime of the next 00:00:00 Pacific."""
    if dt is None:
        dt = datetime.now(UTC)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    pacific_dt = dt.astimezone(PACIFIC)
    next_day = pacific_dt.date() + timedelta(days=1)
    # Midnight in America/Los_Angeles
    midnight = datetime.combine(next_day, time(0, 0), tzinfo=PACIFIC)
    return midnight


def local_night(dt: datetime | None = None, tz: ZoneInfo | None = None) -> str:
    """
    Return the local date of the 02:00 night it belongs to (§3.4, §3.13).
    A time before 12:00 belongs to the previous evening's night.
    """
    if tz is None:
        tz = EASTERN
    if dt is None:
        dt = datetime.now(UTC)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    local_dt = dt.astimezone(tz)
    if local_dt.hour < 12:
        night_date = local_dt.date() - timedelta(days=1)
    else:
        night_date = local_dt.date()
    return night_date.strftime("%Y-%m-%d")
