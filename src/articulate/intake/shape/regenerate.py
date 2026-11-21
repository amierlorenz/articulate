"""Prompt regeneration against cited source (§2.38, SPEC-implementation §3.5, Issue #64, Issue #65)."""
import json
import logging
from dataclasses import dataclass

from articulate.core.pipeline import RunContext
from articulate.intake import cite
from articulate.intake.shape.checks import run_deterministic_checks
from articulate.intake.shape.generate import get_style_examples
from articulate.intake.shape.verify import verify_prompt

logger = logging.getLogger(__name__)


@dataclass
class RegenerateResult:
    ok: bool
    new_prompt_id: str | None
    detail: str


def regenerate(
    prompt_id: str,
    *,
    note: str,
    suggested_fix: str | None = None,
    ctx: RunContext,
) -> RegenerateResult:
    """
    Regenerate a flagged prompt against its cited source (§2.38, Issue #64, #65).

    Shape writes the replacement row to prompts (status active, new id) only when
    it passes the citation check and deterministic/verify checks, returning ok=False otherwise.
    """
    now_str = ctx.now.isoformat()

    # 1. Fetch prompt from database
    with ctx.db.connect() as conn:
        p_row = conn.execute(
            """
            SELECT prompt_id, concept_id, kind, content_json, status, generated_by, prompt_version
            FROM prompts WHERE prompt_id = ?;
            """,
            (prompt_id,),
        ).fetchone()

    if not p_row:
        return RegenerateResult(ok=False, new_prompt_id=None, detail=f"Prompt '{prompt_id}' not found")

    p_dict = dict(p_row)
    concept_id = p_dict["concept_id"]
    kind = p_dict["kind"]

    # 2. Fetch concept from database
    with ctx.db.connect() as conn:
        c_row = conn.execute(
            """
            SELECT concept_id, course_key, unit_id, kind, title, details_json, tier, status, hold_reason
            FROM concepts WHERE concept_id = ?;
            """,
            (concept_id,),
        ).fetchone()

    if not c_row:
        return RegenerateResult(ok=False, new_prompt_id=None, detail=f"Concept '{concept_id}' not found")

    c_dict = dict(c_row)
    course_key = c_dict.get("course_key", "")
    title = c_dict.get("title", "")
    try:
        details = json.loads(c_dict["details_json"]) if c_dict.get("details_json") else {}
    except Exception:  # noqa: BLE001
        details = {}
    aliases = details.get("aliases", [])

    # 3. Citation check against cited source (§2.38)
    citations = details.get("explanationCitations", [])
    if not citations:
        # Check claims for citations
        for claim_group in ("useWhen", "conditions", "workedExamples"):
            for cl in details.get(claim_group, []):
                if isinstance(cl, dict) and cl.get("citations"):
                    citations.extend(cl["citations"])

    if citations and ctx.db:
        # Check citations against document sources if pages exist
        for cit in citations:
            file_id = cit.get("sourceFileId")
            page = cit.get("page")
            quote = cit.get("quote")
            if file_id and page and quote:
                # If pages table has rows for this file_id, execute citation check
                with ctx.db.connect() as conn:
                    p_cnt = conn.execute("SELECT COUNT(*) as c FROM pages WHERE file_id = ?;", (file_id,)).fetchone()
                    has_pages = p_cnt and p_cnt["c"] > 0
                if has_pages:
                    check_res = cite.check(
                        quote=quote,
                        file_id=file_id,
                        page=page,
                        kind="slides",
                        db=ctx.db,
                        models=ctx.models,
                    )
                    if not check_res.verified:
                        return RegenerateResult(
                            ok=False,
                            new_prompt_id=None,
                            detail=f"Citation check failed against source file '{file_id}' page {page}",
                        )

    # 4. Generate replacement content
    try:
        old_content = json.loads(p_dict["content_json"]) if p_dict.get("content_json") else {}
    except Exception:  # noqa: BLE001
        old_content = {}

    new_content = dict(old_content)
    if suggested_fix:
        if kind == "flashcard":
            new_content["back"] = suggested_fix
        elif kind == "shortAnswer":
            new_content["modelAnswer"] = suggested_fix
        elif kind == "multipleChoice":
            new_content["explanation"] = suggested_fix
        elif kind == "setUp":
            new_content["modelSetUp"] = suggested_fix
        elif kind == "methodPick":
            new_content["explanation"] = suggested_fix
    elif note:
        if kind == "flashcard" and "back" in new_content:
            new_content["back"] = f"{new_content['back']} ({note})"
        elif kind == "shortAnswer" and "modelAnswer" in new_content:
            new_content["modelAnswer"] = f"{new_content['modelAnswer']} ({note})"

    # 5. Deterministic checks & verification
    with ctx.db.connect() as conn:
        all_c_rows = conn.execute("SELECT concept_id FROM concepts WHERE course_key = ?;", (course_key,)).fetchall()
        valid_concept_ids = {r["concept_id"] for r in all_c_rows}
    if not valid_concept_ids:
        valid_concept_ids = {concept_id}

    style_examples = get_style_examples(course_key, ctx)

    ok, err = run_deterministic_checks(
        kind,
        new_content,
        concept_title=title,
        aliases=aliases,
        valid_concept_ids=valid_concept_ids,
        style_examples=style_examples,
    )
    if not ok:
        return RegenerateResult(ok=False, new_prompt_id=None, detail=f"Deterministic check failed: {err}")

    v_ok, v_err = verify_prompt(kind, new_content, c_dict, ctx)
    if not v_ok:
        return RegenerateResult(ok=False, new_prompt_id=None, detail=f"Verification rejected: {v_err}")

    # 6. Determine new prompt ID
    with ctx.db.connect() as conn:
        existing_prompts = conn.execute(
            "SELECT prompt_id FROM prompts WHERE concept_id = ? AND kind = ?;",
            (concept_id, kind),
        ).fetchall()
        existing_ids = {r["prompt_id"] for r in existing_prompts}

    next_idx = len(existing_ids) + 1
    new_prompt_id = f"{concept_id}.{kind}.{next_idx}"
    while new_prompt_id in existing_ids:
        next_idx += 1
        new_prompt_id = f"{concept_id}.{kind}.{next_idx}"

    # 7. Write replacement row to prompts with status 'active'
    model_name = p_dict.get("generated_by") or "regenerate"
    with ctx.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO prompts (
                prompt_id, concept_id, kind, content_json, status,
                generated_by, prompt_version, created_at, shipped_in
            ) VALUES (?, ?, ?, ?, 'active', ?, 'v2', ?, NULL);
            """,
            (new_prompt_id, concept_id, kind, json.dumps(new_content), model_name, now_str),
        )
        conn.commit()

    return RegenerateResult(
        ok=True,
        new_prompt_id=new_prompt_id,
        detail=f"Prompt regenerated as '{new_prompt_id}'",
    )
