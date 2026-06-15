"""Integration tests for the Airlock Batch Gateway — full HTTP lifecycle.

These drive the **real ASGI middleware** (``dispatch_batch_gateway``) end to end
through scope/receive/send, against a real ``BatchStore`` and the real on-disk
file store, for **both** gateway providers (``aistudio`` + ``mistral``). The only
thing faked is the provider network transport: a backend that uses the **real**
translation functions but returns canned provider results instead of calling out.

This complements:
  - ``test_batch_gateway.py`` — unit-level gateway/translation/idempotency.
  - ``test_aistudio_batch_e2e.py`` / ``test_mistral_batch_e2e.py`` — live e2e.

No network; safe to run in CI.
"""

from __future__ import annotations

import json

import pytest

from airlock.batch.aistudio import gemini_result_to_openai, openai_line_to_gemini
from airlock.batch.backend import NormalizedStatus
from airlock.batch.middleware import dispatch_batch_gateway
from airlock.batch.mistral import mistral_result_to_openai, openai_line_to_mistral
from airlock.batch.store import BatchStore

MASTER_KEY = "test-master-key-0123456789"


# ---------------------------------------------------------------------------
# Provider-faithful fake backend (real translation; canned transport)
# ---------------------------------------------------------------------------
class IntegrationFakeBackend:
    def __init__(self, name, to_req, from_res, result_for_key):
        self.name = name
        self._to_req = to_req
        self._from_res = from_res
        self._result_for_key = result_for_key
        self._keys: list[str] = []
        self._counter = 0
        self.cancelled: list[str] = []

    def to_provider_request(self, line):
        return self._to_req(line)

    def from_provider_result(self, line):
        return self._from_res(line)

    async def upload(self, src, display_name):
        with open(src, "rb") as f:
            data = f.read()
        for raw in data.decode("utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue  # e.g. multipart boundary lines
            key = obj.get("key") or obj.get("custom_id")
            if key:
                self._keys.append(key)
        return f"ref-{display_name}"

    async def create(self, model, file_ref, display_name):
        self._counter += 1
        return f"job-{self._counter}"

    async def poll(self, job_id):
        return NormalizedStatus(status="completed", raw="done")

    async def fetch(self, job_id):
        return [self._result_for_key(k) for k in self._keys]

    async def cancel(self, job_id):
        self.cancelled.append(job_id)

    async def list_jobs(self, display_name):
        return []


def _aistudio_result(key):
    return {
        "key": key,
        "response": {
            "candidates": [
                {"content": {"parts": [{"text": "PONG"}]}, "finishReason": "STOP"}
            ]
        },
    }


def _mistral_result(key):
    return {
        "custom_id": key,
        "response": {
            "status_code": 200,
            "body": {
                "id": "cmpl-x",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "PONG"},
                        "finish_reason": "stop",
                    }
                ],
            },
        },
    }


def _make_aistudio():
    return IntegrationFakeBackend(
        "aistudio", openai_line_to_gemini, gemini_result_to_openai, _aistudio_result
    )


def _make_mistral():
    return IntegrationFakeBackend(
        "mistral", openai_line_to_mistral, mistral_result_to_openai, _mistral_result
    )


PROVIDERS = [
    pytest.param("aistudio", _make_aistudio, id="aistudio"),
    pytest.param("mistral", _make_mistral, id="mistral"),
]


# ---------------------------------------------------------------------------
# ASGI plumbing
# ---------------------------------------------------------------------------
def _scope(method, path, query=b"", headers=None):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query,
        "headers": headers or [],
    }


def _body_receiver(body: bytes):
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


