import json
import sqlite3
import sys
import threading
from unittest.mock import patch

import pytest

import airlock.datastore as datastore
from airlock.datastore import (
    LegacyDatabaseError,
    close_engine,
    get_db_path,
    init_engine,
)


def _make_legacy_db(path):
    """Create a fixture file carrying the FathomDB 0.3.x schema markers."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE fathom_schema_migrations (version TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE nodes (row_id TEXT PRIMARY KEY, properties TEXT)")
        conn.execute("CREATE TABLE edges (row_id TEXT PRIMARY KEY)")
        conn.commit()


def test_init_engine_without_fathomdb(tmp_path):
    with patch.dict(sys.modules, {"fathomdb": None}):
        assert init_engine(str(tmp_path / "test.db")) is None


def test_init_engine_fresh_db_write_read_close(tmp_path):
    """A-1 done criterion: fresh DB opens, accepts writes, closes cleanly."""
    from fathomdb import read

    db_path = str(tmp_path / "airlock-fathom.db")
    engine = init_engine(db_path)
    assert engine is not None

    receipt = engine.write(
        [
            {
                "kind": "RequestLog",
                "logical_id": "call-1",
                "source_id": "airlock:test",
                "body": json.dumps({"model": "gpt-4"}),
            }
        ]
    )
    assert receipt.row_cursors

    rows = read.list(engine, "RequestLog", limit=10)
    assert [row.logical_id for row in rows] == ["call-1"]
    assert engine.counters().writes == 1

    engine.drain(timeout_s=2)
    engine.close()


def test_init_engine_refuses_legacy_file(tmp_path):
    """A-1 done criterion: a 0.3.1 file fails loudly with a named reason.

    0.8.21 would otherwise open the legacy file without error, report it
    empty, and write into it — silently adopting an abandoned database.
    """
    db_path = tmp_path / "airlock.db"
    _make_legacy_db(db_path)

    with pytest.raises(LegacyDatabaseError) as excinfo:
        init_engine(str(db_path))

    message = str(excinfo.value)
    assert str(db_path) in message
    assert "0.3" in message

    # Refusal means untouched: no 0.8 schema was written into the file.
    with sqlite3.connect(db_path) as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "canonical_nodes" not in names


def test_init_engine_refuses_hybrid_file(tmp_path):
    """A file carrying both schemas (the adoption hazard realized) is refused."""
    db_path = tmp_path / "airlock.db"
    _make_legacy_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE canonical_nodes (write_cursor INTEGER PRIMARY KEY)")
        conn.commit()

    with pytest.raises(LegacyDatabaseError):
        init_engine(str(db_path))


def test_get_db_path_avoids_legacy_filename(tmp_path, monkeypatch):
    """The 0.8.x default filename is distinct from the 0.3.x-era airlock.db."""
    monkeypatch.setenv("AIRLOCK_STATE_DIR", str(tmp_path))
    path = get_db_path()
    assert path == str(tmp_path / "airlock-fathom.db")
    assert not path.endswith("/airlock.db")


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


def test_close_engine_drains_and_releases_singleton(monkeypatch):
    drained = {}

    class FakeEngine:
        def drain(self, *, timeout_s=0):
            drained["timeout_s"] = timeout_s

        def close(self):
            drained["closed"] = True

    monkeypatch.setattr(datastore, "engine", FakeEngine(), raising=False)
    monkeypatch.setattr(datastore, "engine_pid", 123, raising=False)

    close_engine(drain_timeout_s=1.5)

    assert drained == {"timeout_s": 1.5, "closed": True}
    assert datastore.engine is None
    assert datastore.engine_pid is None


def test_close_engine_without_engine_is_noop(monkeypatch):
    monkeypatch.setattr(datastore, "engine", None, raising=False)
    monkeypatch.setattr(datastore, "engine_pid", None, raising=False)
    close_engine()
    assert datastore.engine is None
