"""
S15 — Hooks: install, status, keyword blocking, edit protection.
"""

from __future__ import annotations

import json
from argparse import Namespace
from io import StringIO

import pytest


pytestmark = pytest.mark.harness


class TestHooksInstall:
    def test_install_creates_settings(self, tmp_path):
        from airlock.cli.hooks_cmd import run_install

        args = Namespace(dir=str(tmp_path), force=False)
        run_install(args)
        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings

    def test_install_merge_not_replace(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(json.dumps({"existing_key": "preserved"}))

        from airlock.cli.hooks_cmd import run_install

        args = Namespace(dir=str(tmp_path), force=True)
        run_install(args)
        settings = json.loads(settings_path.read_text())
        assert settings.get("existing_key") == "preserved"
        assert "hooks" in settings

    def test_install_all_hook_types(self, tmp_path):
        from airlock.cli.hooks_cmd import run_install

        args = Namespace(dir=str(tmp_path), force=False)
        run_install(args)
        settings_path = tmp_path / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        hooks = settings["hooks"]
        for hook_type in [
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
        ]:
            assert hook_type in hooks, f"Missing hook type: {hook_type}"


class TestHooksStatus:
    def test_status_shows_hooks(self, tmp_path, capsys):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {"hooks": [{"type": "command", "command": "test"}]}
                        ],
                    }
                }
            )
        )
        from airlock.cli.hooks_cmd import run_status

        args = Namespace(dir=str(tmp_path))
        run_status(args)
        output = capsys.readouterr().out
        assert "SessionStart" in output


class TestSessionStartHook:
    def test_probes_health(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "airlock.hooks.session_start.probe_health", lambda *a, **kw: True
        )
        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        from airlock.hooks.session_start import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert "running" in out.get("additionalContext", "").lower()

    def test_proxy_down(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "airlock.hooks.session_start.probe_health", lambda *a, **kw: False
        )
        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        from airlock.hooks.session_start import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out)
        context = out.get("additionalContext", "").lower()
        assert (
            "not running" in context
            or "not reachable" in context
            or "stopped" in context
        )


class TestKeywordHook:
    def test_keyword_blocks_prompt(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "classified,topsecret")
        hook_input = json.dumps({"prompt": "Tell me about classified operations"})
        monkeypatch.setattr("sys.stdin", StringIO(hook_input))
        from airlock.hooks.pre_submit import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2  # block exit code

    @pytest.mark.parametrize("variant", ["CLASSIFIED", "Classified", "classified"])
    def test_keyword_case_insensitive(self, monkeypatch, variant):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "classified")
        hook_input = json.dumps({"prompt": f"Tell me about {variant}"})
        monkeypatch.setattr("sys.stdin", StringIO(hook_input))
        from airlock.hooks.pre_submit import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2


class TestEditProtection:
    def test_edit_protection_blocks_protected_file(self, monkeypatch):
        hook_input = json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/project/.env", "content": "KEY=val"},
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(hook_input))
        from airlock.hooks.pre_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_edit_protection_passes_clean_file(self, monkeypatch):
        hook_input = json.dumps(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/project/src/main.py",
                    "content": "print('hi')",
                },
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(hook_input))
        from airlock.hooks.pre_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