class _Capture:
    def __init__(self):
        self.status = None
        self.body = b""

    async def __call__(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body":
            self.body += message.get("body", b"")

    def json(self):
        return json.loads(self.body)


async def _request(method, path, query=b"", body=b"", headers=None):
    cap = _Capture()
    await dispatch_batch_gateway(
        _scope(method, path, query, headers), _body_receiver(body), cap
    )
    return cap


def _wire(monkeypatch, tmp_path, fake, *, master_key=None):
    """Point the runtime at a temp store/file-dir + our fake backend."""
    from airlock.batch import runtime

    store = BatchStore(str(tmp_path / "batch.db"))
    monkeypatch.setattr(runtime, "backend_for_alias", lambda model: fake)
    monkeypatch.setattr(runtime, "get_store", lambda: store)
    monkeypatch.setenv("AIRLOCK_STATE_DIR", str(tmp_path))
    if master_key is None:
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)
    else:
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", master_key)
    return store


def _input_jsonl(alias: str) -> bytes:
    lines = [
        {
            "custom_id": "r1",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": alias,
                "messages": [{"role": "user", "content": "Say PONG"}],
                "max_tokens": 8,
            },
        },
        {
            "custom_id": "r2",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": alias,
                "messages": [{"role": "user", "content": "Say PONG again"}],
                "max_tokens": 8,
            },
        },
    ]
    return ("\n".join(json.dumps(line) for line in lines)).encode("utf-8")


# ---------------------------------------------------------------------------
# Full lifecycle: upload -> create -> poll(complete) -> stage -> content
# ---------------------------------------------------------------------------
class TestFullLifecycleThroughASGI:
    @pytest.mark.parametrize("provider,make_fake", PROVIDERS)
    async def test_upload_create_poll_content(
        self, provider, make_fake, monkeypatch, tmp_path
    ):
        fake = make_fake()
        _wire(monkeypatch, tmp_path, fake, master_key=MASTER_KEY)
        auth = [(b"authorization", b"Bearer " + MASTER_KEY.encode())]
        q = f"custom_llm_provider={provider}".encode()
        alias = f"{provider}-batch-alias"

        # upload (raw JSONL body)
        up = await _request("POST", "/v1/files", q, _input_jsonl(alias), auth)
        assert up.status == 200, up.body
        assert up.json()["object"] == "file"
        file_id = up.json()["id"]

        # create
        create_body = json.dumps(
            {
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "model": alias,
            }
        ).encode()
        cr = await _request("POST", "/v1/batches", q, create_body, auth)
        assert cr.status == 200, cr.body
        batch_id = cr.json()["id"]
        assert cr.json()["status"] in ("validating", "in_progress")

        # poll -> completed (fake polls SUCCEEDED), stages results
        gb = await _request("GET", f"/v1/batches/{batch_id}", q, b"", auth)
        assert gb.status == 200, gb.body
        obj = gb.json()
        assert obj["status"] == "completed", obj
        assert obj["request_counts"]["total"] == 2
        out_id = obj["output_file_id"]
        assert out_id

        # content -> translated-back OpenAI output lines
        ct = await _request("GET", f"/v1/files/{out_id}/content", q, b"", auth)
        assert ct.status == 200
        lines = [
            json.loads(line) for line in ct.body.decode().splitlines() if line.strip()
        ]
        by_id = {line["custom_id"]: line for line in lines}
        assert set(by_id) == {"r1", "r2"}
        for cid in ("r1", "r2"):
            row = by_id[cid]
            assert row["error"] is None
            content = row["response"]["body"]["choices"][0]["message"]["content"]
            assert content == "PONG"

    @pytest.mark.parametrize("provider,make_fake", PROVIDERS)
    async def test_multipart_upload_is_parsed(
        self, provider, make_fake, monkeypatch, tmp_path
    ):
        """The documented curl recipe uses multipart (-F file=@...)."""
        fake = make_fake()
        _wire(monkeypatch, tmp_path, fake)
        q = f"custom_llm_provider={provider}".encode()
        alias = f"{provider}-batch-alias"

        boundary = b"----airlocktest"
        payload = _input_jsonl(alias)
        multipart = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="file"; filename="r.jsonl"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            + payload
            + b"\r\n--"
            + boundary
            + b"--\r\n"
        )
        up = await _request("POST", "/v1/files", q, multipart)
        file_id = up.json()["id"]

        create_body = json.dumps(
            {
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "model": alias,
            }
        ).encode()
        cr = await _request("POST", "/v1/batches", q, create_body)
        assert cr.status == 200, cr.body
        gb = await _request("GET", f"/v1/batches/{cr.json()['id']}", q)
        # The two JSONL rows survive; multipart boundary lines are skipped.
        assert gb.json()["request_counts"]["total"] == 2


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------
class TestCancelThroughASGI:
    @pytest.mark.parametrize("provider,make_fake", PROVIDERS)
    async def test_cancel_marks_cancelled(
        self, provider, make_fake, monkeypatch, tmp_path
    ):
        fake = make_fake()
        _wire(monkeypatch, tmp_path, fake)
        q = f"custom_llm_provider={provider}".encode()
        alias = f"{provider}-batch-alias"

        up = await _request("POST", "/v1/files", q, _input_jsonl(alias))
        create_body = json.dumps(
            {
                "input_file_id": up.json()["id"],
                "endpoint": "/v1/chat/completions",
                "model": alias,
            }
        ).encode()
        cr = await _request("POST", "/v1/batches", q, create_body)
        batch_id = cr.json()["id"]

        cx = await _request("POST", f"/v1/batches/{batch_id}/cancel", q)
        assert cx.status == 200, cx.body
        assert cx.json()["status"] == "cancelled"
        assert fake.cancelled  # provider cancel was called


