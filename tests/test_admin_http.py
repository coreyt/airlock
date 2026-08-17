"""Tests for the admin perimeter: PDP + handle_admin_request + middleware
(Pack 0.5.0-ADM-http)."""

from __future__ import annotations

import asyncio
import base64
import json
import time

import pytest

from airlock.admin import policy
from airlock.admin.http import AdminMiddleware, handle_admin_request
from airlock.admin.policy import Principal, configure_admin, decide
from airlock.admin.tokens import mint_token
from airlock.provider_configuration import configure_provider_configuration


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

    def test_remote_tui_requires_anchor_scope_and_rejects_master_key(self):
        configure_admin(
            {"admin": {"enabled": True, "trust_loopback": False, "remote_tui": True}},
            host="0.0.0.0",
            tls_enabled=True,
        )
        master = decide(Principal(bearer="master-key-supersecret-123456"), "admin:read")
        assert not master.allowed and master.status == 403
        loopback_master = decide(
            Principal(loopback=True, bearer="master-key-supersecret-123456"),
            "admin:read",
        )
        assert not loopback_master.allowed and loopback_master.status == 403
        ordinary = mint_token("operator:one", ["admin:read"], 60)
        assert not decide(Principal(bearer=ordinary), "admin:read").allowed
        remote = mint_token("operator:one", ["admin:remote_tui", "admin:read"], 60)
        decision = decide(Principal(bearer=remote), "admin:read")
        assert decision.allowed
        assert decision.actor == "operator:one"
        assert decision.auth_context == "remote_tui_jwt"

    def test_remote_tui_rejects_unallowed_scope_and_long_lived_token(self):
        configure_admin(
            {"admin": {"enabled": True, "trust_loopback": False, "remote_tui": True}},
            host="0.0.0.0",
            tls_enabled=True,
        )
        wrong = mint_token(
            "operator:one", ["admin:remote_tui", "admin:reset_circuit"], 60
        )
        assert not decide(Principal(bearer=wrong), "admin:reset_circuit").allowed
        long_lived = mint_token("operator:one", ["admin:remote_tui", "admin:read"], 901)
        assert not decide(Principal(bearer=long_lived), "admin:read").allowed

    def test_fleet_read_profile_requires_exact_read_capability(self):
        configure_admin(
            {
                "admin": {
                    "enabled": True,
                    "trust_loopback": False,
                    "remote_tui": True,
                    "fleet_read_tui": True,
                }
            },
            host="0.0.0.0",
            tls_enabled=True,
        )
        read_token = mint_token("fleet:one", ["admin:remote_tui", "admin:read"], 60)
        assert decide(Principal(bearer=read_token), "admin:read").allowed
        broad_token = mint_token(
            "fleet:one",
            ["admin:remote_tui", "admin:read", "admin:clear_quarantine"],
            60,
        )
        assert not decide(Principal(bearer=broad_token), "admin:read").allowed
        assert not decide(
            Principal(bearer=broad_token), "admin:clear_quarantine"
        ).allowed

    def test_fleet_token_cannot_replay_across_distinct_signing_secrets(
        self, monkeypatch
    ):
        configure_admin(
            {
                "admin": {
                    "enabled": True,
                    "trust_loopback": False,
                    "remote_tui": True,
                    "fleet_read_tui": True,
                }
            },
            host="0.0.0.0",
            tls_enabled=True,
        )
        monkeypatch.setenv("AIRLOCK_JWT_SECRET", "target-one-signing-secret-000000")
        token = mint_token("fleet:one", ["admin:remote_tui", "admin:read"], 60)
        assert decide(Principal(bearer=token), "admin:read").allowed
        monkeypatch.setenv("AIRLOCK_JWT_SECRET", "target-two-signing-secret-000000")
        assert not decide(Principal(bearer=token), "admin:read").allowed


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

    def test_fleet_read_tui_requires_remote_tui_profile(self):
        with pytest.raises(RuntimeError, match="fleet_read_tui"):
            configure_admin(
                {"admin": {"enabled": True, "fleet_read_tui": True}},
                host="0.0.0.0",
                tls_enabled=True,
            )

    @pytest.mark.parametrize(
        "config,host,tls",
        [
            ({"admin": {"enabled": True, "remote_tui": True}}, "0.0.0.0", True),
            (
                {
                    "admin": {
                        "enabled": True,
                        "trust_loopback": False,
                        "remote_tui": True,
                    }
                },
                "0.0.0.0",
                False,
            ),
            (
                {
                    "admin": {
                        "enabled": True,
                        "trust_loopback": False,
                        "remote_tui": True,
                        "behind_tls_proxy": True,
                    }
                },
                "0.0.0.0",
                True,
            ),
            (
                {
                    "admin": {
                        "enabled": True,
                        "trust_loopback": False,
                        "remote_tui": True,
                        "allow_insecure_tokens": True,
                    }
                },
                "0.0.0.0",
                True,
            ),
            (
                {
                    "admin": {
                        "enabled": True,
                        "trust_loopback": False,
                        "remote_tui": True,
                    }
                },
                "127.0.0.1",
                True,
            ),
        ],
    )
    def test_remote_tui_profile_rejects_insecure_or_ambiguous_startup(
        self, config, host, tls
    ):
        with pytest.raises(RuntimeError, match="remote_tui"):
            configure_admin(config, host=host, tls_enabled=tls)


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

    def test_remote_tui_mutation_has_only_validated_actor_and_auth_context(
        self, fresh_state_store, monkeypatch
    ):
        configure_admin(
            {"admin": {"enabled": True, "trust_loopback": False, "remote_tui": True}},
            host="0.0.0.0",
            tls_enabled=True,
        )
        token = mint_token(
            "operator:one",
            ["admin:remote_tui", "admin:clear_quarantine"],
            60,
        )
        written = []
        monkeypatch.setattr(
            "airlock.admin.http.write_admin_action_record", written.append
        )
        status, _body, _headers = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/clear-quarantine",
            b'{"mode":"probe"}',
            Principal(bearer=token, actor="untrusted-header"),
        )
        assert status == 200
        assert len(written) == 1
        assert written[0]["actor"] == "operator:one"
        assert written[0]["auth_context"] == "remote_tui_jwt"
        assert token not in json.dumps(written[0])
        assert "untrusted-header" not in json.dumps(written[0])

    def test_provider_configuration_requires_distinct_scope_and_is_no_store(self):
        configure_provider_configuration(
            {
                "model_list": [
                    {
                        "model_name": "safe",
                        "litellm_params": {
                            "model": "openai/gpt-safe",
                            "api_key": "sk-SLICE40-SENTINEL",
                        },
                    }
                ]
            },
            loaded_at="2026-08-16T00:00:00Z",
        )
        read_only = mint_token("reader", ["admin:read"], 60)
        status, body, headers = handle_admin_request(
            "GET", "/airlock/admin/config/providers", b"", Principal(bearer=read_only)
        )
        assert status == 403 and "providers" not in body
        scoped = mint_token("config-reader", ["admin:read_config"], 60)
        status, body, headers = handle_admin_request(
            "GET", "/airlock/admin/config/providers", b"", Principal(bearer=scoped)
        )
        assert status == 200
        assert headers == {"cache-control": "no-store"}
        assert "sk-SLICE40-SENTINEL" not in json.dumps(body)

    def test_provider_configuration_disabled_admin_is_404(self):
        configure_admin({"admin": {"enabled": False}})
        status, _body, _headers = handle_admin_request(
            "GET", "/airlock/admin/config/providers", b"", self._loop()
        )
        assert status == 404

    def test_operational_records_are_proxy_owned_and_loopback_only(self, monkeypatch):
        from airlock.operational_reads import OperationalReadPage

        monkeypatch.setattr(
            "airlock.operational_reads.read_records",
            lambda **kwargs: OperationalReadPage(
                records=[{"model": "gpt-4o-mini"}],
                source="fathomdb",
                degraded_reason=None,
                truncated=False,
                limit_hit=None,
            ),
        )
        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/operational/records",
            b'{"days": 1, "limit": 1}',
            self._loop(),
        )
        assert s == 200
        assert body["source"] == "fathomdb"
        assert body["records"] == [{"model": "gpt-4o-mini"}]

        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/operational/records",
            b'{"days": 1, "limit": 1}',
            Principal(bearer="master-key-supersecret-123456"),
        )
        assert s == 403
        assert "loopback" in body["error"]

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/airlock/admin/operational/errors", b'{"days": "bad"}'),
            ("/airlock/admin/operational/search", b'{"query": 3}'),
            (
                "/airlock/admin/operational/search",
                b'{"query": "x", "limit": 51}',
            ),
            (
                "/airlock/admin/operational/search",
                b'{"query": "' + b"x" * 1001 + b'"}',
            ),
        ],
    )
    def test_operational_error_and_search_arguments_are_bounded(self, path, body):
        s, result, _ = handle_admin_request("POST", path, body, self._loop())
        assert s == 400
        assert "error" in result

    def test_session_view_and_break_are_authenticated_and_audited(
        self, fresh_state_store
    ):
        fresh_state_store.set_session("do-not-display", "claude-sonnet", "key:alice")
        s, body, _ = handle_admin_request(
            "GET", "/airlock/admin/sessions", b"", self._loop()
        )
        assert s == 200
        assert body["source"] == "live_admin"
        assert body["sessions"][0]["client_id"] == "key:alice"
        assert "do-not-display" not in str(body)

        selector = base64.urlsafe_b64encode(b"key:alice").decode().rstrip("=")
        s, body, _ = handle_admin_request(
            "POST",
            f"/airlock/admin/session-clients/{selector}/clear",
            b"{}",
            self._loop(),
        )
        assert s == 200
        assert body["op"] == "clear_client_sessions"
        assert body["cleared_sessions"] == 1
        assert fresh_state_store.active_session_snapshot(ttl_seconds=3600) == []

    def test_break_sessions_requires_authenticated_operator(self, fresh_state_store):
        fresh_state_store.set_session("s", "claude-sonnet", "alice")
        selector = base64.urlsafe_b64encode(b"alice").decode().rstrip("=")
        s, _body, _ = handle_admin_request(
            "POST",
            f"/airlock/admin/session-clients/{selector}/clear",
            b"{}",
            Principal(loopback=False),
        )
        assert s == 401

    def test_opaque_session_client_selector_preserves_slash_identity(
        self, fresh_state_store
    ):
        client_id = "tenant/a%2Fb"
        fresh_state_store.set_session("s", "claude-sonnet", client_id)
        selector = base64.urlsafe_b64encode(client_id.encode()).decode().rstrip("=")

        s, body, _ = handle_admin_request(
            "POST",
            f"/airlock/admin/session-clients/{selector}/clear",
            b"{}",
            self._loop(),
        )

        assert s == 200
        assert body["client_id"] == client_id
        assert body["cleared_sessions"] == 1
        assert fresh_state_store.active_session_snapshot(ttl_seconds=3600) == []

    def test_telemetry_view_is_authenticated_and_source_labelled(self):
        s, body, _ = handle_admin_request(
            "GET", "/airlock/admin/telemetry", b"", self._loop()
        )
        assert s == 200
        assert body["source"] == "process_instrumentation"
        assert isinstance(body["exporters"], dict)

        s, _body, _ = handle_admin_request(
            "GET", "/airlock/admin/telemetry", b"", Principal(loopback=False)
        )
        assert s == 401

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

        monkeypatch.setattr(http_mod._state.store, "clear_provider_quarantine", _boom)
        s, body, _ = handle_admin_request(
            "POST",
            "/airlock/admin/providers/openai/clear-quarantine",
            b"{}",
            Principal(loopback=True, actor="op"),
        )
        assert s == 500 and "error" in body
