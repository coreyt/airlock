"""Tests for airlock.client_identity — client identity propagation helpers."""

from __future__ import annotations

import urllib.request

import pytest

from airlock.client_identity import (
    NO_CLIENT_ID,
    add_airlock_client_header,
    client_id_from_api_key,
    extract_airlock_client_from_headers,
    extract_airlock_client_from_kwargs,
    extract_airlock_client_from_request,
    get_runtime_airlock_client,
    normalize_client_id,
)


# ===================================================================
# get_runtime_airlock_client
# ===================================================================


class TestGetRuntimeAirlockClient:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_CLIENT", raising=False)
        assert get_runtime_airlock_client() is None

    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CLIENT", "my-app")
        assert get_runtime_airlock_client() == "my-app"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CLIENT", "  my-app  ")
        assert get_runtime_airlock_client() == "my-app"

    def test_returns_none_for_blank(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CLIENT", "   ")
        assert get_runtime_airlock_client() is None


# ===================================================================
# add_airlock_client_header
# ===================================================================


class TestAddAirlockClientHeader:
    def test_adds_header_when_env_set(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CLIENT", "test-client")
        req = urllib.request.Request("http://example.com")
        result = add_airlock_client_header(req)
        assert result is req
        assert req.get_header("X-airlock-client") == "test-client"

    def test_noop_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_CLIENT", raising=False)
        req = urllib.request.Request("http://example.com")
        add_airlock_client_header(req)
        assert req.get_header("X-airlock-client") is None


# ===================================================================
# extract_airlock_client_from_headers
# ===================================================================


class TestExtractAirlockClientFromHeaders:
    def test_returns_none_for_none(self):
        assert extract_airlock_client_from_headers(None) is None

    def test_returns_none_for_empty(self):
        assert extract_airlock_client_from_headers({}) is None

    @pytest.mark.parametrize(
        "key",
        [
            "x-airlock-client",
            "X-Airlock-Client",
            "airlock-client",
        ],
    )
    def test_finds_all_header_candidates(self, key):
        headers = {key: "found-it"}
        assert extract_airlock_client_from_headers(headers) == "found-it"

    def test_strips_whitespace(self):
        assert (
            extract_airlock_client_from_headers({"x-airlock-client": "  app  "})
            == "app"
        )


# ===================================================================
# extract_airlock_client_from_kwargs
# ===================================================================


class TestExtractAirlockClientFromKwargs:
    def test_from_metadata_airlock_client(self):
        kwargs = {"litellm_params": {"metadata": {"airlock_client": "meta-client"}}}
        assert extract_airlock_client_from_kwargs(kwargs) == "meta-client"

    def test_from_top_level_airlock_client(self):
        kwargs = {"airlock_client": "top-client"}
        assert extract_airlock_client_from_kwargs(kwargs) == "top-client"

    def test_from_kwargs_headers(self):
        kwargs = {"headers": {"x-airlock-client": "hdr-client"}}
        assert extract_airlock_client_from_kwargs(kwargs) == "hdr-client"

    def test_from_request_object(self):
        class FakeRequest:
            headers = {"X-Airlock-Client": "req-client"}

        kwargs = {"request": FakeRequest()}
        assert extract_airlock_client_from_kwargs(kwargs) == "req-client"

    def test_returns_none_when_nothing_found(self):
        assert extract_airlock_client_from_kwargs({}) is None

    def test_returns_none_when_litellm_params_not_mapping(self):
        kwargs = {"litellm_params": "not-a-dict"}
        assert extract_airlock_client_from_kwargs(kwargs) is None


# ===================================================================
# Consolidated identity (golden / characterization oracle)
# ===================================================================


class _FakeKey:
    def __init__(self, api_key):
        self.api_key = api_key


class TestNormalizeClientId:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, NO_CLIENT_ID),
            ("", NO_CLIENT_ID),
            ("   ", NO_CLIENT_ID),
            ("app", "app"),
            ("  app  ", "app"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_client_id(raw) == expected

    def test_state_reexport_is_same_callable(self):
        from airlock.fast.state import NO_CLIENT_ID as STATE_NO_CLIENT_ID
        from airlock.fast.state import normalize_client_id as state_normalize

        assert state_normalize is normalize_client_id
        assert STATE_NO_CLIENT_ID == NO_CLIENT_ID


class TestClientIdFromApiKey:
    @pytest.mark.parametrize(
        "key,expected",
        [
            (None, NO_CLIENT_ID),
            (_FakeKey("sk-1234567890abcdef"), "key:90abcdef"),
            ({"api_key": "sk-1234567890abcdef"}, "key:90abcdef"),
            (_FakeKey("short"), NO_CLIENT_ID),
            ({}, NO_CLIENT_ID),
            ({"api_key": "short"}, NO_CLIENT_ID),
        ],
    )
    def test_from_api_key(self, key, expected):
        assert client_id_from_api_key(key) == expected


class TestExtractAirlockClientFromRequest:
    """Golden parity vs the legacy guardian _request_client_id behavior."""

    @pytest.mark.parametrize(
        "data,key,expected",
        [
            ({"metadata": {"airlock_client": "meta"}}, None, "meta"),
            ({"metadata": {"airlock_client": "  meta  "}}, None, "meta"),
            # whitespace-only airlock_client is truthy => normalizes to no_client,
            # does NOT fall through to the api-key path (legacy behavior).
            (
                {"metadata": {"airlock_client": "   "}},
                _FakeKey("sk-1234567890abcdef"),
                NO_CLIENT_ID,
            ),
            ({"headers": {"x-airlock-client": "hdr"}}, None, "hdr"),
            ({"headers": {"X-Airlock-Client": "hdr2"}}, None, "hdr2"),
            ({"metadata": {"headers": {"airlock-client": "mhdr"}}}, None, "mhdr"),
            # api-key fallback when no header/metadata identity present
            ({}, _FakeKey("sk-1234567890abcdef"), "key:90abcdef"),
            ({}, {"api_key": "sk-1234567890abcdef"}, "key:90abcdef"),
            ({}, None, NO_CLIENT_ID),
            ({}, _FakeKey("short"), NO_CLIENT_ID),
            # metadata identity wins over the api-key fallback
            (
                {"metadata": {"airlock_client": "winner"}},
                _FakeKey("sk-1234567890abcdef"),
                "winner",
            ),
        ],
    )
    def test_request_extraction(self, data, key, expected):
        assert extract_airlock_client_from_request(data, key) == expected

    def test_matches_guardian_delegators(self):
        from airlock.fast.guardian import _extract_client_id, _request_client_id

        data = {"headers": {"X-Airlock-Client": "harness-live:claude-sonnet"}}
        key = {"api_key": "sk-1234567890abcdef"}
        assert _request_client_id(data, key) == "harness-live:claude-sonnet"
        assert _extract_client_id(key) == "key:90abcdef"

    def test_enterprise_logger_parity(self, monkeypatch):
        """The 3rd legacy path (enterprise_logger) shares the canonical normalize."""
        from airlock.callbacks.enterprise_logger import _get_airlock_client

        monkeypatch.delenv("AIRLOCK_CLIENT", raising=False)
        assert _get_airlock_client({"airlock_client": "  x  "}, {}) == "x"
        assert _get_airlock_client({}, {}) == NO_CLIENT_ID
