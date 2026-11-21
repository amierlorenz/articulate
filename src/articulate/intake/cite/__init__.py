"""Citation check library (§3.5, §3.4, §3.6, §3.7, SPEC-requirements 2.23, 2.24, 2.25).

The one quote rule every Phase 2 citation goes through:
text layer (skipped on untrusted pages) → OCR → VL, exact after canonicalising,
operators kept, boundary near math, part labels, joined question/answer retry.
"""
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from articulate.core.db import Database
from articulate.core.flags import Flags
from articulate.core.handoff_schemas.imports import Citation
from articulate.core.models.runner import ModelRunner
from articulate.intake.cite.canon import canon, q_canon, v_canon
from articulate.intake.cite.match import joined_retry, match_quote_in_pages, r7_match
from articulate.intake.cite.vl import transcribe_page

logger = logging.getLogger(__name__)


class CitationCheckResult(BaseModel):
    """Result of checking a quote against document pages (§3.5, §3.7)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    verified: bool
    status: Literal["verified", "unverified"]
    verified_by: Literal["text", "ocr", "vl"] | None = Field(default=None, serialization_alias="verifiedBy")
    joined: bool = False

    @property
    def verifiedBy(self) -> Literal["text", "ocr", "vl"] | None:
        return self.verified_by

    def __bool__(self) -> bool:
        return self.verified


def check(
    quote: str,
    file_id: str,
    page: int,
    kind: str = "slides",
    *,
    db: Database | None = None,
    models: ModelRunner | None = None,
    flags: Flags | None = None,
    file_path: Path | None = None,
    partner_quote: str | None = None,
    partner_role: str | None = None,
    partner_page: int | None = None,
) -> CitationCheckResult:
    """
    Check a quote against document sources in order: text layer → OCR → VL (§3.5, SPEC 2.23).

    - Text layer is skipped if page is marked layer_untrusted.
    - VL runs only on still-failing pages, cached by page hash.
    - Quote may run onto page + 1 (multi-page span).
    - If question/answer partner is on the same page, retries joined (question first).
    """
    if not quote or not quote.strip() or page < 1:
        return CitationCheckResult(verified=False, status="unverified", verified_by=None)

    if db is None:
        return CitationCheckResult(verified=False, status="unverified", verified_by=None)

    # Fetch page and next page from SQLite pages table (§3.4)
    with db.connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT page, page_hash, layer_text, layer_untrusted, ocr_text, vl_text
            FROM pages
            WHERE file_id = ? AND page IN (?, ?)
            ORDER BY page ASC;
            """,
            (file_id, page, page + 1),
        )
        rows = {r["page"]: r for r in cursor.fetchall()}

    curr_row = rows.get(page)
    if not curr_row:
        return CitationCheckResult(verified=False, status="unverified", verified_by=None)

    next_row = rows.get(page + 1)

    layer_untrusted = bool(curr_row["layer_untrusted"])
    curr_layer = curr_row["layer_text"] or ""
    next_layer = next_row["layer_text"] or "" if next_row else ""
    layer_span = f"{curr_layer}\n{next_layer}" if next_layer else curr_layer

    curr_ocr = curr_row["ocr_text"] or ""
    next_ocr = next_row["ocr_text"] or "" if next_row else ""
    ocr_span = f"{curr_ocr}\n{next_ocr}" if next_ocr else curr_ocr

    curr_vl = curr_row["vl_text"] or ""
    next_vl = next_row["vl_text"] or "" if next_row else ""
    vl_span = f"{curr_vl}\n{next_vl}" if next_vl else curr_vl

    has_partner = bool(partner_quote and partner_role and partner_page == page)

    # 1. Source 1: Text Layer (skipped on untrusted pages, #40)
    if not layer_untrusted and layer_span.strip():
        if r7_match(quote, layer_span):
            return CitationCheckResult(verified=True, status="verified", verified_by="text", joined=False)
        if has_partner and joined_retry(quote, partner_quote, partner_role, layer_span):
            return CitationCheckResult(verified=True, status="verified", verified_by="text", joined=True)

    # 2. Source 2: Vision OCR
    if ocr_span.strip():
        if r7_match(quote, ocr_span):
            return CitationCheckResult(verified=True, status="verified", verified_by="ocr", joined=False)
        if has_partner and joined_retry(quote, partner_quote, partner_role, ocr_span):
            return CitationCheckResult(verified=True, status="verified", verified_by="ocr", joined=True)

    # 3. Source 3: VL Transcript (only for still-failing quotes, #35)
    # Check existing VL transcript in DB or cache
    page_hash = curr_row["page_hash"] or ""
    if not curr_vl.strip() and page_hash:
        curr_vl = (
            transcribe_page(
                file_path=file_path,
                page_num=page,
                page_hash=page_hash,
                db=db,
                models=models,
                flags=flags,
                file_id=file_id,
            )
            or ""
        )
        vl_span = f"{curr_vl}\n{next_vl}" if next_vl else curr_vl

    if vl_span.strip():
        if r7_match(quote, vl_span):
            return CitationCheckResult(verified=True, status="verified", verified_by="vl", joined=False)
        if has_partner and joined_retry(quote, partner_quote, partner_role, vl_span):
            return CitationCheckResult(verified=True, status="verified", verified_by="vl", joined=True)

    return CitationCheckResult(verified=False, status="unverified", verified_by=None, joined=False)


