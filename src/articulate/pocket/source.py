"""Pocket source: poll recordings, apply Private rules, dedupe, archive transcripts, and emit items (§3.5, #55)."""
import gzip
import json
import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from articulate.calendar.client import CalendarClient, CalendarEvent
from articulate.calendar.match import classify_recording
from articulate.core.config import (
    CalendarConfig,
    CoursesConfig,
    PocketConfig,
    load_config,
)
from articulate.core.health import Health
from articulate.core.ids import item_id
from articulate.core.jobs import StepSummary
from articulate.core.paths import get_data_dir, transcript_path, vault_path
from articulate.core.pipeline import Item, Privacy, RunContext
from articulate.core.runlog import RunLog
from articulate.core.vault import Vault
from articulate.pocket.client import PocketClient, TokenRejectedError
from articulate.pocket.private import (
    PrivateConfigError,
    is_private_recording,
    resolve_private_rules,
)
from articulate.pocket.sensitivity import check_sensitivity

logger = logging.getLogger(__name__)


def _parse_recording_dt(recording_at: Any) -> datetime:
    """Parse recording_at into timezone-aware UTC datetime."""
    if isinstance(recording_at, (int, float)):
        return datetime.fromtimestamp(float(recording_at), UTC)
    if isinstance(recording_at, str):
        if recording_at.lstrip("-").isdigit():
            return datetime.fromtimestamp(float(recording_at), UTC)
        try:
            clean_str = recording_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


