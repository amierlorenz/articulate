"""Flash Lite judge: compares shortlisted candidates against library and batch concepts (§2.28, §3.6)."""
import logging
from pathlib import Path
from typing import Any

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
        use_when = [u.get("text", "") for u in c["details"].get("useWhen", []) if isinstance(u, dict)]
    elif "useWhen" in c and isinstance(c["useWhen"], list):
        use_when = [u if isinstance(u, str) else u.get("text", "") for u in c["useWhen"]]

    if use_when:
        lines.append("Use when: " + "; ".join(use_when))

    lines.append("Shortlisted concepts to compare against:" + ("" if shortlist_ids else " none"))
    for sid in shortlist_ids:
        if sid in library_by_id:
            lines.append(f"  {_lib_line(library_by_id[sid])}")
        elif sid.startswith("candidate:") and batch_by_id:
            b_cand = batch_by_id.get(sid)
            if b_cand:
                b_title = b_cand.get("title") or ""
                b_exp = b_cand.get("explanation") or ""
                lines.append(f"  [{sid}] (Batch) {b_title}: {b_exp[:400]}")
        else:
            lines.append(f"  [{sid}]")

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
    Returns per-candidate match outcomes (§2.28).
    """
    library_by_id: dict[str, dict[str, Any]] = {}
    for item in library:
        cid = item.get("concept_id") or item.get("id")
        if cid:
            library_by_id[cid] = item

    batch_by_id: dict[str, dict[str, Any]] = {
        f"candidate:{i}": c for i, c in enumerate(candidates)
    }

    course = candidates[0].get("course_key", "") if candidates else ""

    cand_blocks: list[str] = []
    has_any_shortlist = False

    for i, c in enumerate(candidates):
        target_ids = [t[0] for t in shortlist.get(i, [])]
        if target_ids:
            has_any_shortlist = True
        cand_blocks.append(_cand_block(i, c, target_ids, library_by_id, batch_by_id))

    # Initialize default results (all 'new')
    results: list[dict[str, Any]] = []
    for i, c in enumerate(candidates):
        cid = c.get("concept_id") or c.get("id") or f"cand_{i}"
        results.append({
            "candidate_index": i,
            "candidate_id": cid,
            "matches": [],
        })

    # If no candidate has any shortlisted neighbours, all candidates are new
    if not has_any_shortlist or ctx.models is None:
        return results

    template = _get_prompt_template()
    prompt_text = (
        f"{template}\n\n"
        f"Course: {course}\n\n"
        f"Candidates:\n\n" + "\n\n".join(cand_blocks)
    )

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
                if isinstance(c_idx, int) and 0 <= c_idx < len(results):
                    matches = cand_out.get("matches", [])
                    results[c_idx]["matches"] = matches
    except Exception as e:  # noqa: BLE001
        logger.error("Error executing library judge: %s", e)

    return results
