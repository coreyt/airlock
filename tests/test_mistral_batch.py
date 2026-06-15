"""Tests for the Mistral batch adapter (Pack 0.4.0-D).

All no-network: the real ``mistralai`` SDK is never imported. The provider
surface is exercised through an in-memory fake Mistral client.
"""

from __future__ import annotations

import os

import pytest
import yaml

from airlock.batch.backend import BatchBackend, ResultUnavailableError
from airlock.batch.gateway import load_batch_aliases
from airlock.batch.middleware import _gateway_provider
from airlock.batch.mistral import (
    MistralBackend,
    mistral_result_to_openai,
    normalize_mistral_status,
    openai_line_to_mistral,
)


# ---------------------------------------------------------------------------
# In-memory fake Mistral client (no network, no SDK)
# ---------------------------------------------------------------------------
class _FakeJob:
    def __init__(self, job_id, *, status="SUCCESS", metadata=None, output_file=None):
        self.id = job_id
        self.status = status
        self.metadata = metadata or {}
        self.output_file = output_file


class _FakeJobs:
    def __init__(self, jobs, output_text):
        self._jobs = jobs
        self._output_text = output_text
        self.cancelled: list[str] = []
        self.created: list[dict] = []

    def create(self, *, input_files, model, endpoint, metadata):
        self.created.append(
            {
                "input_files": input_files,
                "model": model,
                "endpoint": endpoint,
                "metadata": metadata,
            }
        )
        job = _FakeJob("job-new", metadata=metadata, output_file="out-1")
        self._jobs.append(job)
        return job

    def get(self, *, job_id):
        for job in self._jobs:
            if job.id == job_id:
                return job
        raise KeyError(job_id)

    def list(self):
        return list(self._jobs)

    def cancel(self, *, job_id):
        self.cancelled.append(job_id)


class _FakeFiles:
    def __init__(self, output_text, *, download_ok=True):
        self.output_text = output_text
        self.download_ok = download_ok
        self.uploads: list[dict] = []

    def upload(self, *, purpose, file):
        self.uploads.append({"purpose": purpose, "file": file})
        return type("F", (), {"id": "file-uploaded"})()

    def download(self, *, file_id):
        if not self.download_ok:
            raise RuntimeError("expired")
        return self.output_text.encode("utf-8")


class _FakeBatch:
    def __init__(self, jobs):
        self.jobs = jobs


class _FakeMistralClient:
    def __init__(self, jobs=None, output_text="", download_ok=True):
        jobs = jobs if jobs is not None else []
        self._jobs_obj = _FakeJobs(jobs, output_text)
        self.batch = _FakeBatch(self._jobs_obj)
        self.files = _FakeFiles(output_text, download_ok=download_ok)


def _backend_with_client(client, **kwargs):
    backend = MistralBackend(api_key="x", **kwargs)
    backend._client_obj = client
    return backend


# ---------------------------------------------------------------------------
# (a) OpenAI <-> Mistral translation (near-passthrough; native body preserved)
# ---------------------------------------------------------------------------
class TestTranslation:
    def test_openai_line_to_mistral_request(self):
        line = {
            "custom_id": "req-1",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": "mistral-large-latest",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 64,
            },
        }
        out = openai_line_to_mistral(line)
        assert out["custom_id"] == "req-1"
        assert out["body"]["messages"][0]["content"] == "hello"
        assert out["body"]["max_tokens"] == 64

    def test_mistral_result_preserves_native_body(self):
        native = {
            "id": "x",
            "custom_id": "req-1",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "hi there"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"total_tokens": 7},
                },
            },
        }
        out = mistral_result_to_openai(native)
        assert out["custom_id"] == "req-1"
        assert out["error"] is None
        body = out["response"]["body"]
        # native preserved verbatim
        assert body["usage"] == {"total_tokens": 7}
        # OpenAI choices projected (already OpenAI-shaped)
        assert body["choices"][0]["message"]["content"] == "hi there"
        assert out["response"]["status_code"] == 200

    def test_mistral_result_error_line(self):
        native = {
            "custom_id": "req-2",
            "error": {"code": "bad", "message": "boom"},
        }
        out = mistral_result_to_openai(native)
        assert out["custom_id"] == "req-2"
        assert out["response"] is None
        assert out["error"]["message"] == "boom"
        assert out["error"]["code"] == "bad"

    def test_backend_translation_methods_no_sdk(self):
        backend = MistralBackend(api_key="x")
        req = backend.to_provider_request({"custom_id": "r1", "body": {"messages": []}})
        assert req["custom_id"] == "r1"
        res = backend.from_provider_result(
            {"custom_id": "r1", "response": {"body": {"choices": []}}}
        )
        assert res["custom_id"] == "r1"


