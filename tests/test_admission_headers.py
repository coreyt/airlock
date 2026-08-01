from __future__ import annotations

import json
from types import SimpleNamespace

from airlock.callbacks.model_override_headers import AirlockModelOverrideHeaders
from airlock.proxy_errors import (
    AirlockAdmissionShed,
    admission_shed_response_payload,
    airlock_admission_shed_handler,
)


async def test_admitted_request_emits_admission_header() -> None:
    response = SimpleNamespace(_hidden_params={})
    result = await AirlockModelOverrideHeaders().async_post_call_response_headers_hook(
        data={"metadata": {"airlock_admission": {"action": "admitted"}}},
        user_api_key_dict=None,
        response=response,
    )

    assert result == {"X-Airlock-Admission": "admitted"}
    assert response._hidden_params["additional_headers"] == result


async def test_disabled_admission_emits_no_header() -> None:
    response = SimpleNamespace(_hidden_params={})
    result = await AirlockModelOverrideHeaders().async_post_call_response_headers_hook(
        data={"metadata": {}}, user_api_key_dict=None, response=response
    )

    assert result is None
    assert response._hidden_params == {}


async def test_shed_has_openai_429_and_matching_retry_headers() -> None:
    exc = AirlockAdmissionShed("too many requests", retry_after=2.1)
    body, headers = admission_shed_response_payload(exc)

    assert body["error"]["code"] == "admission_shed"
    assert body["error"]["airlock"]["retry_after"] == 3
    assert headers == {
        "Retry-After": "3",
        "X-Airlock-Admission": "shed; retry_after=3",
    }

    response = await airlock_admission_shed_handler(None, exc)
    assert response.status_code == 429
    assert json.loads(response.body) == body
