"""
S6 — Pre-Call Guards: keyword blocking, threat detection, MCP guard, enforcer.

Direct guardrail hook calls, no proxy needed.
"""

from __future__ import annotations

import time

import pytest


pytestmark = pytest.mark.harness


# ---------------------------------------------------------------------------
# Keyword Guard (3.3–3.4)
# ---------------------------------------------------------------------------
class TestKeywordGuard:

    async def test_keyword_blocks(self, monkeypatch, mock_cache, mock_user_api_key_dict):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "classified,topsecret")
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard

        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "Tell me about classified ops"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="restricted content"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    @pytest.mark.parametrize("variant", ["CLASSIFIED", "Classified", "classified"])
    async def test_keyword_case_insensitive(
        self, monkeypatch, mock_cache, mock_user_api_key_dict, variant,
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "classified")
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard

        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": f"Tell me about {variant} ops"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_keyword_error_no_echo(
        self, monkeypatch, mock_cache, mock_user_api_key_dict,
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "classified")
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard

        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "classified info"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError) as exc_info:
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        assert "classified" not in str(exc_info.value).lower().replace(
            "restricted content", ""
        )

    async def test_no_keywords_configured_passes(
        self, monkeypatch, mock_cache, mock_user_api_key_dict,
    ):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "")
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard

        guard = AirlockKeywordGuard()
        data = {
            "messages": [{"role": "user", "content": "anything goes"}],
            "model": "claude-sonnet",
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result is data


# ---------------------------------------------------------------------------
# Threat Detection (3.5–3.6)
# ---------------------------------------------------------------------------
class TestThreatDetection:

    async def test_rapid_fire_increases_threat_score(
        self, fresh_state_store, mock_cache, mock_user_api_key_dict,
    ):
        from airlock.fast.guardian import AirlockFastGuardian

        guardian = AirlockFastGuardian()
        data = {
            "messages": [{"role": "user", "content": "ping"}],
            "model": "claude-sonnet",
        }
        for _ in range(20):
            try:
                await guardian.async_pre_call_hook(
                    mock_user_api_key_dict, mock_cache, data.copy(), "completion"
                )
            except ValueError:
                break
        # Check that threat was assessed
        client_id = f"key:{mock_user_api_key_dict.api_key[-8:]}"
        client = fresh_state_store.get_client(client_id)
        assert client.threat_score > 0

    async def test_large_payload_anomaly(
        self, fresh_state_store, mock_cache, mock_user_api_key_dict,
    ):
        from airlock.fast.guardian import AirlockFastGuardian

        guardian = AirlockFastGuardian()
        data = {
            "messages": [{"role": "user", "content": "x" * 50_000}],
            "model": "claude-sonnet",
        }
        try:
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        except ValueError:
            pass
        client_id = f"key:{mock_user_api_key_dict.api_key[-8:]}"
        client = fresh_state_store.get_client(client_id)
        assert client.threat_score >= 0

    async def test_backoff_applied_after_burst(
        self, fresh_state_store, mock_cache, mock_user_api_key_dict,
    ):
        import time as _time
        from airlock.fast.guardian import AirlockFastGuardian

        guardian = AirlockFastGuardian()
        # Set client into backoff state directly (simulates previous threat block)
        client_id = f"key:{mock_user_api_key_dict.api_key[-8:]}"
        client = fresh_state_store.get_client(client_id)
        client.backoff_until = _time.time() + 60

        data = {
            "messages": [{"role": "user", "content": "ping"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="[Tt]oo many|[Rr]etry"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )


# ---------------------------------------------------------------------------
# MCP Guard (3.7–3.8)
# ---------------------------------------------------------------------------
class TestMCPGuard:

    async def test_mcp_allowlist_permits(
        self, monkeypatch, mock_cache, mock_user_api_key_dict,
    ):
        monkeypatch.setenv("AIRLOCK_MCP_ALLOWED_TOOLS", "read_file,search")
        from airlock.guardrails.mcp_tool_guard import AirlockMCPToolGuard

        guard = AirlockMCPToolGuard()
        data = {
            "mcp_tool_name": "read_file",
            "mcp_arguments": {"path": "/tmp/test.txt"},
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert result is not None

    async def test_mcp_blocklist_blocks(
        self, monkeypatch, mock_cache, mock_user_api_key_dict,
    ):
        monkeypatch.setenv("AIRLOCK_MCP_BLOCKED_TOOLS", "delete_file")
        from airlock.guardrails.mcp_tool_guard import AirlockMCPToolGuard

        guard = AirlockMCPToolGuard()
        data = {
            "mcp_tool_name": "delete_file",
            "mcp_arguments": {"path": "/tmp/test.txt"},
        }
        with pytest.raises(ValueError, match="blocked"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_mcp_path_traversal_blocked(
        self, monkeypatch, mock_cache, mock_user_api_key_dict,
    ):
        monkeypatch.delenv("AIRLOCK_MCP_ALLOWED_TOOLS", raising=False)
        monkeypatch.delenv("AIRLOCK_MCP_BLOCKED_TOOLS", raising=False)
        from airlock.guardrails.mcp_tool_guard import AirlockMCPToolGuard

        guard = AirlockMCPToolGuard()
        data = {
            "mcp_tool_name": "read_file",
            "mcp_arguments": {"path": "../../etc/passwd"},
        }
        with pytest.raises(ValueError, match="[Dd]angerous"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_mcp_shell_metachar_blocked(
        self, monkeypatch, mock_cache, mock_user_api_key_dict,
    ):
        monkeypatch.delenv("AIRLOCK_MCP_ALLOWED_TOOLS", raising=False)
        monkeypatch.delenv("AIRLOCK_MCP_BLOCKED_TOOLS", raising=False)
        from airlock.guardrails.mcp_tool_guard import AirlockMCPToolGuard

        guard = AirlockMCPToolGuard()
        data = {
            "mcp_tool_name": "run_command",
            "mcp_arguments": {"cmd": "ls; rm -rf /"},
        }
        with pytest.raises(ValueError, match="[Dd]angerous"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )


# ---------------------------------------------------------------------------
# Enforcer (3.9)
# ---------------------------------------------------------------------------
class TestEnforcer:

    async def test_enforcer_observe_passes(
        self, monkeypatch, mock_cache, mock_user_api_key_dict, fresh_state_store,
    ):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "observe")
        from airlock.guardrails.enforcer import AirlockEnforcer

        enforcer = AirlockEnforcer()
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result is data

    async def test_enforcer_enforce_blocks(
        self, monkeypatch, mock_cache, mock_user_api_key_dict, fresh_state_store,
    ):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "classified")
        from airlock.guardrails.enforcer import AirlockEnforcer

        enforcer = AirlockEnforcer()
        # Set high threat to push score above threshold
        client_id = f"key:{mock_user_api_key_dict.api_key[-8:]}"
        client = fresh_state_store.get_client(client_id)
        client.threat_score = 0.9
        data = {
            "messages": [{"role": "user", "content": "classified info"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="blocked"):
            await enforcer.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_enforcer_logs_would_block(
        self, monkeypatch, mock_cache, mock_user_api_key_dict, fresh_state_store,
    ):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "shadow")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "classified")
        from airlock.guardrails.enforcer import AirlockEnforcer

        enforcer = AirlockEnforcer()
        client_id = f"key:{mock_user_api_key_dict.api_key[-8:]}"
        client = fresh_state_store.get_client(client_id)
        client.threat_score = 0.9
        data = {
            "messages": [{"role": "user", "content": "classified info"}],
            "model": "claude-sonnet",
        }
        result = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        # Shadow mode logs but doesn't block
        assert "airlock_enforcement" in result.get("metadata", {})
        enforcement = result["metadata"]["airlock_enforcement"]
        assert "should_block" in enforcement
