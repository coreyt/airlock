"""Default :class:`~airlock.guardrails.providers.base.Transport` built on httpx.

Kept separate from any provider so adapters depend on the seam rather than on
httpx, and so tests can substitute a fake transport with no network access.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

logger = logging.getLogger("airlock.guardrails.providers.http")


class HttpxTransport:
    """Shared async HTTP client with lazy construction.

    The client is created on first use so that merely importing or configuring
    a provider never opens sockets — construction happens inside a running
    event loop, on the request path.
    """

    def __init__(self) -> None:
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient()
        return self._client

    @staticmethod
    def _decode(response: Any) -> dict[str, Any]:
        """Decode a JSON body, treating anything unparseable as empty.

        A provider must not crash on a proxy error page or truncated body; it
        reports an unavailable verdict instead, which an empty dict triggers.
        """
        try:
            payload = response.json()
        except (ValueError, _json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        client = self._ensure_client()
        response = await client.post(url, json=json, headers=headers, timeout=timeout)
        return response.status_code, self._decode(response)

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        client = self._ensure_client()
        response = await client.get(url, headers=headers, timeout=timeout)
        return response.status_code, self._decode(response)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
