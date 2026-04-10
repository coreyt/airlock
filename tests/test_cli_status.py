"""Tests for airlock.cli.status_cmd — proxy health check."""

from __future__ import annotations

import http.server
import threading
from argparse import Namespace

import pytest

from airlock.cli.status_cmd import _health_request, run


@pytest.fixture()
def health_server():
    """Start a minimal HTTP server that responds 200 on /health."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *_args):  # noqa: ANN002
            pass  # suppress request logs

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "127.0.0.1", port
    server.shutdown()


def test_status_healthy_exits_0(health_server, capsys) -> None:
    host, port = health_server
    with pytest.raises(SystemExit) as exc_info:
        run(Namespace(host=host, port=str(port)))
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "running" in out


def test_status_healthy_shows_host_port(health_server, capsys) -> None:
    host, port = health_server
    with pytest.raises(SystemExit) as exc_info:
        run(Namespace(host=host, port=str(port)))
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert host in out
    assert str(port) in out


def test_status_unreachable_exits_1(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        run(Namespace(host="127.0.0.1", port="19999"))
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "not reachable" in err


def test_status_defaults_to_localhost_4000(capsys, monkeypatch) -> None:
    monkeypatch.delenv("AIRLOCK_HOST", raising=False)
    monkeypatch.delenv("AIRLOCK_PORT", raising=False)

    # Force the probe to fail regardless of whether something is actually
    # listening on localhost:4000 in the test environment.
    def _boom(*_args, **_kwargs):
        raise OSError("simulated unreachable")

    monkeypatch.setattr("airlock.cli.status_cmd.urllib.request.urlopen", _boom)

    with pytest.raises(SystemExit) as exc_info:
        run(Namespace(host=None, port=None))
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "localhost" in err
    assert "4000" in err


def test_status_reads_env_vars(capsys, monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_HOST", "10.0.0.1")
    monkeypatch.setenv("AIRLOCK_PORT", "9999")
    with pytest.raises(SystemExit) as exc_info:
        run(Namespace(host=None, port=None))
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "10.0.0.1" in err
    assert "9999" in err


def test_status_flags_override_env(capsys, monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_HOST", "10.0.0.1")
    monkeypatch.setenv("AIRLOCK_PORT", "9999")
    with pytest.raises(SystemExit) as exc_info:
        run(Namespace(host="192.168.1.1", port="5555"))
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "192.168.1.1" in err
    assert "5555" in err


def test_health_request_adds_airlock_client_header(monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_CLIENT", "cli-status-test")
    req = _health_request("http://127.0.0.1:4000/health?client=cli-status")
    assert dict(req.header_items())["X-airlock-client"] == "cli-status-test"
