"""ModelRunner framework and DefaultModelRunner implementation (§3.6)."""
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from articulate.core.clock import next_pacific_midnight
from articulate.core.config import Config
from articulate.core.db import Database
from articulate.core.models.quota import (
    count_local_night_calls,
    get_remaining_quota,
)
from articulate.core.models.usage import _parse_iso, read_claude_usage
from articulate.core.pipeline import Privacy

logger = logging.getLogger(__name__)


class Part(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    pdf: Path | None = None
    image: Path | None = None
    youtube_url: str | None = None


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    step: str
    material: Privacy
    parts: list[Part] = Field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    item_id: str | None = None
    exam_in_ramp: bool = False
    timeout_s: int | None = None
    embed: bool = False


class ModelResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = ""
    parsed: Any | None = None
    vectors: list[list[float]] | None = None
    model: str
    provider: str
    finish_reason: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    attempts: list[str] = Field(default_factory=list)


class Held(Exception):
    """Raised when nothing in the chain could run now; the item holds (§3.6)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Held: {reason}")


class ModelRunner(Protocol):
    def run(self, req: ModelRequest) -> ModelResult:
        ...

    def remaining(self, model: str) -> int:
        ...


def _set_model_state(
    db: Database,
    model: str,
    exhausted_until: str | None = None,
    missing_since: str | None = None,
) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO model_state (model, exhausted_until, missing_since)
            VALUES (?, ?, ?)
            ON CONFLICT(model) DO UPDATE SET
                exhausted_until = COALESCE(excluded.exhausted_until, model_state.exhausted_until),
                missing_since = COALESCE(excluded.missing_since, model_state.missing_since);
            """,
            (model, exhausted_until, missing_since),
        )
        conn.commit()


def _clear_missing_since(db: Database, model: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE model_state SET missing_since = NULL WHERE model = ?;",
            (model,),
        )
        conn.commit()


