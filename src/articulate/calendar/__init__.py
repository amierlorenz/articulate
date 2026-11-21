"""Calendar client and recording classification package (§3.2, §3.3, §3.5, Issue #54)."""
from articulate.calendar.client import CalendarClient, CalendarEvent, TokenRejectedError
from articulate.calendar.match import (
    ClassificationResult,
    RecordingSpan,
    calculate_overlap,
    classify_recording,
    match_class_event,
    match_meeting_event,
    match_private_event,
    normalize_course_key,
    split_class_window,
)

__all__ = [
    "CalendarClient",
    "CalendarEvent",
    "ClassificationResult",
    "RecordingSpan",
    "TokenRejectedError",
    "calculate_overlap",
    "classify_recording",
    "match_class_event",
    "match_meeting_event",
    "match_private_event",
    "normalize_course_key",
    "split_class_window",
]
