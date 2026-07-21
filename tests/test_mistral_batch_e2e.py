"""Live end-to-end test for the Mistral batch path.

The operator gate for the Mistral adapter (status board: "Live Mistral e2e =
future operator gate"). The unit suite proves translation + wiring against mocked
SDK calls; THIS test proves the real round-trip against Mistral's live batch API.

Opt-in and billable. Runs only when ALL of:
  - the ``mistral`` extra is installed (``mistralai`` importable);
  - ``MISTRAL_API_KEY`` is set;
  - ``AIRLOCK_LIVE_MISTRAL_E2E=1`` (explicit opt-in).

It drives the real ``airlock.batch.gateway`` functions against a real
``MistralBackend`` + temp ``BatchStore`` (create -> upload -> create job -> poll
-> fetch -> stage), mirroring ``test_aistudio_batch_e2e.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

pytestmark = pytest.mark.live

pytest.importorskip("mistralai", reason="mistral extra not installed")

_API_KEY = os.getenv("MISTRAL_API_KEY")
_OPT_IN = os.getenv("AIRLOCK_LIVE_MISTRAL_E2E") == "1"

requires_live = pytest.mark.skipif(
    not (_API_KEY and _OPT_IN),
    reason="set MISTRAL_API_KEY and AIRLOCK_LIVE_MISTRAL_E2E=1 to run this billable test",
)

_MODEL = os.getenv("AIRLOCK_MISTRAL_E2E_MODEL", "mistral-small-latest")
_TIMEOUT = float(os.getenv("AIRLOCK_MISTRAL_E2E_TIMEOUT", "1500"))
_POLL = float(os.getenv("AIRLOCK_MISTRAL_E2E_POLL", "20"))

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
                "max_tokens": 16,
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
                    },
                ],
                "temperature": 0,
                "max_tokens": 16,
            },
        },
    ]


def _content(body: dict) -> str:
    return body["response"]["body"]["choices"][0]["message"]["content"]


@requires_live
async def test_mistral_batch_full_lifecycle(tmp_path):
    from airlock.batch import gateway
    from airlock.batch.mistral import MistralBackend
    from airlock.batch.store import BatchStore

    store = BatchStore(db_path=str(tmp_path / "batch.db"))
    backend = MistralBackend(api_key=_API_KEY, provider_model=_MODEL)

    alias = "mistral-batch-e2e"
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
        client="mistral-e2e",
    )
    batch_id = created["id"]
    print(f"\n[e2e] model={_MODEL} batch_id={batch_id} status={created['status']}")
    assert created["status"] in ("validating", "in_progress"), created

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