# ---------------------------------------------------------------------------
# Auth + routing + error surfaces
# ---------------------------------------------------------------------------
class TestGatewayHttpGuards:
    @pytest.mark.parametrize("provider,make_fake", PROVIDERS)
    async def test_missing_bearer_is_401_when_key_set(
        self, provider, make_fake, monkeypatch, tmp_path
    ):
        _wire(monkeypatch, tmp_path, make_fake(), master_key=MASTER_KEY)
        q = f"custom_llm_provider={provider}".encode()
        r = await _request("POST", "/v1/files", q, _input_jsonl("x"))
        assert r.status == 401
        assert r.json()["error"]["code"] == "invalid_api_key"

    @pytest.mark.parametrize("provider,make_fake", PROVIDERS)
    async def test_wrong_bearer_is_401(
        self, provider, make_fake, monkeypatch, tmp_path
    ):
        _wire(monkeypatch, tmp_path, make_fake(), master_key=MASTER_KEY)
        q = f"custom_llm_provider={provider}".encode()
        bad = [(b"authorization", b"Bearer nope")]
        r = await _request("POST", "/v1/batches", q, b"{}", bad)
        assert r.status == 401

    async def test_unknown_alias_is_400(self, monkeypatch, tmp_path):
        from airlock.batch import runtime

        # backend_for_alias returns None for an unconfigured model.
        monkeypatch.setattr(runtime, "backend_for_alias", lambda model: None)
        monkeypatch.setattr(
            runtime, "get_store", lambda: BatchStore(str(tmp_path / "b.db"))
        )
        monkeypatch.setenv("AIRLOCK_STATE_DIR", str(tmp_path))
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)
        body = json.dumps(
            {
                "input_file_id": "file-x",
                "endpoint": "/v1/chat/completions",
                "model": "nope",
            }
        ).encode()
        r = await _request("POST", "/v1/batches", b"custom_llm_provider=aistudio", body)
        assert r.status == 400
        assert "not a configured" in r.json()["error"]["message"]

    async def test_unknown_route_is_404(self, monkeypatch, tmp_path):
        from airlock.batch import runtime

        monkeypatch.setattr(
            runtime, "backend_for_alias", lambda model: _make_aistudio()
        )
        monkeypatch.setattr(
            runtime, "get_store", lambda: BatchStore(str(tmp_path / "b.db"))
        )
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)
        r = await _request("DELETE", "/v1/batches/abc", b"custom_llm_provider=aistudio")
        assert r.status == 404
