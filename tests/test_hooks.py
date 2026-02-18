"""Tests for airlock.hooks — Claude Code hook scripts and _common utilities."""

from __future__ import annotations

import http.server
import json
import threading
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

from airlock.hooks._common import (
    block,
    get_blocked_keywords,
    probe_health,
    proceed,
    read_hook_input,
    respond_json,
)


# ===================================================================
# _common.py
# ===================================================================


class TestReadHookInput:
    def test_parses_json_from_stdin(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", StringIO('{"prompt": "hello"}'))
        result = read_hook_input()
        assert result == {"prompt": "hello"}

    def test_raises_on_invalid_json(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", StringIO("not json"))
        with pytest.raises(json.JSONDecodeError):
            read_hook_input()


class TestBlock:
    def test_exits_with_code_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            block("denied")
        assert exc_info.value.code == 2
        assert "denied" in capsys.readouterr().err


class TestProceed:
    def test_exits_with_code_0(self):
        with pytest.raises(SystemExit) as exc_info:
            proceed()
        assert exc_info.value.code == 0


class TestRespondJson:
    def test_outputs_json_to_stdout(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            respond_json({"key": "value"})
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert json.loads(out) == {"key": "value"}


class TestGetBlockedKeywords:
    def test_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_BLOCKED_KEYWORDS", raising=False)
        assert get_blocked_keywords() == []

    def test_parses_comma_separated(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret, Project X ,alpha")
        result = get_blocked_keywords()
        assert result == ["secret", "project x", "alpha"]

    def test_ignores_empty_entries(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "foo,,bar,")
        assert get_blocked_keywords() == ["foo", "bar"]


class TestProbeHealth:
    @pytest.fixture()
    def health_server(self):
        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")

            def log_message(self, *_args):  # noqa: ANN002
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield "127.0.0.1", str(port)
        server.shutdown()

    def test_returns_true_when_healthy(self, health_server):
        host, port = health_server
        assert probe_health(host, port) is True

    def test_returns_false_when_unreachable(self):
        assert probe_health("127.0.0.1", "19999", timeout=1) is False


# ===================================================================
# session_start.py
# ===================================================================


class TestSessionStart:
    def test_reports_proxy_running(self, monkeypatch, capsys):
        monkeypatch.setattr("airlock.hooks.session_start.probe_health", lambda *a, **kw: True)
        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")

        from airlock.hooks.session_start import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert "running" in out["additionalContext"]

    def test_reports_proxy_down(self, monkeypatch, capsys):
        monkeypatch.setattr("airlock.hooks.session_start.probe_health", lambda *a, **kw: False)
        monkeypatch.setenv("AIRLOCK_HOST", "localhost")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")

        from airlock.hooks.session_start import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert "NOT reachable" in out["additionalContext"]


# ===================================================================
# pre_submit.py
# ===================================================================


class TestPreSubmit:
    def test_proceeds_when_no_keywords(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_BLOCKED_KEYWORDS", raising=False)

        from airlock.hooks.pre_submit import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_proceeds_when_clean(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret")
        monkeypatch.setattr("sys.stdin", StringIO('{"prompt": "hello world"}'))

        from airlock.hooks.pre_submit import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_blocks_on_keyword_match(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "secret,classified")
        monkeypatch.setattr("sys.stdin", StringIO('{"prompt": "tell me the Secret plan"}'))

        from airlock.hooks.pre_submit import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "restricted content" in err
        # Should not echo the keyword back
        assert "secret" not in err.lower().replace("restricted", "")

    def test_case_insensitive_matching(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "Project X")
        monkeypatch.setattr("sys.stdin", StringIO('{"prompt": "what about PROJECT X?"}'))

        from airlock.hooks.pre_submit import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2


# ===================================================================
# pre_tool.py
# ===================================================================


class TestPreTool:
    def test_blocks_env_file(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.stdin",
            StringIO(json.dumps({"tool_input": {"file_path": "/project/.env"}})),
        )
        monkeypatch.delenv("AIRLOCK_PROTECTED_PATHS", raising=False)

        from airlock.hooks.pre_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
        assert ".env" in capsys.readouterr().err

    def test_blocks_config_yaml(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.stdin",
            StringIO(json.dumps({"tool_input": {"file_path": "/project/config.yaml"}})),
        )
        monkeypatch.delenv("AIRLOCK_PROTECTED_PATHS", raising=False)

        from airlock.hooks.pre_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2
        assert "config.yaml" in capsys.readouterr().err

    def test_allows_other_files(self, monkeypatch):
        monkeypatch.setattr(
            "sys.stdin",
            StringIO(json.dumps({"tool_input": {"file_path": "/project/src/app.py"}})),
        )
        monkeypatch.delenv("AIRLOCK_PROTECTED_PATHS", raising=False)

        from airlock.hooks.pre_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_custom_protected_paths(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_PROTECTED_PATHS", "secrets.json,credentials.yaml")
        monkeypatch.setattr(
            "sys.stdin",
            StringIO(json.dumps({"tool_input": {"file_path": "/x/secrets.json"}})),
        )

        from airlock.hooks.pre_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2

    def test_proceeds_when_no_file_path(self, monkeypatch):
        monkeypatch.setattr(
            "sys.stdin",
            StringIO(json.dumps({"tool_input": {}})),
        )
        monkeypatch.delenv("AIRLOCK_PROTECTED_PATHS", raising=False)

        from airlock.hooks.pre_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0


# ===================================================================
# post_tool.py
# ===================================================================


class TestPostTool:
    def test_writes_jsonl_log(self, monkeypatch, tmp_path):
        log_dir = tmp_path / "logs"
        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(log_dir))
        monkeypatch.setattr(
            "sys.stdin",
            StringIO(json.dumps({
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_output": "file1.py\nfile2.py",
            })),
        )

        from airlock.hooks.post_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

        log_files = list(log_dir.glob("claude-hooks-*.jsonl"))
        assert len(log_files) == 1

        record = json.loads(log_files[0].read_text().strip())
        assert record["tool_name"] == "Bash"
        assert record["hook"] == "PostToolUse"

    def test_truncates_large_output(self, monkeypatch, tmp_path):
        log_dir = tmp_path / "logs"
        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(log_dir))
        monkeypatch.setattr(
            "sys.stdin",
            StringIO(json.dumps({
                "tool_name": "Read",
                "tool_input": {},
                "tool_output": "x" * 5000,
            })),
        )

        from airlock.hooks.post_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

        record = json.loads(list(log_dir.glob("*.jsonl"))[0].read_text().strip())
        assert len(record["tool_output"]) < 3000
        assert "truncated" in record["tool_output"]

    def test_never_fails_on_bad_log_dir(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_LOG_DIR", "/nonexistent/readonly/path")
        monkeypatch.setattr(
            "sys.stdin",
            StringIO(json.dumps({"tool_name": "Bash"})),
        )

        from airlock.hooks.post_tool import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
