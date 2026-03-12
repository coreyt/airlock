"""
S3 — CLI: init, status, dogfood, analyze commands.

All mock mode (CLI calls, no proxy needed).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


pytestmark = pytest.mark.harness


class TestInit:
    def test_creates_config(self, tmp_path):
        from airlock.cli.main import main

        main(["init", "--dir", str(tmp_path)])
        assert (tmp_path / "config.yaml").exists()

    def test_creates_env(self, tmp_path):
        from airlock.cli.main import main

        main(["init", "--dir", str(tmp_path)])
        assert (tmp_path / ".env").exists()

    def test_creates_logs_dir(self, tmp_path):
        from airlock.cli.main import main

        main(["init", "--dir", str(tmp_path)])
        assert (tmp_path / "logs").is_dir()

    def test_idempotent(self, tmp_path, capsys):
        from airlock.cli.main import main

        main(["init", "--dir", str(tmp_path)])
        main(["init", "--dir", str(tmp_path)])
        output = capsys.readouterr().out
        assert "already exists" in output.lower() or "skip" in output.lower()

    def test_force_overwrites(self, tmp_path):
        from airlock.cli.main import main

        main(["init", "--dir", str(tmp_path)])
        main(["init", "--dir", str(tmp_path), "--force"])
        assert (tmp_path / "config.yaml").exists()


class TestStatus:
    def test_healthy_exits_0(self, monkeypatch):
        from airlock.cli.main import main

        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        with patch("urllib.request.urlopen", return_value=True):
            with pytest.raises(SystemExit) as exc_info:
                main(["status"])
            assert exc_info.value.code == 0

    def test_down_exits_1(self, monkeypatch):
        from airlock.cli.main import main

        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            with pytest.raises(SystemExit) as exc_info:
                main(["status"])
            assert exc_info.value.code == 1


class TestDogfood:
    def test_outputs_base_url(self, monkeypatch, capsys):
        from airlock.cli.main import main

        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "sk-test")
        main(["dogfood"])
        output = capsys.readouterr().out
        assert "ANTHROPIC_BASE_URL" in output

    def test_outputs_auth_token(self, monkeypatch, capsys):
        from airlock.cli.main import main

        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "sk-test")
        main(["dogfood"])
        output = capsys.readouterr().out
        assert "ANTHROPIC_AUTH_TOKEN" in output or "sk-test" in output


class TestAnalyze:
    def test_json_keys(self, populated_log_dir, capsys, monkeypatch):
        from airlock.cli.main import main

        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(populated_log_dir))
        main(["analyze", "--json"])
        output = capsys.readouterr().out
        data = json.loads(output)
        for key in ["optimizations", "cache_opportunities", "trends", "semantic_insights", "hypotheses"]:
            assert key in data

    def test_with_days_flag(self, populated_log_dir, capsys, monkeypatch):
        from airlock.cli.main import main

        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(populated_log_dir))
        main(["analyze", "--json", "--days", "1"])
        output = capsys.readouterr().out
        data = json.loads(output)
        assert isinstance(data, dict)
