"""A minimal, explicit opt-in OpenAI probe for ``reasoning_effort=max``.

This is deliberately a *provider* probe, not an Airlock proxy test.  LiteLLM's
capability map currently has no decisive ``supports_max_reasoning_effort`` value;
routing the request through LiteLLM could therefore drop the parameter before it
reaches OpenAI and turn a 200 into false evidence.  The mocked contract test pins
the exact HTTP request, while the live test settles whether OpenAI accepts it for
the configured concrete model.

The provider call is billable and is skipped unless both ``OPENAI_API_KEY`` and
``AIRLOCK_LIVE_OPENAI_REASONING_EFFORT=1`` are set.  See
``dev/plans/runs/openai-reasoning-effort-max-probe.md`` before running it.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest


pytestmark = pytest.mark.live

_API_KEY = os.getenv("OPENAI_API_KEY")
_OPT_IN = os.getenv("AIRLOCK_LIVE_OPENAI_REASONING_EFFORT") == "1"
_MODEL = os.getenv("AIRLOCK_OPENAI_REASONING_EFFORT_MODEL", "gpt-5.6-sol")
_ENDPOINT = "https://api.openai.com/v1/chat/completions"

requires_live = pytest.mark.skipif(
    not (_API_KEY and _OPT_IN),
    reason=(
        "set OPENAI_API_KEY and AIRLOCK_LIVE_OPENAI_REASONING_EFFORT=1 "
        "to run this billable OpenAI provider probe"
    ),
)


def _payload(model: str) -> dict[str, Any]:
    """Return the smallest stable request that exercises only the ``max`` bit."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "reasoning_effort": "max",
        "max_completion_tokens": 64,
    }


def _probe(
    api_key: str,
    model: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Response:
    """Send the direct provider request without exposing credentials in output."""
    with httpx.Client(timeout=45.0, transport=transport) as client:
        return client.post(
            _ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "airlock-reasoning-effort-max-probe",
            },
            json=_payload(model),
        )


def test_max_probe_contract_is_direct_and_unmodified() -> None:
    """CI proof: the billable probe sends ``max`` unchanged to OpenAI's endpoint."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": "chatcmpl-contract", "choices": [{"message": {"content": "OK"}}]},
        )

    response = _probe("sk-unit-test", "gpt-5.6-sol", transport=httpx.MockTransport(handler))

    assert response.status_code == 200
    assert seen["method"] == "POST"
    assert seen["url"] == _ENDPOINT
    assert seen["headers"]["authorization"] == "Bearer sk-unit-test"
    assert seen["payload"] == {
        "model": "gpt-5.6-sol",
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "reasoning_effort": "max",
        "max_completion_tokens": 64,
    }


@requires_live
def test_openai_accepts_max_reasoning_effort() -> None:
    """Provider evidence gate.  Failure is evidence, not a reason to guess."""
    assert _API_KEY is not None  # narrows for static checkers; guarded by requires_live.
    response = _probe(_API_KEY, _MODEL)
    request_id = response.headers.get("x-request-id", "unknown")
    assert response.status_code == 200, (
        "OpenAI did not accept reasoning_effort=max; this result is inconclusive "
        "unless the response is a provider validation error. "
        f"model={_MODEL} status={response.status_code} request_id={request_id} "
        f"body={response.text[:1000]}"
    )
    body = response.json()
    assert body.get("id"), f"OpenAI response lacked an id (request_id={request_id})"
    assert body.get("choices"), f"OpenAI response lacked choices (request_id={request_id})"
    print(f"max reasoning-effort probe passed: model={_MODEL} request_id={request_id}")
