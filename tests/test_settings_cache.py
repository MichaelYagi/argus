import pytest

from app.core.settings_cache import SettingsCache, _coerce
from app.db import store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    store.configure(tmp_path / "test.db")
    store.init_db()
    yield
    store.configure(None)


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------

def test_coerce_float():
    assert _coerce("0.5", "float") == 0.5
    assert isinstance(_coerce("0.5", "float"), float)


def test_coerce_int():
    assert _coerce("40", "int") == 40
    assert isinstance(_coerce("40", "int"), int)


def test_coerce_bool_true():
    assert _coerce("true", "bool") is True
    assert _coerce("True", "bool") is True


def test_coerce_bool_false():
    assert _coerce("false", "bool") is False


def test_coerce_string():
    assert _coerce("*", "string") == "*"


# ---------------------------------------------------------------------------
# Load and get
# ---------------------------------------------------------------------------

def test_load_populates_cache():
    c = SettingsCache()
    c.load()
    assert c.get("face.match_threshold") == 0.5
    assert c.get("face.min_face_size") == 40
    assert c.get("system.save_unknown_detections") is True
    assert c.get("system.crop_padding") == 0.2
    assert c.get("object.classes_enabled") == "*"
    assert c.get("system.use_gpu") is True


def test_get_returns_correct_types():
    c = SettingsCache()
    c.load()
    assert isinstance(c.get("face.match_threshold"), float)
    assert isinstance(c.get("face.min_face_size"), int)
    assert isinstance(c.get("system.save_unknown_detections"), bool)
    assert isinstance(c.get("object.classes_enabled"), str)


def test_get_unknown_key_raises():
    c = SettingsCache()
    c.load()
    with pytest.raises(KeyError):
        c.get("nonexistent.key")


def test_get_or_returns_default():
    c = SettingsCache()
    c.load()
    assert c.get_or("nonexistent.key", 42) == 42


def test_get_or_returns_value_when_present():
    c = SettingsCache()
    c.load()
    assert c.get_or("face.match_threshold", 99) == 0.5


# ---------------------------------------------------------------------------
# Set
# ---------------------------------------------------------------------------

def test_set_updates_value():
    c = SettingsCache()
    c.load()
    c.set("face.match_threshold", "0.7", "float")
    assert c.get("face.match_threshold") == 0.7


def test_set_coerces_type():
    c = SettingsCache()
    c.load()
    c.set("system.save_unknown_detections", "false", "bool")
    assert c.get("system.save_unknown_detections") is False


# ---------------------------------------------------------------------------
# All
# ---------------------------------------------------------------------------

def test_all_returns_all_entries():
    c = SettingsCache()
    c.load()
    result = c.all()
    assert len(result) == 15


def test_all_returns_independent_copy():
    c = SettingsCache()
    c.load()
    snapshot = c.all()
    snapshot["face.match_threshold"] = 999
    assert c.get("face.match_threshold") == 0.5
