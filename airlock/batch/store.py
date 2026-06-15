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

# File-scan lifecycle states (design §3.7 ``files:`` row, to-do #2). Values are
# namespaced (``FILE_*``) so they can never be confused with the batch lifecycle
# states above even though both have a "failed" notion.
FILE_UPLOADED = "FILE_UPLOADED"
FILE_SCANNING = "FILE_SCANNING"
FILE_READY = "FILE_READY"
FILE_REJECTED = "FILE_REJECTED"
FILE_FAILED = "FILE_FAILED"

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_files (
                    file_id TEXT PRIMARY KEY,
                    status TEXT,
                    reason TEXT,
                    row_count INTEGER,
                    byte_count INTEGER,
                    scan_enabled INTEGER,
                    lease_until REAL,
                    created_at REAL,
                    updated_at REAL
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

    def reacquire_lease(self, idem: str) -> bool:
        """CAS-reacquire an expired ``CREATING`` lease (design §3.7).

        Atomically extend the lease *only* if the row is still ``CREATING`` with
        an expired lease, inside ``BEGIN IMMEDIATE`` (single-writer). Returns
        True for the one worker that wins the re-acquisition; every other
        concurrent reclaimer gets False and must back off (adopt the row) so no
        two reclaimers can both reconcile + create — bounding duplicates to the
        ≤1-job invariant.
        """
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE batches
                SET lease_until = ?, updated_at = ?
                WHERE idem = ? AND status = ? AND lease_until < ?
                """,
                (now + LEASE_SECONDS, now, idem, CREATING, now),
            )
            won = cur.rowcount == 1
            conn.commit()
        finally:
            conn.close()
        return won

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
        """Atomic compare-and-set gate into ``RETRIEVING`` (design §3.7).

        Returns True for *exactly one* worker — the one that wins the CAS into
        RETRIEVING. That is either a fresh ``CREATED -> RETRIEVING`` transition,
        or a crash-resume that re-acquires an *expired* RETRIEVING lease. A
        concurrent caller observing an active RETRIEVING lease (another fetcher
        is in flight) or a ``STAGED`` row returns False and re-fetches nothing.
        Per-row staging stays idempotent so a reclaimed resume only processes
        the missing rows.
        """
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE batches
                SET status = ?, lease_until = ?, updated_at = ?
                WHERE idem = ?
                  AND (status = ? OR (status = ? AND lease_until < ?))
                """,
                (RETRIEVING, now + LEASE_SECONDS, now, idem, CREATED, RETRIEVING, now),
            )
            won = cur.rowcount == 1
            conn.commit()
        finally:
            conn.close()
        return won

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

    # -- file scan state (design §3.7 files row, to-do #2) ---------------
    def record_file_upload(
        self,
        file_id: str,
        *,
        byte_count: int,
        status: str = FILE_UPLOADED,
        scan_enabled: bool = True,
    ) -> None:
        """Persist a freshly uploaded file in its initial scan state.

        ``status`` defaults to ``FILE_UPLOADED`` (a scan will claim it). When
        scanning is disabled it is recorded ``FILE_READY`` directly with
        ``scan_enabled=False`` so ``create`` knowingly proceeds with the raw
        upload (legacy posture) — distinct from a *scanned* READY file, which
        must always have a scrubbed artifact.
        """
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO batch_files(
                    file_id, status, reason, row_count, byte_count,
                    scan_enabled, lease_until, created_at, updated_at
                ) VALUES (?, ?, NULL, NULL, ?, ?, NULL, ?, ?)
                ON CONFLICT(file_id) DO NOTHING
                """,
                (file_id, status, byte_count, 1 if scan_enabled else 0, now, now),
            )
            conn.commit()

    def claim_file_scan(self, file_id: str) -> bool:
        """Atomic CAS into ``SCANNING`` (design §3.7, mirrors ``reacquire_lease``).

        Returns True for exactly one worker: the one that transitions the row
        from ``UPLOADED`` (or an *expired* ``SCANNING`` lease, i.e. crash-resume)
        into ``SCANNING``. A concurrent scheduler observing an active lease or a
        terminal state gets False and does nothing — the scan runs once.
        """
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE batch_files
                SET status = ?, lease_until = ?, updated_at = ?
                WHERE file_id = ?
                  AND (status = ?
                       OR (status = ? AND lease_until IS NOT NULL AND lease_until < ?))
                """,
                (
                    FILE_SCANNING,
                    now + LEASE_SECONDS,
                    now,
                    file_id,
                    FILE_UPLOADED,
                    FILE_SCANNING,
                    now,
                ),
            )
            won = cur.rowcount == 1
            conn.commit()
        finally:
            conn.close()
        return won

    def set_file_ready(self, file_id: str, *, row_count: int | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE batch_files
                SET status = ?, row_count = ?, lease_until = NULL, updated_at = ?
                WHERE file_id = ?
                """,
                (FILE_READY, row_count, time.time(), file_id),
            )
            conn.commit()

    def set_file_rejected(self, file_id: str, *, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE batch_files
                SET status = ?, reason = ?, lease_until = NULL, updated_at = ?
                WHERE file_id = ?
                """,
                (FILE_REJECTED, reason, time.time(), file_id),
            )
            conn.commit()

    def set_file_failed(self, file_id: str, *, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE batch_files
                SET status = ?, reason = ?, lease_until = NULL, updated_at = ?
                WHERE file_id = ?
                """,
                (FILE_FAILED, error, time.time(), file_id),
            )
            conn.commit()

    def get_file(self, file_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM batch_files WHERE file_id = ?", (file_id,)
            ).fetchone()
        return dict(row) if row is not None else None

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
