import os
import sqlite3
from pathlib import Path
import threading
from typing import Any


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def fathomdb_enabled() -> bool:
    """Return whether FathomDB storage is explicitly enabled."""
    return _env_flag("AIRLOCK_ENABLE_FATHOMDB", default=False)


def _ensure_vector_stub_table(db_path: str) -> None:
    """Create Fathom's missing vec projection table when bootstrapping a fresh DB.

    FathomDB 0.3.1 writes can emit stderr noise about ``vec_nodes_active`` being
    absent on fresh databases even though normal node writes succeed. Airlock's
    request logging does not depend on vector search, so pre-creating the table
    avoids that write-path failure mode without changing the query surface.
    """
    db_parent = Path(db_path).parent
    db_parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vec_nodes_active (
                chunk_id TEXT PRIMARY KEY,
                embedding BLOB
            )
            """
        )
        conn.commit()


def init_engine(db_path: str) -> Any | None:
    try:
        from fathomdb import Engine

        _ensure_vector_stub_table(db_path)
        return Engine.open(db_path, embedder="builtin")
    except ImportError:
        return None


def get_db_path() -> str:
    state_dir = Path(os.getenv("AIRLOCK_STATE_DIR", os.getenv("AIRLOCK_LOG_DIR", "./logs")))
    state_dir.mkdir(parents=True, exist_ok=True)
    return str(state_dir / "airlock.db")


engine: Any | None = None
engine_pid: int | None = None
engine_lock = threading.Lock()


def get_engine() -> Any | None:
    """Lazily initialize the FathomDB engine only when explicitly enabled."""
    global engine, engine_pid
    current_pid = os.getpid()
    if engine is not None:
        if engine_pid == current_pid:
            return engine
        return None
    if not fathomdb_enabled():
        return None
    with engine_lock:
        if engine is not None:
            if engine_pid == current_pid:
                return engine
            return None
        engine = init_engine(get_db_path())
        engine_pid = current_pid if engine is not None else None
        return engine
