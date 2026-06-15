"""BatchBackend protocol + shared types (design §3.1).

The ``BatchBackend`` protocol is the entire provider-specific surface of the
Airlock Batch Gateway. Everything else (HTTP surface, OpenAI batch object
shaping, state store, idempotency, status normalization) is provider-agnostic
and lives in the gateway core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable


@dataclass(frozen=True)
class NormalizedStatus:
    """A provider job status normalized to the OpenAI batch vocabulary.

    ``status`` is one of the OpenAI batch statuses (``validating``,
    ``in_progress``, ``finalizing``, ``completed``, ``failed``, ``expired``,
    ``cancelling``, ``cancelled``). ``raw`` keeps the provider-native enum for
    observability / debugging.
    """

    status: str
    raw: str | None = None


class ResultUnavailableError(Exception):
    """The provider result file is missing or expired (design §7.3).

    The provider result file has its own retention window that can lapse
    independently of the job's 48h expiry, so staging must handle a fetch that
    cannot retrieve the result gracefully rather than crashing.
    """


@runtime_checkable
class BatchBackend(Protocol):
    """The provider-specific adapter surface (design §3.1)."""

    name: str

    def to_provider_request(self, openai_line: dict) -> dict:
        """Translate one OpenAI JSONL request line to the provider's shape."""
        ...

    def from_provider_result(self, native_line: dict) -> dict:
        """Translate one native result line to an OpenAI output line.

        The provider-native response is preserved verbatim in ``response.body``
        (A4); a best-effort OpenAI ``choices`` projection is added alongside.
        """
        ...

    async def upload(self, src: str, display_name: str) -> str:
        """Upload the (already translated) JSONL file; return a provider file ref.

        ``src`` is a path to the translated JSONL on disk; implementations stream
        it rather than buffering the whole upload in memory (design §3.7)."""
        ...

    async def create(self, model: str, file_ref: str, display_name: str) -> str:
        """Create a provider batch job; return the provider job id."""
        ...

    async def poll(self, job_id: str) -> NormalizedStatus:
        """Poll a job and return its normalized status."""
        ...

    async def fetch(self, job_id: str) -> Iterable[dict]:
        """Fetch native result lines. Raise ``ResultUnavailableError`` if the
        result file is missing/expired (§7.3)."""
        ...

    async def cancel(self, job_id: str) -> None:
        """Cancel a provider job."""
        ...

    async def list_jobs(self, display_name: str) -> list[str]:
        """List provider job ids whose ``display_name`` matches (reconcile)."""
        ...
