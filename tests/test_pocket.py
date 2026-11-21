"""Unit and fixture tests for Pocket poll, Private fail-closed, and transcript archive (#55).

Acceptance checks:
- 1.1: watch polls /public/recordings for last 3 days at :15/:45, details only for new/changed, <=10 requests on class day, 429 sleeps until X-Ratelimit-Reset.
- 1.2: Private fails closed: folder subtree or tag 'private' (id or case-insensitive name) never fetched in detail, never archived beyond id/time, never sent to model. Missing folder/tag id stops ingest + health row.
- 1.3: private_events: recording overlapping matching event/calendar is Private.
- 1.4: Sensitivity check: local qwen3:8b checks non-class recording; hit makes it Private + logs Runs.md line.
- 1.5: Class match: overlap with Classes event, largest overlap wins, class window + personal tail.
- 1.6: Merged copies: detail with metadata.merged and original at same recording_at skipped; without original kept (non-wall-clock).
- 1.7: duration: null falls back to last segment's end; pending retries each poll, gives up after 3 days with dashboard row.
- 1.8: Non-Private recording archived once as transcripts/YYYY/MM/<id>.json.gz; re-poll doesn't rewrite.
"""
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from articulate.calendar.client import CalendarEvent
from articulate.core.config import ArticulateConfig, CalendarConfig, PocketConfig, VaultConfig
from articulate.core.db import Database
from articulate.core.health import Health
from articulate.core.pipeline import Privacy, RunContext
from articulate.core.runlog import RunLog
from articulate.core.vault import Vault
from articulate.pocket.client import PocketClient, TokenRejectedError
from articulate.pocket.private import (
    PrivateConfigError,
    is_private_recording,
    resolve_private_rules,
)
from articulate.pocket.sensitivity import check_sensitivity
from articulate.pocket.source import PocketSource, archive_transcript, step


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "articulate.sqlite3")
    db.migrate()
    return db


@pytest.fixture
def test_vault(tmp_path: Path, test_db: Database) -> Vault:
    v_dir = tmp_path / "vault"
    v_dir.mkdir(parents=True, exist_ok=True)
    return Vault(v_dir, test_db)


@pytest.fixture
def test_ctx(tmp_path: Path, test_db: Database, test_vault: Vault) -> RunContext:
    runlog = RunLog(test_vault)
    health = Health(test_db, runlog=runlog, vault=test_vault)
    cfg = ArticulateConfig(
        vault=VaultConfig(path=str(test_vault.vault_dir)),
        pocket=PocketConfig(
            api_base="https://public.heypocketai.com",
            private_folder_id="5194b8b6-ec40-4d99-91a9-059fb610141e",
            private_tag_id="7dbebf3f-418b-4ea1-936d-61c67089d829",
            lookback_days=3,
            give_up_pending_days=3,
        ),
        calendar=CalendarConfig(
            classes_calendar="Classes",
            private_events=["Therapy"],
        ),
    )
    return RunContext(
        run_id="run-test-01",
        job="watch",
        now=datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
        db=test_db,
        config=MagicMock(articulate=cfg, courses=MagicMock(courses={"phy203": MagicMock()})),
        vault=test_vault,
        runlog=runlog,
        health=health,
    )


# --- 1. Private Folder Subtree & Tags (Fail-closed, Acceptance 1.2) ---


def test_private_folder_subtree():
    """Any folder in the configured private folder's subtree is classified Private."""
    folders = [
        {
            "id": "5194b8b6-ec40-4d99-91a9-059fb610141e",
            "name": "Private Space",
            "children": [
                {
                    "id": "subfolder-1",
                    "name": "Sub 1",
                    "children": [
                        {"id": "subfolder-2", "name": "Deep Sub", "children": []}
                    ],
                }
            ],
        },
        {"id": "public-space", "name": "Work", "children": []},
    ]
    tags = [{"id": "7dbebf3f-418b-4ea1-936d-61c67089d829", "name": "private"}]

    priv_folders, priv_tags = resolve_private_rules(
        folders, tags, "5194b8b6-ec40-4d99-91a9-059fb610141e", "7dbebf3f-418b-4ea1-936d-61c67089d829"
    )

    assert priv_folders == {
        "5194b8b6-ec40-4d99-91a9-059fb610141e",
        "subfolder-1",
        "subfolder-2",
    }
    assert priv_tags == {"7dbebf3f-418b-4ea1-936d-61c67089d829"}

    # Recording in deep subfolder
    rec_deep = {"id": "r1", "folder_id": "subfolder-2", "tags": []}
    assert is_private_recording(rec_deep, priv_folders, priv_tags) is True

    # Recording in public space
    rec_pub = {"id": "r2", "folder_id": "public-space", "tags": []}
    assert is_private_recording(rec_pub, priv_folders, priv_tags) is False


