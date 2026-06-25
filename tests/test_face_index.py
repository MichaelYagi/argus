"""Face index regression tests."""

from __future__ import annotations

from app.core import face_index
from app.db import store


def test_build_all_records_model_even_with_no_enrolled_faces(tmp_path):
    """On a freshly-activated model with nothing enrolled yet, build_all must still set
    the current model id — otherwise the first enrollment's rebuild_user() is a no-op and
    matching only comes alive after a restart."""
    store.configure(tmp_path / "t.db")
    store.init_db()
    try:
        face_index.build_all(4242)
        assert face_index._current_model_id == 4242
        # A subsequent rebuild now actually runs (builds an entry for the pair) instead
        # of silently doing nothing.
        face_index.rebuild_user(user_id=1, environment_id=1)
        assert (1, 1) in face_index._id_maps
    finally:
        face_index._current_model_id = None
        face_index._indices.clear()
        face_index._id_maps.clear()
        store.configure(None)
