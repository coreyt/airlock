import os
from typing import Any


def init_engine(db_path: str) -> Any | None:
    try:
        from fathomdb import Engine

        return Engine.open(db_path, embedder="builtin")
    except ImportError:
        return None


state_dir = os.getenv("AIRLOCK_STATE_DIR", os.getenv("AIRLOCK_LOG_DIR", "./logs"))
os.makedirs(state_dir, exist_ok=True)
engine = init_engine(os.path.join(state_dir, "airlock.db"))