def check_citation(
    citation: Citation,
    *,
    file_id: str | None = None,
    kind: str = "slides",
    db: Database | None = None,
    models: ModelRunner | None = None,
    flags: Flags | None = None,
    file_path: Path | None = None,
) -> Citation:
    """
    Check a Citation model in-place, updating status and verifiedBy (§3.5, §3.7).
    """
    target_file = file_id or citation.sourceFileId
    page = citation.page or 1
    res = check(
        quote=citation.quote,
        file_id=target_file,
        page=page,
        kind=kind,
        db=db,
        models=models,
        flags=flags,
        file_path=file_path,
    )
    citation.status = res.status
    citation.verifiedBy = res.verified_by
    return citation


def check_guide_item(
    item: dict[str, Any],
    *,
    file_id: str,
    db: Database | None = None,
    models: ModelRunner | None = None,
    flags: Flags | None = None,
    file_path: Path | None = None,
) -> dict[str, Any]:
    """
    Check question and answer quotes on a guide/exam item with joined retry (§3.5, §3.4, #37).
    Updates item['verified'], item['verified_by'], and item['answer_verified'].
    """
    q_quote = item.get("quote", "")
    q_page = item.get("page", 1)

    ans_quote = item.get("answer_quote") or (item.get("answer", {}).get("quote") if isinstance(item.get("answer"), dict) else "")
    ans_page = item.get("answer_page") or (item.get("answer", {}).get("page") if isinstance(item.get("answer"), dict) else q_page)

    # Check question quote (with answer as partner if on same page)
    q_res = check(
        quote=q_quote,
        file_id=file_id,
        page=q_page,
        kind="guide_or_exam",
        db=db,
        models=models,
        flags=flags,
        file_path=file_path,
        partner_quote=ans_quote if ans_quote else None,
        partner_role="ans" if ans_quote else None,
        partner_page=ans_page,
    )

    item["verified"] = 1 if q_res.verified else 0
    item["verified_by"] = q_res.verified_by

    # Check answer quote (with question as partner if on same page)
    if ans_quote and ans_quote.strip():
        ans_res = check(
            quote=ans_quote,
            file_id=file_id,
            page=ans_page,
            kind="guide_or_exam",
            db=db,
            models=models,
            flags=flags,
            file_path=file_path,
            partner_quote=q_quote if q_quote else None,
            partner_role="q" if q_quote else None,
            partner_page=q_page,
        )
        item["answer_verified"] = 1 if ans_res.verified else 0
    else:
        item["answer_verified"] = 0

    return item


__all__ = [
    "CitationCheckResult",
    "canon",
    "check",
    "check_citation",
    "check_guide_item",
    "joined_retry",
    "match_quote_in_pages",
    "q_canon",
    "r7_match",
    "v_canon",
]
