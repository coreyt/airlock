"""Tests for airlock/fast/circuit_breaker._load_failover_map shim (SET-unify).

The env-driven cache was removed; ``_load_failover_map`` now delegates to
``get_settings().failover_map`` (config fallbacks + AIRLOCK_FAILOVER_MAP override,
no hidden default). The conversion/precedence matrix is covered in test_fast_settings.
"""

from __future__ import annotations

import pytest

import airlock.fast.settings as settings_mod
from airlock.fast.circuit_breaker import _load_failover_map
from airlock.fast.settings import configure_settings


@pytest.fixture(autouse=True)
def _reset_settings():
    settings_mod._configured = None
    yield
    settings_mod._configured = None


def test_loads_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", '{"gpt-4": ["claude"]}')
    assert _load_failover_map() == {"gpt-4": ["claude"]}


def test_delegates_to_get_settings_config() -> None:
    configure_settings(
        {"router_settings": {"fallbacks": [{"claude-sonnet": ["claude-haiku"]}]}}
    )
    assert _load_failover_map() == {"claude-sonnet": ["claude-haiku"]}


def test_invalid_json_falls_back_to_empty(monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", "not json")
    assert _load_failover_map() == {}


def test_unset_env_returns_empty(monkeypatch) -> None:
    monkeypatch.delenv("AIRLOCK_FAILOVER_MAP", raising=False)
    assert _load_failover_map() == {}
