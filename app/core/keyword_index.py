"""In-memory text-embedding matrix for the global keyword vocabulary.

The active CLIP model's text encoder turns the vocabulary (wrapped in the prompt
template) into an (N, D) matrix, cached on disk keyed by (model_id, vocab_version).
Tagging an image is then a cheap matmul of the image vector against this matrix.

Rebuilt whenever the vocabulary, prompt template, or active CLIP model changes — the
vocab_version (bumped on vocabulary/template edits) is the cache key, so stale matrices
are detected automatically.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from app.core import settings_cache
from app.core.engine_registry import registry
from app.core.paths import models_dir

log = logging.getLogger("app.keyword_index")

_lock = threading.Lock()
# Current loaded matrix state.
_words: list[str] = []
_matrix: Any = None          # numpy (N, D)
_model_id: int | None = None
_vocab_version: int | None = None


# Prompt ensemble: each word's text embedding is averaged over these templates and
# re-normalized. This is the standard CLIP zero-shot accuracy trick (OpenAI used ~80
# templates for ImageNet); a handful captures most of the gain. Cost is paid once at
# build time (cached); zero runtime cost. The admin's configured prompt_template is
# folded in as well so that setting still matters.
_PROMPT_TEMPLATES = [
    "a photo of {word}",
    "a photo of a {word}",
    "a picture of {word}",
    "a close-up photo of {word}",
    "a cropped photo of {word}",
    "a snapshot of {word}",
    "{word}",
]
# Bump when the build math changes so stale on-disk matrices are ignored.
_BUILD_SCHEME = "e1"


def _cache_path(model_id: int, version: int):
    return models_dir() / "clip" / "matrix" / f"m{model_id}_v{version}_{_BUILD_SCHEME}.npz"


def _templates() -> list[str]:
    configured = settings_cache.cache.get_or("clip.prompt_template", "a photo of {word}")
    templates = list(_PROMPT_TEMPLATES)
    if "{word}" in configured and configured not in templates:
        templates.append(configured)
    return templates


def _embed_vocabulary(engine, words: list[str]):
    """Ensemble text embeddings: mean over templates, L2-normalized. (N, D)."""
    import numpy as np

    acc = None
    for tmpl in _templates():
        emb = engine.embed_texts([tmpl.format(word=w) for w in words])
        emb = np.asarray(emb, dtype=np.float32)
        acc = emb if acc is None else acc + emb
    acc /= len(_templates())
    norms = np.linalg.norm(acc, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return acc / norms


def build(model_id: int) -> None:
    """Build (or load from cache) the text matrix for the active CLIP model at the
    current vocab version. Safe to call repeatedly; rebuilds only when stale."""
    global _words, _matrix, _model_id, _vocab_version
    import numpy as np

    from app.db import store

    version = store.get_vocab_version()
    with _lock:
        if _model_id == model_id and _vocab_version == version and _matrix is not None:
            return

    words = store.get_vocabulary()
    path = _cache_path(model_id, version)

    matrix = None
    if path.exists():
        try:
            data = np.load(str(path), allow_pickle=True)
            cached_words = list(data["words"])
            if cached_words == words:
                matrix = data["matrix"].astype(np.float32)
        except Exception as exc:  # corrupt cache — rebuild
            log.warning("keyword matrix cache unreadable (%s); rebuilding", exc)

    if matrix is None:
        engine = registry.get_tagging_engine()
        if engine is None:
            log.info("no active CLIP engine; keyword matrix not built")
            return
        if not words:
            matrix = np.zeros((0, 0), dtype=np.float32)
        else:
            matrix = _embed_vocabulary(engine, words)
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(str(path), words=np.array(words, dtype=object), matrix=matrix)

    with _lock:
        _words = words
        _matrix = matrix
        _model_id = model_id
        _vocab_version = version
    log.info("keyword matrix ready: model=%s version=%s words=%d", model_id, version, len(words))


def current_version() -> int | None:
    with _lock:
        return _vocab_version


def reset() -> None:
    """Drop the loaded matrix (e.g. when the CLIP model is deactivated)."""
    with _lock:
        global _words, _matrix, _model_id, _vocab_version
        _words = []
        _matrix = None
        _model_id = None
        _vocab_version = None


def _unit(v) -> Any:
    import numpy as np
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def score(image_vec: Any, top_k: int | None = None, threshold: float | None = None,
          diversity: float | None = None, rel_floor: float | None = None):
    """[(keyword, score), ...] for a single image vector. See _select for the params."""
    with _lock:
        matrix = _matrix
    if matrix is None or getattr(matrix, "size", 0) == 0:
        return []
    sims = matrix @ _unit(image_vec)
    return _select(sims, top_k, threshold, diversity, rel_floor)


def score_pooled(image_vecs: list, top_k: int | None = None, threshold: float | None = None,
                 diversity: float | None = None, rel_floor: float | None = None):
    """Like score() but over several image vectors (whole image + detected region crops).
    Per tag, takes the best score across any region, so a small/secondary object that's
    prominent in its own crop can earn a tag even when it's lost in the global embedding.
    This is the per-region tagging path."""
    import numpy as np
    with _lock:
        matrix = _matrix
    if matrix is None or getattr(matrix, "size", 0) == 0:
        return []
    vecs = [v for v in image_vecs if v is not None]
    if not vecs:
        return []
    sims = np.max(np.stack([matrix @ _unit(v) for v in vecs]), axis=0)  # max-pool over regions
    return _select(sims, top_k, threshold, diversity, rel_floor)


def _select(sims, top_k, threshold, diversity, rel_floor):
    """Pick keywords from a similarity vector: relative+absolute floor, then top-K chosen
    with Maximal Marginal Relevance so they describe *different* things (e.g. "chess,
    family, mug" not "chess, board game, tabletop game, card game ...")."""
    import numpy as np

    with _lock:
        words = _words
        matrix = _matrix
    if matrix is None or matrix.size == 0 or not words:
        return []

    if top_k is None:
        top_k = int(settings_cache.cache.get_or("clip.tag_top_k", 6))
    if threshold is None:
        threshold = float(settings_cache.cache.get_or("clip.tag_threshold", 0.20))
    if diversity is None:
        diversity = float(settings_cache.cache.get_or("clip.tag_diversity", 0.4))
    diversity = min(max(diversity, 0.0), 1.0)
    if rel_floor is None:
        rel_floor = float(settings_cache.cache.get_or("clip.tag_rel_floor", 0.82))
    rel_floor = min(max(rel_floor, 0.0), 1.0)

    # CLIP's absolute scores drift per image, so a fixed cutoff over/under-tags. Use a
    # per-image relative floor (a fraction of this image's top score) on top of the
    # absolute floor: keeps the strong tags on low-scoring images, stays tight on
    # high-scoring ones. rel_floor=0 disables it (pure absolute threshold).
    effective = max(threshold, rel_floor * float(sims.max()))
    cand = np.where(sims >= effective)[0]
    if cand.size == 0:
        return []
    cand = cand[np.argsort(-sims[cand])]  # best first
    k = min(top_k, cand.size)

    if diversity <= 0.0:
        sel = cand[:k]
    else:
        # MMR: greedily pick the candidate maximizing relevance minus its similarity to
        # the already-picked keywords (text-embedding cosine = matrix row dot product).
        pool = list(cand[: max(k * 8, 60)])  # only the strongest candidates matter
        sel: list[int] = []
        while pool and len(sel) < k:
            if not sel:
                best = pool.pop(0)
            else:
                sel_mat = matrix[sel]  # (s, D), rows already L2-normalized
                best_i, best_score = None, -1e9
                for i in pool:
                    redundancy = float(np.max(sel_mat @ matrix[i]))
                    mmr = (1.0 - diversity) * float(sims[i]) - diversity * redundancy
                    if mmr > best_score:
                        best_score, best_i = mmr, i
                best = best_i
                pool.remove(best)
            sel.append(best)

    return sorted(((words[i], float(sims[i])) for i in sel), key=lambda t: t[1], reverse=True)
