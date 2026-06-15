"""Live end-to-end test for the AI Studio (Gemini) batch path.

This is the operator gate referenced in ``dev/plans/runs/STATUS-0.4.0.md`` and
``dev/aistudio-batch-e2e-test-plan.md``. The 0.4.0 unit suite proves translation
and gateway wiring against mocked SDK calls; THIS test proves the real round-trip
against Google's live Gemini batch endpoint.

It is opt-in and billable. It runs only when ALL of:
  - the ``aistudio`` extra is installed (``google.genai`` importable);
  - ``GOOGLE_AISTUDIO_API_KEY`` is set;
  - ``AIRLOCK_LIVE_AISTUDIO_E2E=1`` (explicit opt-in).

It drives the real ``airlock.batch.gateway`` functions against a real
``AIStudioBackend`` + temp ``BatchStore`` so the asserted path is the one the live
proxy runs (create -> upload -> create job -> poll -> fetch -> stage).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

pytestmark = pytest.mark.live

# Skip the whole module cleanly if the optional extra isn't installed.
pytest.importorskip("google.genai", reason="aistudio extra not installed")

_API_KEY = os.getenv("GOOGLE_AISTUDIO_API_KEY")
_OPT_IN = os.getenv("AIRLOCK_LIVE_AISTUDIO_E2E") == "1"

requires_live = pytest.mark.skipif(
    not (_API_KEY and _OPT_IN),
    reason="set GOOGLE_AISTUDIO_API_KEY and AIRLOCK_LIVE_AISTUDIO_E2E=1 to run this billable test",
)

_MODEL = os.getenv("AIRLOCK_AISTUDIO_E2E_MODEL", "gemini-3.5-flash")
_TIMEOUT = float(os.getenv("AIRLOCK_AISTUDIO_E2E_TIMEOUT", "1200"))
_POLL = float(os.getenv("AIRLOCK_AISTUDIO_E2E_POLL", "20"))

_TERMINAL = {"completed", "failed", "expired", "cancelled"}


def _input_lines(alias: str) -> list[dict]:
    """Two deterministic OpenAI batch-input lines with checkable answers."""
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
                # Gemini 3.x flash is a thinking model: a tiny budget is spent on
                # reasoning tokens and the job finishes MAX_TOKENS with empty text.
                # Give enough room to think AND answer (see design note "Open issues").
                "max_tokens": 512,
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
                "max_tokens": 512,
            },
        },
    ]


def _content(body: dict) -> str:
    return body["response"]["body"]["choices"][0]["message"]["content"]


def _finish_reason(body: dict) -> str | None:
    return body["response"]["body"]["choices"][0].get("finish_reason")


@requires_live
async def test_aistudio_batch_full_lifecycle(tmp_path, capsys):
    from airlock.batch import gateway
    from airlock.batch.aistudio import AIStudioBackend
    from airlock.batch.store import BatchStore

    store = BatchStore(db_path=str(tmp_path / "batch.db"))
    backend = AIStudioBackend(api_key=_API_KEY, provider_model=_MODEL)

    alias = "gemini-aistudio-e2e"
    lines = _input_lines(alias)
    jsonl = ("\n".join(json.dumps(line) for line in lines)).encode("utf-8")
    input_file_id = f"file-e2e-{uuid.uuid4().hex}"

    # 1. Create (translate -> upload -> create provider job).
    created = await gateway.create_batch(
        store,
        backend,
        input_file_id=input_file_id,
        model=alias,
        endpoint="/v1/chat/completions",
        params=None,
        jsonl=jsonl,
        client="aistudio-e2e",
    )
    batch_id = created["id"]
    print(f"\n[e2e] model={_MODEL} batch_id={batch_id} status={created['status']}")
    assert created["status"] in ("validating", "in_progress"), created

    # 2. Poll to a terminal state.
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

    # 3. Inspect staged, translated-back output.
    bodies = store.staged_bodies(batch_id)
    by_id = {b["custom_id"]: b for b in bodies}
    print(f"[e2e] staged custom_ids={sorted(by_id)}")
    assert set(by_id) == {"e2e-pong", "e2e-math"}, by_id

    pong = by_id["e2e-pong"]
    assert pong["error"] is None, pong["error"]
    assert _finish_reason(pong) == "stop", (
        f"expected a clean stop, got finish_reason={_finish_reason(pong)!r} "
        f"(MAX_TOKENS means the thinking budget starved the answer)"
    )
    assert "PONG" in _content(pong).upper(), _content(pong)

    math = by_id["e2e-math"]
    assert math["error"] is None, math["error"]
    assert "4" in _content(math), _content(math)
