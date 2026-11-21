"""Builds v2 import batches for Encore hand-off (§3.7, Acceptance check 2.35)."""

import json
import logging
from datetime import UTC, date, datetime
from typing import Any

from articulate.core.handoff_schemas import (
    Citation,
    Claim,
    Concept,
    ConceptDetails,
    ContrastGroup,
    ContrastPair,
    Course,
    Exam,
    Header,
    ImportBatch,
    Prompt,
    SourceFile,
    Unit,
)
from articulate.core.ids import new_ulid
from articulate.core.pipeline import RunContext

logger = logging.getLogger(__name__)


def _parse_claim(c_data: dict[str, Any] | str) -> Claim:
    if isinstance(c_data, str):
        return Claim(text=c_data, provenance="gapFill", citations=[])
    citations: list[Citation] = []
    for cit in c_data.get("citations", []):
        citations.append(
            Citation(
                sourceFileId=cit.get("sourceFileId", ""),
                page=cit.get("page"),
                startSec=cit.get("startSec"),
                endSec=cit.get("endSec"),
                quote=cit.get("quote", ""),
                status=cit.get("status", "verified"),
                verifiedBy=cit.get("verifiedBy"),
            )
        )
    return Claim(
        text=c_data.get("text", ""),
        provenance=c_data.get("provenance", "cited" if citations else "gapFill"),
        citations=citations,
    )


def _parse_concept_details(details_raw: str | dict[str, Any] | None) -> ConceptDetails:
    if not details_raw:
        return ConceptDetails()
    if isinstance(details_raw, str):
        try:
            d = json.loads(details_raw)
        except Exception:  # noqa: BLE001
            return ConceptDetails()
    else:
        d = details_raw

    citations: list[Citation] = []
    for cit in d.get("explanationCitations", []):
        citations.append(
            Citation(
                sourceFileId=cit.get("sourceFileId", ""),
                page=cit.get("page"),
                startSec=cit.get("startSec"),
                endSec=cit.get("endSec"),
                quote=cit.get("quote", ""),
                status=cit.get("status", "verified"),
                verifiedBy=cit.get("verifiedBy"),
            )
        )

    use_when = [_parse_claim(c) for c in d.get("useWhen", [])]
    conditions = [_parse_claim(c) for c in d.get("conditions", [])]
    worked_examples = [_parse_claim(c) for c in d.get("workedExamples", [])]

    return ConceptDetails(
        explanationCitations=citations,
        aliases=list(d.get("aliases", [])),
        useWhen=use_when,
        conditions=conditions,
        workedExamples=worked_examples,
    )