def test_private_tag_by_id_and_by_name():
    """Matching tag by ID or name (case-insensitive) makes recording Private."""
    priv_folders = {"priv-folder"}
    priv_tags = {"priv-tag-id"}

    # Tag by ID
    assert is_private_recording({"id": "r1", "folder_id": None, "tags": [{"id": "priv-tag-id", "name": "custom"}]}, priv_folders, priv_tags) is True

    # Tag by name 'private' (case-insensitive)
    assert is_private_recording({"id": "r2", "folder_id": None, "tags": [{"id": "other-id", "name": "Private"}]}, priv_folders, priv_tags) is True
    assert is_private_recording({"id": "r3", "folder_id": None, "tags": [{"id": "other-id", "name": "PRIVATE"}]}, priv_folders, priv_tags) is True

    # String tag
    assert is_private_recording({"id": "r4", "folder_id": None, "tags": ["private"]}, priv_folders, priv_tags) is True
    assert is_private_recording({"id": "r5", "folder_id": None, "tags": ["work"]}, priv_folders, priv_tags) is False


def test_missing_private_folder_or_tag_stops_ingest(test_ctx: RunContext):
    """Missing configured private folder or tag stops ingest and logs health row."""
    folders = [{"id": "other-space", "name": "Other", "children": []}]
    tags = [{"id": "7dbebf3f-418b-4ea1-936d-61c67089d829", "name": "private"}]

    with pytest.raises(PrivateConfigError):
        resolve_private_rules(
            folders, tags, "5194b8b6-ec40-4d99-91a9-059fb610141e", "7dbebf3f-418b-4ea1-936d-61c67089d829", health=test_ctx.health
        )

    # Health row should be recorded
    with test_ctx.db.connect() as conn:
        row = conn.execute("SELECT * FROM health WHERE kind = 'missing_config' AND key = 'private_folder_id';").fetchone()
        assert row is not None


# --- 2. Merged Copies Deduplication (Acceptance 1.6) ---


def test_merged_copy_with_original_skipped(test_ctx: RunContext):
    """Merged copy with an original at same recording_at is skipped."""
    mock_client = MagicMock()
    # List has original r1 and merged r2 at same recording_at
    mock_client.list_recordings.return_value = [
        {"id": "r1", "title": "Original Lecture", "folder_id": None, "duration": 3600, "state": "completed", "recording_at": 1731950000, "tags": []},
        {"id": "r2", "title": "Merged - Original Lecture", "folder_id": None, "duration": 3600, "state": "completed", "recording_at": 1731950000, "tags": []},
    ]
    mock_client.get_folders.return_value = [{"id": "5194b8b6-ec40-4d99-91a9-059fb610141e", "name": "Private", "children": []}]
    mock_client.get_tags.return_value = [{"id": "7dbebf3f-418b-4ea1-936d-61c67089d829", "name": "private"}]

    mock_client.get_recording.side_effect = lambda rec_id: {
        "success": True,
        "data": {
            "id": rec_id,
            "title": f"Title {rec_id}",
            "recording_at": 1731950000,
            "duration": 3600,
            "transcript": {
                "metadata": {"merged": (rec_id == "r2"), "sourceRecordingCount": 2 if rec_id == "r2" else None},
                "segments": [{"start": 0.0, "end": 100.0, "text": "Hello world"}],
                "text": "Hello world",
            },
        },
    }

    source = PocketSource(client=mock_client)
    items = list(source.discover(test_ctx))

    # r1 should be emitted; r2 (merged) should be skipped!
    item_native_ids = {it.native_id for it in items}
    assert "r1" in item_native_ids
    assert "r2" not in item_native_ids


