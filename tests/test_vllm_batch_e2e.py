"""Live end-to-end test for the local vLLM batch path (gateway-as-executor).

The operator gate for the ``VLLMBackend``. The unit + integration suites prove
translation, the executor (resume/cancel/partial-failure), wiring, and the full
ASGI lifecycle with an injected transport; THIS test proves the real round-trip:
Airlock executing a batch against a **live** vLLM ``/v1/chat/completions`` host.

Opt-in. Runs only when:
  - ``AIRLOCK_LIVE_VLLM_E2E=1`` (explicit opt-in);
  - a vLLM host is reachable at ``AIRLOCK_VLLM_E2E_API_BASE`` (default the lab
    host) serving ``AIRLOCK_VLLM_E2E_MODEL`` (default ``qwen3.6-27b``).

No proxy restart and no provider SDK needed — it drives the real
``airlock.batch.gateway`` functions against a real ``VLLMBackend`` (whose default
``httpx`` transport hits the live host) + a temp ``BatchStore``: create -> upload
(persist) -> spawn executor -> poll -> stage -> assert. Mirrors
``test_mistral_batch_e2e.py`` / ``test_aistudio_batch_e2e.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

pytestmark = pytest.mark.live

_OPT_IN = os.getenv("AIRLOCK_LIVE_VLLM_E2E") == "1"
_API_BASE = os.getenv("AIRLOCK_VLLM_E2E_API_BASE", "http://192.168.1.45:8000/v1")
_API_KEY = os.getenv("VLLM_API_KEY")  # optional; many local hosts are open
_MODEL = os.getenv("AIRLOCK_VLLM_E2E_MODEL", "qwen3.6-27b")
_MAX_TOKENS = int(os.getenv("AIRLOCK_VLLM_E2E_MAX_TOKENS", "256"))
_TIMEOUT = float(os.getenv("AIRLOCK_VLLM_E2E_TIMEOUT", "300"))
_POLL = float(os.getenv("AIRLOCK_VLLM_E2E_POLL", "3"))

requires_live = pytest.mark.skipif(
    not _OPT_IN,
    reason="set AIRLOCK_LIVE_VLLM_E2E=1 (and a reachable vLLM host) to run this test",
)

_TERMINAL = {"completed", "failed", "expired", "cancelled"}


def _input_lines(alias: str) -> list[dict]:
    return [
        {
            "custom_id": "e2e-pong",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": alias,
                "messages": [
                    {"role": "system", "content": "You are a terse echo bot."},
                    {"role": "user", "content": "Reply with exactly one word: PONG"},
                ],
                "temperature": 0,
                "max_tokens": _MAX_TOKENS,
            },
        },
        {
            "custom_id": "e2e-math",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": alias,
                "messages": [
                    {
                        "role": "user",
                        "content": "What is 2+2? Reply with just the number.",
                    }
                ],
                "temperature": 0,
                "max_tokens": _MAX_TOKENS,
            },
        },
    ]


def _content(body: dict) -> str:
    msg = body["response"]["body"]["choices"][0]["message"]
    # A reasoning model may return the answer in reasoning_content; accept either
    # so a clean round-trip is not failed for where the model placed the text.
    return msg.get("content") or msg.get("reasoning_content") or ""


@requires_live
async def test_vllm_batch_full_lifecycle(tmp_path):
    from airlock.batch import gateway
    from airlock.batch.store import BatchStore
    from airlock.batch.vllm import VLLMBackend

    store = BatchStore(db_path=str(tmp_path / "batch.db"))
    backend = VLLMBackend(
        provider_model=_MODEL,
        api_base=_API_BASE,
        api_key=_API_KEY,
        work_dir=str(tmp_path),
    )

    alias = "qwen36-27b-vllm-batch"
    lines = _input_lines(alias)
    jsonl = ("\n".join(json.dumps(line) for line in lines)).encode("utf-8")
    input_file_id = f"file-e2e-{uuid.uuid4().hex}"

    created = await gateway.create_batch(
        store,
        backend,
        input_file_id=input_file_id,
        model=alias,
        endpoint="/v1/chat/completions",
        params=None,
        jsonl=jsonl,
        client="vllm-e2e",
    )
    batch_id = created["id"]
    print(
        f"\n[e2e] host={_API_BASE} model={_MODEL} batch_id={batch_id} "
        f"status={created['status']}"
    )
    assert created["status"] in ("validating", "in_progress"), created

    try:
        obj = created
        waited = 0.0
        while obj["status"] not in _TERMINAL:
            if waited >= _TIMEOUT:
                pytest.fail(
                    f"batch {batch_id} did not finish within {_TIMEOUT}s "
                    f"(last status {obj['status']})"
                )
            await asyncio.sleep(_POLL)
            waited += _POLL
            obj = await gateway.get_batch(store, backend, batch_id)
            print(f"[e2e] +{int(waited)}s status={obj['status']}")

        assert obj["status"] == "completed", (
            f"batch ended {obj['status']}: {obj.get('errors')}"
        )

        bodies = store.staged_bodies(batch_id)
        by_id = {b["custom_id"]: b for b in bodies}
        print(f"[e2e] staged custom_ids={sorted(by_id)}")
        assert set(by_id) == {"e2e-pong", "e2e-math"}, by_id

        pong = by_id["e2e-pong"]
        assert pong["error"] is None, pong["error"]
        assert "PONG" in _content(pong).upper(), _content(pong)

        math = by_id["e2e-math"]
        assert math["error"] is None, math["error"]
        assert "4" in _content(math), _content(math)
    finally:
        # Stop the fire-and-forget executor if we bailed early (timeout/assert)
        # so it can't keep hitting the live host or write into tmp_path during
        # teardown. No-op once the batch has already completed.
        row = store.get_by_batch_id(batch_id)
        if row:
            await backend.cancel(row["idem"])
