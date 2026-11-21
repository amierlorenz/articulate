"""Gemini Embedding 2 shortlist and vector similarity operations (§2.28, §3.6)."""
import hashlib
import json
import logging
import math
from pathlib import Path

from articulate.core.models.runner import ModelRequest, Part
from articulate.core.paths import get_data_dir
from articulate.core.pipeline import Privacy, RunContext

logger = logging.getLogger(__name__)


def clean_text(s: str) -> str:
    """Clean text for embedding by stripping formatting and truncating to 400 chars."""
    if not s:
        return ""
    cleaned = s.replace("**", "").replace("`", "").replace("$", "").strip()
    return cleaned[:400]


def normalize_vector(v: list[float]) -> list[float]:
    """Normalize a vector to unit length."""
    norm = math.sqrt(sum(x * x for x in v))
    if norm < 1e-12:
        return [0.0] * len(v)
    return [x / norm for x in v]


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2, strict=False))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 < 1e-12 or norm2 < 1e-12:
        return 0.0
    return dot / (norm1 * norm2)


def _get_cache_dir() -> Path:
    cache_dir = get_data_dir() / "cache" / "embeddings"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _read_cache(text: str) -> list[float] | None:
    try:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cache_file = _get_cache_dir() / f"{h}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to read embedding cache: %s", e)
    return None


def _write_cache(text: str, vec: list[float]) -> None:
    try:
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cache_file = _get_cache_dir() / f"{h}.json"
        cache_file.write_text(json.dumps(vec), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to write embedding cache: %s", e)


def embed_texts(
    texts: list[str],
    ctx: RunContext,
    *,
    reserve: bool = False,
) -> list[list[float]]:
    """
    Embed a list of texts using Gemini Embedding 2 (step: embed_class).
    Checks local cache first to conserve API quota.
    """
    results: list[list[float] | None] = [None] * len(texts)
    to_fetch_indices: list[int] = []
    to_fetch_parts: list[Part] = []

    for idx, raw_t in enumerate(texts):
        cleaned = clean_text(raw_t)
        cached = _read_cache(cleaned)
        if cached is not None:
            results[idx] = cached
        else:
            to_fetch_indices.append(idx)
            to_fetch_parts.append(Part(text=cleaned))

    if to_fetch_parts and ctx.models is not None:
        req = ModelRequest(
            step="embed_class",
            material=Privacy.CLASS,
            parts=to_fetch_parts,
            embed=True,
        )
        try:
            res = ctx.models.run(req)
            if res.vectors:
                for fetch_idx, vec in zip(to_fetch_indices, res.vectors, strict=False):
                    norm_vec = normalize_vector(vec)
                    results[fetch_idx] = norm_vec
                    _write_cache(clean_text(texts[fetch_idx]), norm_vec)
        except Exception as e:  # noqa: BLE001
            logger.error("Error calling model runner for embeddings: %s", e)

    # Fill any remaining None with zero vectors
    dim = 768
    for r in results:
        if r is not None and len(r) > 0:
            dim = len(r)
            break

    final_vectors: list[list[float]] = []
    for r in results:
        if r is not None:
            final_vectors.append(r)
        else:
            final_vectors.append([0.0] * dim)

    return final_vectors


def compute_shortlist(
    candidate_vectors: list[list[float]],
    library_targets: list[tuple[str, list[float]]],
    batch_targets: list[tuple[str, list[float]]] | None = None,
    threshold: float = 0.70,
    top_k: int = 5,
) -> dict[int, list[tuple[str, float]]]:
    """
    For each candidate, find library concepts and batch candidates scoring >= threshold.
    Returns {cand_idx: [(target_id, score), ...]}.
    """
    shortlist: dict[int, list[tuple[str, float]]] = {}

    for i, c_vec in enumerate(candidate_vectors):
        scored: list[tuple[str, float]] = []

        # 1. Compare with library targets
        for lib_id, l_vec in library_targets:
            score = cosine_similarity(c_vec, l_vec)
            if score >= threshold:
                scored.append((lib_id, score))

        # 2. Compare with other candidates in same batch (if provided)
        if batch_targets:
            for b_idx, (b_id, b_vec) in enumerate(batch_targets):
                if b_idx != i:  # Don't compare with self
                    score = cosine_similarity(c_vec, b_vec)
                    if score >= threshold:
                        scored.append((b_id, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        shortlist[i] = scored[:top_k]

    return shortlist
