"""Long deck splitting, remapping, and merging (§3.5, SPEC 2.20, #36, #61)."""

import copy
import re
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


def norm_title(t: str) -> str:
    """Normalise title for deduplication."""
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def plan_ranges(n: int) -> list[tuple[int, int]]:
    """
    Plan page ranges for an n-page deck.
    <= 40 pages: 1 range [(1, n)].
    > 40 pages: ceil(n / 30) equal ranges.
    """
    if n <= 40:
        return [(1, n)]
    k = (n + 29) // 30
    base = n // k
    rem = n % k
    ranges: list[tuple[int, int]] = []
    curr = 1
    for i in range(k):
        size = base + (1 if i < rem else 0)
        ranges.append((curr, curr + size - 1))
        curr += size
    return ranges


def halve(rng: tuple[int, int]) -> list[tuple[int, int]]:
    """Split a range in half: (first, mid), (mid+1, last)."""
    a, b = rng
    mid = (a + b) // 2
    return [(a, mid), (mid + 1, b)]


def range_note(first_page: int, last_page: int, total_pages: int) -> str:
    """Return prompt note for a sub-range."""
    return (
        f"\n\nThis PDF holds pages {first_page}–{last_page} of a {total_pages}-page deck. "
        f"Cite page numbers within this PDF (1-based)."
    )


def sub_pdf(src_path: Path, first_page: int, last_page: int, dst_path: Path) -> Path:
    """Extract pages first_page..last_page inclusive (1-based) into dst_path."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    src = pdfium.PdfDocument(str(src_path))
    dst = pdfium.PdfDocument.new()
    page_indices = list(range(first_page - 1, last_page))
    dst.import_pages(src, page_indices)
    dst.save(str(dst_path))
    return dst_path


def remap(output: dict[str, Any], first_page: int) -> dict[str, Any]:
    """Remap 1-based sub-PDF page numbers to deck page numbers."""
    delta = first_page - 1
    if delta == 0:
        return output

    out = copy.deepcopy(output)

    def _fix_cites(citations: list[dict[str, Any]]) -> None:
        for ct in citations:
            if "page" in ct and isinstance(ct["page"], int):
                ct["page"] += delta

    def _fix_claim(claim: dict[str, Any]) -> None:
        _fix_cites(claim.get("citations", []))

    for c in out.get("concepts", []):
        if "explanation" in c:
            _fix_claim(c["explanation"])
        for f in ("useWhen", "conditions", "workedExamples"):
            for cl in c.get(f, []):
                _fix_claim(cl)
        for h in c.get("slideHints", []):
            if "page" in h and isinstance(h["page"], int):
                h["page"] += delta

    for g in out.get("contrastGroups", []):
        for ct in g.get("contrasts", []):
            if "decidingFeature" in ct:
                _fix_claim(ct["decidingFeature"])

    return out


def merge(parts: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, int]]:
    """
    Merge multiple extracted parts.
    Same normalised title merges (claims/aliases appended, first explanation kept).
    Key reused by a different concept is renamed.
    Groups dedupe by member set after renames; duplicate contrasts join kept group.
    Groups spanning across the cut with invalid members are dropped.
    """
    concepts: list[dict[str, Any]] = []
    seen_titles: dict[str, dict[str, Any]] = {}
    seen_keys: set[str] = set()
    stats = {"merged": 0, "renamed": 0, "groups_deduped": 0}

    # First pass: merge concepts and compute renames per part
    renamed_keys: dict[str, str] = {}  # old_key -> new_key or kept_key

    for part in parts:
        for c in part.get("concepts", []):
            nt = norm_title(c.get("title", ""))
            if nt in seen_titles:
                stats["merged"] += 1
                keep = seen_titles[nt]
                # Append aliases and claims
                for a in c.get("aliases", []):
                    if a not in keep["aliases"]:
                        keep["aliases"].append(a)
                for f in ("useWhen", "conditions", "workedExamples", "slideHints"):
                    keep[f].extend(c.get(f, []))
                renamed_keys[c["key"]] = keep["key"]
                continue

            k = c["key"]
            if k in seen_keys:
                idx = 2
                new_k = f"{k}-{idx}"
                while new_k in seen_keys:
                    idx += 1
                    new_k = f"{k}-{idx}"
                stats["renamed"] += 1
                renamed_keys[k] = new_k
                c["key"] = new_k

            seen_keys.add(c["key"])
            seen_titles[nt] = c
            concepts.append(c)

    valid_keys = {c["key"] for c in concepts}

    # Second pass: merge and dedupe contrast groups
    merged_groups: list[dict[str, Any]] = []
    seen_member_sets: dict[frozenset[str], dict[str, Any]] = {}

    for part in parts:
        for g in part.get("contrastGroups", []):
            # Remap members
            new_members = []
            for mk in g.get("memberKeys", []):
                target_k = renamed_keys.get(mk, mk)
                if target_k in valid_keys and target_k not in new_members:
                    new_members.append(target_k)

            if len(new_members) < 2:
                continue

            g["memberKeys"] = new_members
            mset = frozenset(new_members)

            # Remap contrasts
            valid_contrasts = []
            seen_pairs = set()
            for ct in g.get("contrasts", []):
                a = renamed_keys.get(ct.get("a", ""), ct.get("a", ""))
                b = renamed_keys.get(ct.get("b", ""), ct.get("b", ""))
                if a in mset and b in mset and a != b:
                    pair = frozenset((a, b))
                    if pair not in seen_pairs:
                        ct["a"] = a
                        ct["b"] = b
                        valid_contrasts.append(ct)
                        seen_pairs.add(pair)
            g["contrasts"] = valid_contrasts

            if mset in seen_member_sets:
                stats["groups_deduped"] += 1
                kept_g = seen_member_sets[mset]
                kept_pairs = {frozenset((ct["a"], ct["b"])) for ct in kept_g.get("contrasts", [])}
                for ct in valid_contrasts:
                    pair = frozenset((ct["a"], ct["b"]))
                    if pair not in kept_pairs:
                        kept_g["contrasts"].append(ct)
                        kept_pairs.add(pair)
            else:
                seen_member_sets[mset] = g
                merged_groups.append(g)

    return {"concepts": concepts, "contrastGroups": merged_groups}, stats
