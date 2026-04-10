"""Tests for ``airlock advise`` CLI subcommand."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from airlock.advisor.agent import AdvisorResult
from airlock.cli.advise_cmd import _resolve_host_port


def _make_result(**kwargs) -> AdvisorResult:
    defaults = {
        "answer": "",
        "tool_calls_made": [],
        "actions_proposed": [],
        "model_used": "local-test",
        "is_local": True,
        "iterations": 1,
        "error": None,
    }
    defaults.update(kwargs)
    return AdvisorResult(**defaults)


class TestAdviseSubparser:
    def test_advise_subparser_registered(self):
        from airlock.cli.main import main

        with pytest.raises(SystemExit) as exc_info:
            main(["advise", "--help"])
        assert exc_info.value.code == 0


class TestAdviseRun:
    def test_run_prints_answer(self, capsys):
        from airlock.cli.advise_cmd import run

        result = _make_result(answer="The answer is 42.")
        args = SimpleNamespace(
            question="What is the answer?",
            host=None,
            port=None,
            model=None,
            local_only=False,
            interactive=False,
        )
        with patch("airlock.cli.advise_cmd.run_advisor", return_value=result):
            run(args)

        captured = capsys.readouterr()
        assert "The answer is 42." in captured.out

    def test_run_local_only_propagated(self, capsys):
        from airlock.cli.advise_cmd import run

        result = _make_result(answer="ok")
        args = SimpleNamespace(
            question="test",
            host=None,
            port=None,
            model=None,
            local_only=True,
            interactive=False,
        )
        with patch(
            "airlock.cli.advise_cmd.run_advisor", return_value=result
        ) as mock_advisor:
            run(args)

        mock_advisor.assert_called_once_with(
            "test",
            proxy_host="localhost",
            proxy_port="4000",
            model=None,
            local_only=True,
        )

    def test_run_model_propagated(self, capsys):
        from airlock.cli.advise_cmd import run

        result = _make_result(answer="ok")
        args = SimpleNamespace(
            question="test",
            host=None,
            port=None,
            model="mymodel",
            local_only=False,
            interactive=False,
        )
        with patch(
            "airlock.cli.advise_cmd.run_advisor", return_value=result
        ) as mock_advisor:
            run(args)

        mock_advisor.assert_called_once_with(
            "test",
            proxy_host="localhost",
            proxy_port="4000",
            model="mymodel",
            local_only=False,
        )

    def test_run_error_exits_1(self, capsys):
        from airlock.cli.advise_cmd import run

        result = _make_result(error="something went wrong")
        args = SimpleNamespace(
            question="test",
            host=None,
            port=None,
            model=None,
            local_only=False,
            interactive=False,
        )
        with patch("airlock.cli.advise_cmd.run_advisor", return_value=result):
            with pytest.raises(SystemExit) as exc_info:
                run(args)
            assert exc_info.value.code == 1

    def test_run_remote_warning(self, capsys):
        from airlock.cli.advise_cmd import run

        result = _make_result(answer="ok", is_local=False, model_used="gpt-4")
        args = SimpleNamespace(
            question="test",
            host=None,
            port=None,
            model=None,
            local_only=False,
            interactive=False,
        )
        with patch("airlock.cli.advise_cmd.run_advisor", return_value=result):
            run(args)

        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_no_question_exits_1(self, capsys):
        from airlock.cli.advise_cmd import run

        args = SimpleNamespace(
            question=None, model=None, local_only=False, interactive=False
        )
        with pytest.raises(SystemExit) as exc_info:
            run(args)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _resolve_host_port
# ---------------------------------------------------------------------------


class TestResolveHostPort:
    def test_resolve_host_port_defaults(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_HOST", raising=False)
        monkeypatch.delenv("AIRLOCK_PORT", raising=False)
        args = SimpleNamespace(host=None, port=None)
        host, port = _resolve_host_port(args)
        assert host == "localhost"
        assert port == "4000"

    def test_resolve_host_port_from_args(self):
        args = SimpleNamespace(host="myhost", port="8080")
        host, port = _resolve_host_port(args)
        assert host == "myhost"
        assert port == "8080"

    def test_resolve_host_port_from_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_HOST", "envhost")
        monkeypatch.setenv("AIRLOCK_PORT", "9000")
        args = SimpleNamespace(host=None, port=None)
        host, port = _resolve_host_port(args)
        assert host == "envhost"
        assert port == "9000"

    def test_resolve_host_port_args_override_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_HOST", "envhost")
        monkeypatch.setenv("AIRLOCK_PORT", "9000")
        args = SimpleNamespace(host="arghost", port="7070")
        host, port = _resolve_host_port(args)
        assert host == "arghost"
        assert port == "7070"