def _raise_unknown_model_health(db: Database, model: str, now: datetime) -> None:
    now_str = now.isoformat()
    try:
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO health (kind, key, detail, interrupt, first_seen, last_seen)
                VALUES ('unknown_model', ?, ?, 0, ?, ?)
                ON CONFLICT(kind, key, first_seen) DO UPDATE SET last_seen = excluded.last_seen;
                """,
                (model, f"unknown or retired model: {model}", now_str, now_str),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not write health row for unknown_model: %s", e)


class DefaultModelRunner:
    """Standard ModelRunner executing the model chain with quota, privacy, and gating (§3.6)."""

    def __init__(
        self,
        db: Database,
        config: Config | None = None,
        job: str = "manual",
        run_id: str | None = None,
        pace: bool = True,
    ):
        self.db = db
        self.config = config
        self.job = job
        self.run_id = run_id
        self.pace = pace
        self.run_claude_stopped = False
        self._last_attempt_time: dict[str, float] = {}

    def remaining(self, model: str) -> int:
        """Return remaining quota for a model today (§3.6)."""
        return get_remaining_quota(self.db, self.config, model, job=self.job)

    def _get_gemini_rpm(self, model: str) -> int:
        if self.config and self.config.models and self.config.models.gemini and self.config.models.gemini.models:
            m_cfg = self.config.models.gemini.models.get(model)
            if m_cfg:
                return m_cfg.rpm
        return 5

    def run(self, req: ModelRequest) -> ModelResult:
        """Execute request across model chain (§3.6)."""
        now = datetime.now(UTC)
        attempts: list[str] = []

        # Lazy imports of provider clients
        from articulate.core.clock import local_night
        from articulate.core.models.claude import call_claude
        from articulate.core.models.gemini import call_gemini
        from articulate.core.models.ollama import call_ollama

        # Determine chain from config (§3.2, §3.6)
        chain: list[str] = []
        if self.config and self.config.models and self.config.models.chains:
            chain = self.config.models.chains.get(req.step, [])

        if not chain:
            # Fallback chains if not configured
            if req.step in ("extract", "guide_exam", "method_prompts"):
                chain = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-hydrogen", "claude@ramp"]
            elif req.step in ("route_file", "rank_lecture", "library_judge", "definition_prompts", "prompt_verify", "postclass_quiz", "contrast_writer"):
                chain = ["gemini-3.5-flash-lite"]
            elif req.step == "embed_class":
                chain = ["gemini-embedding-2"]
            elif req.step == "personal_literature":
                chain = ["claude"]
            elif req.step == "personal_text":
                chain = ["ollama:qwen3:8b"]
            elif req.step == "embed_personal":
                chain = ["ollama:embeddinggemma"]
            elif req.step == "vl_transcribe":
                chain = ["ollama:qwen3-vl:8b-instruct"]
            else:
                chain = ["gemini-3.5-flash-lite"]

        last_skip_reason: str = "quota"

        for model_entry in chain:
            # 1. Privacy Check (§3.6, #29)
            # Personal / private never reach gemini-*
            # Private never reaches claude
            is_gemini_entry = model_entry.startswith("gemini-")
            is_claude_entry = model_entry in ("claude", "claude@ramp")

            if req.material in (Privacy.PERSONAL, Privacy.PRIVATE) and is_gemini_entry:
                continue

            if req.material == Privacy.PRIVATE and is_claude_entry:
                continue

            # Determine provider & target model id
            if is_gemini_entry:
                provider = "gemini"
                target_model = model_entry
            elif is_claude_entry:
                if model_entry == "claude@ramp" and not req.exam_in_ramp:
                    continue
                if self.run_claude_stopped:
                    last_skip_reason = "claude_over_budget"
                    continue
                provider = "claude"
                target_model = "sonnet"
                if self.config and self.config.models and self.config.models.claude:
                    target_model = self.config.models.claude.model
            elif model_entry.startswith("ollama:"):
                provider = "ollama"
                target_model = model_entry[len("ollama:"):]
            else:
                provider = "gemini" if "gemini" in model_entry else "ollama"
                target_model = model_entry

            # 2. Availability Check (§3.6)
            with self.db.connect() as conn:
                state_row = conn.execute("SELECT * FROM model_state WHERE model = ?", (target_model,)).fetchone()
                if state_row:
                    if state_row["exhausted_until"]:
                        try:
                            ex_dt = _parse_iso(state_row["exhausted_until"])
                            if ex_dt > now:
                                last_skip_reason = "quota"
                                continue
                        except Exception:  # noqa: BLE001, S110
                            pass
                    if state_row["missing_since"]:
                        continue

            # 3. Provider-specific Gating & Quota
            if provider == "gemini":
                rem = self.remaining(target_model)
                if rem <= 0:
                    last_skip_reason = "quota"
                    continue

                # 4. Pace Check: at least 60/rpm seconds between attempts on this model
                if self.pace:
                    rpm = self._get_gemini_rpm(target_model)
                    min_interval = 60.0 / max(1, rpm)
                    last_time = self._last_attempt_time.get(target_model, 0.0)
                    now_mono = time.monotonic()
                    time_since = now_mono - last_time
                    if time_since < min_interval:
                        time.sleep(min_interval - time_since)
                    self._last_attempt_time[target_model] = time.monotonic()

                # Call Gemini
                outcome, res, retry_delay = call_gemini(
                    self.db,
                    self.config,
                    req,
                    target_model,
                    run_id=self.run_id,
                    timeout_s=req.timeout_s,
                    now=now,
                )
                attempts.append(outcome)

                if outcome == "ok":
                    _clear_missing_since(self.db, target_model)
                    if res is not None:
                        res.attempts = attempts
                        return res

                if outcome in ("http_500", "http_503"):
                    # One retry on same model (§3.6)
                    self._last_attempt_time[target_model] = time.monotonic()
                    outcome2, res2, _ = call_gemini(
                        self.db,
                        self.config,
                        req,
                        target_model,
                        run_id=self.run_id,
                        timeout_s=req.timeout_s,
                        now=now,
                    )
                    attempts.append(outcome2)
                    if outcome2 == "ok":
                        _clear_missing_since(self.db, target_model)
                        if res2 is not None:
                            res2.attempts = attempts
                            return res2
                    continue

                if outcome == "http_429_minute":
                    # Wait retryDelay once and retry on same model
                    delay = retry_delay or 5.0
                    if self.pace:
                        time.sleep(delay)
                    if self.pace:
                        self._last_attempt_time[target_model] = time.monotonic()
                    outcome2, res2, _ = call_gemini(
                        self.db,
                        self.config,
                        req,
                        target_model,
                        run_id=self.run_id,
                        timeout_s=req.timeout_s,
                        now=now,
                    )
                    attempts.append(outcome2)
                    if outcome2 == "ok":
                        _clear_missing_since(self.db, target_model)
                        if res2 is not None:
                            res2.attempts = attempts
                            return res2
                    continue

                if outcome == "http_429_day":
                    ex_until = next_pacific_midnight(now).isoformat()
                    _set_model_state(self.db, target_model, exhausted_until=ex_until)
                    last_skip_reason = "quota"
                    continue

                if outcome == "http_404":
                    _set_model_state(self.db, target_model, missing_since=now.isoformat())
                    _raise_unknown_model_health(self.db, target_model, now)
                    continue

                if outcome in ("max_tokens", "schema_rejected") and res is not None:
                    # Return immediately to caller with finish_reason (§3.6)
                    res.attempts = attempts
                    return res

                if outcome == "invalid_json":
                    continue

                if outcome == "timeout":
                    continue

            elif provider == "claude":
                # Check max calls per night cap (§3.6)
                l_night = local_night(now)
                calls_tonight = count_local_night_calls(self.db, "claude", l_night)
                max_night = 15
                sess_ceiling = 70.0
                week_ceiling = 50.0
                if self.config and self.config.models and self.config.models.claude:
                    max_night = self.config.models.claude.max_calls_per_night
                    sess_ceiling = self.config.models.claude.session_ceiling_pct
                    week_ceiling = self.config.models.claude.weekly_ceiling_pct

                if calls_tonight >= max_night:
                    last_skip_reason = "claude_over_budget"
                    continue

                # Read usage report (§3.6)
                usage_res = read_claude_usage(self.db, self.config, now=now)
                if not usage_res.ok:
                    last_skip_reason = "claude_over_budget"
                    continue

                if (usage_res.session_pct is not None and usage_res.session_pct >= sess_ceiling) or (
                    usage_res.weekly_pct is not None and usage_res.weekly_pct >= week_ceiling
                ):
                    last_skip_reason = "claude_over_budget"
                    continue

                # Execute call
                outcome, res = call_claude(
                    self.db,
                    self.config,
                    req,
                    target_model,
                    run_id=self.run_id,
                    now=now,
                )
                attempts.append(outcome)

                if outcome == "ok" and res is not None:
                    res.attempts = attempts
                    return res

                if outcome == "rate_limit_warning":
                    self.run_claude_stopped = True
                    last_skip_reason = "claude_over_budget"
                    continue

                continue

            elif provider == "ollama":
                outcome, res = call_ollama(
                    self.db,
                    self.config,
                    req,
                    target_model,
                    run_id=self.run_id,
                    now=now,
                )
                attempts.append(outcome)
                if outcome == "ok" and res is not None:
                    res.attempts = attempts
                    return res
                continue

        # If nothing could run
        raise Held(last_skip_reason)