# ---------------------------------------------------------------------------
# (b) Status map: each Mistral status -> OpenAI vocab; unknown -> in_progress
# ---------------------------------------------------------------------------
class TestStatusMapping:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("QUEUED", "validating"),
            ("RUNNING", "in_progress"),
            ("SUCCESS", "completed"),
            ("FAILED", "failed"),
            ("TIMEOUT_EXCEEDED", "expired"),
            ("CANCELLATION_REQUESTED", "cancelling"),
            ("CANCELLED", "cancelled"),
        ],
    )
    def test_normalize_mistral_status(self, raw, expected):
        assert normalize_mistral_status(raw) == expected

    def test_unknown_status_defaults_to_in_progress(self):
        assert normalize_mistral_status("SOMETHING_NEW") == "in_progress"
        assert normalize_mistral_status(None) == "in_progress"


# ---------------------------------------------------------------------------
# (c) backend_for_alias dispatch on marker["backend"]
# ---------------------------------------------------------------------------
class TestBackendForAlias:
    def test_dispatch_mistral_and_aistudio(self, monkeypatch):
        from airlock.batch import runtime
        from airlock.batch.aistudio import AIStudioBackend

        cfg = {
            "model_list": [
                {
                    "model_name": "mistral-large-batch",
                    "litellm_params": {"model": "mistral/mistral-large-latest"},
                    "airlock_batch": {
                        "backend": "mistral",
                        "provider_model": "mistral-large-latest",
                    },
                },
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
        monkeypatch.setattr(runtime, "get_config", lambda: cfg)
        runtime._config_cache = None

        m = runtime.backend_for_alias("mistral-large-batch")
        assert isinstance(m, MistralBackend)
        assert m.provider_model == "mistral-large-latest"

        a = runtime.backend_for_alias("gemini-3.1-pro-aistudio")
        assert isinstance(a, AIStudioBackend)

        assert runtime.backend_for_alias("plain") is None
        assert runtime.backend_for_alias("nope") is None

    def test_mistral_backend_satisfies_protocol(self):
        assert isinstance(MistralBackend(api_key="x"), BatchBackend)


# ---------------------------------------------------------------------------
# (d) Middleware discrimination: mistral -> gateway; unknown -> call_next
# ---------------------------------------------------------------------------
class TestMiddlewareDiscrimination:
    def test_gateway_provider_includes_mistral(self):
        assert _gateway_provider(b"custom_llm_provider=mistral") == "mistral"
        assert _gateway_provider(b"custom_llm_provider=cohere") is None


# ---------------------------------------------------------------------------
# (e) list_jobs filters by metadata["display_name"]
# ---------------------------------------------------------------------------
class TestListJobs:
    async def test_list_jobs_filters_by_display_name(self):
        jobs = [
            _FakeJob("job-1", metadata={"display_name": "idem-A"}),
            _FakeJob("job-2", metadata={"display_name": "idem-B"}),
            _FakeJob("job-3", metadata={"display_name": "idem-A"}),
            _FakeJob("job-4", metadata={}),
        ]
        backend = _backend_with_client(_FakeMistralClient(jobs=jobs))
        matches = await backend.list_jobs("idem-A")
        assert matches == ["job-1", "job-3"]

    async def test_create_sets_display_name_metadata(self):
        client = _FakeMistralClient(jobs=[])
        backend = _backend_with_client(client, provider_model="mistral-large-latest")
        job_id = await backend.create("ignored", "file-uploaded", "idem-X")
        assert job_id == "job-new"
        created = client._jobs_obj.created[0]
        assert created["metadata"] == {"display_name": "idem-X"}
        assert created["model"] == "mistral-large-latest"
        assert created["endpoint"] == "/v1/chat/completions"
        assert created["input_files"] == ["file-uploaded"]


# ---------------------------------------------------------------------------
# (f) fetch raises ResultUnavailableError when the output file is missing
# ---------------------------------------------------------------------------
class TestFetch:
    async def test_fetch_returns_native_lines(self):
        output = (
            '{"custom_id": "r1", "response": {"status_code": 200, '
            '"body": {"choices": []}}}\n'
        )
        jobs = [_FakeJob("job-1", output_file="out-1")]
        backend = _backend_with_client(
            _FakeMistralClient(jobs=jobs, output_text=output)
        )
        lines = list(await backend.fetch("job-1"))
        assert lines[0]["custom_id"] == "r1"

    async def test_fetch_raises_when_output_file_missing(self):
        jobs = [_FakeJob("job-1", output_file=None)]
        backend = _backend_with_client(_FakeMistralClient(jobs=jobs))
        with pytest.raises(ResultUnavailableError):
            await backend.fetch("job-1")

    async def test_fetch_raises_when_download_fails(self):
        jobs = [_FakeJob("job-1", output_file="out-1")]
        backend = _backend_with_client(_FakeMistralClient(jobs=jobs, download_ok=False))
        with pytest.raises(ResultUnavailableError):
            await backend.fetch("job-1")


# ---------------------------------------------------------------------------
# Lazy SDK: clear error when mistralai missing
# ---------------------------------------------------------------------------
class TestLazySDK:
    async def test_missing_sdk_raises_clear_error(self, monkeypatch):
        backend = MistralBackend(api_key="x")

        def _boom():
            raise ImportError("no mistralai")

        monkeypatch.setattr(backend, "_import_mistral", _boom)
        with pytest.raises(RuntimeError, match="mistral"):
            await backend.upload(b"{}", "disp")

    async def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        backend = MistralBackend(api_key=None)
        monkeypatch.setattr(
            backend, "_import_mistral", lambda: (_ for _ in ()).throw(AssertionError)
        )
        # import succeeds path is exercised via a stub returning a client factory
        monkeypatch.setattr(backend, "_import_mistral", lambda: lambda **kw: object())
        with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
            await backend.upload(b"{}", "disp")


# ---------------------------------------------------------------------------
# Cancel / poll
# ---------------------------------------------------------------------------
class TestCancelPoll:
    async def test_cancel_calls_sdk(self):
        client = _FakeMistralClient(jobs=[_FakeJob("job-1")])
        backend = _backend_with_client(client)
        await backend.cancel("job-1")
        assert client._jobs_obj.cancelled == ["job-1"]

    async def test_poll_normalizes_status(self):
        jobs = [_FakeJob("job-1", status="RUNNING")]
        backend = _backend_with_client(_FakeMistralClient(jobs=jobs))
        status = await backend.poll("job-1")
        assert status.status == "in_progress"
        assert status.raw == "RUNNING"


# ---------------------------------------------------------------------------
# Config + pyproject wiring
# ---------------------------------------------------------------------------
class TestConfigYaml:
    def test_config_has_mistral_batch_aliases(self):
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config.yaml"
        )
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        aliases = load_batch_aliases(cfg)
        assert "mistral-large-batch" in aliases
        assert aliases["mistral-large-batch"]["backend"] == "mistral"
        assert "mistral-small-batch" in aliases
        # sync mistral aliases preserved
        names = {m["model_name"] for m in cfg["model_list"]}
        assert "mistral-large" in names
        assert "mistral-small" in names
