"""Tests for BatchGatewayMiddleware.__call__ routing — specifically the no-param
content-fetch interception (a stock OpenAI SDK ``files.content()`` works without
the ``custom_llm_provider`` query param)."""

from __future__ import annotations

import pytest

from airlock.batch.middleware import BatchGatewayMiddleware


def _scope(method, path, query=b""):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query,
        "headers": [],
    }


class _Inner:
    """Fake LiteLLM app: records whether it was reached."""

    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 299, "headers": []})
        await send({"type": "http.response.body", "body": b"INNER"})


class _Cap:
    def __init__(self):
        self.status = None
        self.body = b""

    async def __call__(self, m):
        if m["type"] == "http.response.start":
            self.status = m["status"]
        elif m["type"] == "http.response.body":
            self.body += m.get("body", b"")


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.fixture(autouse=True)
def _wire(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRLOCK_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)


def _stage(file_id, rows):
    from airlock.batch import runtime

    runtime.write_output(file_id, rows)


class TestNoParamContentInterception:
    async def test_gateway_file_is_served_without_param(self):
        fid = "file-" + "a" * 32
        _stage(fid, [{"custom_id": "r1", "response": {"body": {"ok": 1}}}])
        inner = _Inner()
        cap = _Cap()
        await BatchGatewayMiddleware(inner)(
            _scope("GET", f"/v1/files/{fid}/content"), _receive, cap
        )
        assert inner.called is False  # gateway intercepted, no param needed
        assert cap.status == 200
        assert b"custom_id" in cap.body

    async def test_unknown_file_falls_through_to_litellm(self):
        inner = _Inner()
        cap = _Cap()
        await BatchGatewayMiddleware(inner)(
            _scope("GET", "/v1/files/file-" + "b" * 32 + "/content"), _receive, cap
        )
        assert inner.called is True  # not a gateway file -> native handler
        assert cap.body == b"INNER"

    async def test_traversal_id_falls_through_without_fs_use(self):
        inner = _Inner()
        cap = _Cap()
        await BatchGatewayMiddleware(inner)(
            _scope("GET", "/v1/files/../../etc/passwd/content"), _receive, cap
        )
        assert inner.called is True  # id fails the strict pattern -> no intercept

    async def test_upload_post_still_requires_param(self):
        inner = _Inner()
        cap = _Cap()
        await BatchGatewayMiddleware(inner)(_scope("POST", "/v1/files"), _receive, cap)
        assert inner.called is True  # uploads are NOT intercepted without the param

    async def test_param_path_still_dispatches(self):
        fid = "file-" + "c" * 32
        _stage(fid, [{"custom_id": "r1", "response": {"body": {}}}])
        inner = _Inner()
        cap = _Cap()
        await BatchGatewayMiddleware(inner)(
            _scope("GET", f"/v1/files/{fid}/content", b"custom_llm_provider=vllm"),
            _receive,
            cap,
        )
        assert inner.called is False
        assert cap.status == 200
