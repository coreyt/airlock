"""Unit tests — VLLMBackend protocol + wiring + reconciler (Slices 2/3/4)."""

from __future__ import annotations

import asyncio
import json

import pytest

from airlock.batch import vllm
from airlock.batch.backend import ResultUnavailableError
from airlock.batch.store import BatchStore
from airlock.batch.vllm import VLLMBackend


@pytest.fixture(autouse=True)
def _clear_registry():
    vllm._jobs.clear()
    vllm._semaphore = None  # reset the global bound between tests/event loops
    yield
    vllm._jobs.clear()
    vllm._semaphore = None


def _echo_sender():
    async def send(body):
        return {"id": "c", "choices": [{"message": {"content": "PONG"}}]}

    return send


def _backend(tmp_path, send_chat=None):
    return VLLMBackend(
        provider_model="qwen3.6-27b",
        api_base="http://vllm.local/v1",
        work_dir=str(tmp_path),
        send_chat=send_chat or _echo_sender(),
    )


async def _wait_done(backend, idem, tries=200):
    for _ in range(tries):
        if (await backend.poll(idem)).status == "completed":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("executor did not complete")


class TestUploadPersists:
    async def test_upload_copies_to_durable_path_surviving_src_unlink(self, tmp_path):
        import os

        b = _backend(tmp_path)
        src = tmp_path / "translated.tmp.jsonl"
        src.write_text(json.dumps({"custom_id": "r1", "body": {}}) + "\n")
        ref = await b.upload(str(src), "idem-abc")
        # The core unlinks src right after upload; the durable ref must survive.
        os.unlink(src)
        assert ref == b.provider_input_path("idem-abc")
        assert open(ref).read().strip()  # content persisted


class TestPathSafety:
    @pytest.mark.parametrize(
        "bad", ["../../etc/evil", "/tmp/evil", "..", ".", "a/b", ""]
    )
    def test_unsafe_idem_refused_for_path(self, tmp_path, bad):
        b = _backend(tmp_path)
        with pytest.raises(ValueError):
            b.provider_input_path(bad)

    def test_sha256_idem_is_accepted(self, tmp_path):
        b = _backend(tmp_path)
        ok = "a" * 64
        assert b.provider_input_path(ok).endswith(f"{ok}.provider.jsonl")


class TestCreatePollFetch:
    async def test_create_returns_immediately_then_completes(self, tmp_path):
        b = _backend(tmp_path)
        # Lay down a durable translated input (as upload would).
        ref = b.provider_input_path("idem-1")
        with open(ref, "w") as f:
            for cid in ("r1", "r2"):
                f.write(json.dumps({"custom_id": cid, "body": {"messages": []}}) + "\n")

        job_id = await b.create("qwen3.6-27b", ref, "idem-1")
        assert job_id == "idem-1"
        assert "idem-1" in vllm._jobs  # registered (strong ref)

        await _wait_done(b, "idem-1")
        native = list(await b.fetch("idem-1"))
        assert {n["custom_id"] for n in native} == {"r1", "r2"}

    async def test_fetch_missing_raises_result_unavailable(self, tmp_path):
        b = _backend(tmp_path)
        with pytest.raises(ResultUnavailableError):
            await b.fetch("never-ran")

    async def test_list_jobs_is_empty(self, tmp_path):
        assert await _backend(tmp_path).list_jobs("idem-x") == []

    async def test_create_is_idempotent_no_double_spawn(self, tmp_path):
        b = _backend(tmp_path)
        ref = b.provider_input_path("idem-2")
        with open(ref, "w") as f:
            f.write(json.dumps({"custom_id": "r1", "body": {"messages": []}}) + "\n")
        await b.create("m", ref, "idem-2")
        task1 = vllm._jobs["idem-2"].task
        await b.create("m", ref, "idem-2")  # second create while running
        # Same task object -> not re-spawned.
        assert vllm._jobs.get("idem-2") is None or vllm._jobs["idem-2"].task is task1
        await _wait_done(b, "idem-2")


class TestCancel:
    async def test_cancel_signals_registry(self, tmp_path):
        b = _backend(tmp_path)
        ref = b.provider_input_path("idem-c")
        with open(ref, "w") as f:
            f.write(json.dumps({"custom_id": "r1", "body": {"messages": []}}) + "\n")
        await b.create("m", ref, "idem-c")
        # The job may already be registered; cancel must not raise even if done.
        await b.cancel("idem-c")
        await b.cancel("not-a-job")  # no-op, no raise


class TestReconciler:
    async def test_respawns_created_vllm_batch(self, tmp_path):
        store = BatchStore(str(tmp_path / "b.db"))
        b = _backend(tmp_path)
        # Simulate a pre-crash CREATED vLLM batch with a durable input on disk.
        won, _ = store.claim(
            "idem-r", input_file_id="f", model="qwen-vllm", backend="vllm"
        )
        assert won
        store.set_created("idem-r", job_id="idem-r")
        ref = b.provider_input_path("idem-r")
        with open(ref, "w") as f:
            f.write(json.dumps({"custom_id": "r1", "body": {"messages": []}}) + "\n")

        n = await vllm.reconcile_vllm_batches(store, backend_factory=lambda m: b)
        assert n == 1
        await _wait_done(b, "idem-r")
        assert {x["custom_id"] for x in await b.fetch("idem-r")} == {"r1"}

    async def test_ignores_non_vllm_and_terminal(self, tmp_path):
        store = BatchStore(str(tmp_path / "b.db"))
        store.claim("idem-m", input_file_id="f", model="m", backend="mistral")
        store.set_created("idem-m", job_id="j")
        n = await vllm.reconcile_vllm_batches(store, backend_factory=lambda m: None)
        assert n == 0


class TestBackendForAliasWiring:
    def test_resolves_api_base_and_key_from_litellm_params(self, tmp_path, monkeypatch):
        from airlock.batch import runtime

        monkeypatch.setenv("VLLM_API_KEY", "secret-key")
        cfg = {
            "model_list": [
                {
                    "model_name": "qwen36-27b-vllm-batch",
                    "litellm_params": {
                        "model": "openai/qwen3.6-27b",
                        "api_base": "http://192.168.1.45:8000/v1",
                        "api_key": "os.environ/VLLM_API_KEY",
                    },
                    "airlock_batch": {
                        "backend": "vllm",
                        "provider_model": "qwen3.6-27b",
                    },
                }
            ]
        }
        monkeypatch.setattr(runtime, "get_config", lambda: cfg)
        monkeypatch.setattr(runtime, "_data_dir", lambda: tmp_path)
        be = runtime.backend_for_alias("qwen36-27b-vllm-batch")
        assert isinstance(be, VLLMBackend)
        assert be.name == "vllm"
        assert be.api_base == "http://192.168.1.45:8000/v1"
        assert be.api_key == "secret-key"
        assert be.provider_model == "qwen3.6-27b"
