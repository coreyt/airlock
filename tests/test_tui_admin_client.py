"""Tests for the TUI loopback admin client (Pack 0.5.0-ADM-tui)."""

from __future__ import annotations

import io
import json
import os
import ssl
import urllib.error
from unittest.mock import patch

import pytest

from airlock.tui.admin_client import (
    AdminConnection,
    AdminConnectionError,
    _scheme_and_context,
    admin_post,
    clear_provider_quarantine,
)


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestAdminPost:
    def test_success(self):
        with patch(
            "urllib.request.urlopen", return_value=_FakeResp(200, {"op": "x"})
        ) as m:
            status, payload = admin_post(
                "127.0.0.1", "4000", "/airlock/admin/providers"
            )
        assert status == 200 and payload["op"] == "x"
        # built an http URL on the loopback host
        assert (
            m.call_args[0][0].full_url
            == "http://127.0.0.1:4000/airlock/admin/providers"
        )

    def test_http_error_returns_code_and_payload(self):
        err = urllib.error.HTTPError(
            "u",
            403,
            "forbidden",
            {},
            io.BytesIO(json.dumps({"error": "nope"}).encode()),
        )
        with patch("urllib.request.urlopen", side_effect=err):
            status, payload = admin_post("127.0.0.1", "4000", "/p")
        assert status == 403 and payload["error"] == "nope"

    def test_transport_error_returns_zero(self):
        with patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")
        ):
            status, payload = admin_post("127.0.0.1", "4000", "/p")
        assert status == 0 and "error" in payload

    def test_clear_provider_quarantine_path(self):
        with patch(
            "urllib.request.urlopen", return_value=_FakeResp(200, {"op": "clear"})
        ) as m:
            clear_provider_quarantine("127.0.0.1", "4000", "openai", mode="force")
        req = m.call_args[0][0]
        assert req.full_url.endswith("/airlock/admin/providers/openai/clear-quarantine")
        assert json.loads(req.data)["mode"] == "force"


class TestScheme:
    def test_http_by_default(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_SSL_CERTFILE", raising=False)
        monkeypatch.delenv("AIRLOCK_SSL_KEYFILE", raising=False)
        scheme, ctx = _scheme_and_context("127.0.0.1")
        assert scheme == "http" and ctx is None

    def test_https_loopback_skips_verify(self, monkeypatch):
        import ssl

        monkeypatch.setenv("AIRLOCK_SSL_CERTFILE", "/c")
        monkeypatch.setenv("AIRLOCK_SSL_KEYFILE", "/k")
        scheme, ctx = _scheme_and_context("127.0.0.1")
        assert scheme == "https"
        assert ctx.verify_mode == ssl.CERT_NONE  # R10: loopback only
        assert ctx.check_hostname is False

    def test_https_non_loopback_keeps_verify(self, monkeypatch):
        import ssl

        monkeypatch.setenv("AIRLOCK_SSL_CERTFILE", "/c")
        monkeypatch.setenv("AIRLOCK_SSL_KEYFILE", "/k")
        scheme, ctx = _scheme_and_context("airlock.internal")
        assert scheme == "https"
        assert ctx.verify_mode == ssl.CERT_REQUIRED  # R10 is loopback-only

    def test_malformed_2xx_body_keeps_status(self):
        class _BadResp(_FakeResp):
            def read(self):
                return b"<<not json>>"

        with patch("urllib.request.urlopen", return_value=_BadResp(200, {})):
            status, payload = admin_post("127.0.0.1", "4000", "/p")
        assert status == 200 and "error" in payload  # status preserved


class TestRemoteAdminConnection:
    def _token_file(self, tmp_path):
        token = tmp_path / "remote.jwt"
        token.write_text("remote-token\n")
        token.chmod(0o600)
        return token

    def test_remote_connection_uses_ca_verified_https_and_bearer(self, tmp_path):
        token = self._token_file(tmp_path)
        ca = tmp_path / "ca.pem"
        ca.write_text("not-a-real-ca")
        with patch("ssl.create_default_context") as context:
            context.return_value = ssl.create_default_context()
            connection = AdminConnection.from_files("localhost", "4000", token, ca)
        assert connection.host == "localhost"
        assert connection.ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert connection.ssl_context.check_hostname is True
        with patch(
            "urllib.request.urlopen", return_value=_FakeResp(200, {"ok": True})
        ) as request:
            status, _ = admin_post("ignored", "0", "/p", connection=connection)
        assert status == 200
        sent = request.call_args[0][0]
        assert sent.full_url == "https://localhost:4000/p"
        assert sent.get_header("Authorization") == "Bearer remote-token"

    @pytest.mark.parametrize("mode", [0o644, 0o640])
    def test_remote_connection_rejects_over_permissive_token_file(self, tmp_path, mode):
        token = self._token_file(tmp_path)
        token.chmod(mode)
        with pytest.raises(AdminConnectionError, match="permissions"):
            AdminConnection.from_files("localhost", "4000", token, tmp_path / "ca.pem")

    def test_remote_connection_rejects_empty_token(self, tmp_path):
        token = tmp_path / "remote.jwt"
        token.write_text("\n")
        token.chmod(0o600)
        with pytest.raises(AdminConnectionError, match="empty"):
            AdminConnection.from_files("localhost", "4000", token, tmp_path / "ca.pem")

    def test_remote_connection_rejects_header_unsafe_token(self, tmp_path):
        token = tmp_path / "remote.jwt"
        token.write_text("bad\nvalue")
        token.chmod(0o600)
        with pytest.raises(AdminConnectionError, match="invalid"):
            AdminConnection.from_files("localhost", "4000", token, tmp_path / "ca.pem")

    def test_remote_connection_refuses_a_token_symlink(self, tmp_path):
        target = self._token_file(tmp_path)
        token = tmp_path / "linked.jwt"
        token.symlink_to(target)
        with pytest.raises(AdminConnectionError, match="regular"):
            AdminConnection.from_files("localhost", "4000", token, tmp_path / "ca.pem")

    def test_remote_connection_never_uses_server_tls_environment(
        self, tmp_path, monkeypatch
    ):
        token = self._token_file(tmp_path)
        ca = tmp_path / "ca.pem"
        ca.write_text("not-a-real-ca")
        monkeypatch.setenv("AIRLOCK_SSL_CERTFILE", "/server/cert")
        monkeypatch.setenv("AIRLOCK_SSL_KEYFILE", "/server/key")
        with patch("ssl.create_default_context") as context:
            context.return_value = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            AdminConnection.from_files("localhost", "4000", token, ca)
        context.assert_called_once_with(cafile=os.fspath(ca))
