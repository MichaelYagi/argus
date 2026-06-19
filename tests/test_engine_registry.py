import threading
import time

from app.core.engine_registry import EngineRegistry


class _FakeEngine:
    def __init__(self, name: str):
        self.name = name


def test_initially_no_engines():
    r = EngineRegistry()
    assert r.get_face_engine() is None
    assert r.get_object_engine() is None


def test_swap_face_engine():
    r = EngineRegistry()
    e = _FakeEngine("face-v1")
    r.swap_face_engine(e)
    assert r.get_face_engine() is e


def test_swap_object_engine():
    r = EngineRegistry()
    e = _FakeEngine("obj-v1")
    r.swap_object_engine(e)
    assert r.get_object_engine() is e


def test_face_and_object_slots_are_independent():
    r = EngineRegistry()
    f = _FakeEngine("face")
    o = _FakeEngine("obj")
    r.swap_face_engine(f)
    r.swap_object_engine(o)
    assert r.get_face_engine() is f
    assert r.get_object_engine() is o


def test_swap_replaces_previous_engine():
    r = EngineRegistry()
    e1 = _FakeEngine("v1")
    e2 = _FakeEngine("v2")
    r.swap_face_engine(e1)
    r.swap_face_engine(e2)
    assert r.get_face_engine() is e2


def test_concurrent_readers_always_see_valid_engine():
    """Readers racing a swap never observe a value outside the known set."""
    r = EngineRegistry()
    e1 = _FakeEngine("v1")
    e2 = _FakeEngine("v2")
    r.swap_face_engine(e1)

    errors: list[str] = []

    def reader():
        for _ in range(200):
            engine = r.get_face_engine()
            if engine not in (e1, e2):
                errors.append(f"unexpected engine: {engine}")

    def swapper():
        for _ in range(100):
            r.swap_face_engine(e2)
            time.sleep(0)
            r.swap_face_engine(e1)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=swapper))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors


def test_concurrent_swaps_serialize():
    """Multiple threads swapping simultaneously leave the registry in a consistent state."""
    r = EngineRegistry()
    engines = [_FakeEngine(f"v{i}") for i in range(10)]

    def swap_all():
        for e in engines:
            r.swap_face_engine(e)

    threads = [threading.Thread(target=swap_all) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert r.get_face_engine() in engines
