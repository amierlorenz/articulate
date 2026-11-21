"""Canvas Pick step orchestrator (§3.5, §3.14, SPEC-requirements 2.1–2.10)."""
import hashlib
import logging
import re
import shutil
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from articulate.core import paths
from articulate.core.config import CourseConfig, CoursesConfig
from articulate.core.flags import Flags
from articulate.core.health import Health
from articulate.core.ids import item_id
from articulate.core.jobs import StepSummary
from articulate.core.pipeline import RunContext
from articulate.core.runlog import RunLog
from articulate.core.taskqueue import TaskQueue
from articulate.core.vault import Vault
from articulate.intake.canvas.client import CanvasAuthError, CanvasClient
from articulate.intake.canvas.drop import process_drop_folder
from articulate.intake.canvas.exams import sync_course_exams
from articulate.intake.canvas.route import route_file

logger = logging.getLogger(__name__)

SKIPPED_FOLDER_NAMES = {"course_image", "course_video", "icons"}


def _ensure_context_services(ctx: RunContext) -> None:
    """Ensure vault, flags, health, tasks, and runlog are populated on ctx."""
    if ctx.vault is None:
        try:
            v_path = paths.vault_path(ctx.config)
            ctx.vault = Vault(v_path, ctx.db)
        except Exception:  # noqa: BLE001, S110
            pass

    if ctx.tasks is None and ctx.db is not None:
        ctx.tasks = TaskQueue(ctx.db)

    if ctx.runlog is None and ctx.vault is not None:
        ctx.runlog = RunLog(ctx.vault)

    if ctx.health is None and ctx.db is not None:
        ctx.health = Health(ctx.db, runlog=ctx.runlog, taskqueue=ctx.tasks, vault=ctx.vault)

    if ctx.flags is None and ctx.db is not None:
        ctx.flags = Flags(ctx.db, vault=ctx.vault, taskqueue=ctx.tasks)


def _matches_any_glob(name: str, globs: list[str]) -> bool:
    """Check if name matches any glob in list (case-insensitive)."""
    for g in globs:
        if fnmatch(name.lower(), g.lower()):
            return True
    return False


