"""Tests for airlock.cli.dogfood_cmd — dogfood CLI command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from airlock.cli.dogfood_cmd import _quote_value, run


class TestQuoteValue:
    def test_bash_quoting(self):
        assert _quote_value("http://localhost:4000", "bash") == "'http://localhost:4000'"

    def test_fish_quoting(self):
        assert _quote_value("http://localhost:4000", "fish") == "'http://localhost:4000'"


class TestRunBash:
    def test_outputs_export_lines(self, capsys):
        args = SimpleNamespace(host="localhost", port="4000", master_key="sk-123", shell="bash")
        with patch("airlock.cli.dogfood_cmd._probe_health", return_value=True):
            run(args)
        out = capsys.readouterr().out
        assert "export ANTHROPIC_BASE_URL='http://localhost:4000'" in out
        assert "export ANTHROPIC_AUTH_TOKEN='sk-123'" in out

    def test_no_auth_token_when_no_key(self, capsys):
        args = SimpleNamespace(host="localhost", port="4000", master_key="", shell="bash")
        with patch("airlock.cli.dogfood_cmd._probe_health", return_value=True):
            run(args)
        out = capsys.readouterr().out
        assert "ANTHROPIC_BASE_URL" in out
        assert "ANTHROPIC_AUTH_TOKEN" not in out


class TestRunFish:
    def test_outputs_set_gx_lines(self, capsys):
        args = SimpleNamespace(host="localhost", port="4000", master_key="sk-key", shell="fish")
        with patch("airlock.cli.dogfood_cmd._probe_health", return_value=True):
            run(args)
        out = capsys.readouterr().out
        assert "set -gx ANTHROPIC_BASE_URL" in out
        assert "set -gx ANTHROPIC_AUTH_TOKEN" in out


class TestRunHealthWarning:
    def test_warns_when_proxy_unreachable(self, capsys):
        args = SimpleNamespace(host="localhost", port="4000", master_key="", shell="bash")
        with patch("airlock.cli.dogfood_cmd._probe_health", return_value=False):
            run(args)
        err = capsys.readouterr().err
        assert "Warning" in err
        assert "not reachable" in err
