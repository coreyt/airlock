"""Tests for airlock.client_identity — client identity propagation helpers."""

from __future__ import annotations

import urllib.request

import pytest

from airlock.client_identity import (
    add_airlock_client_header,
    extract_airlock_client_from_headers,
    extract_airlock_client_from_kwargs,
    get_runtime_airlock_client,
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
