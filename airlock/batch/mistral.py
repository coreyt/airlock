"""MistralBackend — the Mistral batch adapter.

The ``mistralai`` SDK is imported **lazily inside the handler** so the proxy
boots without the ``mistral`` extra. Translation is near-passthrough: Mistral's
batch input (``{custom_id, body}``) is OpenAI-shaped and Mistral chat is
OpenAI-compatible, so the result body is already OpenAI-shaped. Translation is
pure and never touches the SDK, so it is fully unit-testable with no network and
no extra installed.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from airlock.batch.backend import NormalizedStatus, ResultUnavailableError

# Mistral job status -> OpenAI batch status (findings doc §1 status table).
_MISTRAL_STATUS_MAP = {
    "QUEUED": "validating",
    "RUNNING": "in_progress",
    "SUCCESS": "completed",
    "FAILED": "failed",
    "TIMEOUT_EXCEEDED": "expired",
    "CANCELLATION_REQUESTED": "cancelling",
    "CANCELLED": "cancelled",
}


def normalize_mistral_status(raw: str | None) -> str:
    """Map a Mistral job status to an OpenAI batch status.

    Unknown / in-flight-looking states default to ``in_progress`` so a poller
    never crashes on a status value a future SDK adds.
    """
    return _MISTRAL_STATUS_MAP.get(raw or "", "in_progress")


# ---------------------------------------------------------------------------
# Translation: OpenAI line -> Mistral request (near-passthrough)
# ---------------------------------------------------------------------------
def openai_line_to_mistral(openai_line: dict) -> dict:
    """Translate one OpenAI batch request line to a Mistral batch request line.

    Both are ``{custom_id, body:{...}}`` (OpenAI-shaped), so this just normalizes
    the envelope (``custom_id`` + ``body``) and drops OpenAI-only routing fields
    (``method``/``url``) that Mistral does not expect.
    """
    custom_id = openai_line.get("custom_id") or openai_line.get("key")
    body = openai_line.get("body") or {}
    return {"custom_id": custom_id, "body": body}


# ---------------------------------------------------------------------------
# Translation: Mistral result -> OpenAI output line (A4)
# ---------------------------------------------------------------------------
def mistral_result_to_openai(native_line: dict) -> dict:
    """Translate one native Mistral result line to an OpenAI output line.

    The native Mistral response body is preserved verbatim in ``response.body``
    (A4). Because the body is already OpenAI-shaped, the ``choices`` projection
    is light — existing choices are kept as-is. The per-line ``error`` case is
    handled like the AI Studio adapter.
    """
    custom_id = native_line.get("custom_id") or native_line.get("key")
    error = native_line.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        code = error.get("code") if isinstance(error, dict) else None
        return {
            "id": f"batch_req_{custom_id}",
            "custom_id": custom_id,
            "response": None,
            "error": {"code": code, "message": message},
        }

    response = native_line.get("response") or {}
    if isinstance(response, dict) and "body" in response:
        status_code = response.get("status_code", 200)
        body = dict(response.get("body") or {})
    else:
        status_code = 200
        body = dict(response)
    # Body is already OpenAI-shaped; ensure a choices key exists (preserve any).
    body.setdefault("choices", body.get("choices") or [])

    return {
        "id": f"batch_req_{custom_id}",
        "custom_id": custom_id,
        "response": {
            "status_code": status_code,
            "request_id": custom_id,
            "body": body,
        },
        "error": None,
    }


# ---------------------------------------------------------------------------
# Backend (lazy SDK)
# ---------------------------------------------------------------------------
_INSTALL_HINT = (
    "Mistral batch requires the 'mistral' extra "
    "(install with: uv sync --extra mistral)."
)


def _download_to_text(raw: Any) -> str:
    """Normalize a ``files.download`` return (bytes / stream / str) to text."""
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8")
    read = getattr(raw, "read", None)
    if callable(read):
        data = read()
        if isinstance(data, (bytes, bytearray)):
            return data.decode("utf-8")
        return str(data)
    return str(raw)


class MistralBackend:
    """``BatchBackend`` for Mistral batch (findings doc §1)."""

    name = "mistral"

    def __init__(
        self, *, api_key: str | None = None, provider_model: str | None = None
    ):
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY")
        self.provider_model = provider_model
        self._client_obj: Any = None

    # translation (pure; no SDK) ----------------------------------------
    def to_provider_request(self, openai_line: dict) -> dict:
        return openai_line_to_mistral(openai_line)

    def from_provider_result(self, native_line: dict) -> dict:
        return mistral_result_to_openai(native_line)

    # lazy SDK ----------------------------------------------------------
    def _import_mistral(self):
        from mistralai import Mistral  # noqa: PLC0415  (lazy: optional 'mistral' extra)

        return Mistral

    def _client(self):
        if self._client_obj is not None:
            return self._client_obj
        try:
            Mistral = self._import_mistral()
        except ImportError as exc:
            raise RuntimeError(_INSTALL_HINT) from exc
        if not self.api_key:
            raise RuntimeError("MISTRAL_API_KEY is required for Mistral batch.")
        self._client_obj = Mistral(api_key=self.api_key)
        return self._client_obj

    # provider ops (network; lazy) --------------------------------------
    async def upload(self, src: str, display_name: str) -> str:
        client = self._client()
        # ``src`` is a file path; stream it from disk so a large upload is never
        # rejoined in memory (codex #4).
        with open(src, "rb") as fh:
            uploaded = client.files.upload(
                purpose="batch",
                file={"file_name": display_name, "content": fh},
            )
        return getattr(uploaded, "id", str(uploaded))

    async def create(self, model: str, file_ref: str, display_name: str) -> str:
        client = self._client()
        # Mistral has no native display_name; key it via metadata so reconcile-
        # by-idem (§3.7) can match jobs back to an idempotency key.
        job = client.batch.jobs.create(
            input_files=[file_ref],
            model=self.provider_model or model,
            endpoint="/v1/chat/completions",
            metadata={"display_name": display_name},
        )
        return getattr(job, "id", str(job))

    async def poll(self, job_id: str) -> NormalizedStatus:
        client = self._client()
        job = client.batch.jobs.get(job_id=job_id)
        raw = getattr(job, "status", None)
        raw = str(raw) if raw is not None else None
        return NormalizedStatus(status=normalize_mistral_status(raw), raw=raw)

    async def fetch(self, job_id: str) -> Iterable[dict]:
        import json  # noqa: PLC0415

        client = self._client()
        job = client.batch.jobs.get(job_id=job_id)
        output_file = getattr(job, "output_file", None)
        if not output_file:
            raise ResultUnavailableError(
                f"batch {job_id} has no output file (missing/expired)"
            )
        try:
            raw = client.files.download(file_id=output_file)
        except Exception as exc:  # noqa: BLE001  result file may be expired (§7.3)
            raise ResultUnavailableError(
                f"output file for {job_id} unavailable: {exc}"
            ) from exc
        text = _download_to_text(raw)
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    async def cancel(self, job_id: str) -> None:
        client = self._client()
        client.batch.jobs.cancel(job_id=job_id)

    async def list_jobs(self, display_name: str) -> list[str]:
        client = self._client()
        listing = client.batch.jobs.list()
        jobs = getattr(listing, "data", None)
        if jobs is None:
            jobs = listing
        matches: list[str] = []
        for job in jobs:
            meta = getattr(job, "metadata", None) or {}
            if isinstance(meta, dict) and meta.get("display_name") == display_name:
                matches.append(getattr(job, "id", str(job)))
        return matches
