"""Tests for the additive /v1/models capability seam (0.5.2-CAP-v1models).

NO-NETWORK: the ASGI middleware is driven in-process via httpx.ASGITransport
against tiny stub ASGI apps. The proxy is never started and no provider is hit.
"""

from __future__ import annotations

import json

import httpx
import pytest

from airlock.models_seam import (
    ModelsCapabilityMiddleware,
    _build_capability_map,
    install_models_capability_seam_on_proxy_app,
)

# --- canned data ------------------------------------------------------------

_MODELS_BODY = {
    "object": "list",
    "data": [
        {
            "id": "aistudio/gemini-3.5-flash",
            "object": "model",
            "created": 1234567890,
            "owned_by": "google",
        },
        {
            "id": "claude-haiku",
            "object": "model",
            "created": 1234567891,
            "owned_by": "anthropic",
        },
        {
            "id": "unknown/model-not-in-map",
            "object": "model",
            "created": 1234567892,
            "owned_by": "system",
        },
    ],
}

_CAP_MAP = {
    "aistudio/gemini-3.5-flash": {
        "airlock_provider": "gemini",
        "endpoints": ["chat", "batch"],
        "underlying": "gemini/gemini-3.5-flash",
        "region": None,
        "deprecated": False,
    },
    "claude-haiku": {
        "airlock_provider": "anthropic",
        "endpoints": ["chat"],
        "underlying": "anthropic/claude-haiku",
        "region": None,
        "deprecated": False,
    },
}


# --- stub ASGI apps ---------------------------------------------------------


def _make_json_app(body: dict, *, status: int = 200, path_ok=("/v1/models", "/models")):
    """Stub ASGI app: returns `body` as JSON on the models paths, else 404 text."""

    async def app(scope, receive, send):
        raw = json.dumps(body).encode()
        if scope["path"] in path_ok:
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(raw)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": raw})
        else:
            payload = b"not found"
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"content-length", str(len(payload)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})

    return app


def _make_raw_app(payload: bytes, content_type: bytes, *, status: int = 200):
    """Stub ASGI app: returns arbitrary bytes on every path."""

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", content_type),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    return app


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


# --- augmentation -----------------------------------------------------------


@pytest.mark.asyncio
async def test_v1_models_augmented_with_airlock_object():
    app = ModelsCapabilityMiddleware(_make_json_app(_MODELS_BODY), _CAP_MAP)
    async with _client(app) as c:
        resp = await c.get("/v1/models")
    assert resp.status_code == 200
    out = resp.json()
    by_id = {item["id"]: item for item in out["data"]}

    assert (
        by_id["aistudio/gemini-3.5-flash"]["airlock"]
        == _CAP_MAP["aistudio/gemini-3.5-flash"]
    )
    assert by_id["claude-haiku"]["airlock"] == _CAP_MAP["claude-haiku"]


@pytest.mark.asyncio
async def test_standard_fields_intact_and_unchanged():
    app = ModelsCapabilityMiddleware(_make_json_app(_MODELS_BODY), _CAP_MAP)
    async with _client(app) as c:
        resp = await c.get("/v1/models")
    out = resp.json()
    assert out["object"] == "list"
    item = next(i for i in out["data"] if i["id"] == "aistudio/gemini-3.5-flash")
    assert item["id"] == "aistudio/gemini-3.5-flash"
    assert item["object"] == "model"
    assert item["created"] == 1234567890
    assert item["owned_by"] == "google"


@pytest.mark.asyncio
async def test_unknown_id_left_unchanged():
    app = ModelsCapabilityMiddleware(_make_json_app(_MODELS_BODY), _CAP_MAP)
    async with _client(app) as c:
        resp = await c.get("/v1/models")
    out = resp.json()
    unknown = next(i for i in out["data"] if i["id"] == "unknown/model-not-in-map")
    assert "airlock" not in unknown


