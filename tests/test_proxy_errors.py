"""Tests for airlock/proxy_errors.py (workstream B / Pack 0.5.0-RES-errors)."""

from __future__ import annotations

import json

import pytest
from litellm import RateLimitError

from airlock.proxy_errors import (
    AirlockProviderBlocked,
    airlock_provider_blocked_handler,
    block_response_payload,
    install_airlock_error_handlers_on_proxy_app,
    retry_after_seconds,
)


class TestAirlockProviderBlocked:
    def test_is_a_rate_limit_error(self):
        exc = AirlockProviderBlocked(
            "blocked",
            llm_provider="openai",
            model="gpt-5.4",
            cooldown_seconds=42.0,
            scope="client_provider",
            reason="quota",
            client_id="key:abc",
        )
        assert isinstance(exc, RateLimitError)
        assert exc.cooldown_seconds == 42.0
        assert exc.scope == "client_provider"
        assert exc.reason == "quota"
        assert exc.client_id == "key:abc"


class TestRetryAfter:
    def test_ceils_and_floors_at_one(self):
        assert retry_after_seconds(0.0) == 1
        assert retry_after_seconds(0.1) == 1
        assert retry_after_seconds(29.2) == 30
        assert retry_after_seconds(208.0) == 208


class TestBlockResponsePayload:
    def _exc(self, **kw):
        base = dict(
            llm_provider="openai",
            model="gpt-5.4",
            cooldown_seconds=29.4,
            scope="provider",
            reason="exceeded your current quota",
            client_id="key:abc",
        )
        base.update(kw)
        return AirlockProviderBlocked("Airlock blocked", **base)

    def test_body_is_openai_shaped_and_enriched(self):
        body, headers = block_response_payload(self._exc())
        assert body["error"]["type"] == "airlock_circuit_breaker"
        assert body["error"]["code"] == "provider_blocked"
        assert body["error"]["param"] is None
        air = body["error"]["airlock"]
        assert air["scope"] == "provider"
        assert air["provider"] == "openai"
        assert air["retry_after"] == 30  # ceil(29.4)
        assert air["source"] == "circuit_breaker"

    def test_headers(self):
        _, headers = block_response_payload(self._exc())
        assert headers["Retry-After"] == "30"
        assert headers["X-Airlock-Provider-State"] == "quarantined"
        assert headers["X-Airlock-Block-Scope"] == "provider"

    def test_reason_is_bounded(self):
        # Use spaced words (no 32+ char token) so truncation, not redaction, applies.
        body, _ = block_response_payload(self._exc(reason="word " * 200))
        assert len(body["error"]["airlock"]["reason"]) == 300


class TestHandler:
    async def test_handler_returns_429(self):
        exc = AirlockProviderBlocked(
            "blocked",
            llm_provider="anthropic",
            model="claude-sonnet",
            cooldown_seconds=88.0,
            scope="model",
            reason="r",
            client_id="key:z",
        )
        resp = await airlock_provider_blocked_handler(None, exc)
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "88"
        assert resp.headers["X-Airlock-Block-Scope"] == "model"
        payload = json.loads(bytes(resp.body))
        assert payload["error"]["type"] == "airlock_circuit_breaker"


class TestInstall:
    def test_returns_false_without_proxy_app(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "litellm.proxy.proxy_server", object())
        assert install_airlock_error_handlers_on_proxy_app() is False

    def test_idempotent_on_fastapi_app(self, monkeypatch):
        import sys

        from fastapi import FastAPI

        app = FastAPI()
        stub = type("M", (), {"app": app})()
        monkeypatch.setitem(sys.modules, "litellm.proxy.proxy_server", stub)
        assert install_airlock_error_handlers_on_proxy_app() is True
        assert getattr(app.state, "airlock_error_handlers_installed") is True
        # second call is a no-op success
        assert install_airlock_error_handlers_on_proxy_app() is True
        assert AirlockProviderBlocked in app.exception_handlers


class TestGuardianRaisesTyped:
    def test_raise_provider_protection_raises_typed(self):
        from airlock.fast.guardian import _raise_provider_protection

        with pytest.raises(AirlockProviderBlocked) as ei:
            _raise_provider_protection(
                {"metadata": {}},
                "key:abc",
                "openai",
                "gpt-5.4",
                "quota",
                30.0,
                scope="client_provider",
            )
        exc = ei.value
        assert isinstance(exc, RateLimitError)
        assert exc.scope == "client_provider"
        assert exc.cooldown_seconds == 30.0
        assert exc.client_id == "key:abc"


class TestSanitizeReason:
    def test_redacts_key_like_tokens(self):
        from airlock.proxy_errors import sanitize_reason

        out = sanitize_reason("quota for sk-ABCD1234efgh5678 exhausted")
        assert "sk-ABCD1234efgh5678" not in out
        assert "[REDACTED]" in out

    def test_redacts_bearer_and_long_secrets(self):
        from airlock.proxy_errors import sanitize_reason

        assert "[REDACTED]" in sanitize_reason("Bearer abcdef123456789")
        assert "[REDACTED]" in sanitize_reason("token " + "a" * 40)

    def test_truncates(self):
        from airlock.proxy_errors import sanitize_reason

        # Spaced words so truncation (not redaction) is what bounds the length.
        assert len(sanitize_reason("word " * 200)) == 300

    def test_message_in_body_is_sanitized(self):
        """The BLOCK: raw reason embedded in the message must not leak."""
        from airlock.fast.guardian import _raise_provider_protection

        with pytest.raises(AirlockProviderBlocked) as ei:
            _raise_provider_protection(
                {"metadata": {}},
                "key:abc",
                "openai",
                "gpt-5.4",
                "upstream said sk-LEAK1234567890abcdef bad",
                30.0,
                scope="provider",
            )
        body, _ = block_response_payload(ei.value)
        assert "sk-LEAK1234567890abcdef" not in body["error"]["message"]
        assert "sk-LEAK1234567890abcdef" not in body["error"]["airlock"]["reason"]
