"""Tests for airlock/sidecar.py

Uses starlette.testclient.TestClient (sync) which correctly triggers the
FastAPI lifespan context so app.state is populated before any request.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from starlette.testclient import TestClient

from airlock.sidecar import make_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LITELLM_MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {"id": "claude-sonnet", "object": "model", "created": 1704067200, "owned_by": "airlock"},
        {"id": "gpt-4o", "object": "model", "created": 1704067200, "owned_by": "airlock"},
    ],
}


def _make_mock_client(models_body: dict | None = None, status: int = 200) -> MagicMock:
    """Return a mock httpx.AsyncClient whose /v1/models returns a canned response."""
    client = MagicMock(spec=httpx.AsyncClient)

    body_bytes = json.dumps(models_body or _LITELLM_MODELS_RESPONSE).encode()
    models_resp = MagicMock()
    models_resp.status_code = status
    models_resp.content = body_bytes
    models_resp.headers = httpx.Headers({"content-type": "application/json"})
    models_resp.json = lambda: json.loads(body_bytes)
    client.get = AsyncMock(return_value=models_resp)

    # Stream proxy response
    stream_resp = MagicMock()
    stream_resp.status_code = 200
    stream_resp.headers = httpx.Headers({"content-type": "application/json"})

    async def _aiter_bytes(chunk_size=4096):
        yield b'{"ok": true}'

    stream_resp.aiter_bytes = _aiter_bytes
    stream_resp.aclose = AsyncMock()
    client.build_request = MagicMock(return_value=MagicMock())
    client.send = AsyncMock(return_value=stream_resp)
    client.aclose = AsyncMock()

    return client


@pytest.fixture
def config_yaml(tmp_path) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(
        "model_list:\n"
        "  - model_name: claude-sonnet\n"
        "    litellm_params:\n"
        "      model: anthropic/claude-sonnet-4-20250514\n"
        "  - model_name: gpt-4o\n"
        "    litellm_params:\n"
        "      model: openai/gpt-4o\n"
    )
    return str(p)


@pytest.fixture
def client_and_mock(config_yaml):
    """TestClient + the injected mock httpx client."""
    mock = _make_mock_client()
    app = make_app(
        internal_port=9999,
        config_path=config_yaml,
        live_fetch=False,
        _http_client=mock,
    )
    with TestClient(app, raise_server_exceptions=True) as tc:
        yield tc, mock


# ---------------------------------------------------------------------------
# /v1/models — catalog augmentation
# ---------------------------------------------------------------------------

class TestModelsEndpoint:
    def test_returns_200(self, client_and_mock):
        tc, _ = client_and_mock
        resp = tc.get("/v1/models")
        assert resp.status_code == 200

    def test_alias_names_present(self, client_and_mock):
        tc, _ = client_and_mock
        ids = [m["id"] for m in tc.get("/v1/models").json()["data"]]
        assert "claude-sonnet" in ids
        assert "gpt-4o" in ids

    def test_provider_pinned_names_added(self, client_and_mock):
        """Provider-pinned IDs from config should be merged into the response."""
        tc, _ = client_and_mock
        ids = [m["id"] for m in tc.get("/v1/models").json()["data"]]
        assert "anthropic/claude-sonnet-4-20250514" in ids
        assert "openai/gpt-4o" in ids

    def test_no_duplicate_ids(self, client_and_mock):
        tc, _ = client_and_mock
        ids = [m["id"] for m in tc.get("/v1/models").json()["data"]]
        assert len(ids) == len(set(ids))

    def test_auth_header_forwarded(self, client_and_mock):
        tc, mock = client_and_mock
        tc.get("/v1/models", headers={"Authorization": "Bearer sk-test"})
        forwarded = mock.get.call_args[1]["headers"]
        # Header keys may be lowercased by httpx
        assert forwarded.get("authorization") == "Bearer sk-test"

    def test_propagates_litellm_401(self, config_yaml):
        mock = _make_mock_client(status=401)
        mock.get.return_value.content = b'{"error": "unauthorized"}'
        app = make_app(
            internal_port=9999, config_path=config_yaml,
            live_fetch=False, _http_client=mock,
        )
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/v1/models", headers={"Authorization": "Bearer bad"})
        assert resp.status_code == 401

    def test_returns_503_when_litellm_unreachable(self, config_yaml):
        mock = MagicMock(spec=httpx.AsyncClient)
        mock.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock.aclose = AsyncMock()
        app = make_app(
            internal_port=9999, config_path=config_yaml,
            live_fetch=False, _http_client=mock,
        )
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/v1/models")
        assert resp.status_code == 503

    def test_response_has_object_list(self, client_and_mock):
        tc, _ = client_and_mock
        assert tc.get("/v1/models").json().get("object") == "list"

    def test_litellm_models_preserved(self, client_and_mock):
        """Models already in LiteLLM's list must remain in the merged response."""
        tc, _ = client_and_mock
        ids = [m["id"] for m in tc.get("/v1/models").json()["data"]]
        # These come from the mocked LiteLLM response directly
        assert "claude-sonnet" in ids
        assert "gpt-4o" in ids


# ---------------------------------------------------------------------------
# Pass-through proxy
# ---------------------------------------------------------------------------

class TestProxy:
    def test_post_route_proxied(self, client_and_mock):
        tc, mock = client_and_mock
        tc.post(
            "/v1/chat/completions",
            json={"model": "claude-sonnet", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert mock.send.called

    def test_get_health_proxied(self, client_and_mock):
        tc, mock = client_and_mock
        tc.get("/health")
        assert mock.send.called

    def test_proxy_returns_503_when_litellm_unreachable(self, config_yaml):
        mock = MagicMock(spec=httpx.AsyncClient)
        mock.get = AsyncMock(return_value=MagicMock(
            status_code=200,
            content=json.dumps(_LITELLM_MODELS_RESPONSE).encode(),
            headers=httpx.Headers({"content-type": "application/json"}),
            json=lambda: _LITELLM_MODELS_RESPONSE,
        ))
        mock.build_request = MagicMock(return_value=MagicMock())
        mock.send = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock.aclose = AsyncMock()
        app = make_app(
            internal_port=9999, config_path=config_yaml,
            live_fetch=False, _http_client=mock,
        )
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.post("/v1/chat/completions", json={"model": "x"})
        assert resp.status_code == 503
