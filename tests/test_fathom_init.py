import sys
from unittest.mock import patch

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