@pytest.mark.asyncio
async def test_content_length_matches_new_body():
    app = ModelsCapabilityMiddleware(_make_json_app(_MODELS_BODY), _CAP_MAP)
    async with _client(app) as c:
        resp = await c.get("/v1/models")
    assert int(resp.headers["content-length"]) == len(resp.content)
    # the augmented body is larger than the canned original
    assert len(resp.content) > len(json.dumps(_MODELS_BODY).encode())


@pytest.mark.asyncio
async def test_models_alias_path_augmented():
    app = ModelsCapabilityMiddleware(_make_json_app(_MODELS_BODY), _CAP_MAP)
    async with _client(app) as c:
        resp = await c.get("/models")
    out = resp.json()
    item = next(i for i in out["data"] if i["id"] == "claude-haiku")
    assert item["airlock"] == _CAP_MAP["claude-haiku"]


# --- pass-through (byte-for-byte) -------------------------------------------


@pytest.mark.asyncio
async def test_other_path_passthrough_byte_for_byte():
    raw = b'{"choices": [{"message": {"content": "hi"}}]}'
    inner = _make_raw_app(raw, b"application/json")
    app = ModelsCapabilityMiddleware(inner, _CAP_MAP)
    async with _client(app) as c:
        resp = await c.post("/v1/chat/completions")
    assert resp.content == raw


@pytest.mark.asyncio
async def test_non_200_passthrough_byte_for_byte():
    inner = _make_json_app(_MODELS_BODY, status=500)
    app = ModelsCapabilityMiddleware(inner, _CAP_MAP)
    async with _client(app) as c:
        resp = await c.get("/v1/models")
    assert resp.status_code == 500
    assert resp.content == json.dumps(_MODELS_BODY).encode()


@pytest.mark.asyncio
async def test_non_json_passthrough_byte_for_byte():
    raw = b"<html>not json</html>"
    inner = _make_raw_app(raw, b"text/html")
    app = ModelsCapabilityMiddleware(inner, _CAP_MAP)
    async with _client(app) as c:
        resp = await c.get("/v1/models")
    assert resp.content == raw


@pytest.mark.asyncio
async def test_unexpected_json_shape_passthrough():
    # dict without a list `data` → not transformed
    body = {"object": "list", "data": "not-a-list"}
    inner = _make_json_app(body)
    app = ModelsCapabilityMiddleware(inner, _CAP_MAP)
    async with _client(app) as c:
        resp = await c.get("/v1/models")
    assert resp.json() == body


@pytest.mark.asyncio
async def test_empty_map_transparent_passthrough():
    inner = _make_json_app(_MODELS_BODY)
    app = ModelsCapabilityMiddleware(inner, {})
    async with _client(app) as c:
        resp = await c.get("/v1/models")
    out = resp.json()
    for item in out["data"]:
        assert "airlock" not in item


# --- install: idempotency + pre/post-start ----------------------------------


class _FakeState:
    pass


class _FakeApp:
    """Mimics the slice of FastAPI the installer touches."""

    def __init__(self, *, middleware_stack):
        self.state = _FakeState()
        self.middleware_stack = middleware_stack
        self.added = []

    def add_middleware(self, cls, **kwargs):
        self.added.append((cls, kwargs))


@pytest.fixture
def _patched_proxy_app(monkeypatch):
    import sys
    import types

    def _install(app):
        mod = types.ModuleType("litellm.proxy.proxy_server")
        mod.app = app
        monkeypatch.setitem(sys.modules, "litellm.proxy.proxy_server", mod)

    return _install


def test_install_prestart_uses_add_middleware(monkeypatch, _patched_proxy_app):
    import airlock.models_seam as seam

    monkeypatch.setattr(seam, "_build_capability_map", lambda: dict(_CAP_MAP))

    class _Fa(_FakeApp):
        pass

    # make isinstance(app, FastAPI) pass
    monkeypatch.setattr(seam, "FastAPI", _Fa, raising=False)
    app = _Fa(middleware_stack=None)
    monkeypatch.setattr(
        "airlock.models_seam._get_proxy_app", lambda: app, raising=False
    )

    ok = install_models_capability_seam_on_proxy_app()
    assert ok is True
    assert app.added and app.added[0][0] is ModelsCapabilityMiddleware
    assert app.added[0][1]["capability_map"] == _CAP_MAP
    assert app.state.airlock_models_seam_installed is True