def test_merged_copy_without_original_kept(test_ctx: RunContext):
    """Merged copy without an original is kept and timeline is not wall-clock."""
    mock_client = MagicMock()
    mock_client.list_recordings.return_value = [
        {"id": "r2", "title": "Merged Only", "folder_id": None, "duration": 7200, "state": "completed", "recording_at": 1731950000, "tags": []},
    ]
    mock_client.get_folders.return_value = [{"id": "5194b8b6-ec40-4d99-91a9-059fb610141e", "name": "Private", "children": []}]
    mock_client.get_tags.return_value = [{"id": "7dbebf3f-418b-4ea1-936d-61c67089d829", "name": "private"}]

    mock_client.get_recording.return_value = {
        "success": True,
        "data": {
            "id": "r2",
            "title": "Merged Only",
            "recording_at": 1731950000,
            "duration": 7200,
            "transcript": {
                "metadata": {"merged": True, "sourceRecordingCount": 2},
                "segments": [{"start": 0.0, "end": 7200.0, "text": "Long lecture"}],
                "text": "Long lecture",
            },
        },
    }

    source = PocketSource(client=mock_client)
    items = list(source.discover(test_ctx))

    assert len(items) == 1
    assert items[0].native_id == "r2"
    assert items[0].payload.get("merged") is True
    assert items[0].payload.get("wall_clock") is False


# --- 3. duration: null and pending (Acceptance 1.7) ---


def test_null_duration_fallback(test_ctx: RunContext):
    """duration: null falls back to the last segment's end."""
    mock_client = MagicMock()
    mock_client.list_recordings.return_value = [
        {"id": "r-null", "title": "Null Duration Note", "folder_id": None, "duration": None, "state": "completed", "recording_at": 1731950000, "tags": []},
    ]
    mock_client.get_folders.return_value = [{"id": "5194b8b6-ec40-4d99-91a9-059fb610141e", "name": "Private", "children": []}]
    mock_client.get_tags.return_value = [{"id": "7dbebf3f-418b-4ea1-936d-61c67089d829", "name": "private"}]

    mock_client.get_recording.return_value = {
        "success": True,
        "data": {
            "id": "r-null",
            "title": "Null Duration Note",
            "recording_at": 1731950000,
            "duration": None,
            "transcript": {
                "segments": [
                    {"start": 0.0, "end": 10.0, "text": "Part 1"},
                    {"start": 10.0, "end": 55.4, "text": "Part 2"},
                ],
                "text": "Part 1 Part 2",
            },
        },
    }

    source = PocketSource(client=mock_client)
    items = list(source.discover(test_ctx))

    assert len(items) == 1
    assert items[0].payload.get("duration") == 55.4


def test_pending_recording_give_up_after_3_days(test_ctx: RunContext):
    """Pending recording retries each poll and gives up after 3 days with dashboard row."""
    mock_client = MagicMock()
    # 4 days ago
    old_epoch = int((test_ctx.now - timedelta(days=4)).timestamp())
    mock_client.list_recordings.return_value = [
        {"id": "r-pend", "title": "Stuck Pending", "folder_id": None, "duration": 60, "state": "pending", "recording_at": old_epoch, "created_at": old_epoch, "tags": []},
    ]
    mock_client.get_folders.return_value = [{"id": "5194b8b6-ec40-4d99-91a9-059fb610141e", "name": "Private", "children": []}]
    mock_client.get_tags.return_value = [{"id": "7dbebf3f-418b-4ea1-936d-61c67089d829", "name": "private"}]

    source = PocketSource(client=mock_client)
    items = list(source.discover(test_ctx))

    # Not emitted as ready item
    assert len(items) == 0

    # Dashboard row recorded
    with test_ctx.db.connect() as conn:
        row = conn.execute("SELECT * FROM health WHERE kind = 'pending_recording_abandoned' AND key = 'r-pend';").fetchone()
        assert row is not None


# --- 4. Rate Limiting 429 and Token Rejection 401/403 (Acceptance 1.1) ---


def test_rate_limit_429_sleeps_and_retries():
    """429 sleeps until X-Ratelimit-Reset."""
    client = PocketClient(token="test_token")
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"X-Ratelimit-Reset": "1005"}
    mock_resp_429.text = "Rate limit exceeded"

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"success": True, "data": []}

    with (
        patch.object(client, "_request_raw", side_effect=[mock_resp_429, mock_resp_200]),
        patch("time.time", return_value=1000.0),
        patch("time.sleep") as mock_sleep,
    ):
        res = client.get_folders()
        assert res == []
        mock_sleep.assert_called_once_with(5.0)


