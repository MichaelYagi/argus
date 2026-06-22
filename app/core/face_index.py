"""In-memory faiss index for fast face similarity search.

One index per user, built over representative (averaged) embeddings.
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
_indices: dict[int, Any]       = {}  # user_id → faiss.IndexFlatIP (or numpy matrix)
_id_maps: dict[int, list[int]] = {}  # user_id → [identity_id, ...]
_current_model_id: int | None  = None


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_for_user(model_id: int, user_id: int) -> None:
    """Build or rebuild the faiss index for one user."""
    import numpy as np

    from app.db import store

    # Compute any missing representatives first
    with store._connect() as conn:
        stale = conn.execute(
            """SELECT DISTINCT fe.identity_id FROM face_embeddings fe
               JOIN identities i ON i.id = fe.identity_id
               WHERE fe.model_id = ? AND i.user_id = ?
                 AND i.representative_embedding IS NULL""",
            (model_id, user_id),
        ).fetchall()
    for row in stale:
        store.compute_and_store_representative(row["identity_id"], model_id)

    rows = store.get_representative_embeddings(model_id, user_id)

    with _lock:
        global _current_model_id
        _current_model_id = model_id

        if not rows:
            _indices.pop(user_id, None)
            _id_maps[user_id] = []
            return

        faiss = _try_import_faiss()
        use_faiss = faiss is not None

        vectors, id_map = [], []
        for row in rows:
            emb = row["representative_embedding"]
            if not emb:
                continue
            vec  = np.frombuffer(bytes(emb), dtype=np.float32).copy()
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec /= norm
            vectors.append(vec)
            id_map.append(row["identity_id"])

        _id_maps[user_id] = id_map

        if not vectors:
            _indices.pop(user_id, None)
            return

        if use_faiss:
            idx = faiss.IndexFlatIP(len(vectors[0]))
            idx.add(np.stack(vectors).astype(np.float32))
            _indices[user_id] = idx
        else:
            _indices[user_id] = np.stack(vectors).astype(np.float32)


def build_all(model_id: int) -> None:
    """Rebuild index for every user that has face data for this model."""
    if _try_import_faiss() is None:
        log.warning("faiss disabled or unavailable — using numpy similarity search")

    from app.db import store
    with store._connect() as conn:
        user_ids = [r[0] for r in conn.execute(
            """SELECT DISTINCT i.user_id FROM identities i
               JOIN face_embeddings fe ON fe.identity_id = i.id
               WHERE fe.model_id = ?""",
            (model_id,),
        ).fetchall()]
    for uid in user_ids:
        build_for_user(model_id, uid)
    log.info("Face index built for model_id=%s (%d users)", model_id, len(user_ids))


def rebuild_user(user_id: int) -> None:
    """Rebuild index for one user using the current active model."""
    if _current_model_id is not None:
        build_for_user(_current_model_id, user_id)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(
    embedding: Any,
    user_id: int,
    threshold: float,
    k: int = 5,
) -> list[tuple[int, float]]:
    """Return up to k (identity_id, similarity) pairs above threshold, sorted descending."""
    import numpy as np

    with _lock:
        index  = _indices.get(user_id)
        id_map = list(_id_maps.get(user_id, []))

    if not id_map:
        return []

    vec  = np.asarray(embedding, dtype=np.float32).copy()
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm

    faiss = _try_import_faiss()
    if faiss is not None:
        try:
            if isinstance(index, faiss.swigfaiss.Index):
                k_actual = min(k, index.ntotal)
                scores, idxs = index.search(vec.reshape(1, -1), k_actual)
                return [
                    (id_map[i], float(s))
                    for s, i in zip(scores[0], idxs[0])
                    if i >= 0 and float(s) >= threshold
                ]
        except Exception:
            pass

    # numpy fallback
    if index is None or not hasattr(index, "shape"):
        return []
    sims = index @ vec
    ranked = sorted(
        ((id_map[i], float(sims[i])) for i in range(len(id_map)) if float(sims[i]) >= threshold),
        key=lambda x: x[1], reverse=True,
    )
    return ranked[:k]
