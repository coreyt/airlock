import sys
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


def test_get_engine_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AIRLOCK_ENABLE_FATHOMDB", raising=False)
    monkeypatch.setattr(datastore, "engine", None, raising=False)

    with patch("airlock.datastore.init_engine") as mock_init:
        assert datastore.get_engine() is None

    mock_init.assert_not_called()


def test_get_engine_enabled_uses_init(monkeypatch):
    monkeypatch.setenv("AIRLOCK_ENABLE_FATHOMDB", "1")
    monkeypatch.setattr(datastore, "engine", None, raising=False)

    with patch("airlock.datastore.init_engine", return_value="engine") as mock_init:
        assert datastore.get_engine() == "engine"

    mock_init.assert_called_once()
