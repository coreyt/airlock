"""Tests for the Airlock Batch Gateway + AI Studio adapter (Pack 0.4.0-C).

All no-network: the real ``google-genai`` SDK is never imported. The provider
surface is exercised through an in-memory ``FakeBackend`` implementing the
``BatchBackend`` protocol.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
import yaml

from airlock.batch.aistudio import (
    AIStudioBackend,
    gemini_result_to_openai,
    normalize_aistudio_status,
    openai_line_to_gemini,
)
from airlock.batch.backend import NormalizedStatus, ResultUnavailableError
from airlock.batch.gateway import (
    create_batch,
    get_batch,
    load_batch_aliases,
    load_batch_profile,
    provider_sync_params,
    stage_results,
)
from airlock.batch.middleware import (
    BatchGatewayMiddleware,
    _authorized,
    _gateway_provider,
    _is_batch_request,
    dispatch_batch_gateway,
)
from airlock.batch.store import RETRIEVING, BatchStore, compute_idem


# ---------------------------------------------------------------------------
# Fake backend (in-memory; no network)
# ---------------------------------------------------------------------------
class FakeBackend:
    name = "aistudio"

    def __init__(self, native_results=None, fetch_error=None):
        self.uploads: list[tuple[bytes, str]] = []
        self.creates: list[tuple[str, str, str]] = []
        self.cancels: list[str] = []
        self.fetch_calls = 0
        self._poll_status = "JOB_STATE_SUCCEEDED"
        self._native_results = native_results or []
        self._fetch_error = fetch_error
        self._jobs_by_display: dict[str, list[str]] = {}
        self._counter = 0

    def to_provider_request(self, openai_line: dict) -> dict:
        return openai_line_to_gemini(openai_line)

    def from_provider_result(self, native_line: dict) -> dict:
        return gemini_result_to_openai(native_line)

    async def upload(self, src, display_name: str) -> str:
        # The real backend streams from a file path; accept either a path or
        # raw bytes so the in-memory fake stays faithful to the streamed path.
        if isinstance(src, (bytes, bytearray)):
            data = bytes(src)
        else:
            with open(src, "rb") as f:
                data = f.read()
        self.uploads.append((data, display_name))
        return f"file-ref-{display_name}"

    async def create(self, model: str, file_ref: str, display_name: str) -> str:
        self.creates.append((model, file_ref, display_name))
        self._counter += 1
        job_id = f"job-{self._counter}"
        self._jobs_by_display.setdefault(display_name, []).append(job_id)
        return job_id

    async def poll(self, job_id: str) -> NormalizedStatus:
        return NormalizedStatus(
            status=normalize_aistudio_status(self._poll_status),
            raw=self._poll_status,
        )

    async def fetch(self, job_id: str):
        self.fetch_calls += 1
        if self._fetch_error is not None:
            raise self._fetch_error
        return list(self._native_results)

    async def cancel(self, job_id: str) -> None:
        self.cancels.append(job_id)

    async def list_jobs(self, display_name: str) -> list[str]:
        return list(self._jobs_by_display.get(display_name, []))


@pytest.fixture
def store(tmp_path):
    return BatchStore(str(tmp_path / "batch.db"))


# ---------------------------------------------------------------------------
# (a) Middleware discrimination — query string only, body never buffered
# ---------------------------------------------------------------------------
class _RecordingApp:
    def __init__(self):
        self.called = False
        self.scope = None

    async def __call__(self, scope, receive, send):
        self.called = True
        self.scope = scope


def _scope(method="POST", path="/v1/batches", query=b""):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query,
    }


async def _noop_send(_message):
    return None


class TestMiddlewareDiscrimination:
    async def test_is_batch_request_matches_files_and_batches(self):
        assert _is_batch_request("POST", "/v1/batches")
        assert _is_batch_request("POST", "/v1/files")
        assert _is_batch_request("GET", "/v1/batches/batch-1")
        assert _is_batch_request("POST", "/v1/batches/batch-1/cancel")
        assert _is_batch_request("GET", "/v1/files/file-1/content")
        assert not _is_batch_request("POST", "/v1/chat/completions")

    def test_gateway_provider_reads_query(self):
        assert _gateway_provider(b"custom_llm_provider=aistudio") == "aistudio"
        assert _gateway_provider(b"custom_llm_provider=openai") is None
        assert _gateway_provider(b"") is None

    async def test_non_aistudio_falls_through_without_buffering(self):
        app = _RecordingApp()
        mw = BatchGatewayMiddleware(app)
        received = []

        async def receive():
            received.append(1)
            return {"type": "http.request", "body": b"", "more_body": False}

        await mw(
            _scope(query=b"custom_llm_provider=openai"),
            receive,
            _noop_send,
        )
        assert app.called is True
        # The middleware must not drain the request body itself.
        assert received == []

    async def test_chat_path_falls_through(self):
        app = _RecordingApp()
        mw = BatchGatewayMiddleware(app)
        await mw(_scope(path="/v1/chat/completions"), None, _noop_send)
        assert app.called is True

    async def test_aistudio_routes_to_gateway(self, monkeypatch):
        app = _RecordingApp()
        mw = BatchGatewayMiddleware(app)
        dispatched = {}

        async def fake_dispatch(scope, receive, send):
            dispatched["yes"] = True

        monkeypatch.setattr(
            "airlock.batch.middleware.dispatch_batch_gateway", fake_dispatch
        )
        await mw(
            _scope(query=b"custom_llm_provider=aistudio"),
            None,
            _noop_send,
        )
        assert dispatched.get("yes") is True
        assert app.called is False


# ---------------------------------------------------------------------------
# (b) OpenAI <-> Gemini translation; native body preserved (A4)
# ---------------------------------------------------------------------------
class TestTranslation:
    def test_openai_line_to_gemini_request(self):
        line = {
            "custom_id": "req-1",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": "gemini-3.1-pro-preview",
                "messages": [
                    {"role": "system", "content": "be terse"},
                    {"role": "user", "content": "hello"},
                ],
                "temperature": 0.2,
                "max_tokens": 64,
            },
        }
        out = openai_line_to_gemini(line)
        assert out["key"] == "req-1"
        req = out["request"]
        assert req["contents"][0]["role"] == "user"
        assert req["contents"][0]["parts"][0]["text"] == "hello"
        assert req["system_instruction"]["parts"][0]["text"] == "be terse"
        assert req["generationConfig"]["temperature"] == 0.2
        assert req["generationConfig"]["maxOutputTokens"] == 64

    def test_gemini_result_preserves_native_body(self):
        native = {
            "key": "req-1",
            "response": {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "hi there"}], "role": "model"},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"totalTokenCount": 7},
            },
        }
        out = gemini_result_to_openai(native)
        assert out["custom_id"] == "req-1"
        body = out["response"]["body"]
        # native preserved verbatim
        assert body["candidates"] == native["response"]["candidates"]
        assert body["usageMetadata"] == native["response"]["usageMetadata"]
        # best-effort OpenAI projection added alongside
        assert body["choices"][0]["message"]["content"] == "hi there"
        assert out["error"] is None

    def test_gemini_result_error_line(self):
        native = {"key": "req-2", "error": {"code": 500, "message": "boom"}}
        out = gemini_result_to_openai(native)
        assert out["custom_id"] == "req-2"
        assert out["response"] is None
        assert out["error"]["message"] == "boom"


# ---------------------------------------------------------------------------
# (d) Status mapping (§3.5)
# ---------------------------------------------------------------------------
class TestStatusMapping:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("JOB_STATE_PENDING", "validating"),
            ("JOB_STATE_RUNNING", "in_progress"),
            ("JOB_STATE_SUCCEEDED", "completed"),
            ("JOB_STATE_FAILED", "failed"),
            ("JOB_STATE_CANCELLED", "cancelled"),
            ("JOB_STATE_EXPIRED", "expired"),
        ],
    )
    def test_normalize_aistudio_status(self, raw, expected):
        assert normalize_aistudio_status(raw) == expected

    def test_unknown_status_defaults_to_in_progress(self):
        assert normalize_aistudio_status("JOB_STATE_WAT") == "in_progress"


# ---------------------------------------------------------------------------
# (c) Idempotency (§3.7)
# ---------------------------------------------------------------------------
class TestIdempotency:
    def test_compute_idem_is_deterministic(self):
        a = compute_idem("file-1", "m", "/v1/chat/completions", {"x": 1})
        b = compute_idem("file-1", "m", "/v1/chat/completions", {"x": 1})
        c = compute_idem("file-2", "m", "/v1/chat/completions", {"x": 1})
        assert a == b
        assert a != c

    async def test_duplicate_create_yields_one_job(self, store):
        backend = FakeBackend()
        jsonl = b'{"custom_id":"r1","body":{"messages":[]}}\n'
        obj1 = await create_batch(
            store,
            backend,
            input_file_id="file-1",
            model="gemini-3.1-pro-preview",
            endpoint="/v1/chat/completions",
            params={},
            jsonl=jsonl,
        )
        obj2 = await create_batch(
            store,
            backend,
            input_file_id="file-1",
            model="gemini-3.1-pro-preview",
            endpoint="/v1/chat/completions",
            params={},
            jsonl=jsonl,
        )
        assert obj1["id"] == obj2["id"]
        # exactly one provider job created
        assert len(backend.creates) == 1

    async def test_reconcile_adopts_earliest_and_cancels_duplicates(self, store):
        backend = FakeBackend()
        idem = compute_idem("file-1", "m", "/v1/chat/completions", {})
        # Simulate a crash window: row stuck CREATING with an expired lease,
        # and the provider already has two orphaned jobs for this display_name.
        store.claim(
            idem,
            batch_id="batch-x",
            input_file_id="file-1",
            model="m",
            endpoint="/v1/chat/completions",
            backend="aistudio",
        )
        store.expire_lease(idem)
        backend._jobs_by_display[idem] = ["job-a", "job-b"]

        obj = await create_batch(
            store,
            backend,
            input_file_id="file-1",
            model="m",
            endpoint="/v1/chat/completions",
            params={},
            jsonl=b"",
        )
        # adopted earliest, cancelled the rest, created nothing new
        row = store.get(idem)
        assert row["job_id"] == "job-a"
        assert backend.cancels == ["job-b"]
        assert len(backend.creates) == 0
        assert obj["id"] == "batch-x"

    async def test_retrieving_to_staged_gate_fetches_once(self, store):
        native = [
            {
                "key": "r1",
                "response": {"candidates": [{"content": {"parts": [{"text": "a"}]}}]},
            },
            {
                "key": "r2",
                "response": {"candidates": [{"content": {"parts": [{"text": "b"}]}}]},
            },
        ]
        backend = FakeBackend(native_results=native)
        idem = await _seed_created(store, backend)

        obj1 = await stage_results(store, backend, idem)
        obj2 = await stage_results(store, backend, idem)
        assert obj1["status"] == "completed"
        assert obj2["status"] == "completed"
        # second staging re-fetches nothing
        assert backend.fetch_calls == 1

    async def test_resume_stages_only_missing_rows(self, store):
        native = [
            {
                "key": "r1",
                "response": {"candidates": [{"content": {"parts": [{"text": "a"}]}}]},
            },
            {
                "key": "r2",
                "response": {"candidates": [{"content": {"parts": [{"text": "b"}]}}]},
            },
        ]
        backend = FakeBackend(native_results=native)
        idem = await _seed_created(store, backend)
        row = store.get(idem)
        # Pretend an interrupted staging already wrote r1, then crashed: the
        # RETRIEVING lease is expired so a resumer may CAS-reclaim the gate.
        store.begin_retrieving(idem)
        store.stage_row(row["batch_id"], "r1", "sha-r1", {"custom_id": "r1"})
        store.expire_lease(idem)

        await stage_results(store, backend, idem)
        keys = store.staged_keys(row["batch_id"])
        assert set(keys) == {"r1", "r2"}


# ---------------------------------------------------------------------------
# (f) §7.3 — missing/expired result file handled gracefully
# ---------------------------------------------------------------------------
class TestExpiredResult:
    async def test_expired_result_marks_failed_without_crash(self, store):
        backend = FakeBackend(fetch_error=ResultUnavailableError("result file expired"))
        idem = await _seed_created(store, backend)
        obj = await stage_results(store, backend, idem)
        assert obj["status"] in {"failed", "expired"}
        assert obj["errors"] is not None
        row = store.get(idem)
        assert row["status"] == "FAILED"


# ---------------------------------------------------------------------------
# (e) §7.4 — airlock_batch marker does not leak to provider on sync path
# ---------------------------------------------------------------------------
class TestSyncMarkerLeak:
    def test_provider_sync_params_strip_marker(self):
        entry = {
            "model_name": "gemini-3.1-pro-aistudio",
            "litellm_params": {
                "model": "gemini/gemini-3.1-pro-preview",
                "api_key": "os.environ/GOOGLE_AISTUDIO_API_KEY",
            },
            "airlock_batch": {
                "backend": "aistudio",
                "provider_model": "gemini-3.1-pro-preview",
            },
        }
        params = provider_sync_params(entry)
        assert "airlock_batch" not in params
        assert params["model"] == "gemini/gemini-3.1-pro-preview"

    def test_marker_absent_from_forwarded_provider_call(self):
        # End-to-end-ish: the marker must never reach the actual provider call
        # invocation on the SYNC path (codex #5).
        entry = {
            "model_name": "gemini-3.1-pro-aistudio",
            "litellm_params": {
                "model": "gemini/gemini-3.1-pro-preview",
                "api_key": "os.environ/GOOGLE_AISTUDIO_API_KEY",
            },
            "airlock_batch": {
                "backend": "aistudio",
                "provider_model": "gemini-3.1-pro-preview",
            },
        }
        forwarded = provider_sync_params(entry)
        captured: dict = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        fake_completion(**forwarded)
        assert "airlock_batch" not in captured
        assert captured["model"] == "gemini/gemini-3.1-pro-preview"

    def test_load_batch_aliases(self):
        config = {
            "model_list": [
                {
                    "model_name": "gemini-3.1-pro-aistudio",
                    "litellm_params": {"model": "gemini/gemini-3.1-pro-preview"},
                    "airlock_batch": {
                        "backend": "aistudio",
                        "provider_model": "gemini-3.1-pro-preview",
                    },
                },
                {
                    "model_name": "plain",
                    "litellm_params": {"model": "openai/gpt-4o"},
                },
            ]
        }
        aliases = load_batch_aliases(config)
        assert "gemini-3.1-pro-aistudio" in aliases
        assert aliases["gemini-3.1-pro-aistudio"]["backend"] == "aistudio"
        assert "plain" not in aliases


# ---------------------------------------------------------------------------
# OpenAI batch object shaping
# ---------------------------------------------------------------------------
class TestBatchObject:
    async def test_to_openai_batch_object_shape(self, store):
        backend = FakeBackend()
        obj = await create_batch(
            store,
            backend,
            input_file_id="file-1",
            model="gemini-3.1-pro-preview",
            endpoint="/v1/chat/completions",
            params={},
            jsonl=b"",
        )
        assert obj["object"] == "batch"
        assert obj["id"].startswith("batch-")
        assert obj["input_file_id"] == "file-1"
        assert obj["endpoint"] == "/v1/chat/completions"
        assert obj["status"] == "in_progress"

    async def test_get_batch_stages_on_completed(self, store):
        native = [
            {
                "key": "r1",
                "response": {"candidates": [{"content": {"parts": [{"text": "a"}]}}]},
            },
        ]
        backend = FakeBackend(native_results=native)
        created = await create_batch(
            store,
            backend,
            input_file_id="file-1",
            model="m",
            endpoint="/v1/chat/completions",
            params={},
            jsonl=b"",
        )
        obj = await get_batch(store, backend, created["id"])
        assert obj["status"] == "completed"
        assert obj["output_file_id"] is not None


# ---------------------------------------------------------------------------
# Config: aliases + batch_profile present in config.yaml
# ---------------------------------------------------------------------------
class TestConfigYaml:
    def test_config_has_aistudio_aliases_and_profile(self):
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config.yaml"
        )
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        aliases = load_batch_aliases(cfg)
        assert "gemini-3.5-flash-aistudio" in aliases
        assert "gemini-3.1-pro-aistudio" in aliases
        profile = load_batch_profile(cfg)
        assert "default" in profile
        # scan_at_upload field is present (it is a NO-OP stub in this pack)
        assert "scan_at_upload" in profile["default"]


# ---------------------------------------------------------------------------
# AIStudioBackend lazy import (no network)
# ---------------------------------------------------------------------------
class TestAIStudioBackendLazy:
    def test_translation_works_without_sdk(self):
        backend = AIStudioBackend(api_key="x")
        out = backend.to_provider_request(
            {
                "custom_id": "r1",
                "body": {"messages": [{"role": "user", "content": "hi"}]},
            }
        )
        assert out["key"] == "r1"

    async def test_missing_sdk_raises_clear_error(self, monkeypatch):
        backend = AIStudioBackend(api_key="x")

        def _boom():
            raise ImportError("no genai")

        monkeypatch.setattr(backend, "_import_genai", _boom)
        with pytest.raises(RuntimeError, match="aistudio"):
            await backend.upload(b"{}", "disp")


# ---------------------------------------------------------------------------
# (g) Gateway auth (codex #1) — master key enforced before any handling
# ---------------------------------------------------------------------------
def _auth_scope(authorization: str | None):
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("latin-1")))
    return {"headers": headers}


async def _noop_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


class TestGatewayAuth:
    def test_authorized_allows_when_key_unset(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)
        # Parity with the proxy: unset key -> open (allow).
        assert _authorized(_auth_scope(None)) is True
        assert _authorized(_auth_scope("Bearer anything")) is True

    def test_authorized_requires_correct_bearer_when_key_set(self, monkeypatch):
        key = "x" * 24
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", key)
        assert _authorized(_auth_scope(None)) is False
        assert _authorized(_auth_scope("Bearer wrong-key")) is False
        assert _authorized(_auth_scope(key)) is False  # missing "Bearer "
        assert _authorized(_auth_scope(f"Bearer {key}")) is True

    async def test_dispatch_rejects_missing_auth_with_401(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "y" * 24)
        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/files",
            "query_string": b"custom_llm_provider=aistudio",
            "headers": [],
        }
        await dispatch_batch_gateway(scope, _noop_receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 401

    async def test_dispatch_rejects_wrong_auth_with_401(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "y" * 24)
        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/batches",
            "query_string": b"custom_llm_provider=aistudio",
            "headers": [(b"authorization", b"Bearer nope")],
        }
        await dispatch_batch_gateway(scope, _noop_receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 401


# ---------------------------------------------------------------------------
# (h) Concurrency (codex #2 + #3) — CAS gates: <=1 job, exactly one fetcher
# ---------------------------------------------------------------------------
class TestConcurrency:
    async def test_concurrent_expired_lease_reclaim_yields_one_job(self, store):
        backend = FakeBackend()
        idem = compute_idem("file-1", "m", "/v1/chat/completions", {})
        # A crashed winner left the row CREATING with an expired lease and no
        # provider-side orphan job yet.
        store.claim(
            idem,
            batch_id="batch-x",
            input_file_id="file-1",
            model="m",
            endpoint="/v1/chat/completions",
            backend="aistudio",
        )
        store.expire_lease(idem)

        await asyncio.gather(
            *[
                create_batch(
                    store,
                    backend,
                    input_file_id="file-1",
                    model="m",
                    endpoint="/v1/chat/completions",
                    params={},
                    jsonl=b"",
                )
                for _ in range(6)
            ]
        )
        # Only the CAS winner of the expired lease may create -> <=1 job.
        assert len(backend.creates) == 1

    async def test_concurrent_begin_retrieving_one_fetcher(self, store):
        native = [
            {
                "key": "r1",
                "response": {"candidates": [{"content": {"parts": [{"text": "a"}]}}]},
            },
        ]
        backend = FakeBackend(native_results=native)
        idem = await _seed_created(store, backend)

        await asyncio.gather(*[stage_results(store, backend, idem) for _ in range(6)])
        # Only the CREATED->RETRIEVING CAS winner fetches.
        assert backend.fetch_calls == 1
        assert store.get(idem)["status"] == "STAGED"


# ---------------------------------------------------------------------------
# (i) Create path streams the upload from disk (codex #4) — bounded memory
# ---------------------------------------------------------------------------
class TestStreamingCreate:
    async def test_create_streams_input_path_to_provider(self, store, tmp_path):
        src = tmp_path / "input.jsonl"
        lines = [
            '{"custom_id":"r1","body":{"messages":[{"role":"user","content":"a"}]}}',
            '{"custom_id":"r2","body":{"messages":[{"role":"user","content":"b"}]}}',
        ]
        src.write_text("\n".join(lines) + "\n", encoding="utf-8")
        backend = FakeBackend()

        await create_batch(
            store,
            backend,
            input_file_id="file-1",
            model="m",
            endpoint="/v1/chat/completions",
            params={},
            input_path=str(src),
        )
        assert len(backend.uploads) == 1
        uploaded, _disp = backend.uploads[0]
        translated = [
            line for line in uploaded.decode("utf-8").splitlines() if line.strip()
        ]
        # One translated provider line per input line.
        assert len(translated) == 2


# ---------------------------------------------------------------------------
# (j) §3.7 bound under lease expiry — at-least-once, duplicate <=1, auto-cancel
#
# These tests PROVE (characterize) the EXISTING §3.7 invariant: a slow owner
# whose 60s lease expires can race a reclaimer into AT MOST ONE duplicate
# provider job, which the reconciler detects (display_name==idem grouping) and
# auto-cancels, leaving exactly one surviving job; and that repeated/slow result
# fetches stage each row exactly once (idempotent, no duplicate output). They
# assert behavior the mechanism already provides — no production change. If any
# assertion fails, the design bound does NOT hold and that is a real defect.
# ---------------------------------------------------------------------------
class TestLeaseExpiryDuplicateBound:
    async def test_expired_lease_duplicate_create_bounded_to_one_and_cancelled(
        self, store, monkeypatch
    ):
        events = _capture_batch_events(monkeypatch)

        backend = FakeBackend()
        idem = compute_idem("file-1", "m", "/v1/chat/completions", {})
        # A slow/dead original owner: row stuck CREATING with an EXPIRED lease.
        store.claim(
            idem,
            batch_id="batch-x",
            input_file_id="file-1",
            model="m",
            endpoint="/v1/chat/completions",
            backend="aistudio",
        )
        store.expire_lease(idem)
        # Worst case (§3.7): the slow owner already created its orphan AND a
        # racing reclaimer created a second job before the provider listing
        # surfaced the first -> two jobs share display_name==idem.
        backend._jobs_by_display[idem] = ["job-slow", "job-dup"]

        obj = await create_batch(
            store,
            backend,
            input_file_id="file-1",
            model="m",
            endpoint="/v1/chat/completions",
            params={},
            jsonl=b"",
        )

        # CAS-reacquired the expired lease + reconciled via list_jobs(idem):
        # adopt earliest, create nothing new.
        row = store.get(idem)
        assert row["job_id"] == "job-slow"
        assert backend.creates == []
        # Bounded to <=1 duplicate, auto-cancelled -> exactly one surviving job.
        assert _surviving_jobs(backend, idem) == ["job-slow"]
        assert backend.cancels == ["job-dup"]
        # The auto-cancel is recorded for the operator audit surface (§3.7 #4).
        assert any(e["event"] == "batch_duplicate_cancelled" for e in events)
        assert obj["id"] == "batch-x"

    async def test_expired_lease_no_orphan_creates_exactly_one(self, store):
        backend = FakeBackend()
        idem = compute_idem("file-1", "m", "/v1/chat/completions", {})
        # Slow/dead owner left CREATING + expired lease, but the provider has no
        # orphan job yet (listing had not surfaced one / it was never created).
        store.claim(
            idem,
            batch_id="batch-x",
            input_file_id="file-1",
            model="m",
            endpoint="/v1/chat/completions",
            backend="aistudio",
        )
        store.expire_lease(idem)

        await create_batch(
            store,
            backend,
            input_file_id="file-1",
            model="m",
            endpoint="/v1/chat/completions",
            params={},
            jsonl=b"",
        )

        # No orphan to adopt -> the reclaimer creates exactly one, none cancelled.
        assert len(backend.creates) == 1
        assert backend.cancels == []
        assert len(_surviving_jobs(backend, idem)) == 1


class TestSlowFetchIdempotentRestage:
    async def test_repeated_fetch_under_expired_retrieving_lease_is_idempotent(
        self, store
    ):
        native = [
            {
                "key": "r1",
                "response": {"candidates": [{"content": {"parts": [{"text": "a"}]}}]},
            },
            {
                "key": "r2",
                "response": {"candidates": [{"content": {"parts": [{"text": "b"}]}}]},
            },
        ]
        backend = FakeBackend(native_results=native)
        idem = await _seed_created(store, backend)
        batch_id = store.get(idem)["batch_id"]

        # Pass 1: full fetch + stage.
        obj1 = await stage_results(store, backend, idem)
        assert obj1["status"] == "completed"
        assert backend.fetch_calls == 1
        first = store.staged_keys(batch_id)
        assert set(first) == {"r1", "r2"}

        # A slow/duplicate second pass: the gate believes the first fetcher died,
        # so an EXPIRED RETRIEVING lease lets a second pass re-fetch the same
        # result. Per-row staging must stay idempotent (§3.7 #3).
        _force_expired_retrieving(store, idem)
        obj2 = await stage_results(store, backend, idem)
        assert obj2["status"] == "completed"
        # The second pass really did re-fetch (it was allowed to proceed)...
        assert backend.fetch_calls == 2

        # ...yet each (batch_id, row_key) is staged exactly once, sha stable.
        second = store.staged_keys(batch_id)
        assert set(second) == {"r1", "r2"}
        assert second == first  # content_sha unchanged -> no drift, no churn
        # No duplicate output rows: exactly one body per row_key after re-stage.
        bodies = store.staged_bodies(batch_id)
        assert len(bodies) == 2
        assert sorted(b["custom_id"] for b in bodies) == ["r1", "r2"]
        assert store.get(idem)["row_count"] == 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _surviving_jobs(backend, idem: str) -> list[str]:
    """Provider jobs for ``idem`` that were NOT cancelled (the survivors)."""
    return [
        job
        for job in backend._jobs_by_display.get(idem, [])
        if job not in backend.cancels
    ]


def _capture_batch_events(monkeypatch) -> list[dict]:
    """Capture ``write_batch_record`` calls emitted by the gateway."""
    events: list[dict] = []

    def fake_write(**kwargs):
        events.append(kwargs)
        return kwargs

    monkeypatch.setattr(
        "airlock.callbacks.enterprise_logger.write_batch_record", fake_write
    )
    return events


def _force_expired_retrieving(store, idem: str) -> None:
    """Force a batch back into RETRIEVING with an expired lease (test-only).

    Simulates a fetcher that the gate believes has died, so the next
    ``begin_retrieving`` CAS re-acquires the expired RETRIEVING lease and a
    second staging pass is allowed to proceed over the same result.
    """
    with store._connect() as conn:
        conn.execute(
            "UPDATE batches SET status = ?, lease_until = ? WHERE idem = ?",
            (RETRIEVING, time.time() - 1.0, idem),
        )
        conn.commit()


async def _seed_created(store, backend) -> str:
    await create_batch(
        store,
        backend,
        input_file_id="file-1",
        model="m",
        endpoint="/v1/chat/completions",
        params={},
        jsonl=b"",
    )
    return compute_idem("file-1", "m", "/v1/chat/completions", {})


# ---------------------------------------------------------------------------
# install_batch_gateway_on_proxy_app — timing regression (prod outage 2026-06-15)
# ---------------------------------------------------------------------------
class TestInstallBatchGatewayOnProxyApp:
    """The installer must work whether the LiteLLM app has started or not.

    Regression: it ran from a callback imported *during* the startup lifespan,
    so ``app.add_middleware`` raised "Cannot add middleware after an application
    has started" and crash-looped the proxy. The fix wraps the built ASGI stack
    when the app has already started.
    """

    def _register_app(self, monkeypatch, app):
        import sys
        import types

        mod = types.ModuleType("litellm.proxy.proxy_server")
        mod.app = app
        monkeypatch.setitem(sys.modules, "litellm.proxy.proxy_server", mod)

    def test_install_before_start_uses_add_middleware(self, monkeypatch):
        from fastapi import FastAPI

        from airlock.batch.middleware import install_batch_gateway_on_proxy_app

        app = FastAPI()
        assert app.middleware_stack is None  # not started
        self._register_app(monkeypatch, app)

        assert install_batch_gateway_on_proxy_app() is True
        assert any(m.cls is BatchGatewayMiddleware for m in app.user_middleware)

    def test_install_after_start_wraps_stack_without_raising(self, monkeypatch):
        from fastapi import FastAPI

        from airlock.batch.middleware import install_batch_gateway_on_proxy_app

        app = FastAPI()
        app.middleware_stack = app.build_middleware_stack()  # simulate "started"
        assert app.middleware_stack is not None
        self._register_app(monkeypatch, app)

        # Must NOT raise (the bug), and must wrap the built stack outermost.
        assert install_batch_gateway_on_proxy_app() is True
        assert isinstance(app.middleware_stack, BatchGatewayMiddleware)

    def test_install_is_idempotent_after_start(self, monkeypatch):
        from fastapi import FastAPI

        from airlock.batch.middleware import install_batch_gateway_on_proxy_app

        app = FastAPI()
        app.middleware_stack = app.build_middleware_stack()
        self._register_app(monkeypatch, app)

        assert install_batch_gateway_on_proxy_app() is True
        first = app.middleware_stack
        assert install_batch_gateway_on_proxy_app() is True  # second call no-ops
        assert app.middleware_stack is first  # not double-wrapped
