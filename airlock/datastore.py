import logging
import os
import sqlite3
from pathlib import Path
import threading
from typing import Any

logger = logging.getLogger("airlock.datastore")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def fathomdb_enabled() -> bool:
    """Return whether FathomDB storage is explicitly enabled.

    Returns
    -------
    bool
        ``True`` when ``AIRLOCK_ENABLE_FATHOMDB`` is set to a truthy
        value, otherwise ``False``.
    """
    return _env_flag("AIRLOCK_ENABLE_FATHOMDB", default=False)


class LegacyDatabaseError(RuntimeError):
    """Raised when the configured database path holds a FathomDB 0.3.x file."""


# Tables only the 0.3.x schema creates. 0.8.x uses `_fathomdb_migrations` and
# `canonical_nodes` / `canonical_edges`, so any of these marks a legacy file —
# including a hybrid one that a 0.8 engine already wrote into, which 0.8.21
# would otherwise open without error and silently adopt.
_LEGACY_TABLES = ("fathom_schema_migrations", "nodes", "edges")


def _is_legacy_db(db_path: str) -> bool:
    """Return whether ``db_path`` is an existing FathomDB 0.3.x database."""
    path = Path(db_path)
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            placeholders = ",".join("?" for _ in _LEGACY_TABLES)
            row = conn.execute(
                "SELECT 1 FROM sqlite_master"
                f" WHERE type = 'table' AND name IN ({placeholders})"
                " LIMIT 1",
                _LEGACY_TABLES,
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        # Not readable as SQLite at all — let Engine.open produce its own,
        # typed error rather than mislabeling the file as legacy.
        return False


def init_engine(db_path: str) -> Any | None:
    """Open FathomDB engine for given database path.

    Parameters
    ----------
    db_path : str
        Filesystem path to target database file.

    Returns
    -------
    Any or None
        Open FathomDB engine when dependency is installed; otherwise
        ``None``.

    Raises
    ------
    LegacyDatabaseError
        When ``db_path`` holds a FathomDB 0.3.x database. 0.8.x opens such a
        file without error and writes into it, producing a file carrying both
        schemas — so the legacy file is refused loudly instead.
    """
    try:
        from fathomdb import Engine
    except ImportError:
        return None

    if _is_legacy_db(db_path):
        raise LegacyDatabaseError(
            f"{db_path} is a FathomDB 0.3.x database. Airlock 0.5.11 abandoned "
            "the 0.3.x store (no migration path); the file was left in place and "
            "its records remain in the JSONL logs. Move it aside, or point "
            "AIRLOCK_STATE_DIR somewhere else, to let Airlock create a fresh "
            "0.8.x database."
        )

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # No default embedder: a plain open performs no network access, and vector
    # writes fail typed (EmbedderNotConfiguredError) instead of silently no-op.
    engine = Engine.open(db_path, use_default_embedder=False)
    try:
        _declare_projections(engine)
    except Exception:
        engine.close()
        raise
    return engine


def _declare_projections(engine: Any) -> None:
    """Declare the RequestLog projections; the engine owns every derived index.

    Never passes ``drop``: a destructive change (role removal, tokenizer
    change) raises ``ProjectionDestructiveError`` with the delta rather than
    silently losing an index — treat any such failure as a schema change to be
    made deliberately, not routed around.

    No projection sets ``vector=True``: with no embedder configured, dense
    retrieval is unavailable by design in this deployment (an embedder is a
    separate owner decision — it performs network access on first use).
    """
    from fathomdb import ProjectionRole, ProjectionSpec

    filterable = frozenset({ProjectionRole.FILTERABLE})
    filterable_rankable = frozenset(
        {ProjectionRole.FILTERABLE, ProjectionRole.RANKABLE}
    )
    fts = frozenset({ProjectionRole.SEARCHABLE})
    specs = [
        ProjectionSpec("timestamp", roles=filterable_rankable),
        ProjectionSpec("model", roles=filterable),
        ProjectionSpec("airlock_provider", roles=filterable),
        ProjectionSpec("success", roles=filterable),
        ProjectionSpec("cost", roles=filterable_rankable),
        ProjectionSpec("failure_category", roles=filterable),
        ProjectionSpec("error_type", roles=filterable),
        ProjectionSpec("airlock_client", roles=filterable),
        ProjectionSpec("error", roles=fts, fts=True),
        ProjectionSpec("messages_json", roles=fts, fts=True),
        ProjectionSpec("response_text", roles=fts, fts=True),
    ]
    delta = engine.configure_projections(specs)
    if not delta.unchanged:
        logger.info("FathomDB projections configured: %s", delta)


def get_db_path() -> str:
    """Return filesystem path for Airlock state database.

    Returns
    -------
    str
        Path to ``airlock-fathom.db`` under ``AIRLOCK_STATE_DIR``,
        ``AIRLOCK_LOG_DIR``, or ``./logs``.

    Notes
    -----
    The filename is distinct from the 0.3.x-era ``airlock.db`` so a 0.8.x
    build pointed at an existing state directory can never adopt the legacy
    file by default. The explicit legacy check in :func:`init_engine` still
    guards paths supplied directly.
    """
    state_dir = Path(
        os.getenv("AIRLOCK_STATE_DIR", os.getenv("AIRLOCK_LOG_DIR", "./logs"))
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    return str(state_dir / "airlock-fathom.db")


engine: Any | None = None
engine_pid: int | None = None
engine_lock = threading.Lock()


def get_engine() -> Any | None:
    """Return process-local FathomDB engine singleton.

    Returns
    -------
    Any or None
        Existing engine bound to current process, newly initialized
        engine when FathomDB is enabled, or ``None`` when FathomDB is
        disabled or current process does not own cached engine.

    Notes
    -----
    Airlock keeps engine initialization lazy, PID-bound, and protected by
    a lock to avoid same-process races during concurrent callback writes.
    """
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
        engine_pid = os.getpid() if engine is not None else None
        return engine


def close_engine(*, drain_timeout_s: float = 5.0) -> None:
    """Drain and close the process-local engine, releasing the singleton.

    Parameters
    ----------
    drain_timeout_s : float
        Seconds to wait for in-flight writes before closing.
    """
    global engine, engine_pid
    with engine_lock:
        current = engine
        engine = None
        engine_pid = None
    if current is None:
        return
    try:
        current.drain(timeout_s=drain_timeout_s)
    finally:
        current.close()