def build_import_batch(ctx: RunContext) -> ImportBatch | None:
    """
    Builds the nightly v2 import batch delta (§3.7).
    Returns None if there is no shippable content.
    """
    if ctx.db is None:
        return None

    with ctx.db.connect() as conn:
        # 1. Find shippable concepts and prompts
        # An active concept ships only once it has at least one active prompt (§3.7).
        # Retired concepts ship with status='retired'.
        # Delta: concepts where shipped_in IS NULL or having prompts where shipped_in IS NULL.
        candidate_concepts_rows = conn.execute(
            """
            SELECT * FROM concepts
            WHERE status IN ('active', 'retired');
            """
        ).fetchall()

        all_prompts_rows = conn.execute(
            """
            SELECT * FROM prompts
            WHERE status IN ('active', 'retired');
            """
        ).fetchall()

        # Group prompts by concept_id
        prompts_by_concept: dict[str, list[dict[str, Any]]] = {}
        for pr in all_prompts_rows:
            prompts_by_concept.setdefault(pr["concept_id"], []).append(dict(pr))

        # Determine which concepts need to ship
        concepts_to_ship: list[dict[str, Any]] = []
        for c in candidate_concepts_rows:
            cid = c["concept_id"]
            c_prompts = prompts_by_concept.get(cid, [])
            active_prompts = [p for p in c_prompts if p["status"] == "active"]
            unshipped_prompts = [p for p in c_prompts if p["shipped_in"] is None]

            # Rule: active concepts must have at least one active prompt to ship
            if c["status"] == "active" and not active_prompts:
                continue

            # Delta condition: concept is unshipped OR has unshipped prompts OR is newly retired
            needs_ship = (c["shipped_in"] is None) or bool(unshipped_prompts)
            if needs_ship:
                concepts_to_ship.append(dict(c))

        # 2. Check exams
        # Rule: Exams with no date aren't shipped.
        exam_rows = conn.execute(
            """
            SELECT * FROM exams
            WHERE date IS NOT NULL AND date != '';
            """
        ).fetchall()

        # Build exams
        shippable_exams: list[Exam] = []
        exam_unit_ids_by_course: dict[str, set[str]] = {}

        for er in exam_rows:
            raw_date = er["date"]
            parsed_date: date | None = None
            try:
                parsed_date = date.fromisoformat(raw_date.strip())
            except Exception:  # noqa: BLE001, S112
                continue

            unit_ids: list[str] = []
            if er["unit_ids_json"]:
                try:
                    unit_ids = json.loads(er["unit_ids_json"])
                except Exception:  # noqa: BLE001
                    unit_ids = []

            course_key = er["course_key"]
            exam_unit_ids_by_course.setdefault(course_key, set()).update(unit_ids)

            kind_val = (
                er["kind"]
                if er["kind"] in ("test", "final")
                else ("final" if "final" in er["name"].lower() else "test")
            )

            shippable_exams.append(
                Exam(
                    id=er["exam_id"],
                    courseId=course_key,
                    name=er["name"],
                    date=parsed_date,
                    kind=kind_val,
                    unitIds=unit_ids,
                )
            )

        # If no concepts to ship and no exams to ship, no shippable content
        if not concepts_to_ship and not shippable_exams:
            return None

        # Determine all courses involved
        course_keys = {c["course_key"] for c in concepts_to_ship}
        for ex in shippable_exams:
            course_keys.add(ex.courseId)

        if not course_keys:
            return None

        # 3. Build Course objects
        course_objects: list[Course] = []

        for c_key in sorted(course_keys):
            c_row = conn.execute("SELECT * FROM courses WHERE course_key = ?;", (c_key,)).fetchone()

            course_name = c_row["name"] if c_row and c_row["name"] else c_key.upper()
            coding_bool = bool(c_row["coding"]) if c_row else False

            # Contrast groups for course (full list per upsert)
            cg_rows = conn.execute(
                "SELECT * FROM contrast_groups WHERE course_key = ?;", (c_key,)
            ).fetchall()
            contrast_groups: list[ContrastGroup] = []
            for cg in cg_rows:
                member_ids: list[str] = []
                if cg["member_ids_json"]:
                    try:
                        member_ids = json.loads(cg["member_ids_json"])
                    except Exception:  # noqa: BLE001, S110
                        pass

                contrasts: list[ContrastPair] = []
                if cg["contrasts_json"]:
                    try:
                        raw_contrasts = json.loads(cg["contrasts_json"])
                        for rc in raw_contrasts:
                            contrasts.append(
                                ContrastPair(
                                    conceptA=rc.get("conceptA", ""),
                                    conceptB=rc.get("conceptB", ""),
                                    decidingFeature=_parse_claim(rc.get("decidingFeature", {})),
                                )
                            )
                    except Exception:  # noqa: BLE001, S110
                        pass

                contrast_groups.append(
                    ContrastGroup(
                        id=cg["group_id"],
                        name=cg["name"] or cg["group_id"],
                        question=cg["question"],
                        memberIds=member_ids,
                        contrasts=contrasts,
                    )
                )

            # Source files for course (full list per upsert)
            sf_rows = conn.execute(
                """
                SELECT * FROM source_files
                WHERE course_key = ? AND (superseded_by IS NULL OR superseded_by = '');
                """,
                (c_key,),
            ).fetchall()
            source_files: list[SourceFile] = []
            for sf in sf_rows:
                origin_val = (
                    sf["origin"] if sf["origin"] in ("canvas", "dropped", "pocket") else "canvas"
                )
                content_hash = sf["archive_path"] or sf["file_id"]
                source_files.append(
                    SourceFile(
                        id=sf["file_id"],
                        origin=origin_val,
                        displayName=sf["display_name"] or sf["file_id"],
                        pageCount=sf["page_count"],
                        contentHash=content_hash,
                        canvasFileId=sf["canvas_file_id"],
                    )
                )

            # Units for course
            # Must include units containing shipping concepts AND units referenced by exams
            course_concepts = [c for c in concepts_to_ship if c["course_key"] == c_key]
            concept_unit_ids = {c["unit_id"] for c in course_concepts if c["unit_id"]}
            needed_unit_ids = concept_unit_ids.union(exam_unit_ids_by_course.get(c_key, set()))

            unit_rows = conn.execute(
                "SELECT * FROM units WHERE course_key = ?;", (c_key,)
            ).fetchall()
            unit_map = {u["unit_id"]: u for u in unit_rows}

            units_list: list[Unit] = []
            for u_id in sorted(needed_unit_ids):
                u_row = unit_map.get(u_id)
                u_name = u_row["name"] if u_row and u_row["name"] else u_id
                u_order = u_row["position"] if u_row and u_row["position"] is not None else 1

                # Concepts in this unit
                unit_concepts: list[Concept] = []
                for c in course_concepts:
                    if c["unit_id"] == u_id:
                        cid = c["concept_id"]
                        c_prompts = prompts_by_concept.get(cid, [])

                        p_models: list[Prompt] = []
                        for pr in c_prompts:
                            content_dict = {}
                            if pr["content_json"]:
                                try:
                                    content_dict = json.loads(pr["content_json"])
                                except Exception:  # noqa: BLE001, S110
                                    pass
                            p_models.append(
                                Prompt(
                                    id=pr["prompt_id"],
                                    kind=pr["kind"],
                                    status=pr["status"]
                                    if pr["status"] in ("active", "retired")
                                    else "active",
                                    content=content_dict,
                                )
                            )

                        tier_val = (
                            c["tier"]
                            if c["tier"] in ("core", "standard", "background", "out")
                            else "core"
                        )
                        kind_val = (
                            c["kind"]
                            if c["kind"] in ("definition", "fact", "method")
                            else "definition"
                        )
                        status_val = (
                            c["status"] if c["status"] in ("active", "retired") else "active"
                        )

                        details_model = _parse_concept_details(c["details_json"])
                        aliases_list: list[str] = []
                        if c["aliases_json"]:
                            try:
                                aliases_list = json.loads(c["aliases_json"])
                            except Exception:  # noqa: BLE001, S110
                                pass
                        if aliases_list and not details_model.aliases:
                            details_model.aliases = aliases_list

                        unit_concepts.append(
                            Concept(
                                id=cid,
                                title=c["title"] or cid,
                                explanation=c["confidence_note"] or (c["title"] or cid),
                                kind=kind_val,
                                status=status_val,
                                tier=tier_val,
                                details=details_model,
                                groupIds=[],
                                tags=[],
                                prompts=p_models,
                            )
                        )

                units_list.append(
                    Unit(
                        id=u_id,
                        name=u_name,
                        order=u_order,
                        concepts=unit_concepts,
                    )
                )

            course_objects.append(
                Course(
                    id=c_key,
                    name=course_name,
                    shortName=c_key.upper(),
                    coding=coding_bool,
                    units=units_list,
                    contrastGroups=contrast_groups,
                    sourceFiles=source_files,
                )
            )

    batch_ulid = new_ulid()
    created_now = ctx.now if ctx.now is not None else datetime.now(UTC)
    header = Header(
        schema="encore.import",
        version=2,
        id=f"import-{batch_ulid}",
        created_at=created_now,
        producer="articulate 0.1.0",
    )

    return ImportBatch(
        header=header,
        courses=course_objects,
        exams=shippable_exams,
    )
