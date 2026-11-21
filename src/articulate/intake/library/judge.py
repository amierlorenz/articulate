"""Flash Lite judge: compares shortlisted candidates against library and batch concepts (§2.28, §3.6, §7.1)."""

import json
import logging
from pathlib import Path
from typing import Any

from articulate.core.ids import slugify
from articulate.core.models.runner import ModelRequest, Part
from articulate.core.pipeline import Privacy, RunContext

logger = logging.getLogger(__name__)

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate", "matches"],
                "properties": {
                    "candidate": {"type": "integer"},
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "relation", "reason", "adds"],
                            "properties": {
                                "id": {"type": "string"},
                                "relation": {"type": "string", "enum": ["same", "confusable"]},
                                "reason": {"type": "string"},
                                "adds": {"type": "array", "items": {"type": "string"}},
                                "group": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
    },
}

FALLBACK_SYSTEM_PROMPT = """You compare newly extracted study concepts with the learner's existing library so the app proposes enrichment instead of duplicates.

For each candidate return every library concept or batch candidate it relates to, with a relation:
- "same": teaches the same idea; studying both would be redundant. Different wording, titles or courses still count.
- "confusable": a different idea a student could plausibly mix up with the candidate (similar name, similar use, look-alike method).
Leave out library concepts that are merely on the same topic. Most candidates have no matches.

For each "same" match, list in `adds` only what the candidate would add to the library concept: its kind, use-when cues and any statement the library explanation lacks. Empty if nothing new.

For each "confusable" match within the same course, assign `group`:
- If the shortlisted concept belongs to existing contrast group(s) and the candidate is confusable with its members and answers the group's question, return `group: <id>` (or `group:<id>`) to join that group. A candidate may join several groups if it matches and answers their questions.
- Otherwise, return `new` to form a new contrast pair group.
For cross-course confusable matches (different courses), cross-course matches stay notes only: leave `group` empty or omit it.

Use the library ids exactly as given."""


def _get_prompt_template() -> str:
    prompt_file = Path(__file__).parents[4] / "prompts" / "library-judge.md"
    if prompt_file.exists():
        try:
            return prompt_file.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001, S110
            pass
    return FALLBACK_SYSTEM_PROMPT


def _lib_line(c: dict[str, Any]) -> str:
    cid = c.get("concept_id") or c.get("id") or ""
    course = c.get("course_key") or c.get("course") or ""
    title = c.get("title") or ""
    exp = c.get("explanation") or ""
    return f"[{cid}] ({course}) {title}: {exp[:400]}"


def _cand_block(
    idx: int,
    c: dict[str, Any],
    shortlist_ids: list[str],
    library_by_id: dict[str, dict[str, Any]],
    batch_by_id: dict[str, dict[str, Any]] | None = None,
    groups: list[dict[str, Any]] | None = None,
) -> str:
    title = c.get("title") or ""
    kind = c.get("kind") or "concept"
    exp = c.get("explanation") or ""
    if not exp and "details" in c and isinstance(c["details"], dict):
        citations = c["details"].get("explanationCitations", [])
        if citations:
            exp = citations[0].get("quote", "")

    lines = [f"Candidate {idx}: {title} (kind: {kind})", f"Definition: {exp}"]

    use_when = []
    if "details" in c and isinstance(c["details"], dict):
        use_when = [
            u.get("text", "") for u in c["details"].get("useWhen", []) if isinstance(u, dict)
        ]
    elif "useWhen" in c and isinstance(c["useWhen"], list):
        use_when = [u if isinstance(u, str) else u.get("text", "") for u in c["useWhen"]]

    if use_when:
        lines.append("Use when: " + "; ".join(use_when))

    cand_course = c.get("course_key") or c.get("course") or ""

    lines.append("Shortlisted concepts to compare against:" + ("" if shortlist_ids else " none"))
    for sid in shortlist_ids:
        target_course = ""
        b_cand = None
        if sid in library_by_id:
            lib_concept = library_by_id[sid]
            target_course = lib_concept.get("course_key") or lib_concept.get("course") or ""
            lines.append(f"  {_lib_line(lib_concept)}")
        elif sid.startswith("candidate:") and batch_by_id:
            b_cand = batch_by_id.get(sid)
            if b_cand:
                target_course = b_cand.get("course_key") or b_cand.get("course") or cand_course
                b_title = b_cand.get("title") or ""
                b_exp = b_cand.get("explanation") or ""
                lines.append(f"  [{sid}] (Batch) {b_title}: {b_exp[:400]}")
        else:
            lines.append(f"  [{sid}]")

        # If same course, list each shortlisted concept's same-course groups (§7.1)
        if groups and cand_course and target_course == cand_course:
            matching_groups = []
            for g in groups:
                g_course = g.get("course_key", "")
                if g_course and g_course != cand_course:
                    continue
                m_list = g.get("members") or g.get("member_ids") or []
                actual_sid = sid
                if sid.startswith("candidate:") and b_cand:
                    actual_sid = b_cand.get("concept_id") or b_cand.get("id") or sid
                if actual_sid in m_list or sid in m_list:
                    matching_groups.append(g)

            for mg in matching_groups:
                gid = mg.get("group_id") or mg.get("id") or ""
                gname = mg.get("name") or ""
                gq = mg.get("question") or ""
                m_names = ", ".join(mg.get("members") or mg.get("member_ids") or [])
                lines.append(
                    f'    Contrast group: [{gid}] "{gname}" | Question: {gq} | Members: {m_names}'
                )

    return "\n".join(lines)


