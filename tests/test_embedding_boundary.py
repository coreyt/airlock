"""Authenticated ingress-contract tests for embedding request bodies."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute

from airlock.embedding_boundary import (
    _install_authenticated_embedding_validator,
    _is_embedding_path,
)


def _embedding_app() -> tuple[FastAPI, list[str]]:
    """A route shaped like LiteLLM: auth reads/caches body before endpoint."""
    calls: list[str] = []
    app = FastAPI()

    async def authenticated(request: Request) -> None:
        calls.append("auth")
        await request.body()
        if request.headers.get("authorization") != "Bearer valid":
            raise HTTPException(401, "unauthorized")

    @app.post("/v1/embeddings", dependencies=[Depends(authenticated)])
    async def embeddings(request: Request):
        calls.append("endpoint")
        return await request.json()

    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/v1/embeddings"
    )
    _install_authenticated_embedding_validator(route)
    return app, calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("api_base", "https://unreviewed.example"),
        ("api_key", "unreviewed-key"),
        ("custom_llm_provider", "openai"),
        ("deployment_id", "unreviewed-deployment"),
        ("headers", {"X-Airlock-Client": "forged"}),
        ("extra_headers", {"Authorization": "Bearer forged"}),
        ("bogus_embedding_option", 1),
    ],
)
async def test_authenticated_embedding_body_rejects_unreviewed_controls(field, value):
    app, calls = _embedding_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/embeddings",
            headers={"Authorization": "Bearer valid"},
            json={"model": "text-embedding-3-small", "input": "safe", field: value},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_embedding_option"
    assert response.json()["error"]["param"] == field
    assert calls == ["auth"]


@pytest.mark.asyncio
async def test_unauthenticated_invalid_embedding_body_is_rejected_before_validation():
    app, calls = _embedding_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/embeddings",
            json={
                "model": "text-embedding-3-small",
                "input": "safe",
                "api_base": "https://attacker.example",
            },
        )

    assert response.status_code == 401
    assert calls == ["auth"]


@pytest.mark.parametrize(
    "path",
    [
        "/v1/embeddings",
        "/embeddings",
        "/engines/text-embedding-3-small/embeddings",
        "/openai/deployments/text-embedding-3-small/embeddings",
    ],
)
def test_every_litellm_embedding_route_is_selected_for_authenticated_validation(path):
    assert _is_embedding_path(path)


@pytest.mark.asyncio
async def test_authenticated_embedding_body_preserves_only_documented_options_for_dispatch():
    app, calls = _embedding_app()
    body = {
        "model": "text-embedding-3-small",
        "input": ["one", "two"],
        "dimensions": 512,
        "encoding_format": "base64",
        "user": "benchmark-run",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/embeddings", headers={"Authorization": "Bearer valid"}, json=body
        )

    assert response.status_code == 200
    assert response.json() == body
    assert json.loads(response.content) == body
    assert calls == ["auth", "endpoint"]