def archive_transcript(
    recording_id: str,
    recording_at: Any,
    detail: dict[str, Any],
    data_dir: Path | None = None,
) -> Path:
    """
    Archive transcript payload once as transcripts/YYYY/MM/<id>.json.gz (Spec §3.1, Acceptance 1.8).
    Includes original detail payload plus fetched_at. Re-polls do not rewrite it.
    """
    dt = _parse_recording_dt(recording_at)
    y_str = dt.strftime("%Y")
    m_str = dt.strftime("%m")

    if data_dir is not None:
        target_path = data_dir / "transcripts" / y_str / m_str / f"{recording_id}.json.gz"
    else:
        target_path = transcript_path(recording_id, y_str, m_str)

    # Check if already archived
    if target_path.exists():
        return target_path

    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure fetched_at is present
    payload = dict(detail)
    if "fetched_at" not in payload:
        payload["fetched_at"] = datetime.now(UTC).isoformat()

    compressed_bytes = gzip.compress(json.dumps(payload).encode("utf-8"))

    # Write atomically via temp file
    temp_path = target_path.with_name(f"{target_path.name}.tmp.{os.getpid()}")
    try:
        temp_path.write_bytes(compressed_bytes)
        temp_path.replace(target_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return target_path


def _is_archived(recording_id: str, data_dir: Path | None = None) -> Path | None:
    """Check if recording transcript is already archived anywhere under transcripts/."""
    root = (data_dir or get_data_dir()) / "transcripts"
    if not root.exists():
        return None
    hits = sorted(root.glob(f"*/*/{recording_id}.json.gz"))
    return hits[0] if hits else None


def _load_archived_detail(path: Path) -> dict[str, Any] | None:
    try:
        raw_bytes = path.read_bytes()
        return json.loads(gzip.decompress(raw_bytes))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read archived transcript %s: %s", path, e)
        return None


class PocketSource:
    """Discovers recordings from Pocket, applies rules, and emits Items."""

    name = "pocket"

    def __init__(
        self,
        client: PocketClient | None = None,
        calendar_events: list[CalendarEvent] | None = None,
    ):
        self.client = client
        self.calendar_events = calendar_events

    def discover(self, ctx: RunContext) -> Iterable[Item]:
        """Discover Pocket recordings and emit Items."""
        pocket_cfg: PocketConfig
        if ctx.config is not None and getattr(ctx.config, "articulate", None) is not None:
            pocket_cfg = ctx.config.articulate.pocket
        else:
            pocket_cfg = PocketConfig()

        calendar_cfg: CalendarConfig
        if ctx.config is not None and getattr(ctx.config, "articulate", None) is not None:
            calendar_cfg = ctx.config.articulate.calendar
        else:
            calendar_cfg = CalendarConfig()

        courses_cfg: CoursesConfig | None = None
        if ctx.config is not None and getattr(ctx.config, "courses", None) is not None:
            courses_cfg = ctx.config.courses

        client = self.client or PocketClient(
            api_base=pocket_cfg.api_base,
            health=ctx.health,
        )

        # 1. Fetch folders and tags
        try:
            folders = client.get_folders()
            tags = client.get_tags()
            priv_folders, priv_tags = resolve_private_rules(
                folders,
                tags,
                private_folder_id=pocket_cfg.private_folder_id,
                private_tag_id=pocket_cfg.private_tag_id,
                health=ctx.health,
            )
        except PrivateConfigError:
            logger.error("Private folder/tag validation failed; aborting Pocket ingest.")
            return

        # 2. List recordings within lookback window
        recordings = client.list_recordings(lookback_days=pocket_cfg.lookback_days)

        # Index all seen recording_at timestamps for dedupe
        seen_recording_at: dict[int, list[str]] = {}
        for r in recordings:
            r_at = int(_parse_recording_dt(r.get("recording_at", r.get("created_at", 0))).timestamp())
            seen_recording_at.setdefault(r_at, []).append(str(r.get("id")))

        now = ctx.now
        now_epoch = int(now.timestamp())
        give_up_seconds = pocket_cfg.give_up_pending_days * 86400

        # Optional Calendar Client for class/meeting/private event matching
        all_events: list[CalendarEvent] = list(self.calendar_events or [])
        req_count = getattr(client, "request_count", 0)
        if self.calendar_events is None and isinstance(req_count, (int, float)) and req_count < 10:
            try:
                cal_client = CalendarClient(config=calendar_cfg, health=ctx.health)
                # Look back 3 days to now + 1 day
                time_min = now - timedelta(days=pocket_cfg.lookback_days + 1)
                time_max = now + timedelta(days=1)
                all_events = cal_client.list_events(calendar_cfg.classes_calendar, time_min, time_max)
            except Exception as ce:  # noqa: BLE001
                logger.info("Calendar events not loaded directly: %s", ce)

        for rec in recordings:
            rec_id = str(rec.get("id", ""))
            state = rec.get("state", "completed")
            rec_start = _parse_recording_dt(rec.get("recording_at", rec.get("created_at", 0)))
            rec_at_epoch = int(rec_start.timestamp())
            title = str(rec.get("title") or "Recording")

            # Check pending recordings
            if state == "pending":
                elapsed = now_epoch - rec_at_epoch
                if elapsed > give_up_seconds:
                    logger.warning("Pending recording %s gave up after %d days", rec_id, pocket_cfg.give_up_pending_days)
                    if ctx.health is not None:
                        ctx.health.raise_(
                            "pending_recording_abandoned",
                            key=rec_id,
                            detail=f"Pending recording {rec_id} abandoned after {pocket_cfg.give_up_pending_days} days",
                            interrupt=False,
                        )
                # Retry on later polls, skip now
                continue

            # Check Private folder / tag rule (Fail-closed: NEVER fetch detail!)
            if is_private_recording(rec, priv_folders, priv_tags):
                yield Item(
                    item_id=item_id("pocket", rec_id),
                    source="pocket",
                    kind="recording",
                    native_id=rec_id,
                    privacy=Privacy.PRIVATE,
                    payload={
                        "recording_id": rec_id,
                        "title": title,
                        "recording_at": rec_start.isoformat(),
                        "private": True,
                    },
                )
                continue

            # Non-Private recording: fetch detail or load archived
            archived_path = _is_archived(rec_id)
            if archived_path is not None:
                detail = _load_archived_detail(archived_path) or {}
            else:
                detail = client.get_recording(rec_id)
                archive_transcript(rec_id, rec_at_epoch, detail)

            data = detail.get("data", detail)
            transcript_data = data.get("transcript") or {}
            metadata = transcript_data.get("metadata") or {}
            is_merged = bool(metadata.get("merged"))

            # Merged copy dedupe (Acceptance 1.6)
            if is_merged:
                # Check if original exists with same recording_at
                originals = [o_id for o_id in seen_recording_at.get(rec_at_epoch, []) if o_id != rec_id]
                if originals:
                    logger.info("Skipping merged recording %s (original %s exists at %s)", rec_id, originals[0], rec_at_epoch)
                    continue

            # Duration fallback
            duration = data.get("duration")
            if duration is None or float(duration) <= 0:
                segments = transcript_data.get("segments") or []
                if segments:
                    duration = float(segments[-1].get("end") or 0.0)
                else:
                    duration = 0.0
            else:
                duration = float(duration)

            transcript_text = str(transcript_data.get("text") or "")

            # Classify recording against Google Calendar
            cls_res = classify_recording(
                rec_start=rec_start,
                rec_duration_s=duration,
                events=all_events,
                config=calendar_cfg,
                courses_config=courses_cfg,
            )

            # 1. Calendar Private match
            if cls_res.privacy == Privacy.PRIVATE:
                yield Item(
                    item_id=item_id("pocket", rec_id),
                    source="pocket",
                    kind="recording",
                    native_id=rec_id,
                    privacy=Privacy.PRIVATE,
                    payload={
                        "recording_id": rec_id,
                        "title": title,
                        "recording_at": rec_start.isoformat(),
                        "duration": duration,
                        "private": True,
                    },
                )
                continue

            # 2. Class match -> split spans
            if cls_res.privacy == Privacy.CLASS and cls_res.spans:
                for span in cls_res.spans:
                    if span.privacy == Privacy.CLASS:
                        span_id = f"{rec_id}:class:{int(span.start_offset_seconds)}"
                        yield Item(
                            item_id=item_id("pocket", span_id),
                            source="pocket",
                            kind="span",
                            native_id=span_id,
                            privacy=Privacy.CLASS,
                            course_key=cls_res.course_key,
                            payload={
                                "recording_id": rec_id,
                                "title": title,
                                "start_sec": span.start_offset_seconds,
                                "end_sec": span.end_offset_seconds,
                                "course_key": cls_res.course_key,
                                "span_kind": "class",
                            },
                        )
                    else:
                        # Personal lead-in or tail: run sensitivity check
                        sens = check_sensitivity(transcript_text, ctx.models, ctx.runlog, rec_id)
                        tail_priv = Privacy.PRIVATE if sens else Privacy.PERSONAL
                        span_id = f"{rec_id}:tail:{int(span.start_offset_seconds)}"
                        yield Item(
                            item_id=item_id("pocket", span_id),
                            source="pocket",
                            kind="span",
                            native_id=span_id,
                            privacy=tail_priv,
                            payload={
                                "recording_id": rec_id,
                                "title": f"{title} (tail)",
                                "start_sec": span.start_offset_seconds,
                                "end_sec": span.end_offset_seconds,
                                "span_kind": "tail",
                                "cut_from": rec_id,
                            },
                        )
                continue

            # 3. Personal (unmatched or meeting): run sensitivity check
            is_sens = check_sensitivity(transcript_text, ctx.models, ctx.runlog, rec_id)
            final_priv = Privacy.PRIVATE if is_sens else Privacy.PERSONAL

            yield Item(
                item_id=item_id("pocket", rec_id),
                source="pocket",
                kind="recording",
                native_id=rec_id,
                privacy=final_priv,
                payload={
                    "recording_id": rec_id,
                    "title": title,
                    "recording_at": rec_start.isoformat(),
                    "duration": duration,
                    "merged": is_merged,
                    "wall_clock": not is_merged,
                    "is_meeting": cls_res.is_meeting,
                    "meeting_title": cls_res.meeting_title,
                    "meeting_time": [t.isoformat() for t in cls_res.meeting_time] if cls_res.meeting_time else None,
                },
            )


def _ensure_services(ctx: RunContext) -> None:
    """Ensure context services are populated."""
    if ctx.config is None:
        try:
            ctx.config = load_config(db=ctx.db)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not load config: %s", e)
    if ctx.vault is None and ctx.config is not None and getattr(ctx.config, "articulate", None) is not None:
        ctx.vault = Vault(vault_path(ctx.config), ctx.db)
    if ctx.runlog is None and ctx.vault is not None:
        ctx.runlog = RunLog(ctx.vault)
    if ctx.health is None:
        ctx.health = Health(ctx.db, runlog=ctx.runlog, taskqueue=ctx.tasks, vault=ctx.vault)


def step(ctx: RunContext) -> StepSummary:
    """Pocket poll step for watch and nightly jobs (registry order 100)."""
    _ensure_services(ctx)
    summary = StepSummary()

    source = PocketSource()
    now_str = ctx.now.isoformat()

    try:
        items = list(source.discover(ctx))
    except PrivateConfigError as pce:
        logger.error("Pocket poll halted due to configuration error: %s", pce)
        return StepSummary(failed=1, detail=str(pce))
    except TokenRejectedError as tre:
        logger.error("Pocket token rejected: %s", tre)
        return StepSummary(failed=1, detail=str(tre))
    except Exception as e:  # noqa: BLE001
        logger.error("Pocket discover failed: %s", e)
        return StepSummary(failed=1, detail=str(e))

    with ctx.db.connect() as conn:
        for item in items:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT item_id, status FROM items WHERE source = ? AND native_id = ? AND version = ?;",
                (item.source, item.native_id, item.version),
            )
            row = cursor.fetchone()

            if row is None:
                initial_status = "skipped" if item.privacy == Privacy.PRIVATE else "pending"
                initial_stage = "private" if item.privacy == Privacy.PRIVATE else "entry"

                conn.execute(
                    """
                    INSERT INTO items (
                        item_id, source, kind, native_id, version, course_key,
                        privacy, exam_key, priority, stage, status,
                        attempts, consecutive_failures, payload_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?);
                    """,
                    (
                        item.item_id,
                        item.source,
                        item.kind,
                        item.native_id,
                        item.version,
                        item.course_key,
                        item.privacy.value,
                        item.exam_key,
                        item.priority,
                        initial_stage,
                        initial_status,
                        json.dumps(item.payload),
                        now_str,
                        now_str,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO item_events (item_id, run_id, at, stage, event, detail)
                    VALUES (?, ?, ?, ?, 'discovered', 'Pocket item discovered');
                    """,
                    (item.item_id, ctx.run_id, now_str, initial_stage),
                )
                summary.done += 1
            else:
                summary.held += 1

        conn.commit()

    return summary
