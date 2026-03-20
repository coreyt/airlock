"""Airlock sidecar — thin FastAPI reverse proxy in front of LiteLLM.

Intercepts GET /v1/models to return an augmented catalog:
  - Alias names from config (e.g. "claude-sonnet")
  - Provider-pinned IDs from config (e.g. "anthropic/claude-sonnet-4-20250514")
  - Live models discovered from each provider's own /v1/models API at startup

All other requests are streamed through to the internal LiteLLM process unchanged,
preserving SSE, auth headers, and chunked responses.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from airlock.models_catalog import _load_config, build_catalog_from_config

logger = logging.getLogger("airlock.sidecar")

# Headers that must be dropped when forwarding to the upstream or back to the client
_DROP_REQUEST_HEADERS = frozenset({"host", "content-length", "transfer-encoding"})
_DROP_RESPONSE_HEADERS = frozenset({"transfer-encoding", "content-encoding", "content-length"})

_ALL_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


def make_app(
    internal_port: int,
    config_path: str | Path | None = None,
    *,
    live_fetch: bool = True,
    live_timeout: float = 10.0,
    _http_client: httpx.AsyncClient | None = None,  # injectable for tests
) -> FastAPI:
    """Create the Airlock sidecar FastAPI application.

    Args:
        internal_port: Port that the internal LiteLLM proxy is listening on (127.0.0.1).
        config_path: Path to config.yaml; defaults to AIRLOCK_CONFIG or ./config.yaml.
        live_fetch: If True, query provider APIs at startup to discover live models.
        live_timeout: Per-provider HTTP timeout for live model discovery.
        _http_client: Inject a custom httpx.AsyncClient (for tests).
    """
    _base_url = f"http://127.0.0.1:{internal_port}"

    def _resolved_config_path() -> Path | None:
        if config_path is not None:
            return Path(config_path)
        env = os.getenv("AIRLOCK_CONFIG")
        if env:
            return Path(env)
        p = Path("config.yaml")
        return p if p.is_file() else None

    # ---------------------------------------------------------------------------
    # App-level state
    # ---------------------------------------------------------------------------

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Build the static catalog (config + optional live provider queries).
        # Live queries are blocking/threaded; run before the event loop is busy.
        cfg_path = _resolved_config_path()
        config = _load_config(cfg_path)

        if live_fetch:
            import asyncio
            from airlock.models_catalog import fetch_live_provider_models
            loop = asyncio.get_event_loop()
            live_models = await loop.run_in_executor(
                None,
                fetch_live_provider_models,
                config,
                live_timeout,
            )
        else:
            live_models = []

        base_catalog = build_catalog_from_config(config=config)
        base_ids = {m["id"] for m in base_catalog}
        extras = [m for m in live_models if m["id"] not in base_ids]
        app.state.catalog = base_catalog + extras

        # Create the shared async HTTP client for upstream calls
        if _http_client is not None:
            app.state.client = _http_client
            _owns_client = False
        else:
            app.state.client = httpx.AsyncClient(
                base_url=_base_url,
                timeout=httpx.Timeout(connect=10.0, read=None, write=None, pool=None),
                follow_redirects=False,
            )
            _owns_client = True

        logger.info(
            "sidecar: catalog ready — %d entries (%d config, %d live)",
            len(app.state.catalog), len(base_catalog), len(extras),
        )

        yield  # ---------- serve requests ----------

        if _owns_client:
            await app.state.client.aclose()

    # ---------------------------------------------------------------------------
    # FastAPI app
    # ---------------------------------------------------------------------------

    app = FastAPI(title="Airlock", docs_url=None, redoc_url=None, lifespan=lifespan)

    # ---------------------------------------------------------------------------
    # GET /v1/models — augmented catalog
    # ---------------------------------------------------------------------------

    @app.get("/v1/models")
    async def list_models(request: Request) -> Response:
        """Return alias + provider-pinned + live-discovered model IDs.

        Forwards the request to LiteLLM first to enforce auth, then merges
        the catalog on top of LiteLLM's own model list.
        """
        client: httpx.AsyncClient = request.app.state.client
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}

        try:
            upstream = await client.get("/v1/models", headers=headers)
        except httpx.ConnectError:
            logger.warning("sidecar: LiteLLM not reachable for /v1/models — returning 503")
            return JSONResponse(
                {"error": {"message": "proxy not ready", "type": "proxy_error", "code": 503}},
                status_code=503,
            )

        if upstream.status_code != 200:
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers={k: v for k, v in upstream.headers.items() if k.lower() not in _DROP_RESPONSE_HEADERS},
                media_type=upstream.headers.get("content-type"),
            )

        try:
            data = upstream.json()
        except Exception:
            # Unexpected body — forward as-is
            return Response(content=upstream.content, status_code=200)

        existing_ids: set[str] = {m["id"] for m in data.get("data", []) if "id" in m}
        extras = [m for m in request.app.state.catalog if m["id"] not in existing_ids]
        data.setdefault("data", []).extend(extras)

        return JSONResponse(data)

    # ---------------------------------------------------------------------------
    # Everything else — transparent stream proxy
    # ---------------------------------------------------------------------------

    @app.api_route("/{path:path}", methods=_ALL_METHODS)
    async def proxy_to_litellm(request: Request, path: str) -> Response:
        """Stream all other requests to the internal LiteLLM unchanged."""
        client: httpx.AsyncClient = request.app.state.client
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST_HEADERS}
        body = await request.body()

        try:
            req = client.build_request(
                method=request.method,
                url=f"/{path}",
                headers=headers,
                content=body,
                params=dict(request.query_params),
            )
            upstream = await client.send(req, stream=True)
        except httpx.ConnectError as exc:
            logger.warning("sidecar: LiteLLM not reachable: %s", exc)
            return JSONResponse(
                {"error": {"message": "proxy not ready", "type": "proxy_error", "code": 503}},
                status_code=503,
            )

        async def _stream() -> AsyncIterator[bytes]:
            async for chunk in upstream.aiter_bytes(chunk_size=4096):
                yield chunk
            await upstream.aclose()

        response_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in _DROP_RESPONSE_HEADERS
        }
        return StreamingResponse(
            _stream(),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )

    return app
