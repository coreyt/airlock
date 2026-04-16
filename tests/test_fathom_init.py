import sqlite3
import sys
import threading
from unittest.mock import patch

import airlock.datastore as datastore
from airlock.datastore import init_engine


def test_init_engine_without_fathomdb(tmp_path):
    with patch.dict(sys.modules, {"fathomdb": None}):
        assert init_engine(str(tmp_path / "test.db")) is None


def test_init_engine_with_fathomdb(tmp_path):
    # This should fail in RED phase because fathomdb is not installed yet
    # so init_engine will return None
    engine = init_engine(str(tmp_path / "test.db"))
    assert engine is not None
    assert engine.__class__.__name__ == "Engine"


def test_init_engine_bootstraps_vec_stub_table(tmp_path):
    db_path = tmp_path / "test.db"
    seen = {}

    class FakeEngine:
        @staticmethod
        def open(path, embedder=None):
            with sqlite3.connect(path) as conn:
                row = conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'vec_nodes_active'
                    """
                ).fetchone()
            seen["table_exists"] = row is not None
            seen["embedder"] = embedder
            return "engine"

    fake_module = type("FakeFathomModule", (), {"Engine": FakeEngine})

    with patch.dict(sys.modules, {"fathomdb": fake_module}):
        engine = init_engine(str(db_path))

    assert engine == "engine"
    assert seen["table_exists"] is True
    assert seen["embedder"] == "builtin"


def test_get_engine_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AIRLOCK_ENABLE_FATHOMDB", raising=False)
    monkeypatch.setattr(datastore, "engine", None, raising=False)

    with patch("airlock.datastore.init_engine") as mock_init:
        assert datastore.get_engine() is None

    mock_init.assert_not_called()


def test_get_engine_enabled_uses_init(monkeypatch):
    monkeypatch.setenv("AIRLOCK_ENABLE_FATHOMDB", "1")
    monkeypatch.setattr(datastore, "engine", None, raising=False)
    monkeypatch.setattr(datastore, "engine_pid", None, raising=False)

    with patch("airlock.datastore.init_engine", return_value="engine") as mock_init:
        assert datastore.get_engine() == "engine"

    mock_init.assert_called_once()


def test_get_engine_returns_none_for_foreign_process(monkeypatch):
    monkeypatch.setenv("AIRLOCK_ENABLE_FATHOMDB", "1")
    monkeypatch.setattr(datastore, "engine", "engine", raising=False)
    monkeypatch.setattr(datastore, "engine_pid", 111, raising=False)
    monkeypatch.setattr(datastore.os, "getpid", lambda: 222)

    with patch("airlock.datastore.init_engine") as mock_init:
        assert datastore.get_engine() is None

    mock_init.assert_not_called()


def test_get_engine_initializes_once_under_concurrent_calls(monkeypatch):
    monkeypatch.setenv("AIRLOCK_ENABLE_FATHOMDB", "1")
    monkeypatch.setattr(datastore, "engine", None, raising=False)
    monkeypatch.setattr(datastore, "engine_pid", None, raising=False)

    call_count = 0
    call_count_lock = threading.Lock()
    release = threading.Event()
    ready = threading.Barrier(4)
    results = []
    errors = []

    def fake_init_engine(_db_path):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
        release.wait(timeout=2)
        return "engine"

    def worker():
        try:
            ready.wait(timeout=2)
            results.append(datastore.get_engine())
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    with patch("airlock.datastore.init_engine", side_effect=fake_init_engine):
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        release.set()
        for thread in threads:
            thread.join(timeout=2)

    assert errors == []
    assert results == ["engine"] * 4
    assert call_count == 1