def _compute_sha256(path: Path) -> str:
    """Compute sha256 of file content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _get_nearest_exam(course_key: str, ctx: RunContext) -> tuple[str | None, float]:
    """
    Find nearest dated exam for priority ordering (§3.5, SPEC 2.7).
    Returns (exam_key, priority_score).
    Lower priority runs first. Undated exams get high priority value.
    """
    with ctx.db.connect() as conn:
        rows = conn.execute(
            "SELECT exam_id, date FROM exams WHERE course_key = ? ORDER BY date ASC",
            (course_key,),
        ).fetchall()

        now_str = ctx.now.strftime("%Y-%m-%d")
        for r in rows:
            d = r["date"]
            if d and d >= now_str:
                # Days difference
                try:
                    dt = datetime.fromisoformat(d)
                    days = (dt - ctx.now).total_seconds() / 86400.0
                    return (r["exam_id"], max(0.0, days))
                except Exception:  # noqa: BLE001, S110
                    pass

        # If only undated or past exams
        for r in rows:
            if not r["date"]:
                return (r["exam_id"], 1000000.0)

    return (None, 500000.0)


def pick_step(ctx: RunContext) -> StepSummary:
    """
    Registry step: intake.pick (order 200, jobs: nightly + pass2).
    Discovers files from module File items, module Page links, Files tab,
    announcement attachments, and Drop/ folder. Routes them and syncs exams.
    """
    _ensure_context_services(ctx)
    summary = StepSummary()

    # Load courses config
    courses_cfg = ctx.config.courses if (ctx.config and ctx.config.courses) else None
    if not courses_cfg:
        c_path = paths.courses_toml_path()
        if c_path.exists():
            try:
                from articulate.core.config import load_courses_config
                courses_cfg = load_courses_config(c_path, db=ctx.db)
            except Exception:  # noqa: BLE001
                # Fallback to direct tomllib parsing if config model has issues with optional fields
                import tomllib
                try:
                    with open(c_path, "rb") as f:
                        raw = tomllib.load(f)
                    c_dict: dict[str, CourseConfig] = {}
                    for k, v in raw.get("courses", {}).items():
                        c_dict[k] = CourseConfig(
                            canvas_id=v.get("canvas_id", 0),
                            name=v.get("name", k),
                            skip=v.get("skip"),
                            active=v.get("active", True),
                        )
                    courses_cfg = CoursesConfig(
                        default_skip=raw.get("default_skip", ["Syllabus*"]),
                        courses=c_dict,
                    )
                except Exception as te:  # noqa: BLE001
                    logger.warning("Could not load courses.toml: %s", te)

    if not courses_cfg or not courses_cfg.courses:
        logger.info("No courses found to pick.")
        return summary

    default_skip = courses_cfg.default_skip

    for c_key, c_val in courses_cfg.courses.items():
        if not c_val.active:
            continue

        course_id = c_val.canvas_id
        if not course_id:
            # Drop-only course
            drop_items = process_drop_folder(c_key, ctx)
            for di in drop_items:
                if di.is_identical:
                    continue
                _handle_picked_file(
                    ctx=ctx,
                    course_key=c_key,
                    origin="drop",
                    slot=di.slot,
                    display_name=di.display_name,
                    archive_path=di.archive_path,
                    file_hash=di.file_id,
                    folder_hint="Drop",
                    is_changed=di.is_changed,
                    summary=summary,
                )
            continue

        # 1. Sync exams and dates
        client = CanvasClient()
        try:
            sync_course_exams(c_key, course_id, c_val, client, ctx)
        except CanvasAuthError as ae:
            logger.error("Canvas auth error syncing exams for course %s: %s", c_key, ae)
            if ctx.health:
                ctx.health.raise_("token_rejected", key="canvas", detail=str(ae), interrupt=True)
            summary.failed += 1
            continue
        except Exception as e:  # noqa: BLE001
            logger.warning("Error syncing exams for course %s: %s", c_key, e)

        # 2. Discover Canvas files from the 4 places (§3.14, SPEC 2.1)
        # We collect: dict[canvas_file_id, file_meta]
        discovered_by_file_id: dict[int, dict[str, Any]] = {}

        # Determine skip rules for this course (Rule 2.3)
        course_skip = c_val.skip if c_val.skip is not None else default_skip

        # Place 1 & 2: Modules (File items and Page links)
        try:
            modules = client.list_modules(course_id)
            for mod in modules:
                mod_name = mod.get("name", "")
                if _matches_any_glob(mod_name, course_skip):
                    logger.debug("Skipping module '%s' due to skip rules", mod_name)
                    continue

                for m_item in mod.get("items", []):
                    item_type = m_item.get("type")
                    if item_type == "File":
                        c_fid = m_item.get("content_id")
                        if c_fid and c_fid not in discovered_by_file_id:
                            discovered_by_file_id[c_fid] = {
                                "canvas_file_id": c_fid,
                                "slot": str(m_item.get("id", c_fid)),
                                "display_name": m_item.get("title", f"file_{c_fid}"),
                                "folder_hint": mod_name,
                                "download_url": m_item.get("url") or f"{client.base_url}/files/{c_fid}/download",
                            }
                    elif item_type == "Page":
                        page_url = m_item.get("page_url")
                        if page_url:
                            try:
                                page_data = client.get_page(course_id, page_url)
                                body_html = page_data.get("body", "")
                                # Scan for /files/(\d+)
                                for m_link in re.finditer(r'/files/(\d+)(?:/download)?["\']?>([^<]*)<', body_html):
                                    c_fid = int(m_link.group(1))
                                    link_title = m_link.group(2).strip() or f"file_{c_fid}"
                                    if c_fid not in discovered_by_file_id:
                                        discovered_by_file_id[c_fid] = {
                                            "canvas_file_id": c_fid,
                                            "slot": f"page:{m_item.get('id', page_url)}:{c_fid}",
                                            "display_name": link_title,
                                            "folder_hint": f"Page: {m_item.get('title', page_url)}",
                                            "download_url": f"{client.base_url}/files/{c_fid}/download",
                                        }
                            except Exception as pe:  # noqa: BLE001
                                logger.warning("Could not read page %s: %s", page_url, pe)
        except CanvasAuthError as ae:
            if ctx.health:
                ctx.health.raise_("token_rejected", key="canvas", detail=str(ae), interrupt=True)
            summary.failed += 1
            return summary
        except Exception as me:  # noqa: BLE001
            logger.warning("Error fetching modules for course %s: %s", c_key, me)

        # Place 3: Files tab (when readable, status 200, skip 403)
        try:
            folders = client.list_folders(course_id)
            folder_map = {f.get("id"): f.get("name", "") for f in folders if isinstance(f, dict)}
            skipped_folder_ids = {
                f.get("id")
                for f in folders
                if isinstance(f, dict) and f.get("name", "").lower() in SKIPPED_FOLDER_NAMES
            }

            files_tab = client.list_files(course_id)
            for f in files_tab:
                c_fid = f.get("id")
                if not c_fid or c_fid in discovered_by_file_id:
                    continue
                f_id = f.get("folder_id")
                if f_id in skipped_folder_ids:
                    continue

                d_name = f.get("display_name", f"file_{c_fid}")
                folder_name = folder_map.get(f_id, "")
                discovered_by_file_id[c_fid] = {
                    "canvas_file_id": c_fid,
                    "slot": f"{folder_name}/{d_name}" if folder_name else d_name,
                    "display_name": d_name,
                    "folder_hint": folder_name,
                    "download_url": f.get("url") or f"{client.base_url}/files/{c_fid}/download",
                }
        except Exception as fe:  # noqa: BLE001
            logger.warning("Files tab error for course %s: %s", c_key, fe)

        # Place 4: Posted Announcements attachments and links
        try:
            announcements = client.list_announcements(course_id)
            for ann in announcements:
                if not ann.get("posted_at") or ann.get("published") is False:
                    continue
                ann_title = ann.get("title", "Announcement")
                ann_id = ann.get("id", 0)

                # Attachments list
                for att in ann.get("attachments", []):
                    c_fid = att.get("id")
                    if c_fid and c_fid not in discovered_by_file_id:
                        d_name = att.get("display_name", f"file_{c_fid}")
                        discovered_by_file_id[c_fid] = {
                            "canvas_file_id": c_fid,
                            "slot": f"announcement:{ann_id}:{d_name}",
                            "display_name": d_name,
                            "folder_hint": ann_title,
                            "download_url": att.get("url") or f"{client.base_url}/files/{c_fid}/download",
                        }

                # Body links
                msg = ann.get("message", "")
                for m_link in re.finditer(r'/files/(\d+)(?:/download)?["\']?>([^<]*)<', msg):
                    c_fid = int(m_link.group(1))
                    link_title = m_link.group(2).strip() or f"file_{c_fid}"
                    if c_fid not in discovered_by_file_id:
                        discovered_by_file_id[c_fid] = {
                            "canvas_file_id": c_fid,
                            "slot": f"announcement:{ann_id}:{link_title}",
                            "display_name": link_title,
                            "folder_hint": ann_title,
                            "download_url": f"{client.base_url}/files/{c_fid}/download",
                        }
        except Exception as ane:  # noqa: BLE001
            logger.warning("Announcements check error for course %s: %s", c_key, ane)

        # Process discovered Canvas files
        data_dir = paths.get_data_dir()
        temp_dir = data_dir / "work" / ctx.run_id / "downloads"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Check existing slots for this course in source_files
        existing_canvas_slots: dict[str, str] = {}
        with ctx.db.connect() as conn:
            rows = conn.execute(
                "SELECT slot, file_id FROM source_files WHERE course_key = ?",
                (c_key,),
            ).fetchall()
            for r in rows:
                existing_canvas_slots[r["slot"]] = r["file_id"]

        for c_fid, f_info in discovered_by_file_id.items():
            slot = f_info["slot"]
            d_name = f_info["display_name"]
            f_hint = f_info["folder_hint"]
            dl_url = f_info["download_url"]

            # Download temporarily to compute sha256
            safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", d_name)
            temp_file = temp_dir / f"{c_fid}_{safe_name}"

            try:
                client.download_file(dl_url, temp_file)
            except Exception as de:  # noqa: BLE001
                logger.warning("Failed to download file %s (%s): %s", c_fid, d_name, de)
                continue

            file_hash = _compute_sha256(temp_file)
            ext = temp_file.suffix.lstrip(".") or "bin"
            arch_p = paths.archive_path(c_key, file_hash, ext)

            if not arch_p.exists():
                arch_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(temp_file, arch_p)

            old_hash = existing_canvas_slots.get(slot)
            if old_hash == file_hash:
                # Identical re-upload -> nothing
                continue

            is_changed = old_hash is not None and old_hash != file_hash

            _handle_picked_file(
                ctx=ctx,
                course_key=c_key,
                origin="canvas",
                slot=slot,
                display_name=d_name,
                archive_path=arch_p,
                file_hash=file_hash,
                folder_hint=f_hint,
                canvas_file_id=c_fid,
                is_changed=is_changed,
                summary=summary,
            )

        # 3. Process Drop folder for this course
        drop_items = process_drop_folder(c_key, ctx)
        for di in drop_items:
            if di.is_identical:
                continue
            _handle_picked_file(
                ctx=ctx,
                course_key=c_key,
                origin="drop",
                slot=di.slot,
                display_name=di.display_name,
                archive_path=di.archive_path,
                file_hash=di.file_id,
                folder_hint="Drop",
                is_changed=di.is_changed,
                summary=summary,
            )

    return summary


def _handle_picked_file(
    ctx: RunContext,
    course_key: str,
    origin: str,
    slot: str,
    display_name: str,
    archive_path: Path,
    file_hash: str,
    folder_hint: str = "",
    canvas_file_id: int | None = None,
    is_changed: bool = False,
    summary: StepSummary | None = None,
) -> None:
    """Handle a new or changed file: route, insert source_files row, and create pipeline item (§3.5)."""
    now_str = ctx.now.isoformat()
    exam_key, priority = _get_nearest_exam(course_key, ctx)

    # Changed file handling (#23, SPEC 2.5)
    if is_changed:
        if ctx.runlog:
            ctx.runlog.line(f"- Source file changed in slot {slot}: {display_name} (hash: {file_hash[:8]})")

        with ctx.db.connect() as conn:
            conn.execute(
                """
                UPDATE source_files SET superseded_by = ?
                WHERE course_key = ? AND slot = ? AND superseded_by IS NULL;
                """,
                (file_hash, course_key, slot),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO source_files (
                    file_id, course_key, origin, slot, canvas_file_id,
                    display_name, folder_hint, archive_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    file_hash,
                    course_key,
                    origin,
                    slot,
                    canvas_file_id,
                    display_name,
                    folder_hint,
                    str(archive_path),
                    now_str,
                ),
            )
            conn.commit()

        # Changed file becomes an item at stage 'reintake' where it waits for Phase 3 (§3.5)
        it_id = item_id(origin, slot, file_hash)
        with ctx.db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO items (
                    item_id, source, kind, native_id, version,
                    course_key, privacy, exam_key, priority, stage, status,
                    created_at, updated_at
                ) VALUES (?, ?, 'slides', ?, ?, ?, 'class', ?, ?, 'reintake', 'pending', ?, ?);
                """,
                (
                    it_id,
                    origin,
                    slot,
                    file_hash,
                    course_key,
                    exam_key,
                    priority,
                    now_str,
                    now_str,
                ),
            )
            conn.commit()

        if summary:
            summary.done += 1
        return

    # Brand new file: Route file via route.py (§3.6, SPEC 2.6)
    role = route_file(archive_path, display_name, folder_hint=folder_hint, models=ctx.models)

    with ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO source_files (
                file_id, course_key, origin, slot, canvas_file_id,
                display_name, folder_hint, role, archive_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                file_hash,
                course_key,
                origin,
                slot,
                canvas_file_id,
                display_name,
                folder_hint,
                role,
                str(archive_path),
                now_str,
            ),
        )
        conn.commit()

    it_id = item_id(origin, slot, file_hash)

    if role == "other":
        if ctx.flags:
            ctx.flags.add(
                "intake.other_file",
                f"File '{display_name}' classified as other ({folder_hint})",
                choices=[("keep", "keep"), ("dismiss", "dismiss")],
            )
        stage = "convert"
        status = "skipped"
        kind = "other_file"
    else:
        stage = "convert"
        status = "pending"
        kind = "slides" if role == "slides" else "guide_or_exam"

    with ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO items (
                item_id, source, kind, native_id, version,
                course_key, privacy, exam_key, priority, stage, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'class', ?, ?, ?, ?, ?, ?);
            """,
            (
                it_id,
                origin,
                kind,
                slot,
                file_hash,
                course_key,
                exam_key,
                priority,
                stage,
                status,
                now_str,
                now_str,
            ),
        )
        conn.commit()

    if summary:
        summary.done += 1
