"""Ingest attempts, flag files, failures, and acks from Encore (§3.7, Acceptance check 2.37, 2.38)."""

import json
import logging
from datetime import UTC, datetime

from articulate.core.handoff_schemas import (
    Attempt,
    Flag,
    Header,
    State,
    load_handoff_file,
)
from articulate.core.paths import from_encore_dir, to_encore_failed_dir
from articulate.core.pipeline import RunContext

logger = logging.getLogger(__name__)


def ingest_from_encore(ctx: RunContext) -> dict[str, int]:
    """
    Ingest attempts, flags, acks, and failed import batches from Encore hand-off folder.
    - Attempts: committed to SQLite deduped on attemptId, then deleted (§3.7). Never sent to Gemini.
    - Flags: withdraw prompt (status='retired'), attempt Shape regeneration or list on weekly flags, delete file (§3.7).
    - Failed: failed/ file + .error.txt -> handoff_files.failed_at + weekly flag (§3.7).
    - Acks: appliedBatches from state.json -> handoff_files.acked_at.
    """
    counts = {
        "attempts": 0,
        "flags": 0,
        "acks": 0,
        "failures": 0,
    }

    if ctx.db is None:
        return counts

    now_dt = ctx.now if ctx.now is not None else datetime.now(UTC)
    now_str = now_dt.isoformat()

    # 1. Process from-encore/
    handoff_from = from_encore_dir()
    if handoff_from.exists():
        for p in sorted(handoff_from.iterdir()):
            if p.name.startswith("."):
                continue

            # A. attempts-*.jsonl
            if p.name.startswith("attempts-") and p.name.endswith(".jsonl"):
                try:
                    lines = p.read_text(encoding="utf-8").strip().splitlines()
                    if not lines:
                        p.unlink(missing_ok=True)
                        continue

                    # Validate header on line 0
                    hdr_data = json.loads(lines[0])
                    hdr = Header.model_validate(hdr_data)
                    if hdr.schema_ != "encore.attempt" or hdr.version != 1:
                        raise ValueError(
                            f"Unknown attempt schema {hdr.schema_} version {hdr.version}"
                        )

                    parsed_attempts: list[Attempt] = []
                    for line in lines[1:]:
                        if not line.strip():
                            continue
                        parsed_attempts.append(Attempt.model_validate(json.loads(line)))

                    with ctx.db.connect() as conn:
                        for att in parsed_attempts:
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO attempts (
                                    attempt_id, prompt_id, concept_id, answered_at,
                                    answer_text, grade, ms_taken, session_kind,
                                    calibrated, recorded_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                                """,
                                (
                                    str(att.attemptId),
                                    att.promptId,
                                    att.conceptId,
                                    att.answeredAt.isoformat(),
                                    att.answerText,
                                    att.grade,
                                    att.msTaken,
                                    att.sessionKind,
                                    1 if att.calibrated else 0,
                                    now_str,
                                ),
                            )
                        conn.commit()

                    counts["attempts"] += len(parsed_attempts)
                    # Commited to SQLite -> delete file (§3.7)
                    p.unlink(missing_ok=True)
                except Exception as e:  # noqa: BLE001
                    logger.error("Failed to ingest attempts file %s: %s", p.name, e)
                    if ctx.flags:
                        ctx.flags.add(
                            "encore_schema",
                            f"Encore attempts file {p.name} failed: {e}",
                            [("retry", "retry"), ("drop", "drop")],
                        )

            # B. flag-*.json
            elif p.name.startswith("flag-") and p.name.endswith(".json"):
                try:
                    flag = load_handoff_file(
                        p, Flag, expected_schema="encore.flag", expected_version=1
                    )
                    flag_id_str = str(flag.flagId)
                    prompt_id = flag.promptId
                    concept_id = flag.conceptId

                    is_second_flag = False
                    with ctx.db.connect() as conn:
                        # 1. Withdraw prompt at once
                        conn.execute(
                            "UPDATE prompts SET status = 'retired' WHERE prompt_id = ?;",
                            (prompt_id,),
                        )

                        # 2. Check if this prompt was already flagged before
                        prev = conn.execute(
                            "SELECT COUNT(*) as cnt FROM handoff_card_flags WHERE prompt_id = ?;",
                            (prompt_id,),
                        ).fetchone()
                        is_second_flag = bool(prev and prev["cnt"] > 0)
                        conn.commit()

                    state = "queued"
                    detail = ""

                    if is_second_flag:
                        # Second flag on same prompt -> weekly list directly
                        state = "listed"
                        detail = "Second flag on prompt -> weekly list"
                        with ctx.db.connect() as conn:
                            conn.execute(
                                "UPDATE handoff_card_flags SET state = 'listed', updated_at = ? WHERE prompt_id = ?;",
                                (now_str, prompt_id),
                            )
                            conn.commit()
                        if ctx.flags:
                            ctx.flags.add(
                                "card_wrong",
                                f"Card flagged second time: {concept_id} ({flag.reason})",
                                [("keep", "keep"), ("drop", "drop")],
                                item_id=prompt_id,
                            )
                    else:
                        # First flag -> attempt Shape regeneration (§3.7, proposal)
                        regen_fn = None
                        try:
                            from articulate.intake.shape import regenerate

                            regen_fn = regenerate
                        except (ImportError, AttributeError):
                            pass

                        if regen_fn is not None:
                            try:
                                res = regen_fn(
                                    prompt_id,
                                    note=flag.note,
                                    suggested_fix=flag.suggestedFix,
                                    ctx=ctx,
                                )
                                if getattr(res, "ok", False):
                                    state = "regenerated"
                                    detail = f"Regenerated as {getattr(res, 'new_prompt_id', None)}"
                                else:
                                    state = "listed"
                                    detail = f"Regeneration failed: {getattr(res, 'detail', '')}"
                                    if ctx.flags:
                                        ctx.flags.add(
                                            "card_wrong",
                                            f"Card regeneration failed for {concept_id}: {detail}",
                                            [("keep", "keep"), ("drop", "drop")],
                                            item_id=prompt_id,
                                        )
                            except Exception as rx:  # noqa: BLE001
                                state = "queued"
                                detail = f"Error during regeneration: {rx}"
                        else:
                            state = "queued"
                            detail = "Shape regeneration not available, queued"

                    # Record in handoff_card_flags
                    with ctx.db.connect() as conn:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO handoff_card_flags (
                                flag_id, prompt_id, concept_id, reason, note,
                                suggested_fix, flagged_at, state, detail,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """,
                            (
                                flag_id_str,
                                prompt_id,
                                concept_id,
                                flag.reason,
                                flag.note,
                                flag.suggestedFix,
                                flag.flaggedAt.isoformat(),
                                state,
                                detail,
                                now_str,
                                now_str,
                            ),
                        )
                        conn.commit()

                    counts["flags"] += 1
                    # Committed to SQLite -> delete file (§3.7)
                    p.unlink(missing_ok=True)
                except Exception as e:  # noqa: BLE001
                    logger.error("Failed to ingest flag file %s: %s", p.name, e)
                    if ctx.flags:
                        ctx.flags.add(
                            "encore_schema",
                            f"Encore flag file {p.name} failed: {e}",
                            [("retry", "retry"), ("drop", "drop")],
                        )

            # C. state.json acks
            elif p.name == "state.json":
                try:
                    state_obj = load_handoff_file(
                        p, State, expected_schema="encore.state", expected_version=1
                    )
                    with ctx.db.connect() as conn:
                        for b_id in state_obj.appliedBatches:
                            cur = conn.execute(
                                """
                                UPDATE handoff_files
                                SET acked_at = ?
                                WHERE (file_id = ? OR file_id = ? OR file_id = ?) AND acked_at IS NULL;
                                """,
                                (now_str, b_id, f"import-{b_id}", b_id.removeprefix("import-")),
                            )
                            if cur.rowcount > 0:
                                counts["acks"] += cur.rowcount
                        conn.commit()
                except Exception as e:  # noqa: BLE001
                    logger.debug("Could not read appliedBatches from state.json: %s", e)

    # 2. Process to-encore/failed/
    failed_dir = to_encore_failed_dir()
    if failed_dir.exists():
        for fp in sorted(failed_dir.iterdir()):
            if fp.name.startswith(".") or fp.name.endswith(".error.txt"):
                continue

            # Identify batch / file id
            stem = fp.stem
            batch_id = stem.removeprefix("import-")

            error_file = failed_dir / f"{batch_id}.error.txt"
            if not error_file.exists():
                error_file = failed_dir / f"{stem}.error.txt"

            err_text = ""
            if error_file.exists():
                err_text = error_file.read_text(encoding="utf-8").strip()

            with ctx.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE handoff_files
                    SET failed_at = ?, error = ?
                    WHERE file_id = ? OR file_id = ? OR file_id = ?;
                    """,
                    (now_str, err_text, stem, batch_id, f"import-{batch_id}"),
                )
                conn.commit()

            if ctx.flags:
                ctx.flags.add(
                    "import_failed",
                    f"Encore import failed for {batch_id}: {err_text}",
                    [("retry", "retry"), ("drop", "drop")],
                    item_id=batch_id,
                )

            counts["failures"] += 1
            # Clean up processed failed files
            fp.unlink(missing_ok=True)
            if error_file.exists():
                error_file.unlink(missing_ok=True)

    return counts
