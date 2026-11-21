"""Shared harness for the tasks slice tests: temp DB, vault, fake model runner, fake shortcuts."""
import gzip
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.health import Health
from articulate.core.models.runner import Held, ModelRequest, ModelResult
from articulate.core.pipeline import RunContext
from articulate.core.runlog import RunLog
from articulate.core.taskqueue import TaskQueue
from articulate.core.vault import Vault

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tasks"
FAKE_SHORTCUTS = FIXTURES / "fake-shortcuts"
VOICE_NOTE = "recording-detail-voice-note.json"
VOICE_NOTE_OUTPUT = "model-output-voice-note.json"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _one_hot(text: str, dim: int = 512) -> list[float]:
    """Unrelated texts get orthogonal vectors, so nothing is a near-duplicate by accident."""
    vec = [0.0] * dim
    vec[3 + int(hashlib.sha256(text.encode()).hexdigest(), 16) % (dim - 3)] = 1.0
    return vec


class FakeRunner:
    """Stands in for ModelRunner: extraction returns a fixture, embeddings come from `vectors`."""

    def __init__(self, output: dict | None = None):
        self.output = output if output is not None else {"tasks": []}
        self.vectors: dict[str, list[float]] = {}
        self.calls: list[ModelRequest] = []
        self.held: str | None = None

    def run(self, req: ModelRequest) -> ModelResult:
        self.calls.append(req)
        if self.held:
            raise Held(self.held)
        if req.embed:
            vecs = [self.vectors.get(p.text or "") or _one_hot(p.text or "") for p in req.parts]
            return ModelResult(model="embeddinggemma", provider="ollama", vectors=vecs)
        return ModelResult(model="qwen3:8b", provider="ollama", text=json.dumps(self.output),
                           parsed=self.output)

    def remaining(self, model: str) -> int:
        return 1000

    @property
    def extract_calls(self) -> list[ModelRequest]:
        return [c for c in self.calls if not c.embed]


class FakeShortcuts:
    """Owns the JSON state file the fake `shortcuts` binary reads and writes."""

    def __init__(self, path: Path):
        self.path = path
        self.write({"installed": True, "fail": False, "tasks": {}, "tombstones": [], "calls": []})

    def read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, state: dict) -> None:
        self.path.write_text(json.dumps(state), encoding="utf-8")

    def set(self, **kw) -> None:
        s = self.read()
        s.update(kw)
        self.write(s)

    def calls(self, shortcut: str | None = None) -> list[dict]:
        return [c for c in self.read()["calls"] if shortcut is None or c["shortcut"] == shortcut]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    monkeypatch.setenv("ARTICULATE_DATA", str(data))
    bin_copy = tmp_path / "shortcuts"
    shutil.copy(FAKE_SHORTCUTS, bin_copy)
    bin_copy.chmod(0o755)
    shortcuts = FakeShortcuts(tmp_path / "shortcuts-state.json")
    monkeypatch.setenv("FAKE_SHORTCUTS_STATE", str(shortcuts.path))

    db = Database(tmp_path / "test.sqlite3")
    db.migrate()
    vault = Vault(tmp_path / "Vault", db)
    runlog = RunLog(vault)
    queue = TaskQueue(db)
    health = Health(db, runlog=runlog, taskqueue=queue, vault=vault)
    flags = Flags(db, vault=vault, taskqueue=queue)
    runner = FakeRunner(load_fixture(VOICE_NOTE_OUTPUT))

    class Env:
        pass

    e = Env()
    e.tmp = tmp_path
    e.data = data
    e.db = db
    e.vault = vault
    e.runlog = runlog
    e.queue = queue
    e.health = health
    e.flags = flags
    e.runner = runner
    e.shortcuts = shortcuts
    e.shortcuts_bin = str(bin_copy)

    def ctx(job: str = "watch", now: datetime | None = None) -> RunContext:
        return RunContext(
            run_id="01TESTRUN", job=job, now=now or datetime(2026, 11, 20, 5, 15, tzinfo=UTC),
            db=db, config=None, models=runner, vault=vault, flags=flags, health=health,
            tasks=queue, runlog=runlog,
        )

    e.ctx = ctx
    return e


def archive_transcript(data_dir: Path, detail: dict) -> Path:
    """Write a transcript where pocket archives it: TRANSCRIPTS/YYYY/MM/<id>.json.gz."""
    rec = detail["data"]
    dt = datetime.fromtimestamp(rec["recording_at"], tz=UTC)
    path = data_dir / "transcripts" / f"{dt:%Y}" / f"{dt:%m}" / f"{rec['id']}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(detail).encode("utf-8")))
    return path


def add_item(db: Database, *, item_id: str, native_id: str, kind: str = "recording",
             privacy: str = "personal", status: str = "done", payload: dict | None = None,
             source: str = "pocket") -> None:
    now = "2026-11-20T05:00:00+00:00"
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO items (item_id, source, kind, native_id, version, privacy, stage, status,
                                  payload_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, '', ?, 'note', ?, ?, ?, ?);""",
            (item_id, source, kind, native_id, privacy, status, json.dumps(payload or {}), now, now),
        )
        conn.commit()


def task_rows(db: Database) -> dict[str, dict]:
    with db.connect() as conn:
        return {r["external_id"]: dict(r) for r in conn.execute("SELECT * FROM tasks;").fetchall()}
