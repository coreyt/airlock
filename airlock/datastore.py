import os
from pathlib import Path
from typing import Any


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def fathomdb_enabled() -> bool:
    """Return whether FathomDB storage is explicitly enabled."""
    return _env_flag("AIRLOCK_ENABLE_FATHOMDB", default=False)


def init_engine(db_path: str) -> Any | None:
    try:
        from fathomdb import Engine

        return Engine.open(db_path, embedder="builtin")
    except ImportError:
        return None


def get_db_path() -> str:
    state_dir = Path(os.getenv("AIRLOCK_STATE_DIR", os.getenv("AIRLOCK_LOG_DIR", "./logs")))
    state_dir.mkdir(parents=True, exist_ok=True)
    return str(state_dir / "airlock.db")


engine: Any | None = None


def get_engine() -> Any | None:
    """Lazily initialize the FathomDB engine only when explicitly enabled."""
    global engine
    if engine is not None:
        return engine
    if not fathomdb_enabled():
        return None
    engine = init_engine(get_db_path())
    return engine
