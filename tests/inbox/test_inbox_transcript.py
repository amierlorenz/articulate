"""Archived transcript reading, windows, `≈` timestamps and chunking for Inbox notes."""
import gzip
import json
from pathlib import Path

from articulate.inbox.transcript import Transcript, format_ts, load_archived

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _detail(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_loads_committed_pocket_detail_shape():
    t = Transcript.from_detail(_detail("pocket/recording-detail-null-duration.json"))
    assert t.recording_id == "rec-003"
    assert t.title == "Audio Note"
    assert t.recording_at == 1731950000
    assert t.word_count() == 5
    # duration null falls back to the last segment's end (1.7)
    assert t.duration == 45.0


def test_load_archived_finds_the_file_by_recording_id(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICULATE_DATA", str(tmp_path))
    detail = _detail("pocket/recording-detail-lecture.json")
    detail["fetched_at"] = "2026-11-20T10:00:00-05:00"
    target = tmp_path / "transcripts" / "2024" / "11" / "rec-001.json.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(gzip.compress(json.dumps(detail).encode()))

    t = load_archived("rec-001")
    assert t is not None
    assert t.title == "Physics Lecture"
    assert load_archived("rec-missing") is None


def test_format_ts():
    assert format_ts(0) == "0:00:00"
    assert format_ts(83.9) == "0:01:23"
    assert format_ts(3725) == "1:02:05"


def test_timestamp_interpolates_by_character_inside_a_segment():
    t = Transcript(
        recording_id="r",
        title="t",
        recording_at=0,
        segments=[(0.0, 10.0, "aaaa bbbb"), (100.0, 200.0, "cccc dddd")],
    )
    text = t.text()
    assert t.time_at(text.index("cccc")) == 100.0
    # "dddd" starts halfway through the second segment's characters
    assert 140.0 < t.time_at(text.index("dddd")) < 160.0


def test_window_keeps_segments_that_start_inside_it():
    t = Transcript(
        recording_id="r",
        title="t",
        recording_at=0,
        segments=[(0.0, 60.0, "class part"), (4500.0, 4560.0, "tail one"), (4600.0, 4700.0, "tail two")],
    )
    tail = t.window(4500.0, None)
    assert [s[2] for s in tail.segments] == ["tail one", "tail two"]
    assert tail.offset == 4500.0


def test_chunks_split_long_recordings_by_time_and_by_size():
    segs = [(i * 60.0, i * 60.0 + 60.0, "word " * 20) for i in range(40)]  # 40 min, 800 words
    t = Transcript(recording_id="r", title="t", recording_at=0, segments=segs)
    assert t.needs_chunking()
    chunks = t.chunks()
    assert len(chunks) == 4
    assert sum(len(c.segments) for c in chunks) == 40

    short = Transcript(recording_id="r", title="t", recording_at=0, segments=segs[:10])
    assert not short.needs_chunking()

    wordy = Transcript(
        recording_id="r", title="t", recording_at=0, segments=[(0.0, 60.0, "word " * 3500)]
    )
    assert wordy.needs_chunking()


def test_recording_at_accepts_epoch_seconds_and_iso_strings():
    from datetime import UTC, datetime

    epoch = Transcript.from_detail(_detail("pocket/recording-detail-null-duration.json"))
    assert epoch.recorded_at() == datetime.fromtimestamp(1731950000, UTC)

    detail = _detail("pocket/recording-detail-null-duration.json")
    detail["data"]["recording_at"] = "2026-11-18T14:08:44Z"  # the shape real Pocket details have
    iso = Transcript.from_detail(detail)
    assert iso.recorded_at() == datetime(2026, 11, 18, 14, 8, 44, tzinfo=UTC)
