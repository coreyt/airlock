"""Tests for the guardrail-skip resolver (Pack 0.5.0-ADM-skip, CC-10/CC-11)."""

from __future__ import annotations

import pytest

from airlock.admin.tokens import mint_token
from airlock.guardrails import overrides
from airlock.guardrails.overrides import (
    configure_guardrail_overrides,
    effective_mode,
    resolve_guardrail_decision,
)

AUTH_KEY = "sk-secret-12345678"  # -> key:12345678
AUTH_ID = "key:12345678"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AIRLOCK_JWT_SECRET", "jwt-secret-abcdefghijklmnop")
    saved = overrides._cfg
    yield
    overrides._cfg = saved


def _user_key(api_key=AUTH_KEY):
    return {"api_key": api_key}


def _data_with_token(token):
    return {"metadata": {}, "headers": {"x-airlock-capability": token}}


class TestResolver:
    def test_off_by_default(self):
        configure_guardrail_overrides({})  # allow_capability_skip false
        tok = mint_token(AUTH_ID, ["guardrail:skip:keyword"], 60)
        d = resolve_guardrail_decision(_data_with_token(tok), _user_key())
        assert d == {}

    def test_skip_keyword_when_enabled(self):
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        tok = mint_token(AUTH_ID, ["guardrail:skip:keyword"], 60)
        d = resolve_guardrail_decision(_data_with_token(tok), _user_key())
        assert d == {"keyword": "observe"}

    def test_sub_must_match_authenticated_id_cc11(self):
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        # token minted for a DIFFERENT sub -> rejected even though header could be forged
        tok = mint_token("key:99999999", ["guardrail:skip:keyword"], 60)
        d = resolve_guardrail_decision(_data_with_token(tok), _user_key())
        assert d == {}

    def test_unauthenticated_no_skip_cc11(self):
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        tok = mint_token(AUTH_ID, ["guardrail:skip:keyword"], 60)
        # no authenticated key -> no skip
        d = resolve_guardrail_decision(_data_with_token(tok), None)
        assert d == {}

    def test_pii_never_skippable(self):
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        tok = mint_token(AUTH_ID, ["guardrail:skip:pii_redact"], 60)
        d = resolve_guardrail_decision(_data_with_token(tok), _user_key())
        assert d == {}  # PII non-skippable by default

    def test_invalid_token_no_skip(self):
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        d = resolve_guardrail_decision(_data_with_token("not.a.jwt"), _user_key())
        assert d == {}

    def test_config_can_make_pii_skippable(self):
        configure_guardrail_overrides(
            {
                "guardrail_overrides": {
                    "allow_capability_skip": True,
                    "skippable": {"pii_redact": {"skippable": True, "downgrade_to": "off"}},
                }
            }
        )
        tok = mint_token(AUTH_ID, ["guardrail:skip:pii_redact"], 60)
        d = resolve_guardrail_decision(_data_with_token(tok), _user_key())
        assert d == {"pii_redact": "off"}

    def test_list_headers_supported(self):
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        tok = mint_token(AUTH_ID, ["guardrail:skip:keyword"], 60)
        data = {"metadata": {"headers": [(b"x-airlock-capability", tok.encode())]}}
        d = resolve_guardrail_decision(data, _user_key())
        assert d == {"keyword": "observe"}

    def test_decision_cached(self):
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        tok = mint_token(AUTH_ID, ["guardrail:skip:keyword"], 60)
        data = _data_with_token(tok)
        resolve_guardrail_decision(data, _user_key())
        assert data["metadata"]["airlock_guardrail_decision"] == {"keyword": "observe"}


class TestEffectiveMode:
    def test_defaults_enforce(self):
        assert effective_mode({"metadata": {}}, "keyword") == "enforce"

    def test_reads_decision(self):
        data = {"metadata": {"airlock_guardrail_decision": {"keyword": "observe"}}}
        assert effective_mode(data, "keyword") == "observe"
        assert effective_mode(data, "pii_redact") == "enforce"


class TestKeywordGuardHonorsDecision:
    async def test_observe_does_not_block(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_KW_ENABLED", "1")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "topsecret")
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard

        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "the topsecret plan"}],
            "metadata": {"airlock_guardrail_decision": {"keyword": "observe"}},
        }
        # observe -> scanned + logged but NOT blocked
        out = await guard.async_pre_call_hook(None, None, data, "completion")
        assert out is data

    async def test_enforce_blocks(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_KW_ENABLED", "1")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "topsecret")
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard

        guard = AirlockKeywordGuard()
        data = {"messages": [{"role": "user", "content": "the topsecret plan"}]}
        with pytest.raises(ValueError):
            await guard.async_pre_call_hook(None, None, data, "completion")
