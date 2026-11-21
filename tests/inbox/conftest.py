"""Shared setup for inbox tests: temp data dir, DB, vault, archive and a scripted model runner."""
import gzip
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.ids import item_id as make_item_id
from articulate.core.models import Held, ModelRequest, ModelResult
from articulate.core.pipeline import Item, PipelineRunner, Privacy, RunContext
from articulate.core.runlog import RunLog
from articulate.core.vault import Vault

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
EASTERN = ZoneInfo("America/New_York")


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class ScriptedModels:
    """Answers by request type (the JSON schema's keys): gate, note or merge. Records every request."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.gate: Callable[[str], dict] = lambda text: {"decision": "note", "kind": "idea", "reason": "x"}
        self.note: list[dict] | Callable[[str], dict] = []
        self.merge: dict | None = None
        self.raise_held: str | None = None

    @staticmethod
    def mode(req: ModelRequest) -> str:
        props = (req.json_schema or {}).get("properties", {})
        if "decision" in props:
            return "gate"
        if "points" in props:
            return "merge"
        return "note"

    def calls(self, mode: str) -> list[ModelRequest]:
        return [r for r in self.requests if self.mode(r) == mode]

    def run(self, req: ModelRequest) -> ModelResult:
        self.requests.append(req)
        if self.raise_held:
            raise Held(self.raise_held)
        text = "\n".join(p.text or "" for p in req.parts)
        mode = self.mode(req)
        if mode == "gate":
            parsed: Any = self.gate(text)
        elif mode == "merge":
            parsed = self.merge
        elif callable(self.note):
            parsed = self.note(text)
        else:
            parsed = self.note.pop(0)
        return ModelResult(text=json.dumps(parsed), parsed=parsed, model="qwen3:8b", provider="ollama")

    def remaining(self, model: str) -> int:
        return 999


class Env:
    def __init__(self, tmp_path: Path):
        self.data = tmp_path / "data"
        self.db = Database(tmp_path / "test.sqlite3")
        self.db.migrate()
        self.vault_dir = tmp_path / "vault"
        self.vault = Vault(self.vault_dir, self.db)
        self.models = ScriptedModels()
        self.now = datetime(2026, 11, 20, 19, 15, tzinfo=UTC)

    def ctx(self) -> RunContext:
        runlog = RunLog(self.vault)
        return RunContext(
            run_id="01RUN",
            job="watch",
            now=self.now,
            db=self.db,
            config=None,
            models=self.models,
            vault=self.vault,
            flags=Flags(self.db, vault=self.vault),
            runlog=runlog,
        )

    def archive(self, fixture: str) -> dict:
        detail = load_fixture(fixture)
        detail["fetched_at"] = "2026-11-20T10:00:00-05:00"
        rec_id = detail["data"]["id"]
        when = datetime.fromtimestamp(detail["data"]["recording_at"], UTC)
        path = self.data / "transcripts" / f"{when.year:04d}" / f"{when.month:02d}" / f"{rec_id}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(detail).encode()))
        return detail

    def emit(self, kind: str, native_id: str, privacy: Privacy, payload: dict | None = None) -> str:
        """Simulate pocket's Source: upsert one Item through the pipeline's discovery (§3.5)."""
        item = Item(
            item_id=make_item_id("pocket", native_id),
            source="pocket",
            kind=kind,
            native_id=native_id,
            privacy=privacy,
            payload=payload or {},
        )

        class _Source:
            name = "pocket"

            def discover(self, ctx: RunContext) -> list[Item]:
                return [item]

        PipelineRunner(self.db, sources=[_Source()]).discover_items(self.ctx())
        return item.item_id

    def notes(self) -> list[Path]:
        return sorted((self.vault_dir / "Inbox").glob("*.md")) if (self.vault_dir / "Inbox").exists() else []

    def ledger(self, item_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM inbox_items WHERE item_id = ?;", (item_id,)).fetchone()
        return dict(row) if row else None


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    monkeypatch.setenv("ARTICULATE_DATA", str(tmp_path / "data"))
    return Env(tmp_path)
