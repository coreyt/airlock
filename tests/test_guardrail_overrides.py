"""Tests for the guardrail-skip resolver (Pack 0.5.0-ADM-skip, CC-10/CC-11)."""

from __future__ import annotations

import pytest

from airlock.admin.tokens import mint_token
from airlock.guardrails import overrides
from airlock.guardrails.overrides import (
    configure_guardrail_overrides,
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

    def test_decision_stamped(self):
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        tok = mint_token(AUTH_ID, ["guardrail:skip:keyword"], 60)
        data = _data_with_token(tok)
        resolve_guardrail_decision(data, _user_key())
        assert data["metadata"]["airlock_guardrail_decision"] == {"keyword": "observe"}



class TestKeywordGuardHonorsDecision:
    async def test_observe_does_not_block(self, monkeypatch):
        # A REAL capability token (not an injected decision) downgrades to observe.
        monkeypatch.setenv("AIRLOCK_KW_ENABLED", "1")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "topsecret")
        configure_guardrail_overrides(
            {"guardrail_overrides": {"allow_capability_skip": True}}
        )
        tok = mint_token(AUTH_ID, ["guardrail:skip:keyword"], 60)
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard

        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "the topsecret plan"}],
            "headers": {"x-airlock-capability": tok},
        }
        out = await guard.async_pre_call_hook(
            {"api_key": AUTH_KEY}, None, data, "completion"
        )
        assert out is data  # observe -> scanned + logged but not blocked

    async def test_enforce_blocks(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_KW_ENABLED", "1")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "topsecret")
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard

        guard = AirlockKeywordGuard()
        data = {"messages": [{"role": "user", "content": "the topsecret plan"}]}
        with pytest.raises(ValueError):
            await guard.async_pre_call_hook(None, None, data, "completion")


class TestAdmSkipFix1Security:
    """The CRITICAL metadata-injection bypass + crash hardening (review BLOCK)."""

    def test_injected_decision_is_ignored(self):
        # Feature ON but client injects a decision with NO token -> must be wiped.
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        data = {
            "metadata": {"airlock_guardrail_decision": {"keyword": "observe", "pii_redact": "off"}},
            "headers": {},
        }
        d = resolve_guardrail_decision(data, _user_key())
        assert d == {}  # injection overwritten by the verified (empty) result
        assert data["metadata"]["airlock_guardrail_decision"] == {}

    def test_injected_decision_ignored_even_when_feature_off(self):
        configure_guardrail_overrides({})  # off by default
        data = {"metadata": {"airlock_guardrail_decision": {"keyword": "observe"}}}
        assert resolve_guardrail_decision(data, _user_key()) == {}

    async def test_keyword_guard_blocks_despite_injection(self, monkeypatch):
        # The end-to-end bypass: client injects the decision, no token -> still blocked.
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        monkeypatch.setenv("AIRLOCK_KW_ENABLED", "1")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "topsecret")
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard

        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "the topsecret plan"}],
            "metadata": {"airlock_guardrail_decision": {"keyword": "observe"}},
        }
        with pytest.raises(ValueError):  # injection ignored -> still enforced
            await guard.async_pre_call_hook({"api_key": AUTH_KEY}, None, data, "completion")

    def test_malformed_utf8_header_no_crash(self):
        configure_guardrail_overrides({"guardrail_overrides": {"allow_capability_skip": True}})
        data = {"metadata": {"headers": [(b"x-airlock-capability", b"\xff\xfe bad")]}}
        # must not raise UnicodeDecodeError; invalid token -> no skip
        assert resolve_guardrail_decision(data, _user_key()) == {}
