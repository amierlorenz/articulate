"""Archived Pocket transcripts as Inbox notes read them: windows, `≈` timestamps and chunks.

The archive is pocket's (§3.1 `TRANSCRIPTS`: the detail payload as fetched, plus `fetched_at`); this module only
reads it.
"""
import gzip
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from articulate.core.paths import get_data_dir

LONG_SECONDS = 30 * 60  # 1.14: recordings over ~30 min are summarised in chunks
LONG_WORDS = 3000  # qwen3:8b's default context can't take more in one call
CHUNK_SECONDS = 10 * 60
CHUNK_WORDS = 2500

Segment = tuple[float, float, str]


def format_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


@dataclass
class Transcript:
    recording_id: str
    title: str
    recording_at: int | str  # as Pocket sends it: epoch seconds or an ISO 8601 string
    segments: list[Segment]
    duration: float | None = None
    offset: float = 0.0  # where this window starts on the recording's timeline
    _starts: list[int] = field(default_factory=list, repr=False, compare=False)

    @classmethod
    def from_detail(cls, detail: dict[str, Any]) -> "Transcript":
        data = detail["data"] if isinstance(detail.get("data"), dict) else detail
        transcript = data.get("transcript") or {}
        segments: list[Segment] = []
        for seg in transcript.get("segments") or []:
            text = (seg.get("text") or "").strip()
            if text:
                segments.append((float(seg.get("start") or 0.0), float(seg.get("end") or 0.0), text))
        duration = data.get("duration")
        if duration is None and segments:
            duration = segments[-1][1]
        return cls(
            recording_id=str(data.get("id", "")),
            title=str(data.get("title") or ""),
            recording_at=data.get("recording_at") or 0,
            segments=segments,
            duration=float(duration) if duration is not None else None,
        )

    def recorded_at(self) -> datetime:
        """When the recording started, as an aware UTC datetime."""
        raw = self.recording_at
        if isinstance(raw, str) and not raw.lstrip("-").isdigit():
            return datetime.fromisoformat(raw).astimezone(UTC)
        return datetime.fromtimestamp(int(raw), UTC)

    def text(self) -> str:
        self._starts = []
        parts = []
        pos = 0
        for _, _, seg_text in self.segments:
            self._starts.append(pos)
            parts.append(seg_text)
            pos += len(seg_text) + 1
        return "\n".join(parts)

    def word_count(self) -> int:
        return sum(len(s[2].split()) for s in self.segments)

    def time_at(self, char_pos: int) -> float:
        """Seconds on the recording's timeline for a character of `text()`, interpolated inside a segment."""
        if not self._starts:
            self.text()
        idx = 0
        for i, start in enumerate(self._starts):
            if start <= char_pos:
                idx = i
            else:
                break
        seg_start, seg_end, seg_text = self.segments[idx]
        frac = (char_pos - self._starts[idx]) / max(1, len(seg_text))
        return seg_start + (seg_end - seg_start) * min(1.0, max(0.0, frac))

    def window(self, start: float, end: float | None) -> "Transcript":
        segs = [s for s in self.segments if s[1] > start and (end is None or s[0] < end)]
        return replace(self, segments=segs, offset=start, _starts=[])

    def span_seconds(self) -> float:
        if not self.segments:
            return 0.0
        return self.segments[-1][1] - self.segments[0][0]

    def needs_chunking(self) -> bool:
        return self.span_seconds() > LONG_SECONDS or self.word_count() > LONG_WORDS

    def chunks(self) -> list["Transcript"]:
        out: list[Transcript] = []
        current: list[Segment] = []
        words = 0
        for seg in self.segments:
            if current and (seg[0] - current[0][0] >= CHUNK_SECONDS or words >= CHUNK_WORDS):
                out.append(replace(self, segments=current, offset=current[0][0], _starts=[]))
                current, words = [], 0
            current.append(seg)
            words += len(seg[2].split())
        if current:
            out.append(replace(self, segments=current, offset=current[0][0], _starts=[]))
        return out


def load_archived(recording_id: str) -> Transcript | None:
    """Read pocket's archived detail for a recording (any `YYYY/MM` folder), or None if it isn't archived yet."""
    root = get_data_dir() / "transcripts"
    for path in sorted(root.glob(f"*/*/{recording_id}.json.gz")):
        detail = json.loads(gzip.decompress(path.read_bytes()))
        return Transcript.from_detail(detail)
    return None