def test_token_rejected_401_403(test_ctx: RunContext):
    """401/403 raises TokenRejectedError and interrupting health row."""
    client = PocketClient(token="bad_token", health=test_ctx.health)
    mock_resp_401 = MagicMock()
    mock_resp_401.status_code = 401
    mock_resp_401.text = "Unauthorized"

    with (
        patch.object(client, "_request_raw", return_value=mock_resp_401),
        pytest.raises(TokenRejectedError),
    ):
        client.get_folders()

    with test_ctx.db.connect() as conn:
        row = conn.execute("SELECT * FROM health WHERE kind = 'token_rejected';").fetchone()
        assert row is not None
        assert row["interrupt"] == 1


# --- 5. Transcript Archive (Acceptance 1.8) ---


def test_archive_written_once(tmp_path: Path):
    """Transcript archive written once; re-poll doesn't rewrite."""
    detail = {
        "success": True,
        "data": {
            "id": "rec-arch-1",
            "title": "Archive Test",
            "recording_at": 1731950000,
            "transcript": {"text": "archived text", "segments": []},
        },
    }

    # First write
    p1 = archive_transcript("rec-arch-1", 1731950000, detail, data_dir=tmp_path)
    assert p1.exists()
    mtime1 = p1.stat().st_mtime_ns

    # Read back and verify fetched_at added
    content = json.loads(gzip.decompress(p1.read_bytes()))
    assert "fetched_at" in content

    # Second write should be no-op (same path, not rewritten)
    p2 = archive_transcript("rec-arch-1", 1731950000, {"changed": "data"}, data_dir=tmp_path)
    assert p1 == p2
    assert p2.stat().st_mtime_ns == mtime1

    # Verify content was NOT overwritten
    content2 = json.loads(gzip.decompress(p2.read_bytes()))
    assert content2.get("data", {}).get("title") == "Archive Test"


# --- 6. Sensitivity Check (Acceptance 1.4) ---


def test_sensitivity_check_hit(test_ctx: RunContext):
    """Sensitivity hit makes material Private and logs a line in Runs.md."""
    mock_models = MagicMock()
    mock_models.run.return_value = MagicMock(
        parsed={"sensitive": True, "category": "health", "reason": "Mentions medical surgery"}
    )
    test_ctx.models = mock_models

    is_sens = check_sensitivity(
        "I had medical surgery yesterday",
        runner=mock_models,
        runlog=test_ctx.runlog,
        recording_id="rec-sens",
    )
    assert is_sens is True

    # Check Runs.md line logged
    runs_md = test_ctx.vault.vault_dir / "_system" / "Runs.md"
    assert runs_md.exists()
    text = runs_md.read_text(encoding="utf-8")
    assert "sensitivity hit" in text.lower()
    assert "rec-sens" in text


# --- 7. Calendar Match and Class Window Splitting (Acceptance 1.3 & 1.5) ---


def test_class_window_split_and_tail(test_ctx: RunContext):
    """Class recording is split into class window (CLASS) and tail (PERSONAL)."""
    rec_start = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    mock_client = MagicMock()
    mock_client.list_recordings.return_value = [
        {"id": "rec-cls", "title": "Physics Class", "folder_id": None, "duration": 5400, "state": "completed", "recording_at": int(rec_start.timestamp()), "tags": []},
    ]
    mock_client.get_folders.return_value = [{"id": "5194b8b6-ec40-4d99-91a9-059fb610141e", "name": "Private", "children": []}]
    mock_client.get_tags.return_value = [{"id": "7dbebf3f-418b-4ea1-936d-61c67089d829", "name": "private"}]

    mock_client.get_recording.return_value = {
        "success": True,
        "data": {
            "id": "rec-cls",
            "title": "Physics Class",
            "recording_at": int(rec_start.timestamp()),
            "duration": 5400,
            "transcript": {
                "segments": [{"start": 0.0, "end": 5400.0, "text": "Class lecture and after-chat"}],
                "text": "Class lecture and after-chat",
            },
        },
    }

    # Calendar event: 10:00 to 11:00 (60 min). Class window = 10:00 -> 11:10 (70 min = 4200 s). Tail = 4200 -> 5400 s.
    cal_event = CalendarEvent(
        id="evt-phy",
        summary="PHY 203 Lecture",
        description="phy203\nCh 9 Physics",
        start=rec_start,
        end=rec_start + timedelta(minutes=60),
        calendar="Classes",
    )

    source = PocketSource(client=mock_client, calendar_events=[cal_event])
    items = list(source.discover(test_ctx))

    class_spans = [it for it in items if it.privacy == Privacy.CLASS]
    personal_spans = [it for it in items if it.privacy == Privacy.PERSONAL]

    assert len(class_spans) == 1
    assert class_spans[0].course_key == "phy203"
    assert class_spans[0].payload["start_sec"] == 0.0
    assert class_spans[0].payload["end_sec"] == 4200.0

    assert len(personal_spans) == 1
    assert personal_spans[0].payload["start_sec"] == 4200.0
    assert personal_spans[0].payload["end_sec"] == 5400.0