def test_install_poststart_wraps_stack(monkeypatch):
    import airlock.models_seam as seam

    monkeypatch.setattr(seam, "_build_capability_map", lambda: dict(_CAP_MAP))

    class _Fa(_FakeApp):
        pass

    monkeypatch.setattr(seam, "FastAPI", _Fa, raising=False)
    sentinel = object()
    app = _Fa(middleware_stack=sentinel)
    monkeypatch.setattr(
        "airlock.models_seam._get_proxy_app", lambda: app, raising=False
    )

    ok = install_models_capability_seam_on_proxy_app()
    assert ok is True
    assert isinstance(app.middleware_stack, ModelsCapabilityMiddleware)
    assert app.middleware_stack.app is sentinel
    assert not app.added  # add_middleware not used post-start


def test_install_idempotent(monkeypatch):
    import airlock.models_seam as seam

    monkeypatch.setattr(seam, "_build_capability_map", lambda: dict(_CAP_MAP))

    class _Fa(_FakeApp):
        pass

    monkeypatch.setattr(seam, "FastAPI", _Fa, raising=False)
    app = _Fa(middleware_stack=None)
    monkeypatch.setattr(
        "airlock.models_seam._get_proxy_app", lambda: app, raising=False
    )

    assert install_models_capability_seam_on_proxy_app() is True
    n_after_first = len(app.added)
    assert install_models_capability_seam_on_proxy_app() is True
    assert len(app.added) == n_after_first  # no double-wrap


def test_install_returns_false_without_app(monkeypatch):
    monkeypatch.setattr(
        "airlock.models_seam._get_proxy_app", lambda: None, raising=False
    )
    assert install_models_capability_seam_on_proxy_app() is False


# --- _build_capability_map: never-crash contract -----------------------------


def _write_config(tmp_path, text: str, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    monkeypatch.setenv("AIRLOCK_CONFIG", str(cfg))
    return cfg


def test_build_map_missing_config_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRLOCK_CONFIG", str(tmp_path / "does-not-exist.yaml"))
    assert _build_capability_map() == {}


def test_build_map_non_list_model_list_returns_empty(tmp_path, monkeypatch):
    # truthy non-list model_list must not be iterated (would raise)
    _write_config(tmp_path, "model_list: true\n", monkeypatch)
    assert _build_capability_map() == {}


def test_build_map_dict_model_list_returns_empty(tmp_path, monkeypatch):
    _write_config(tmp_path, "model_list:\n  foo: bar\n", monkeypatch)
    assert _build_capability_map() == {}


def test_build_map_skips_malformed_entry_keeps_good(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        (
            "model_list:\n"
            "  - model_name: bad-entry\n"
            "    litellm_params: not-a-dict\n"
            "  - model_name: good-entry\n"
            "    litellm_params:\n"
            "      model: anthropic/claude-haiku\n"
        ),
        monkeypatch,
    )
    result = _build_capability_map()
    assert "good-entry" in result
    assert result["good-entry"]["airlock_provider"] == "anthropic"
    assert result["good-entry"]["underlying"] == "anthropic/claude-haiku"
    # the malformed entry is skipped, not crashing the whole map
    assert "bad-entry" not in result


def test_build_map_per_entry_exception_skipped(tmp_path, monkeypatch):
    # capability_record raising for one entry must not abort the whole map
    import airlock.models_seam as seam

    real = seam.capability_record

    def _boom(entry):
        if entry.get("model_name") == "explode":
            raise ValueError("boom")
        return real(entry)

    monkeypatch.setattr(seam, "capability_record", _boom)
    _write_config(
        tmp_path,
        (
            "model_list:\n"
            "  - model_name: explode\n"
            "    litellm_params:\n"
            "      model: gemini/x\n"
            "  - model_name: fine\n"
            "    litellm_params:\n"
            "      model: gemini/y\n"
        ),
        monkeypatch,
    )
    result = _build_capability_map()
    assert "fine" in result
    assert "explode" not in result
