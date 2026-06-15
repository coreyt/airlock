"""Provider-agnostic batch gateway core (design §3).

Holds the OpenAI batch-object shaping, status mapping (§3.5), result staging
(§3.6), and the §3.7 idempotency orchestration (race-free claim, reconcile +
auto-cancel of duplicates, RETRIEVING->STAGED gate). The provider surface is
the injected ``BatchBackend``; nothing here imports a provider SDK.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
import uuid

from airlock.batch.backend import BatchBackend, ResultUnavailableError
from airlock.batch.store import (
    CANCELLED,
    CREATED,
    CREATING,
    FAILED,
    RETRIEVING,
    STAGED,
    BatchStore,
    compute_idem,
)

logger = logging.getLogger("airlock.batch")

# Internal batch state -> OpenAI batch status (design §3.5).
_INTERNAL_TO_OPENAI = {
    CREATING: "validating",
    CREATED: "in_progress",
    RETRIEVING: "finalizing",
    STAGED: "completed",
    FAILED: "failed",
    CANCELLED: "cancelled",
}

_TERMINAL = {STAGED, FAILED, CANCELLED}

# Default batch profile (design §4.2 / §3.3). ``scan_at_upload`` is a NO-OP stub
# in this pack — the insertion point for the async scan pipeline (to-do #2).
DEFAULT_BATCH_PROFILE = {
    "default": {
        "scan_at_upload": True,
        "keyword_block": True,
        "pii_redact": True,
        "pii_hydrate_output": False,
        "output_scan_mode": "observe",
        "max_rows": 50000,
        "max_bytes": 2147483648,
        "max_concurrent_jobs": 5,
    }
}


def _record(event: str, row: dict, *, status: str, error: str | None = None) -> None:
    """Emit a batch lifecycle observability event via Pack B's writer."""
    try:
        from airlock.callbacks.enterprise_logger import write_batch_record

        write_batch_record(
            event=event,
            batch_id=row.get("batch_id") or "",
            provider=row.get("backend") or "",
            model=row.get("model"),
            status=status,
            row_count=row.get("row_count"),
            input_file_id=row.get("input_file_id"),
            job_id=row.get("job_id"),
            client=row.get("client"),
            error=error,
        )
    except Exception:  # noqa: BLE001  observability must never break the gateway
        logger.debug("write_batch_record failed for event=%s", event, exc_info=True)


# ---------------------------------------------------------------------------
# Config helpers (§5, §7.4)
# ---------------------------------------------------------------------------
def load_batch_aliases(config: dict | None) -> dict[str, dict]:
    """Build ``{alias: {backend, provider_model}}`` from the ``airlock_batch``
    markers in ``model_list``. Entries without the marker are ignored."""
    aliases: dict[str, dict] = {}
    for entry in (config or {}).get("model_list", []) or []:
        if not isinstance(entry, dict):
            continue
        marker = entry.get("airlock_batch")
        alias = entry.get("model_name")
        if isinstance(marker, dict) and isinstance(alias, str):
            aliases[alias] = dict(marker)
    return aliases


def load_batch_profile(config: dict | None) -> dict:
    """Return the ``batch_profile`` block, falling back to the default."""
    profile = (config or {}).get("batch_profile")
    if isinstance(profile, dict) and profile:
        return profile
    return DEFAULT_BATCH_PROFILE


def provider_sync_params(entry: dict) -> dict:
    """Return the litellm params forwarded to the provider on the SYNC path.

    Resolves §7.4: the ``airlock_batch`` marker is a *sibling* of
    ``litellm_params`` (not nested inside it), so it never reaches the provider
    SDK on a sync completion. This helper returns exactly what litellm forwards
    and asserts the marker is absent.
    """
    params = dict(entry.get("litellm_params") or {})
    params.pop("airlock_batch", None)
    return params


