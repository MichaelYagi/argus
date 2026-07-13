"""In-memory faiss index for fast face similarity search.

One index per (user, environment), built over representative (averaged) embeddings.
Falls back to numpy cosine similarity if faiss is unavailable.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

log = logging.getLogger(__name__)

# On Apple Silicon, faiss-cpu and torch each vendor their own libomp; loading
# both in one process segfaults. Set ARGUS_DISABLE_FAISS=true to skip faiss
# entirely (its libomp loads at import) and use the numpy fallback — fine for
# matching below tens of thousands of enrolled faces.
_FAISS_DISABLED = os.environ.get("ARGUS_DISABLE_FAISS", "").strip().lower() in (
    "1", "true", "yes", "on",
)


def _try_import_faiss() -> Any | None:
    """Return the faiss module, or None if disabled or unavailable.

    When disabled, faiss is never imported, so its libomp never loads.
    """
    if _FAISS_DISABLED:
        return None
    try:
        import faiss
        return faiss
    except ImportError:
        return None

_lock = threading.Lock()
# Keys are (user_id, environment_id) so each environment has an isolated index.
_indices: dict[tuple[int, int], Any]       = {}  # -> faiss.IndexFlatIP (or numpy matrix)
_id_maps: dict[tuple[int, int], list[int]] = {}  # -> [identity_id, ...]
_current_model_id: int | None  = None


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _strategy() -> str:
    """'topk_weighted' (default), 'best', or 'average'."""
    from app.core import settings_cache
    s = str(settings_cache.cache.get_or("face.match_strategy", "topk_weighted")).strip().lower()
    if s in ("best", "average", "topk_weighted"):
        return s
    return "topk_weighted"


def _top_k() -> int:
    from app.core import settings_cache
    try:
        return max(1, int(settings_cache.cache.get_or("face.match_top_k", "5")))
    except (ValueError, TypeError):
        return 5


def build_for_user(model_id: int, user_id: int, environment_id: int) -> None:
    """Build or rebuild the in-memory index for one (user, environment), per strategy.

    - topk_weighted: confidence-weighted average of the top-K enrollment embeddings per identity.
    - best:          one vector per reference embedding; search collapses to best score per identity.
    - average:       simple centroid per identity (stored in identities.representative_embedding).
    """
    import numpy as np
    from collections import defaultdict

    from app.db import store

    strategy = _strategy()
    key = (user_id, environment_id)
    vectors: list[np.ndarray] = []
    id_map:  list[int]        = []

    def _norm(v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    if strategy == "best":
        for r in store.get_reference_embeddings(model_id, user_id, environment_id):
            if not r["embedding"]:
                continue
            vectors.append(_norm(np.frombuffer(bytes(r["embedding"]), dtype=np.float32).copy()))
            id_map.append(r["identity_id"])

    elif strategy == "topk_weighted":
        k = _top_k()
        by_identity: dict[int, list[tuple[bytes, float]]] = defaultdict(list)
        for r in store.get_reference_embeddings_with_confidence(model_id, user_id, environment_id):
            if r["embedding"]:
                by_identity[r["identity_id"]].append((bytes(r["embedding"]), float(r["confidence"])))
        for iid, embs in by_identity.items():
            top = sorted(embs, key=lambda x: x[1], reverse=True)[:k]
            total_w = sum(c for _, c in top) or len(top)
            acc = np.zeros(
                np.frombuffer(top[0][0], dtype=np.float32).shape, dtype=np.float32
            )
            for emb_bytes, conf in top:
                acc += _norm(np.frombuffer(emb_bytes, dtype=np.float32).copy()) * (conf / total_w)
            vectors.append(_norm(acc))
            id_map.append(iid)

    else:  # average
        # Always recompute centroids — stale representatives from a different model
        # would leave the index empty or wrong.
        identity_ids = store.list_identity_ids_for_model(model_id, user_id, environment_id)
        for iid in identity_ids:
            store.compute_and_store_representative(iid, model_id)
        for r in store.get_representative_embeddings(model_id, user_id, environment_id):
            if not r["representative_embedding"]:
                continue
            vectors.append(_norm(np.frombuffer(bytes(r["representative_embedding"]), dtype=np.float32).copy()))
            id_map.append(r["identity_id"])

    with _lock:
        global _current_model_id
        _current_model_id = model_id
        _id_maps[key] = id_map

        if not vectors:
            _indices.pop(key, None)
            return

        faiss = _try_import_faiss()
        if faiss is not None:
            idx = faiss.IndexFlatIP(len(vectors[0]))
            idx.add(np.stack(vectors).astype(np.float32))
            _indices[key] = idx
        else:
            _indices[key] = np.stack(vectors).astype(np.float32)


def build_all(model_id: int) -> None:
    """Rebuild index for every (user, environment) that has face data for this model."""
    if _try_import_faiss() is None:
        log.info("faiss disabled or unavailable — using numpy similarity search")

    from app.db import store
    pairs = store.list_user_env_pairs_for_model(model_id)
    for uid, env_id in pairs:
        build_for_user(model_id, uid, env_id)
    # build_for_user sets _current_model_id under lock for each pair it processes.
    # If there are no enrolled faces yet, set it explicitly so rebuild_user() on the
    # first enrollment knows which model to use.
    if not pairs:
        global _current_model_id
        with _lock:
            _current_model_id = model_id
    log.info("Face index built for model_id=%s (%d environments)", model_id, len(pairs))


def rebuild_user(user_id: int, environment_id: int) -> None:
    """Rebuild index for one (user, environment) using the current active model."""
    with _lock:
        model_id = _current_model_id
    if model_id is not None:
        build_for_user(model_id, user_id, environment_id)


def clear_environment(user_id: int, environment_id: int) -> None:
    """Drop a (user, environment) index — used when an environment is deleted."""
    key = (user_id, environment_id)
    with _lock:
        _indices.pop(key, None)
        _id_maps.pop(key, None)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(
    embedding: Any,
    user_id: int,
    environment_id: int,
    threshold: float,
    k: int = 5,
) -> list[tuple[int, float]]:
    """Return up to k (identity_id, similarity) pairs above threshold, sorted descending."""
    import numpy as np

    key = (user_id, environment_id)
    with _lock:
        index  = _indices.get(key)
        id_map = list(_id_maps.get(key, []))

    if not id_map:
        return []

    vec  = np.asarray(embedding, dtype=np.float32).copy()
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm

    pairs: list[tuple[int, float]] = []

    faiss = _try_import_faiss()
    used_faiss = False
    if faiss is not None:
        try:
            if isinstance(index, faiss.swigfaiss.Index):
                # 'best' mode has many vectors per identity, so pull a wider pool
                # of neighbours before collapsing to one score per identity.
                k_search = min(index.ntotal, max(k * 5, 50))
                scores, idxs = index.search(vec.reshape(1, -1), k_search)
                pairs = [(id_map[i], float(s)) for s, i in zip(scores[0], idxs[0]) if i >= 0]
                used_faiss = True
        except Exception:
            log.warning("faiss search failed, falling back to numpy", exc_info=True)
            used_faiss = False

    if not used_faiss:
        if index is None or not hasattr(index, "shape"):
            return []
        try:
            sims = index @ vec
        except ValueError:
            log.warning(
                "face index / query dimension mismatch (index=%s, query dim=%d) — model swap in progress?",
                index.shape, vec.shape[0],
            )
            return []
        pairs = [(id_map[i], float(sims[i])) for i in range(len(id_map))]

    # Collapse to the best score per identity (no-op when one vector per identity).
    best: dict[int, float] = {}
    for iid, s in pairs:
        if s >= threshold and (iid not in best or s > best[iid]):
            best[iid] = s
    ranked = sorted(best.items(), key=lambda x: x[1], reverse=True)
    return ranked[:k]
