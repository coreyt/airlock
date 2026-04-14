from typing import Any


def init_engine(db_path: str) -> Any | None:
    try:
        from fathomdb import Engine

        return Engine.open(db_path, embedder="builtin")
    except ImportError:
        return None
