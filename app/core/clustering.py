"""Unsupervised face clustering — group unlabeled face embeddings into
"probably one person" clusters via cosine-threshold connected components.

No new dependencies: numpy for similarity, union-find for grouping. Row-by-row
similarity keeps memory at O(n) rather than materializing the full n x n matrix,
which is fine for the thousands-of-faces scale Argus targets.
"""

from __future__ import annotations

from typing import Any


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_embeddings(
    items: list[tuple[int, bytes]], threshold: float, min_size: int = 2,
) -> list[list[int]]:
    """Group detection ids whose embeddings are within ``threshold`` cosine similarity.

    ``items`` is a list of (detection_id, embedding_bytes). Returns clusters (lists of
    detection ids) with at least ``min_size`` members, largest first. Singletons and
    sub-threshold faces are dropped — a "suggested person" needs corroborating faces.
    """
    import numpy as np

    if len(items) < min_size:
        return []

    ids = [i for i, _ in items]
    vecs = []
    for _, emb in items:
        v = np.frombuffer(bytes(emb), dtype=np.float32).astype(np.float32)
        n = np.linalg.norm(v)
        vecs.append(v / n if n > 0 else v)
    mat = np.stack(vecs)  # (n, d), L2-normalized → dot product is cosine similarity

    uf = _UnionFind(len(ids))
    for i in range(len(ids)):
        sims = mat[i + 1:] @ mat[i]  # similarity of i against every j > i
        for offset in np.where(sims >= threshold)[0]:
            uf.union(i, i + 1 + int(offset))

    groups: dict[int, list[int]] = {}
    for idx in range(len(ids)):
        groups.setdefault(uf.find(idx), []).append(ids[idx])

    clusters = [g for g in groups.values() if len(g) >= min_size]
    clusters.sort(key=len, reverse=True)
    return clusters


def best_internal_score(items_by_id: dict[int, Any], cluster: list[int]) -> int:
    """Return the cluster member with the highest detection confidence — used to pick a
    representative crop for display. ``items_by_id`` maps id -> row with a 'confidence' key."""
    return max(cluster, key=lambda did: items_by_id[did]["confidence"])
