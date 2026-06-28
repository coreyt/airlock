"""
Airlock SQL Logger — LiteLLM custom callback for writing logs to a SQL database.

Uses SQLAlchemy Core to insert log records into an ``airlock_logs`` table.
The table is auto-created on first use.

Env vars:
    AIRLOCK_SQL_URL — SQLAlchemy connection string
        (e.g. "sqlite:///logs.db", "postgresql://user:pass@host/db")
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("airlock.callbacks.sql")

try:
    import sqlalchemy as sa

    _SA_AVAILABLE = True
except ImportError:
    _SA_AVAILABLE = False

from litellm.integrations.custom_logger import CustomLogger

from .projections import project_sql


def _get_table() -> Any:
    """Define the airlock_logs table schema."""
    if not _SA_AVAILABLE:
        raise ImportError(
            "sqlalchemy is required for SQL logging: pip install airlock-llm[sql]"
        )

    metadata = sa.MetaData()
    table = sa.Table(
        "airlock_logs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.String, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("model", sa.String),
        sa.Column("user", sa.String),
        sa.Column("team", sa.String),
        sa.Column("request_id", sa.String),
        sa.Column("messages", sa.Text),  # JSON-encoded
        sa.Column("response", sa.Text),  # JSON-encoded
        sa.Column("error", sa.Text),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("prompt_tokens", sa.Integer),
        sa.Column("completion_tokens", sa.Integer),
        sa.Column("total_tokens", sa.Integer),
    )
    return metadata, table


class AirlockSQLLogger(CustomLogger):
    """LiteLLM callback that writes log records to a SQL database."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._url = os.getenv("AIRLOCK_SQL_URL", "")
        self._engine = None
        self._table = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        if not _SA_AVAILABLE:
            raise ImportError(
                "sqlalchemy is required for SQL logging: pip install airlock-llm[sql]"
            )
        if not self._url:
            logger.warning("AIRLOCK_SQL_URL not set, SQL logging disabled")
            self._initialized = True
            return

        self._engine = sa.create_engine(self._url)
        metadata, self._table = _get_table()
        metadata.create_all(self._engine)
        self._initialized = True

    def _insert(self, record: dict[str, Any]) -> None:
        self._ensure_initialized()
        if self._engine is None or self._table is None:
            return

        try:
            with self._engine.connect() as conn:
                conn.execute(self._table.insert().values(**record))
                conn.commit()
        except Exception:
            logger.exception("sql_insert_failed model=%s", record.get("model"))

    def record_event(self, event: Any) -> None:
        """Recorder sink: insert the sql projection of one ``RequestEvent``.

        Reuses the existing ``_insert``/``_ensure_initialized`` path unchanged
        (engine/table guard + exception swallow).
        """
        self._insert(project_sql(event))


# Module-level instance for LiteLLM config.yaml callback registration.
proxy_sql_logger = AirlockSQLLogger()
