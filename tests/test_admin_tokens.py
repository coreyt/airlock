"""Tests for airlock/admin/tokens.py + the mint-token CLI (Pack 0.5.0-ADM-jwt)."""

from __future__ import annotations

import time

import pytest

from airlock.admin.tokens import (
    TokenError,
    has_scope,
    mint_token,
    token_scopes,
    verify_token,
)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("AIRLOCK_JWT_SECRET", "test-signing-secret-0123456789")
    monkeypatch.delenv("AIRLOCK_JWT_SECRET_PREV", raising=False)


class TestMintVerify:
    def test_round_trip(self):
        tok = mint_token("key:abc12345", ["guardrail:skip:keyword"], 3600)
        claims = verify_token(tok)
        assert claims["sub"] == "key:abc12345"
        assert claims["iss"] == "airlock"
        assert claims["scope"] == ["guardrail:skip:keyword"]
        assert "exp" in claims and "iat" in claims and "jti" in claims

    def test_scope_helpers(self):
        claims = verify_token(mint_token("s", ["admin:clear_quarantine"], 60))
        assert token_scopes(claims) == ["admin:clear_quarantine"]
        assert has_scope(claims, "admin:clear_quarantine")
        assert not has_scope(claims, "admin:force_quarantine")

    def test_expired_rejected(self):
        tok = mint_token("s", ["x"], 10, now=time.time() - 100)  # exp in the past
        with pytest.raises(TokenError):
            verify_token(tok, leeway=0)

    def test_empty_sub_or_bad_ttl(self):
        with pytest.raises(TokenError):
            mint_token("", ["x"], 60)
        with pytest.raises(TokenError):
            mint_token("s", ["x"], 0)

    def test_wrong_secret_rejected(self, monkeypatch):
        tok = mint_token("s", ["x"], 60)
        monkeypatch.setenv("AIRLOCK_JWT_SECRET", "a-totally-different-secret-value")
        with pytest.raises(TokenError):
            verify_token(tok)

    def test_tampered_token_rejected(self):
        tok = mint_token("s", ["x"], 60)
        with pytest.raises(TokenError):
            verify_token(tok + "x")

    def test_denylist_revocation(self):
        tok = mint_token("s", ["x"], 60)
        jti = verify_token(tok)["jti"]
        with pytest.raises(TokenError):
            verify_token(tok, denylist={jti})


class TestRotation:
    def test_prev_secret_accepted(self, monkeypatch):
        # mint under the original secret
        tok = mint_token("s", ["x"], 60)
        # rotate: new current secret, original moved to PREV
        monkeypatch.setenv("AIRLOCK_JWT_SECRET", "new-current-secret-abcdefghij")
        monkeypatch.setenv("AIRLOCK_JWT_SECRET_PREV", "test-signing-secret-0123456789")
        claims = verify_token(tok)  # verified via the previous secret
        assert claims["sub"] == "s"


class TestDeriveFromMaster:
    def test_derives_when_no_jwt_secret(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_JWT_SECRET", raising=False)
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "master-key-value-1234567890")
        tok = mint_token("s", ["x"], 60)  # uses derived key
        assert verify_token(tok)["sub"] == "s"

    def test_no_secret_at_all_errors(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_JWT_SECRET", raising=False)
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)
        with pytest.raises(TokenError):
            mint_token("s", ["x"], 60)


class TestCli:
    def test_parse_ttl(self):
        from airlock.cli.admin_cmd import _parse_ttl

        assert _parse_ttl("30m") == 1800
        assert _parse_ttl("1h") == 3600
        assert _parse_ttl("24h") == 86400
        assert _parse_ttl("2d") == 172800
        assert _parse_ttl("3600") == 3600

    def test_mint_token_command_prints_token(self, capsys):
        from types import SimpleNamespace

        from airlock.cli.admin_cmd import run

        run(
            SimpleNamespace(
                admin_action="mint-token",
                sub="key:abc12345",
                scopes=["guardrail:skip:keyword"],
                ttl="1h",
            )
        )
        out = capsys.readouterr().out.strip()
        assert verify_token(out)["sub"] == "key:abc12345"
