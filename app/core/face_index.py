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
    """'best' (every reference indexed, default) or 'average' (centroid per identity)."""
    from app.core import settings_cache
    s = str(settings_cache.cache.get_or("face.match_strategy", "best")).strip().lower()
    return "average" if s == "average" else "best"


def build_for_user(model_id: int, user_id: int, environment_id: int) -> None:
    """Build or rebuild the in-memory index for one (user, environment), per strategy.

    - average: one centroid (representative) vector per identity.
    - best:    one vector per reference embedding (id_map repeats the identity).
    Search collapses to the best score per identity either way.
    """
    import numpy as np

    from app.db import store

    strategy = _strategy()
    key = (user_id, environment_id)

    if strategy == "best":
        rows = [
            (r["identity_id"], r["embedding"])
            for r in store.get_reference_embeddings(model_id, user_id, environment_id)
        ]
    else:
        # Always recompute centroids for this model — the NULL-check optimisation
        # misses stale representatives (computed for a different model or before
        # the current embeddings existed), leaving the index empty or wrong.
        with store._connect() as conn:
            identity_ids = [r[0] for r in conn.execute(
                """SELECT DISTINCT fe.identity_id FROM face_embeddings fe
                   JOIN identities i ON i.id = fe.identity_id
                   WHERE fe.model_id = ? AND i.user_id = ? AND i.environment_id = ?""",
                (model_id, user_id, environment_id),
            ).fetchall()]
        for iid in identity_ids:
            store.compute_and_store_representative(iid, model_id)
        rows = [
            (r["identity_id"], r["representative_embedding"])
            for r in store.get_representative_embeddings(model_id, user_id, environment_id)
        ]

    with _lock:
        global _current_model_id
        _current_model_id = model_id

        if not rows:
            _indices.pop(key, None)
            _id_maps[key] = []
            return

        faiss = _try_import_faiss()
        use_faiss = faiss is not None

        vectors, id_map = [], []
        for identity_id, emb in rows:
            if not emb:
                continue
            vec  = np.frombuffer(bytes(emb), dtype=np.float32).copy()
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec /= norm
            vectors.append(vec)
            id_map.append(identity_id)

        _id_maps[key] = id_map

        if not vectors:
            _indices.pop(key, None)
            return

        if use_faiss:
            idx = faiss.IndexFlatIP(len(vectors[0]))
            idx.add(np.stack(vectors).astype(np.float32))
            _indices[key] = idx
        else:
            _indices[key] = np.stack(vectors).astype(np.float32)


def build_all(model_id: int) -> None:
    """Rebuild index for every (user, environment) that has face data for this model."""
    if _try_import_faiss() is None:
        log.warning("faiss disabled or unavailable — using numpy similarity search")

    # Record the active model even when there are no enrolled faces yet, so a later
    # rebuild_user() (the first enrollment) isn't a no-op. Otherwise the index only
    # comes alive after a restart on a freshly-activated model.
    global _current_model_id
    with _lock:
        _current_model_id = model_id

    from app.db import store
    with store._connect() as conn:
        pairs = [(r[0], r[1]) for r in conn.execute(
            """SELECT DISTINCT i.user_id, i.environment_id FROM identities i
               JOIN face_embeddings fe ON fe.identity_id = i.id
               WHERE fe.model_id = ?""",
            (model_id,),
        ).fetchall()]
    for uid, env_id in pairs:
        build_for_user(model_id, uid, env_id)
    log.info("Face index built for model_id=%s (%d environments)", model_id, len(pairs))


def rebuild_user(user_id: int, environment_id: int) -> None:
    """Rebuild index for one (user, environment) using the current active model."""
    if _current_model_id is not None:
        build_for_user(_current_model_id, user_id, environment_id)


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
            used_faiss = False

    if not used_faiss:
        if index is None or not hasattr(index, "shape"):
            return []
        sims = index @ vec
        pairs = [(id_map[i], float(sims[i])) for i in range(len(id_map))]

    # Collapse to the best score per identity (no-op when one vector per identity).
    best: dict[int, float] = {}
    for iid, s in pairs:
        if s >= threshold and (iid not in best or s > best[iid]):
            best[iid] = s
    ranked = sorted(best.items(), key=lambda x: x[1], reverse=True)
    return ranked[:k]
