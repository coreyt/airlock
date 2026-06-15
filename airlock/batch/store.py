"""SQLite state store + idempotency keys (design §3.3 + §3.7).

One row per batch (keyed by the deterministic idempotency key ``idem``), plus
per-output-row staging rows keyed by ``(batch_id, row_key)``. All create
mutations go through a single-writer ``BEGIN IMMEDIATE`` transaction so the
claim is race-free (§3.7).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

# Batch lifecycle states (design §3.7 state machine).
CREATING = "CREATING"
CREATED = "CREATED"
RETRIEVING = "RETRIEVING"
STAGED = "STAGED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

# How long a create lease is held before another worker may reconcile (§3.7).
LEASE_SECONDS = 60.0


def get_batch_db_path() -> str:
    """Return the SQLite path for batch state under the Airlock data dir."""
    state_dir = Path(
        os.getenv("AIRLOCK_STATE_DIR", os.getenv("AIRLOCK_LOG_DIR", "./logs"))
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    return str(state_dir / "airlock-batch.db")


def compute_idem(
    input_file_id: str, model: str, endpoint: str, params: dict | None
) -> str:
    """Deterministic idempotency key (design §3.7).

    ``idem = sha256(input_file_id ∥ model ∥ endpoint ∥ canonical(params))``.
    This is the state-store primary key *and* the provider ``display_name``.
    """
    canon = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    raw = "\x00".join([input_file_id or "", model or "", endpoint or "", canon])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class BatchStore:
    """SQLite-backed batch state store."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_batch_db_path()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # -- connection -------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    idem TEXT PRIMARY KEY,
                    batch_id TEXT UNIQUE,
                    input_file_id TEXT,
                    model TEXT,
                    endpoint TEXT,
                    backend TEXT,
                    job_id TEXT,
                    status TEXT,
                    lease_until REAL,
                    output_file_id TEXT,
                    client TEXT,
                    row_count INTEGER,
                    error TEXT,
                    created_at REAL,
                    updated_at REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_rows (
                    batch_id TEXT,
                    row_key TEXT,
                    content_sha TEXT,
                    body TEXT,
                    PRIMARY KEY (batch_id, row_key)
                )
                """
            )
            conn.commit()

    # -- create / claim ---------------------------------------------------
    def claim(
        self,
        idem: str,
        *,
        batch_id: str | None = None,
        input_file_id: str = "",
        model: str = "",
        endpoint: str = "",
        backend: str = "",
        client: str | None = None,
    ) -> tuple[bool, dict]:
        """Atomic write-ahead claim (design §3.7).

        ``INSERT … ON CONFLICT(idem) DO NOTHING`` inside ``BEGIN IMMEDIATE`` so
        exactly one concurrent caller wins the row. Returns ``(won, row)`` where
        ``won`` is True only for the caller that inserted the row.
        """
        now = time.time()
        bid = batch_id or f"batch-{uuid.uuid4().hex}"
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                INSERT INTO batches(
                    idem, batch_id, input_file_id, model, endpoint, backend,
                    job_id, status, lease_until, client, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                ON CONFLICT(idem) DO NOTHING
                """,
                (
                    idem,
                    bid,
                    input_file_id,
                    model,
                    endpoint,
                    backend,
                    CREATING,
                    now + LEASE_SECONDS,
                    client,
                    now,
                    now,
                ),
            )
            won = cur.rowcount == 1
            conn.commit()
        finally:
            conn.close()
        return won, self.get(idem)

    def expire_lease(self, idem: str) -> None:
        """Force the create lease to expire (used to simulate a crash window)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE batches SET lease_until = ? WHERE idem = ?",
                (time.time() - 1.0, idem),
            )
            conn.commit()

    def lease_expired(self, row: dict) -> bool:
        lease = row.get("lease_until")
        return lease is None or float(lease) < time.time()

    def set_created(self, idem: str, *, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE batches
                SET job_id = ?, status = ?, updated_at = ?
                WHERE idem = ?
                """,
                (job_id, CREATED, time.time(), idem),
            )
            conn.commit()

    # -- result staging (§3.6 + §3.7) ------------------------------------
    def begin_retrieving(self, idem: str) -> bool:
        """Atomic ``CREATED -> RETRIEVING`` gate.

        Returns True if staging should proceed (we transitioned the row, or it
        was already RETRIEVING — a resumable interrupted stage). Returns False
        if the batch is already STAGED (a second caller re-fetches nothing).
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM batches WHERE idem = ?", (idem,)
            ).fetchone()
            if row is None:
                conn.commit()
                return False
            status = row["status"]
            if status == STAGED:
                conn.commit()
                return False
            if status == CREATED:
                conn.execute(
                    "UPDATE batches SET status = ?, updated_at = ? WHERE idem = ?",
                    (RETRIEVING, time.time(), idem),
                )
            conn.commit()
            # CREATED -> RETRIEVING (proceed) or already RETRIEVING (resume).
            return status in (CREATED, RETRIEVING)
        finally:
            conn.close()

    def staged_keys(self, batch_id: str) -> dict[str, str]:
        """Return ``{row_key: content_sha}`` already staged for a batch."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT row_key, content_sha FROM batch_rows WHERE batch_id = ?",
                (batch_id,),
            ).fetchall()
        return {r["row_key"]: r["content_sha"] for r in rows}

    def stage_row(
        self, batch_id: str, row_key: str, content_sha: str, body: dict
    ) -> None:
        """Upsert one output row keyed by ``(batch_id, row_key)`` (§3.7)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO batch_rows(batch_id, row_key, content_sha, body)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(batch_id, row_key)
                DO UPDATE SET content_sha = excluded.content_sha,
                              body = excluded.body
                """,
                (batch_id, row_key, content_sha, json.dumps(body)),
            )
            conn.commit()

    def staged_bodies(self, batch_id: str) -> list[dict]:
        """Return all staged output line bodies for a batch (ordered by key)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT body FROM batch_rows WHERE batch_id = ? ORDER BY row_key",
                (batch_id,),
            ).fetchall()
        return [json.loads(r["body"]) for r in rows if r["body"]]

    def set_staged(
        self, idem: str, *, output_file_id: str, row_count: int | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE batches
                SET status = ?, output_file_id = ?, row_count = ?, updated_at = ?
                WHERE idem = ?
                """,
                (STAGED, output_file_id, row_count, time.time(), idem),
            )
            conn.commit()

    def set_failed(self, idem: str, *, error: str, status: str = FAILED) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE batches SET status = ?, error = ?, updated_at = ? WHERE idem = ?",
                (status, error, time.time(), idem),
            )
            conn.commit()

    # -- reads ------------------------------------------------------------
    def get(self, idem: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE idem = ?", (idem,)
            ).fetchone()
        return dict(row) if row is not None else None

    def get_by_batch_id(self, batch_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return dict(row) if row is not None else None
