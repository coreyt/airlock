"""Tests for per-client paid-service authorization (#21, pack C-2).

The failure that matters is failing *open* — a policy that looks configured
but lets an unauthorized caller spend credits. Each way that could happen gets
an explicit test: unauthenticated callers, unrecognized aliases, the forgeable
client header, and enforcement mode.
"""

from __future__ import annotations

import pytest

from airlock.paid_services import (
    KNOWN_SERVICES,
    authorize,
    check_or_raise,
    classify_service,
    enforcement_enabled,
)


class TestServiceClassification:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("tavily-search", "tavily"),
            ("tavily/web-search", "tavily"),
            ("perplexity-sonar", "perplexity"),
            ("perplexity/sonar-deep-research", "perplexity"),
            # Config exposes bare "sonar" aliases too; an exact-match table
            # would fail open on these.
            ("perplexity-sonar-reasoning-pro", "perplexity"),
            ("newscatcher", "newscatcher"),
            ("NewsCatcher_search", "newscatcher"),
            ("gpt-4o", None),
            ("claude-opus-5", None),
            ("", None),
            (None, None),
        ],
    )
    def test_classification(self, name, expected):
        assert classify_service(name) == expected

    def test_every_known_service_classifies_itself(self):
        for service in KNOWN_SERVICES:
            assert classify_service(service) == service


