"""Tests for the advisor agent loop."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from airlock.advisor.agent import run_advisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(body: dict, status: int = 200):
    """Create a mock urllib response."""
    mock = MagicMock()
    mock.status = status
    mock.read.return_value = json.dumps(body).encode("utf-8")
    mock.getheaders.return_value = []
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _chat_response(content: str, tool_calls: list | None = None):
    """Build an OpenAI-format chat completion response."""
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [
            {
                "message": msg,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
    }


def _tool_call(name: str, arguments: dict, call_id: str = "call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


@pytest.fixture()
def tmp_config(tmp_path):
    """Write a minimal config.yaml with a local model and return its path."""
    cfg = {
        "model_list": [
            {
                "model_name": "test-local",
                "litellm_params": {
                    "model": "test-local",
                    "api_base": "http://localhost:11434",
                },
            }
        ]
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg))
    return str(p)


@pytest.fixture()
def tmp_log_dir(log_dir):
    """Wrap conftest log_dir as a str path for run_advisor()."""
    return str(log_dir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSimpleQuestion:
    @patch("airlock.advisor.agent.urllib.request.urlopen")
    def test_simple_question_no_tools(self, mock_urlopen, tmp_config, tmp_log_dir):
        mock_urlopen.return_value = _mock_response(
            _chat_response("Everything looks healthy.")
        )

        result = run_advisor(
            "How is the system?",
            config_path=tmp_config,
            log_dir=tmp_log_dir,
        )

        assert result.answer == "Everything looks healthy."
        assert result.tool_calls_made == []
        assert result.error is None
        assert result.model_used == "test-local"
        assert result.is_local is True
        assert result.iterations == 1


class TestToolCalls:
    @patch("airlock.advisor.agent.urllib.request.urlopen")
    def test_tool_call_then_answer(self, mock_urlopen, tmp_config, tmp_log_dir):
        # First call: LLM requests a tool call
        first_resp = _chat_response(
            "",
            tool_calls=[_tool_call("get_state_snapshot", {})],
        )
        # Second call: LLM gives final answer
        second_resp = _chat_response("System is healthy based on snapshot.")

        mock_urlopen.side_effect = [
            _mock_response(first_resp),
            _mock_response(second_resp),
        ]

        result = run_advisor(
            "What is the system state?",
            config_path=tmp_config,
            log_dir=tmp_log_dir,
        )

        assert result.answer == "System is healthy based on snapshot."
        assert "get_state_snapshot" in result.tool_calls_made
        assert result.iterations == 2
        assert result.error is None

    @patch("airlock.advisor.agent.urllib.request.urlopen")
    def test_multiple_tool_calls(self, mock_urlopen, tmp_config, tmp_log_dir):
        # LLM requests two tools at once
        first_resp = _chat_response(
            "",
            tool_calls=[
                _tool_call("get_state_snapshot", {}, "call_1"),
                _tool_call("get_circuit_health", {}, "call_2"),
            ],
        )
        second_resp = _chat_response("Both checks passed.")

        mock_urlopen.side_effect = [
            _mock_response(first_resp),
            _mock_response(second_resp),
        ]

        result = run_advisor(
            "Check everything",
            config_path=tmp_config,
            log_dir=tmp_log_dir,
        )

        assert "get_state_snapshot" in result.tool_calls_made
        assert "get_circuit_health" in result.tool_calls_made
        assert len(result.tool_calls_made) == 2
        assert result.answer == "Both checks passed."


class TestIterationLimit:
    @patch("airlock.advisor.agent.urllib.request.urlopen")
    def test_max_iterations_respected(self, mock_urlopen, tmp_config, tmp_log_dir):
        # LLM always returns a tool call -- never stops
        always_tool = _chat_response(
            "thinking...",
            tool_calls=[_tool_call("get_state_snapshot", {})],
        )
        mock_urlopen.return_value = _mock_response(always_tool)

        result = run_advisor(
            "Keep going forever",
            config_path=tmp_config,
            log_dir=tmp_log_dir,
            max_iterations=3,
        )

        assert result.iterations == 3
        # Should have a non-empty answer from the last content or fallback
        assert result.answer


class TestActionParsing:
    @patch("airlock.advisor.agent.urllib.request.urlopen")
    def test_action_block_parsed(self, mock_urlopen, tmp_config, tmp_log_dir):
        text = (
            'I recommend this change. ACTION: {"type": "config_change", '
            '"key": "threat_threshold", "value": 0.8}'
        )
        mock_urlopen.return_value = _mock_response(_chat_response(text))

        result = run_advisor(
            "Fix threat detection",
            config_path=tmp_config,
            log_dir=tmp_log_dir,
        )

        assert len(result.actions_proposed) == 1
        assert result.actions_proposed[0]["type"] == "config_change"


class TestErrorHandling:
    @patch("airlock.advisor.agent.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen, tmp_config, tmp_log_dir):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        result = run_advisor(
            "Hello",
            config_path=tmp_config,
            log_dir=tmp_log_dir,
        )

        assert result.error is not None
        assert "Connection refused" in result.error

    @patch("airlock.advisor.agent.urllib.request.urlopen")
    def test_tool_error_continues(self, mock_urlopen, tmp_config, tmp_log_dir):
        # First call: LLM requests a tool that will fail
        first_resp = _chat_response(
            "",
            tool_calls=[_tool_call("get_config", {"config_path": "/nonexistent"})],
        )
        # Second call: LLM gives answer despite tool error
        second_resp = _chat_response("Could not read config but here is advice.")

        mock_urlopen.side_effect = [
            _mock_response(first_resp),
            _mock_response(second_resp),
        ]

        result = run_advisor(
            "Show config",
            config_path=tmp_config,
            log_dir=tmp_log_dir,
        )

        assert result.answer == "Could not read config but here is advice."
        assert "get_config" in result.tool_calls_made
        assert result.error is None

    def test_local_only_no_model_error(self, tmp_path, tmp_log_dir):
        # Config with only remote models
        cfg = {
            "model_list": [
                {
                    "model_name": "gpt-4",
                    "litellm_params": {"model": "gpt-4"},
                }
            ]
        }
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.dump(cfg))

        result = run_advisor(
            "Hello",
            config_path=str(cfg_path),
            log_dir=tmp_log_dir,
            local_only=True,
        )

        assert result.error is not None
        assert "local" in result.error.lower()


class TestAuditLogging:
    @patch("airlock.advisor.agent.urllib.request.urlopen")
    def test_audit_logged(self, mock_urlopen, tmp_config, tmp_log_dir):
        mock_urlopen.return_value = _mock_response(_chat_response("All good."))

        run_advisor(
            "Status check",
            config_path=tmp_config,
            log_dir=tmp_log_dir,
        )

        audit_path = Path(tmp_log_dir) / "advisor-audit.jsonl"
        assert audit_path.exists()
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[-1])
        assert record["action_type"] == "query"
        assert record["outcome"] == "success"
        assert record["model_used"] == "test-local"
