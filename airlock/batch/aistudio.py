"""AIStudioBackend — the AI Studio (Gemini) batch adapter.

The ``google-genai`` SDK is imported **lazily inside the handler** so the proxy
boots without the ``aistudio`` extra. Translation (OpenAI JSONL line <-> Gemini
``{key, request:{contents,…}}`` / ``candidates``) is pure and never touches the
SDK, so it is fully unit-testable with no network and no extra installed.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from airlock.batch.backend import NormalizedStatus, ResultUnavailableError

# Gemini job-state enum -> OpenAI batch status (design §3.5, §2 status table).
_AISTUDIO_STATUS_MAP = {
    "JOB_STATE_UNSPECIFIED": "validating",
    "JOB_STATE_PENDING": "validating",
    "JOB_STATE_QUEUED": "in_progress",
    "JOB_STATE_RUNNING": "in_progress",
    "JOB_STATE_PAUSED": "in_progress",
    "JOB_STATE_SUCCEEDED": "completed",
    "JOB_STATE_FAILED": "failed",
    "JOB_STATE_CANCELLING": "cancelling",
    "JOB_STATE_CANCELLED": "cancelled",
    "JOB_STATE_EXPIRED": "expired",
}


def normalize_aistudio_status(raw: str | None) -> str:
    """Map a Gemini ``JOB_STATE_*`` to an OpenAI batch status.

    Unknown / in-flight-looking states default to ``in_progress`` so a poller
    never crashes on an enum value a future SDK adds.
    """
    return _AISTUDIO_STATUS_MAP.get(raw or "", "in_progress")


# ---------------------------------------------------------------------------
# Translation: OpenAI line -> Gemini request (design §3.1, A7)
# ---------------------------------------------------------------------------
_ROLE_MAP = {"user": "user", "assistant": "model", "tool": "user"}


def _content_to_parts(content: Any) -> list[dict]:
    """Normalize an OpenAI ``message.content`` to Gemini ``parts``."""
    if isinstance(content, str):
        return [{"text": content}]
    if isinstance(content, list):
        parts: list[dict] = []
        for chunk in content:
            if isinstance(chunk, dict):
                text = chunk.get("text")
                if text is not None:
                    parts.append({"text": text})
            elif isinstance(chunk, str):
                parts.append({"text": chunk})
        return parts
    if content is None:
        return []
    return [{"text": str(content)}]


def openai_line_to_gemini(openai_line: dict) -> dict:
    """Translate one OpenAI batch request line to a Gemini batch request line.

    OpenAI: ``{custom_id, method, url, body:{model, messages, …}}``
    Gemini: ``{key, request:{contents, system_instruction?, generationConfig?}}``
    """
    key = openai_line.get("custom_id") or openai_line.get("key")
    body = openai_line.get("body") or {}
    messages = body.get("messages") or []

    contents: list[dict] = []
    system_parts: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        parts = _content_to_parts(msg.get("content"))
        if role == "system":
            system_parts.extend(parts)
            continue
        contents.append({"role": _ROLE_MAP.get(role, "user"), "parts": parts})

    request: dict[str, Any] = {"contents": contents}
    if system_parts:
        request["system_instruction"] = {"parts": system_parts}

    gen_config: dict[str, Any] = {}
    if "temperature" in body:
        gen_config["temperature"] = body["temperature"]
    if "top_p" in body:
        gen_config["topP"] = body["top_p"]
    if "max_tokens" in body and body["max_tokens"] is not None:
        gen_config["maxOutputTokens"] = body["max_tokens"]
    if gen_config:
        request["generationConfig"] = gen_config

    return {"key": key, "request": request}


# ---------------------------------------------------------------------------
# Translation: Gemini result -> OpenAI output line (design §3.1, A4)
# ---------------------------------------------------------------------------
def _candidates_to_choices(gemini_response: dict) -> list[dict]:
    """Best-effort projection of Gemini ``candidates`` to OpenAI ``choices``."""
    choices: list[dict] = []
    for idx, cand in enumerate(gemini_response.get("candidates") or []):
        if not isinstance(cand, dict):
            continue
        parts = ((cand.get("content") or {}).get("parts")) or []
        text = "".join(
            p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")
        )
        choices.append(
            {
                "index": idx,
                "message": {"role": "assistant", "content": text},
                "finish_reason": _map_finish_reason(cand.get("finishReason")),
            }
        )
    return choices


def _map_finish_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
    }.get(reason, reason.lower())


def gemini_result_to_openai(native_line: dict) -> dict:
    """Translate one native Gemini result line to an OpenAI output line.

    The native Gemini response is preserved verbatim in ``response.body`` (A4);
    a best-effort ``choices`` projection is added alongside so callers never
    lose tool calls / thinking blocks / safety reasons.
    """
    key = native_line.get("key") or native_line.get("custom_id")
    error = native_line.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        code = error.get("code") if isinstance(error, dict) else None
        return {
            "id": f"batch_req_{key}",
            "custom_id": key,
            "response": None,
            "error": {"code": code, "message": message},
        }

    gemini_response = native_line.get("response") or {}
    body = dict(gemini_response)  # native preserved verbatim
    body["choices"] = _candidates_to_choices(gemini_response)

    return {
        "id": f"batch_req_{key}",
        "custom_id": key,
        "response": {
            "status_code": 200,
            "request_id": key,
            "body": body,
        },
        "error": None,
    }


# ---------------------------------------------------------------------------
# Backend (lazy SDK)
# ---------------------------------------------------------------------------
_INSTALL_HINT = (
    "AI Studio batch requires the 'aistudio' extra "
    "(install with: uv sync --extra aistudio)."
)


class AIStudioBackend:
    """``BatchBackend`` for AI Studio Gemini batch (design §3.1)."""

    name = "aistudio"

    def __init__(
        self, *, api_key: str | None = None, provider_model: str | None = None
    ):
        self.api_key = api_key or os.getenv("GOOGLE_AISTUDIO_API_KEY")
        self.provider_model = provider_model
        self._client_obj: Any = None

    # translation (pure; no SDK) ----------------------------------------
    def to_provider_request(self, openai_line: dict) -> dict:
        return openai_line_to_gemini(openai_line)

    def from_provider_result(self, native_line: dict) -> dict:
        return gemini_result_to_openai(native_line)

    # lazy SDK ----------------------------------------------------------
    def _import_genai(self):
        from google import genai  # noqa: PLC0415  (lazy: optional 'aistudio' extra)

        return genai

    def _client(self):
        if self._client_obj is not None:
            return self._client_obj
        try:
            genai = self._import_genai()
        except ImportError as exc:
            raise RuntimeError(_INSTALL_HINT) from exc
        if not self.api_key:
            raise RuntimeError(
                "GOOGLE_AISTUDIO_API_KEY is required for AI Studio batch."
            )
        self._client_obj = genai.Client(api_key=self.api_key)
        return self._client_obj

    # provider ops (network; lazy) --------------------------------------
    async def upload(self, jsonl: bytes, display_name: str) -> str:
        client = self._client()
        import io  # noqa: PLC0415

        uploaded = client.files.upload(
            file=io.BytesIO(jsonl),
            config={"display_name": display_name, "mime_type": "application/jsonl"},
        )
        return getattr(uploaded, "name", str(uploaded))

    async def create(self, model: str, file_ref: str, display_name: str) -> str:
        client = self._client()
        job = client.batches.create(
            model=self.provider_model or model,
            src=file_ref,
            config={"display_name": display_name},
        )
        return getattr(job, "name", str(job))

    async def poll(self, job_id: str) -> NormalizedStatus:
        client = self._client()
        job = client.batches.get(name=job_id)
        raw = getattr(getattr(job, "state", None), "name", None) or str(
            getattr(job, "state", "")
        )
        return NormalizedStatus(status=normalize_aistudio_status(raw), raw=raw)

    async def fetch(self, job_id: str) -> Iterable[dict]:
        import json  # noqa: PLC0415

        client = self._client()
        job = client.batches.get(name=job_id)
        dest = getattr(job, "dest", None)
        result_file = getattr(dest, "file_name", None) if dest else None
        if not result_file:
            raise ResultUnavailableError(
                f"batch {job_id} has no result file (missing/expired)"
            )
        try:
            raw = client.files.download(file=result_file)
        except Exception as exc:  # noqa: BLE001  result file may be expired (§7.3)
            raise ResultUnavailableError(
                f"result file for {job_id} unavailable: {exc}"
            ) from exc
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    async def cancel(self, job_id: str) -> None:
        client = self._client()
        client.batches.cancel(name=job_id)

    async def list_jobs(self, display_name: str) -> list[str]:
        client = self._client()
        matches: list[str] = []
        for job in client.batches.list():
            if getattr(job, "display_name", None) == display_name:
                matches.append(getattr(job, "name", str(job)))
        return matches
