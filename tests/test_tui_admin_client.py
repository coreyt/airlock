"""Tests for the TUI loopback admin client (Pack 0.5.0-ADM-tui)."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

from airlock.tui.admin_client import (
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
            status, payload = admin_post("127.0.0.1", "4000", "/airlock/admin/providers")
        assert status == 200 and payload["op"] == "x"
        # built an http URL on the loopback host
        assert m.call_args[0][0].full_url == "http://127.0.0.1:4000/airlock/admin/providers"

    def test_http_error_returns_code_and_payload(self):
        err = urllib.error.HTTPError(
            "u", 403, "forbidden", {}, io.BytesIO(json.dumps({"error": "nope"}).encode())
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
        scheme, ctx = _scheme_and_context()
        assert scheme == "http" and ctx is None

    def test_https_when_tls_configured(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_SSL_CERTFILE", "/c")
        monkeypatch.setenv("AIRLOCK_SSL_KEYFILE", "/k")
        scheme, ctx = _scheme_and_context()
        assert scheme == "https" and ctx is not None
