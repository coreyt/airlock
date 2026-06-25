"""Tests for the admin perimeter: PDP + handle_admin_request + middleware
(Pack 0.5.0-ADM-http)."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from airlock.admin import policy
from airlock.admin.http import AdminMiddleware, handle_admin_request
from airlock.admin.policy import Principal, configure_admin, decide
from airlock.admin.tokens import mint_token


@pytest.fixture(autouse=True)
def _admin_env(monkeypatch):
    monkeypatch.setenv("AIRLOCK_MASTER_KEY", "master-key-supersecret-123456")
    monkeypatch.setenv("AIRLOCK_JWT_SECRET", "jwt-signing-secret-abcdefghij")
    saved = policy._admin_config
    configure_admin({"admin": {"enabled": True}})
    yield
    policy._admin_config = saved


# --------------------------------------------------------------------------- PDP
class TestPDP:
    def test_loopback_grants(self):
        d = decide(Principal(loopback=True), "admin:clear_quarantine")
        assert d.allowed

    def test_loopback_only_denies_remote_token(self):
        tok = mint_token("ops", ["admin:force_quarantine"], 60)
        d = decide(Principal(bearer=tok), "admin:force_quarantine", loopback_only=True)
        assert not d.allowed and d.status == 403

    def test_master_key_grants(self):
        d = decide(
            Principal(bearer="master-key-supersecret-123456"), "admin:clear_quarantine"
        )
        assert d.allowed and d.actor == "master_key"

    def test_jwt_with_scope_grants(self):
        tok = mint_token("key:abc", ["admin:clear_quarantine"], 60)
        d = decide(Principal(bearer=tok), "admin:clear_quarantine")
        assert d.allowed and d.actor == "key:abc"

    def test_jwt_missing_scope_403(self):
        tok = mint_token("key:abc", ["admin:reset_circuit"], 60)
        d = decide(Principal(bearer=tok), "admin:clear_quarantine")
        assert not d.allowed and d.status == 403

    def test_no_auth_401(self):
        d = decide(Principal(), "admin:clear_quarantine")
        assert not d.allowed and d.status == 401

    def test_invalid_token_403(self):
        d = decide(Principal(bearer="not.a.jwt"), "admin:clear_quarantine")
        assert not d.allowed and d.status == 403

    def test_trust_loopback_off_falls_through(self):
        configure_admin({"admin": {"enabled": True, "trust_loopback": False}})
        d = decide(Principal(loopback=True), "admin:clear_quarantine")  # no bearer
        assert not d.allowed and d.status == 401


class TestConfigureAdminFailClosed:
    def test_exposed_no_tls_raises(self):
        with pytest.raises(RuntimeError):
            configure_admin(
                {"admin": {"enabled": True}}, host="0.0.0.0", tls_enabled=False
            )

    def test_exposed_with_tls_ok(self):
        configure_admin({"admin": {"enabled": True}}, host="0.0.0.0", tls_enabled=True)

    def test_exposed_behind_tls_proxy_ok(self):
        configure_admin(
            {"admin": {"enabled": True, "behind_tls_proxy": True}}, host="0.0.0.0"
        )

    def test_loopback_ok(self):
        configure_admin({"admin": {"enabled": True}}, host="127.0.0.1")

    def test_disabled_no_check(self):
        configure_admin({"admin": {"enabled": False}}, host="0.0.0.0")  # no raise


# ------------------------------------------------------ handle_admin_request
class TestHandleAdminRequest:
    def _loop(self):
        return Principal(loopback=True, actor="op")

    def test_disabled_404(self):
        configure_admin({"admin": {"enabled": False}})
        s, body, _ = handle_admin_request(
            "GET", "/airlock/admin/providers", b"", self._loop()
        )
        assert s == 404

    def test_unknown_route_404(self):
        s, body, _ = handle_admin_request(
            "GET", "/airlock/admin/nope", b"", self._loop()
        )
        assert s == 404

    def test_get_providers(self, fresh_state_store):
        fresh_state_store.get_provider("openai").quarantine_until = time.time() + 100
        s, body, _ = handle_admin_request(
            "GET", "/airlock/admin/providers", b"", self._loop()
        )
        assert s == 200
        assert body["providers"]["openai"]["quarantined"] is True

    def test_clear_quarantine_loopback(self, fresh_state_store):
        fresh_state_store.get_provider("openai").quarantine_until = time.time() + 100
        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/clear-quarantine",
            json.dumps({"mode": "force"}).encode(),
            self._loop(),
        )
        assert s == 200 and body["op"] == "clear_provider_quarantine"
        assert fresh_state_store.get_provider("openai").is_quarantined() is False

    def test_clear_quarantine_no_auth_401(self, fresh_state_store):
        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/clear-quarantine",
            b"{}",
            Principal(loopback=False),  # no bearer
        )
        assert s == 401

    def test_clear_quarantine_jwt(self, fresh_state_store):
        tok = mint_token("key:runner", ["admin:clear_quarantine"], 60)
        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/clear-quarantine",
            b"{}",
            Principal(loopback=False, bearer=tok),
        )
        assert s == 200 and body["actor"] == "key:runner"

    def test_force_quarantine_requires_loopback(self, fresh_state_store):
        tok = mint_token("ops", ["admin:force_quarantine"], 60)
        s, _, _ = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/quarantine",
            b"{}",
            Principal(loopback=False, bearer=tok),  # remote -> denied
        )
        assert s == 403
        s2, body2, _ = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/quarantine",
            b"{}",
            self._loop(),  # loopback -> ok
        )
        assert s2 == 200 and fresh_state_store.get_provider("openai").is_quarantined()

    def test_client_provider_clear(self, fresh_state_store):
        cp = fresh_state_store.get_client_provider("key:v", "openai")
        cp.quarantine_until = time.time() + 100
        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/clients/key:v/providers/openai/clear-quarantine",
            json.dumps({"mode": "force"}).encode(),
            self._loop(),
        )
        assert s == 200
        assert (
            fresh_state_store.get_client_provider("key:v", "openai").is_quarantined()
            is False
        )

    def test_invalid_mode_400(self, fresh_state_store):
        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/clear-quarantine",
            json.dumps({"mode": "BAD"}).encode(),
            self._loop(),
        )
        assert s == 400

    def test_invalid_json_400(self, fresh_state_store):
        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/clear-quarantine",
            b"{not json",
            self._loop(),
        )
        assert s == 400


# ----------------------------------------------------------------- middleware
class TestMiddleware:
    def _run(self, scope, body=b""):
        sent = []
        sent_iter = iter([{"type": "http.request", "body": body, "more_body": False}])

        async def receive():
            return next(sent_iter)

        async def send(msg):
            sent.append(msg)

        async def downstream(scope, receive, send):
            sent.append({"type": "PASSTHROUGH"})

        mw = AdminMiddleware(downstream)
        asyncio.run(mw(scope, receive, send))
        return sent

    def test_admin_path_handled(self, fresh_state_store):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/airlock/admin/providers",
            "client": ("127.0.0.1", 5000),
            "headers": [],
        }
        sent = self._run(scope)
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 200
        assert not any(m.get("type") == "PASSTHROUGH" for m in sent)

    def test_non_admin_path_passes_through(self, fresh_state_store):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "client": ("127.0.0.1", 5000),
            "headers": [],
        }
        sent = self._run(scope)
        assert any(m.get("type") == "PASSTHROUGH" for m in sent)

    def test_remote_get_providers_unauthed(self, fresh_state_store):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/airlock/admin/providers",
            "client": ("203.0.113.7", 5000),  # remote
            "headers": [],
        }
        sent = self._run(scope)
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 401


class TestAdmHttpFix1:
    """From the ADM-http PASS_WITH_NOTES security review."""

    def test_empty_master_key_not_matched(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "")
        # empty bearer -> no auth -> 401
        assert decide(Principal(bearer=""), "admin:clear_quarantine").status == 401
        # non-empty junk bearer -> not master, not a valid JWT -> 403
        assert decide(Principal(bearer="x"), "admin:clear_quarantine").status == 403

    def test_missing_client_is_not_loopback(self, fresh_state_store):
        # scope without a client address must NOT be treated as operator.
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/airlock/admin/providers",
            "client": None,
            "headers": [],
        }
        sent = TestMiddleware()._run(scope)
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 401  # fail closed

    def test_oversized_body_413(self, fresh_state_store):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/airlock/admin/providers/openai/clear-quarantine",
            "client": ("127.0.0.1", 5000),
            "headers": [],
        }
        sent = TestMiddleware()._run(scope, body=b"x" * (70 * 1024))
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 413

    def test_handler_exception_becomes_500(self, fresh_state_store, monkeypatch):
        import airlock.admin.http as http_mod

        def _boom(*a, **k):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(
            http_mod._state.store, "clear_provider_quarantine", _boom
        )
        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/clear-quarantine",
            b"{}",
            Principal(loopback=True, actor="op"),
        )
        assert s == 500 and "error" in body
