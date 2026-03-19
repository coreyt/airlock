"""Tests for airlock/callbacks/model_override_headers.py."""

from __future__ import annotations

from types import SimpleNamespace

from airlock.callbacks.model_override_headers import (
    AirlockModelOverrideHeaders,
)


class TestModelOverrideHeaders:
    async def test_returns_override_header_and_updates_hidden_params(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={})
        data = {
            "metadata": {
                "airlock_response_headers": {
                    "X-Airlock-Model-Override": "claude-haiku",
                }
            }
        }

        result = await hook.async_post_call_response_headers_hook(
            data=data,
            user_api_key_dict=None,
            response=response,
        )

        assert result == {"X-Airlock-Model-Override": "claude-haiku"}
        assert response._hidden_params["additional_headers"] == {
            "X-Airlock-Model-Override": "claude-haiku",
        }

    async def test_returns_none_when_no_override_present(self):
        hook = AirlockModelOverrideHeaders()
        response = SimpleNamespace(_hidden_params={})

        result = await hook.async_post_call_response_headers_hook(
            data={"metadata": {}},
            user_api_key_dict=None,
            response=response,
        )

        assert result is None
        assert response._hidden_params == {}