# --- 8. Private Recording Never Fetched in Detail (Code Enforcement) ---


def test_private_recording_never_fetched_in_detail(test_ctx: RunContext):
    """Code strictly enforces: Private recordings are NEVER requested past the list call."""
    mock_client = MagicMock()
    mock_client.list_recordings.return_value = [
        {"id": "rec-priv", "title": "Secret Note", "folder_id": "5194b8b6-ec40-4d99-91a9-059fb610141e", "duration": 60, "state": "completed", "recording_at": 1731950000, "tags": []},
    ]
    mock_client.get_folders.return_value = [{"id": "5194b8b6-ec40-4d99-91a9-059fb610141e", "name": "Private", "children": []}]
    mock_client.get_tags.return_value = [{"id": "7dbebf3f-418b-4ea1-936d-61c67089d829", "name": "private"}]

    source = PocketSource(client=mock_client)
    items = list(source.discover(test_ctx))

    assert len(items) == 1
    assert items[0].privacy == Privacy.PRIVATE
    assert items[0].native_id == "rec-priv"

    # CRITICAL: get_recording must NOT have been called!
    mock_client.get_recording.assert_not_called()


# --- 9. Step End-to-End ---


def test_step_end_to_end(test_ctx: RunContext):
    """Test step() execution within the job pipeline."""
    with patch("articulate.pocket.source.PocketClient") as mock_cls:
        client_inst = mock_cls.return_value
        client_inst.get_folders.return_value = [{"id": "5194b8b6-ec40-4d99-91a9-059fb610141e", "name": "Private", "children": []}]
        client_inst.get_tags.return_value = [{"id": "7dbebf3f-418b-4ea1-936d-61c67089d829", "name": "private"}]
        client_inst.list_recordings.return_value = [
            {"id": "rec-01", "title": "Normal Meeting", "folder_id": None, "duration": 1800, "state": "completed", "recording_at": int(test_ctx.now.timestamp()), "tags": []}
        ]
        client_inst.get_recording.return_value = {
            "success": True,
            "data": {
                "id": "rec-01",
                "title": "Normal Meeting",
                "recording_at": int(test_ctx.now.timestamp()),
                "duration": 1800,
                "transcript": {"segments": [{"start": 0.0, "end": 1800.0, "text": "Meeting notes"}], "text": "Meeting notes"},
            },
        }

        summary = step(test_ctx)
        assert summary.done >= 1
        assert summary.failed == 0

        # Verify item was inserted into SQLite items table
        with test_ctx.db.connect() as conn:
            rows = conn.execute("SELECT * FROM items WHERE source = 'pocket';").fetchall()
            assert len(rows) >= 1
            assert rows[0]["native_id"] == "rec-01"
            assert rows[0]["privacy"] == "personal"


# --- 10. Real Integration Test (@pytest.mark.real) ---


@pytest.mark.real
def test_real_pocket_client():
    """Live check against real Pocket API while holding the lock (§3.5, Issue #55)."""
    import os
    import time

    now_local = datetime.now()
    if (now_local.hour == 1 and now_local.minute >= 45) or (2 <= now_local.hour < 4):
        pytest.skip("Never run real checks between 01:45 and 04:00")

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
        client = PocketClient()
        folders = client.get_folders()
        assert isinstance(folders, list)
        folder_names = [f.get("name") for f in folders]
        assert "Private" in folder_names, f"'Private' folder not found in: {folder_names}"

        tags = client.get_tags()
        assert isinstance(tags, list)
        tag_names = [t.get("name", "").lower() for t in tags]
        assert "private" in tag_names, f"'private' tag not found in: {tag_names}"

        recordings = client.list_recordings(lookback_days=7)
        assert isinstance(recordings, list)
    finally:
        if os.path.exists(lock_path):
            os.rmdir(lock_path)

