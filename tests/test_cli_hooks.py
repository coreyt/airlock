"""Tests for airlock hooks/dogfood CLI subcommands."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from airlock.cli.main import main


# ===================================================================
# airlock hooks install
# ===================================================================


class TestHooksInstall:
    def test_creates_settings_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main(["hooks", "install", "--dir", str(tmp_path)])
        settings = tmp_path / ".claude" / "settings.json"
        assert settings.is_file()
        data = json.loads(settings.read_text())
        assert "hooks" in data
        assert "SessionStart" in data["hooks"]
        assert "UserPromptSubmit" in data["hooks"]
        assert "PreToolUse" in data["hooks"]
        assert "PostToolUse" in data["hooks"]

    def test_merges_into_existing_settings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {"permissions": {"allow": ["Read"]}}
        (claude_dir / "settings.json").write_text(json.dumps(existing))

        main(["hooks", "install", "--dir", str(tmp_path)])

        data = json.loads((claude_dir / "settings.json").read_text())
        assert data["permissions"] == {"allow": ["Read"]}
        assert "hooks" in data

    def test_refuses_overwrite_without_force(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {}}))

        with pytest.raises(SystemExit) as exc_info:
            main(["hooks", "install", "--dir", str(tmp_path)])
        assert exc_info.value.code == 1
        assert "force" in capsys.readouterr().err.lower()

    def test_force_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"old": []}}))

        main(["hooks", "install", "--dir", str(tmp_path), "--force"])

        data = json.loads((claude_dir / "settings.json").read_text())
        assert "old" not in data["hooks"]
        assert "SessionStart" in data["hooks"]

    def test_prints_summary(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        main(["hooks", "install", "--dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert "SessionStart" in out
        assert "PostToolUse" in out
        assert "Next steps" in out


# ===================================================================
# airlock hooks status
# ===================================================================


class TestHooksStatus:
    def test_shows_configured_hooks(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # Install first, then check status
        main(["hooks", "install", "--dir", str(tmp_path)])
        capsys.readouterr()  # clear output

        main(["hooks", "status", "--dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert "SessionStart" in out
        assert "pre_submit" in out
        assert "pre_tool" in out
        assert "(async)" in out

    def test_no_settings_exits_1(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["hooks", "status", "--dir", str(tmp_path)])
        assert exc_info.value.code == 1
        assert "No .claude/settings.json" in capsys.readouterr().err


# ===================================================================
# airlock hooks (no subcommand)
# ===================================================================


class TestHooksNoSubcommand:
    def test_prints_help(self, capsys):
        main(["hooks"])
        out = capsys.readouterr().out
        assert "install" in out or "status" in out


# ===================================================================
# airlock dogfood
# ===================================================================


class TestDogfood:
    def test_prints_export_lines(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "sk-airlock-test")
        # Mock health check to avoid network call
        monkeypatch.setattr("airlock.cli.dogfood_cmd._probe_health", lambda *a: True)

        main(["dogfood"])
        out = capsys.readouterr().out
        assert "ANTHROPIC_BASE_URL" in out
        assert "http://localhost:4000" in out
        assert "ANTHROPIC_AUTH_TOKEN" in out
        assert "sk-airlock-test" in out

    def test_warns_when_proxy_down(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)
        monkeypatch.setattr("airlock.cli.dogfood_cmd._probe_health", lambda *a: False)

        main(["dogfood"])
        err = capsys.readouterr().err
        assert "not reachable" in err

    def test_no_auth_token_without_master_key(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)
        monkeypatch.setattr("airlock.cli.dogfood_cmd._probe_health", lambda *a: True)

        main(["dogfood"])
        out = capsys.readouterr().out
        assert "ANTHROPIC_BASE_URL" in out
        assert "ANTHROPIC_AUTH_TOKEN" not in out

    def test_fish_shell_syntax(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "sk-test")
        monkeypatch.setattr("airlock.cli.dogfood_cmd._probe_health", lambda *a: True)

        main(["dogfood", "--shell", "fish"])
        out = capsys.readouterr().out
        assert "set -gx ANTHROPIC_BASE_URL" in out

    def test_custom_host_port(self, monkeypatch, capsys):
        monkeypatch.delenv("AIRLOCK_HOST", raising=False)
        monkeypatch.delenv("AIRLOCK_PORT", raising=False)
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)
        monkeypatch.setattr("airlock.cli.dogfood_cmd._probe_health", lambda *a: True)

        main(["dogfood", "--host", "10.0.0.1", "--port", "8080"])
        out = capsys.readouterr().out
        assert "http://10.0.0.1:8080" in out


# ===================================================================
# main.py routing
# ===================================================================


class TestMainRouting:
    def test_help_lists_hooks_and_dogfood(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "hooks" in out
        assert "dogfood" in out

    def test_hooks_routes_to_install(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with mock.patch("airlock.cli.hooks_cmd.run_install") as m:
            main(["hooks", "install", "--dir", str(tmp_path)])
        m.assert_called_once()

    def test_hooks_routes_to_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with mock.patch("airlock.cli.hooks_cmd.run_status") as m:
            main(["hooks", "status", "--dir", str(tmp_path)])
        m.assert_called_once()

    def test_dogfood_routes_to_cmd(self, monkeypatch):
        with mock.patch("airlock.cli.dogfood_cmd.run") as m:
            main(["dogfood"])
        m.assert_called_once()