def run_judge(
    candidates: list[dict[str, Any]],
    library: list[dict[str, Any]],
    shortlist: dict[int, list[tuple[str, float]]],
    ctx: RunContext,
    *,
    groups: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Execute Flash Lite judge comparing shortlisted candidates against library and batch concepts.
    Returns per-candidate match outcomes (§2.28, §7.1).
    Receives shortlisted concept same-course groups, returns group:<id> | new for confusable matches,
    writes group membership, keeps cross-course matches as notes, and respects pair tombstones.
    """
    library_by_id: dict[str, dict[str, Any]] = {}
    for item in library:
        cid = item.get("concept_id") or item.get("id")
        if cid:
            library_by_id[cid] = item

    batch_by_id: dict[str, dict[str, Any]] = {f"candidate:{i}": c for i, c in enumerate(candidates)}

    course = candidates[0].get("course_key", "") if candidates else ""

    # 1. Load or normalize contrast groups
    caller_provided_groups = groups is not None
    groups_list: list[dict[str, Any]] = []
    if caller_provided_groups and groups is not None:
        groups_list = groups
    elif ctx.db is not None:
        try:
            with ctx.db.connect() as conn:
                rows = conn.execute(
                    "SELECT group_id, course_key, name, question, member_ids_json, contrasts_json, status FROM contrast_groups WHERE status != 'retired';"
                ).fetchall()
                for r in rows:
                    m_ids = []
                    if r["member_ids_json"]:
                        try:
                            m_ids = json.loads(r["member_ids_json"])
                        except Exception:  # noqa: BLE001
                            m_ids = []
                    groups_list.append(
                        {
                            "group_id": r["group_id"],
                            "id": r["group_id"],
                            "course_key": r["course_key"],
                            "name": r["name"],
                            "question": r["question"],
                            "members": m_ids,
                            "member_ids": m_ids,
                            "status": r["status"],
                        }
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not query contrast_groups from DB: %s", e)

    # Normalize group dicts so we can reliably read and update members
    normalized_groups: list[dict[str, Any]] = []
    for g in groups_list:
        gid = g.get("group_id") or g.get("id") or ""
        m_ids = g.get("members") or g.get("member_ids")
        if m_ids is None and g.get("member_ids_json"):
            try:
                m_ids = json.loads(g["member_ids_json"])
            except Exception:  # noqa: BLE001
                m_ids = []
        if m_ids is None:
            m_ids = []
        g["group_id"] = gid
        g["id"] = gid
        g["members"] = m_ids
        g["member_ids"] = m_ids
        normalized_groups.append(g)

    # 2. Load pair tombstones (§3.4, §7.1, SPEC-requirements 3.17)
    tombstoned_pairs: set[tuple[str, str]] = set()
    tombstoned_raw_keys: set[str] = set()
    if ctx.db is not None:
        try:
            with ctx.db.connect() as conn:
                t_rows = conn.execute("SELECT key FROM tombstones WHERE kind = 'pair';").fetchall()
                for tr in t_rows:
                    t_key = tr["key"]
                    tombstoned_raw_keys.add(t_key)
                    if "|" in t_key:
                        p1, p2 = t_key.split("|", 1)
                        tombstoned_pairs.add((p1, p2))
                        tombstoned_pairs.add((p2, p1))
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not query tombstones from DB: %s", e)

    def is_tombstoned(id_a: str, id_b: str) -> bool:
        if (id_a, id_b) in tombstoned_pairs or (id_b, id_a) in tombstoned_pairs:
            return True
        key1 = f"{id_a}|{id_b}"
        key2 = f"{id_b}|{id_a}"
        key_min = f"{min(id_a, id_b)}|{max(id_a, id_b)}"
        return bool(tombstoned_raw_keys.intersection({key1, key2, key_min}))

    cand_blocks: list[str] = []
    has_any_shortlist = False

    for i, c in enumerate(candidates):
        target_ids = [t[0] for t in shortlist.get(i, [])]
        if target_ids:
            has_any_shortlist = True
        cand_blocks.append(
            _cand_block(i, c, target_ids, library_by_id, batch_by_id, normalized_groups)
        )

    # Initialize default results (all 'new')
    results: list[dict[str, Any]] = []
    for i, c in enumerate(candidates):
        cid = c.get("concept_id") or c.get("id") or f"cand_{i}"
        results.append(
            {
                "candidate_index": i,
                "candidate_id": cid,
                "matches": [],
            }
        )

    # If no candidate has any shortlisted neighbours, all candidates are new
    if not has_any_shortlist or ctx.models is None:
        return results

    template = _get_prompt_template()
    prompt_text = f"{template}\n\nCourse: {course}\n\nCandidates:\n\n" + "\n\n".join(cand_blocks)

    req = ModelRequest(
        step="library_judge",
        material=Privacy.CLASS,
        parts=[Part(text=prompt_text)],
        json_schema=JUDGE_SCHEMA,
    )

    try:
        model_res = ctx.models.run(req)
        parsed = model_res.parsed
        if parsed and isinstance(parsed, dict) and "candidates" in parsed:
            for cand_out in parsed["candidates"]:
                c_idx = cand_out.get("candidate")
                if not (isinstance(c_idx, int) and 0 <= c_idx < len(results)):
                    continue

                matches = cand_out.get("matches", [])
                results[c_idx]["matches"] = matches

                cand = candidates[c_idx]
                cand_id = cand.get("concept_id") or cand.get("id") or results[c_idx]["candidate_id"]
                cand_course = cand.get("course_key") or cand.get("course") or course

                for m in matches:
                    relation = m.get("relation")
                    if relation != "confusable":
                        continue

                    target_id = m.get("id", "")
                    target_concept = library_by_id.get(target_id)
                    if not target_concept and target_id.startswith("candidate:"):
                        target_concept = batch_by_id.get(target_id)

                    target_concept_id = target_id
                    target_course = ""
                    if target_concept:
                        target_concept_id = (
                            target_concept.get("concept_id")
                            or target_concept.get("id")
                            or target_id
                        )
                        target_course = (
                            target_concept.get("course_key") or target_concept.get("course") or ""
                        )

                    # Cross-course confusable -> note only (§7.1, 3.13)
                    if cand_course and target_course and cand_course != target_course:
                        logger.info(
                            "Cross-course confusable match: %s (%s) vs %s (%s) stays note only",
                            cand_id,
                            cand_course,
                            target_concept_id,
                            target_course,
                        )
                        continue

                    # Tombstoned pair -> never re-added to contrast groups (§7.1, 3.17)
                    if is_tombstoned(cand_id, target_concept_id):
                        logger.info(
                            "Tombstoned pair: %s vs %s will not be re-added to contrast groups",
                            cand_id,
                            target_concept_id,
                        )
                        continue

                    group_val = m.get("group")
                    group_str = (group_val or "").strip()

                    gid_to_join: str | None = None
                    if group_str == "new":
                        pass
                    elif group_str.startswith("group:"):
                        gid_to_join = group_str[len("group:") :].strip()
                    elif group_str.startswith("group: "):
                        gid_to_join = group_str[len("group: ") :].strip()
                    elif any(
                        (g.get("group_id") or g.get("id")) == group_str for g in normalized_groups
                    ):
                        gid_to_join = group_str

                    if gid_to_join:
                        # Join existing group
                        target_group = next(
                            (
                                g
                                for g in normalized_groups
                                if (g.get("group_id") or g.get("id")) == gid_to_join
                            ),
                            None,
                        )
                        if target_group:
                            existing_members = (
                                target_group.get("members") or target_group.get("member_ids") or []
                            )
                            if any(is_tombstoned(cand_id, mem) for mem in existing_members):
                                logger.info(
                                    "Candidate %s tombstoned with a member of group %s; skipping join",
                                    cand_id,
                                    gid_to_join,
                                )
                                continue

                            if cand_id not in existing_members:
                                existing_members.append(cand_id)
                                target_group["members"] = existing_members
                                target_group["member_ids"] = existing_members

                                if ctx.db is not None:
                                    try:
                                        with ctx.db.connect() as conn:
                                            conn.execute(
                                                "UPDATE contrast_groups SET member_ids_json = ? WHERE group_id = ?;",
                                                (json.dumps(existing_members), gid_to_join),
                                            )
                                            conn.commit()
                                    except Exception as err:  # noqa: BLE001
                                        logger.warning(
                                            "Failed to update contrast_groups in DB: %s", err
                                        )
                    elif group_str == "new":
                        # Create new pair group
                        already_grouped = any(
                            cand_id in (g.get("members") or [])
                            and target_concept_id in (g.get("members") or [])
                            for g in normalized_groups
                        )
                        if not already_grouped:
                            cand_title = cand.get("title") or cand_id
                            target_title = (
                                target_concept.get("title") if target_concept else None
                            ) or target_concept_id
                            pair_course = cand_course or target_course or course
                            g_slug = slugify(f"{cand_title} vs {target_title}")
                            new_gid = f"{pair_course}.g.{g_slug}"

                            existing_gids = {
                                g.get("group_id") or g.get("id") for g in normalized_groups
                            }
                            if new_gid in existing_gids:
                                from articulate.core.ids import new_ulid

                                new_gid = f"{pair_course}.g.{g_slug}_{new_ulid()[:6]}"

                            pair_members = sorted([cand_id, target_concept_id])
                            new_g_dict = {
                                "group_id": new_gid,
                                "id": new_gid,
                                "course_key": pair_course,
                                "name": f"{cand_title} vs. {target_title}",
                                "question": f"How do {cand_title} and {target_title} differ?",
                                "members": pair_members,
                                "member_ids": pair_members,
                                "status": "held",
                            }
                            normalized_groups.append(new_g_dict)
                            if caller_provided_groups and new_g_dict not in groups_list:
                                groups_list.append(new_g_dict)

                            if ctx.db is not None:
                                try:
                                    with ctx.db.connect() as conn:
                                        conn.execute(
                                            """
                                            INSERT INTO contrast_groups (
                                                group_id, course_key, name, question, member_ids_json, contrasts_json, status
                                            ) VALUES (?, ?, ?, ?, ?, '[]', 'held')
                                            ON CONFLICT(group_id) DO UPDATE SET
                                                member_ids_json = excluded.member_ids_json;
                                            """,
                                            (
                                                new_gid,
                                                pair_course,
                                                new_g_dict["name"],
                                                new_g_dict["question"],
                                                json.dumps(pair_members),
                                            ),
                                        )
                                        conn.commit()
                                except Exception as err:  # noqa: BLE001
                                    logger.warning(
                                        "Failed to insert new contrast_group into DB: %s", err
                                    )
    except Exception as e:  # noqa: BLE001
        logger.error("Error executing library judge: %s", e)

    return results