class TestDefaultIsUnrestricted:
    def test_no_config_allows_everything(self, monkeypatch):
        """Today's behavior must be unchanged until an operator opts in."""
        for service in KNOWN_SERVICES:
            monkeypatch.delenv(
                f"AIRLOCK_PAID_SERVICE_ALLOW_{service.upper()}", raising=False
            )
        assert enforcement_enabled() is False
        decision = authorize("tavily-search", "key:abcd1234")
        assert decision.allowed is True
        assert decision.reason == "unrestricted"

    def test_non_paid_model_is_never_gated(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PAID_SERVICE_ALLOW_TAVILY", "key:nobody")
        decision = authorize("gpt-4o", None)
        assert decision.allowed is True
        assert decision.service is None


class TestAllowlistEnforcement:
    @pytest.fixture(autouse=True)
    def _allowlist(self, monkeypatch):
        monkeypatch.setenv(
            "AIRLOCK_PAID_SERVICE_ALLOW_TAVILY", "key:abcd1234, key:efgh5678"
        )

    def test_allowlisted_client_is_permitted(self):
        assert authorize("tavily-search", "key:abcd1234").allowed is True

    def test_whitespace_around_entries_is_tolerated(self):
        assert authorize("tavily-search", "key:efgh5678").allowed is True

    def test_other_client_is_refused(self):
        decision = authorize("tavily-search", "key:zzzz9999")
        assert decision.allowed is False
        assert decision.reason == "not_allowlisted"

    def test_unauthenticated_caller_is_refused_not_waved_through(self):
        """Failing open here would make the allowlist trivially bypassable."""
        decision = authorize("tavily-search", None)
        assert decision.allowed is False
        assert decision.reason == "unauthenticated"

    def test_a_service_without_its_own_allowlist_stays_unrestricted(self):
        """Configuring one service must not implicitly gate the others."""
        assert authorize("perplexity-sonar", "key:zzzz9999").allowed is True

    def test_empty_allowlist_refuses_everyone(self, monkeypatch):
        """An explicitly empty list means "nobody", not "everybody"."""
        monkeypatch.setenv("AIRLOCK_PAID_SERVICE_ALLOW_TAVILY", "")
        assert authorize("tavily-search", "key:abcd1234").allowed is False


class TestCheckOrRaise:
    def test_refusal_raises_permission_error(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_PAID_SERVICE_ALLOW_TAVILY", "key:abcd1234")
        with pytest.raises(PermissionError) as excinfo:
            check_or_raise("tavily-search", "key:zzzz9999")
        assert "tavily" in str(excinfo.value)

    def test_refusal_does_not_leak_the_allowlist(self, monkeypatch):
        """A refused caller must not learn which tenants *are* authorized."""
        monkeypatch.setenv(
            "AIRLOCK_PAID_SERVICE_ALLOW_TAVILY", "key:secret01,key:secret02"
        )
        with pytest.raises(PermissionError) as excinfo:
            check_or_raise("tavily-search", "key:zzzz9999")
        message = str(excinfo.value)
        assert "secret01" not in message
        assert "secret02" not in message

    def test_permitted_call_returns_the_decision(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_PAID_SERVICE_ALLOW_TAVILY", raising=False)
        decision = check_or_raise("tavily-search", "key:abcd1234")
        assert decision.allowed is True
        assert decision.service == "tavily"


class TestEnforcementPointWiring:
    """The gate must not be subject to the adaptive enforcement mode.

    `observe` means "score but don't block on heuristics". Authorization is a
    hard gate — if it honored observe mode, the policy would silently do
    nothing in the deployment's default configuration.
    """

    @pytest.fixture
    def enforcer(self):
        from airlock.guardrails.enforcer import AirlockEnforcer

        return AirlockEnforcer()

    async def test_denied_in_observe_mode(self, enforcer, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "observe")
        monkeypatch.setenv("AIRLOCK_PAID_SERVICE_ALLOW_TAVILY", "key:abcd1234")

        class Key:
            api_key = "sk-live-zzzz9999"

        with pytest.raises(PermissionError):
            await enforcer.async_pre_call_hook(
                Key(), None, {"model": "tavily-search"}, "completion"
            )

    async def test_allowed_request_is_stamped_with_the_decision(
        self, enforcer, monkeypatch
    ):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "observe")
        monkeypatch.setenv("AIRLOCK_PAID_SERVICE_ALLOW_TAVILY", "key:abcd1234")

        class Key:
            api_key = "sk-live-abcd1234"

        data: dict = {"model": "tavily-search"}
        await enforcer.async_pre_call_hook(Key(), None, data, "completion")
        stamp = data["metadata"]["airlock_paid_service"]
        assert stamp == {
            "service": "tavily",
            "allowed": True,
            "reason": "allowlisted",
        }

    async def test_ordinary_model_is_not_stamped(self, enforcer, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "observe")

        class Key:
            api_key = "sk-live-abcd1234"

        data: dict = {"model": "gpt-4o"}
        await enforcer.async_pre_call_hook(Key(), None, data, "completion")
        assert "airlock_paid_service" not in (data.get("metadata") or {})

    async def test_forgeable_client_header_cannot_grant_access(
        self, enforcer, monkeypatch
    ):
        """Authorizing on X-Airlock-Client would let anyone claim a tenant."""
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "observe")
        monkeypatch.setenv("AIRLOCK_PAID_SERVICE_ALLOW_TAVILY", "key:abcd1234")

        class Key:
            api_key = "sk-live-zzzz9999"

        data = {
            "model": "tavily-search",
            "metadata": {"headers": {"x-airlock-client": "key:abcd1234"}},
        }
        with pytest.raises(PermissionError):
            await enforcer.async_pre_call_hook(Key(), None, data, "completion")


class TestMCPPathIsGatedToo:
    async def test_newscatcher_tool_call_is_authorized(self, monkeypatch):
        from airlock.guardrails.mcp_tool_guard import AirlockMCPToolGuard

        monkeypatch.setenv("AIRLOCK_PAID_SERVICE_ALLOW_NEWSCATCHER", "key:abcd1234")
        guard = AirlockMCPToolGuard()

        class Key:
            api_key = "sk-live-zzzz9999"

        with pytest.raises(PermissionError):
            await guard.async_pre_call_hook(
                Key(),
                None,
                {"mcp_tool_name": "newscatcher_search"},
                "mcp_call",
            )

    async def test_unrelated_tool_is_untouched(self, monkeypatch):
        from airlock.guardrails.mcp_tool_guard import AirlockMCPToolGuard

        monkeypatch.setenv("AIRLOCK_PAID_SERVICE_ALLOW_NEWSCATCHER", "key:abcd1234")
        guard = AirlockMCPToolGuard()

        class Key:
            api_key = "sk-live-zzzz9999"

        data = {"mcp_tool_name": "read_file"}
        result = await guard.async_pre_call_hook(Key(), None, data, "mcp_call")
        assert result is data