# ---------------------------------------------------------------------------
# OpenAI batch object shaping (§3.5)
# ---------------------------------------------------------------------------
def to_openai_batch_object(row: dict, *, status_override: str | None = None) -> dict:
    """Shape a state-store row into an OpenAI batch object."""
    status = status_override or _INTERNAL_TO_OPENAI.get(
        row.get("status", ""), "validating"
    )
    errors = None
    if row.get("error"):
        errors = {
            "object": "list",
            "data": [{"code": "batch_error", "message": row["error"]}],
        }
    return {
        "id": row.get("batch_id"),
        "object": "batch",
        "endpoint": row.get("endpoint"),
        "input_file_id": row.get("input_file_id"),
        "completion_window": "24h",
        "status": status,
        "output_file_id": row.get("output_file_id"),
        "model": row.get("model"),
        "errors": errors,
        "created_at": int(row.get("created_at") or time.time()),
        "request_counts": {
            "total": row.get("row_count") or 0,
            "completed": (row.get("row_count") or 0) if status == "completed" else 0,
            "failed": 0,
        },
    }


def _content_sha(body: dict) -> str:
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Create — race-free claim + reconcile (§3.7)
# ---------------------------------------------------------------------------
async def create_batch(
    store: BatchStore,
    backend: BatchBackend,
    *,
    input_file_id: str,
    model: str,
    endpoint: str,
    params: dict | None,
    jsonl: bytes = b"",
    input_path: str | None = None,
    client: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Create (or adopt) a provider batch job idempotently (design §3.7).

    The input is streamed from ``input_path`` (the stored upload file) when
    given, falling back to in-memory ``jsonl`` (used by unit tests); the whole
    upload is never materialized as a single rejoined buffer (§3.7, codex #4).
    """
    idem = idempotency_key or compute_idem(input_file_id, model, endpoint, params)

    won, row = store.claim(
        idem,
        input_file_id=input_file_id,
        model=model,
        endpoint=endpoint,
        backend=backend.name,
        client=client,
    )

    if not won:
        # A concurrent/earlier caller owns this idem.
        status = row["status"]
        if status in (CREATED, RETRIEVING, STAGED) or status in _TERMINAL:
            return to_openai_batch_object(row)
        if status == CREATING:
            if not store.lease_expired(row):
                # In-flight: return the in-progress batch, back off (no new job).
                return to_openai_batch_object(row)
            # Expired lease: only the worker that CAS-reacquires it may
            # reconcile + create; every other reclaimer backs off (§3.7).
            if not store.reacquire_lease(idem):
                return to_openai_batch_object(store.get(idem))
        # CAS winner of the expired-lease reclaim -> reconcile below.

    # We own creation (winner) OR we re-acquired an expired CREATING lease.
    # Reconcile first: a crashed winner may have already created the job(s).
    existing = await backend.list_jobs(idem)
    if existing:
        primary = existing[0]
        for extra in existing[1:]:
            await backend.cancel(extra)  # bound duplicates to <=1 (§3.7)
            _record("batch_duplicate_cancelled", row, status="cancelled")
        store.set_created(idem, job_id=primary)
        out = store.get(idem)
        _record("batch_adopted", out, status="in_progress")
        return to_openai_batch_object(out)

    # No orphan exists -> stream-translate to a temp file + upload + create one.
    provider_path = _translate_input_to_file(
        backend, _iter_input_lines(jsonl, input_path)
    )
    try:
        file_ref = await backend.upload(provider_path, idem)
    finally:
        _safe_unlink(provider_path)
    job_id = await backend.create(model, file_ref, idem)
    store.set_created(idem, job_id=job_id)
    out = store.get(idem)
    _record("batch_created", out, status="in_progress")
    return to_openai_batch_object(out)


def _iter_input_lines(jsonl: bytes, input_path: str | None):
    """Yield raw JSONL lines from the stored file (streamed) or in-memory bytes.

    Reading line by line keeps memory bounded for ~2GB uploads (codex #4).
    """
    if input_path is not None and os.path.exists(input_path):
        with open(input_path, "r", encoding="utf-8") as f:
            for raw in f:
                yield raw
    elif jsonl:
        for raw in jsonl.decode("utf-8").splitlines():
            yield raw


def _translate_input_to_file(backend: BatchBackend, lines) -> str:
    """Stream-translate OpenAI lines to provider lines in a temp file (§3.7).

    Each input line is translated and written one at a time, so peak memory is
    a single line rather than the whole upload (codex #4). Returns the temp
    file path; the caller is responsible for unlinking it after upload.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    try:
        with tmp:
            for raw in lines:
                if not raw.strip():
                    continue
                try:
                    openai_line = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                tmp.write(json.dumps(backend.to_provider_request(openai_line)) + "\n")
        return tmp.name
    except BaseException:
        _safe_unlink(tmp.name)
        raise


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Result staging — idempotent + bounded (§3.6 + §3.7) + §7.3
# ---------------------------------------------------------------------------
async def stage_results(store: BatchStore, backend: BatchBackend, idem: str) -> dict:
    """Fetch + translate + stage results, gated and bounded (design §3.7)."""
    row = store.get(idem)
    if row is None:
        raise KeyError(idem)
    if row["status"] == STAGED:
        return to_openai_batch_object(row)

    if not store.begin_retrieving(idem):
        # Another worker already staged this batch; re-fetch nothing.
        return to_openai_batch_object(store.get(idem))

    row = store.get(idem)
    try:
        native_lines = list(await backend.fetch(row["job_id"]))
    except ResultUnavailableError as exc:
        # §7.3: the provider result file is missing/expired — fail gracefully.
        store.set_failed(idem, error=str(exc), status=FAILED)
        out = store.get(idem)
        _record("batch_result_unavailable", out, status="failed", error=str(exc))
        return to_openai_batch_object(out)

    batch_id = row["batch_id"]
    already = store.staged_keys(batch_id)  # resume diff: only process missing
    staged_count = len(already)
    for native in native_lines:
        openai_line = backend.from_provider_result(native)
        key = openai_line.get("custom_id")
        if key is None:
            continue
        sha = _content_sha(openai_line)
        if key in already:
            if already[key] != sha:
                logger.warning(
                    "batch %s row %s content drift detected (non-determinism)",
                    batch_id,
                    key,
                )
            continue
        store.stage_row(batch_id, key, sha, openai_line)
        staged_count += 1

    output_file_id = f"file-{uuid.uuid4().hex}"
    store.set_staged(idem, output_file_id=output_file_id, row_count=staged_count)
    out = store.get(idem)
    _record("batch_completed", out, status="completed")
    return to_openai_batch_object(out)


# ---------------------------------------------------------------------------
# Retrieve — poll + (maybe) stage (§3.5)
# ---------------------------------------------------------------------------
async def get_batch(
    store: BatchStore, backend: BatchBackend, batch_id: str
) -> dict | None:
    """Return the OpenAI batch object, polling + staging if appropriate."""
    row = store.get_by_batch_id(batch_id)
    if row is None:
        # Allow lookup by idem too.
        row = store.get(batch_id)
    if row is None:
        return None
    idem = row["idem"]

    if row["status"] in _TERMINAL:
        return to_openai_batch_object(row)

    ns = await backend.poll(row["job_id"])
    if ns.status == "completed":
        return await stage_results(store, backend, idem)
    if ns.status in ("failed", "expired", "cancelled"):
        target = CANCELLED if ns.status == "cancelled" else FAILED
        store.set_failed(idem, error=ns.raw or ns.status, status=target)
        out = store.get(idem)
        _record(f"batch_{ns.status}", out, status=ns.status, error=ns.raw)
        return to_openai_batch_object(out, status_override=ns.status)

    return to_openai_batch_object(row, status_override=ns.status)
