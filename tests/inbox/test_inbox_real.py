"""Real check on local qwen3:8b with a real long recording (SPEC-requirements 1.10, 1.14 R).

Uses a recording from `DATA/fixtures/real/` (listed in `real-manifest.toml`); skips when it or Ollama is missing.
Nothing it writes leaves the temp folder, and it prints no transcript text.
"""
import gzip
import json
import shutil
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path

import httpx
import pytest
from conftest import EASTERN

from articulate.core.models import DefaultModelRunner
from articulate.core.pipeline import Privacy
from articulate.inbox import run_inbox

REAL_DIR = Path("~/Library/Application Support/articulate/fixtures/real").expanduser()
REAL = "d7be217b-cfb4-44e6-b5f6-a3d5000eaeec.json"  # 43 min, chunked


def _ollama_has_qwen() -> bool:
    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3).json()
    except httpx.HTTPError:
        return False
    return any(m["name"] == "qwen3:8b" for m in tags.get("models", []))


@pytest.mark.real
def test_real_long_recording_on_qwen(request, env):
    if "real" not in (request.config.getoption("-m") or ""):
        pytest.skip("runs only with -m real (several minutes on qwen3:8b)")
    if dt_time(1, 45) <= datetime.now(EASTERN).time() <= dt_time(4, 0):
        pytest.skip("not during the nightly window")
    src = REAL_DIR / REAL
    if not src.exists():
        pytest.skip("real fixture missing")
    if not _ollama_has_qwen():
        pytest.skip("Ollama with qwen3:8b not running")

    detail = json.loads(src.read_text())
    rec_id = detail["data"]["id"]
    target = env.data / "transcripts" / "real" / "real" / f"{rec_id}.json.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(json.dumps(detail).encode()))
    item = env.emit("recording", rec_id, Privacy.PERSONAL)

    ctx = env.ctx()
    ctx.models = DefaultModelRunner(env.db, None, job="manual", run_id=ctx.run_id)
    run_inbox(ctx, tz=EASTERN, meeting_matcher=lambda s, e, c: None)

    row = env.ledger(item)
    with env.db.connect() as conn:
        providers = {r[0] for r in conn.execute("SELECT DISTINCT provider FROM model_calls;")}
        calls = conn.execute("SELECT COUNT(*) FROM model_calls;").fetchone()[0]
    assert providers == {"ollama"}
    print(f"\nreal inbox: status={row['status']} outcome={row['outcome']} "
          f"hold={row['hold_reason']} kind={row['kind']} calls={calls}")
    assert (row["status"], row["outcome"] or row["hold_reason"]) in {
        ("done", "note"), ("done", "tasks_only"), ("done", "skip_gate"), ("held", "quote_check_failed"),
    }
    if row["outcome"] == "note":
        [path] = env.notes()
        body = path.read_text()
        assert 1 <= body.count(" ≈") <= 5
    shutil.rmtree(env.vault_dir, ignore_errors=True)
