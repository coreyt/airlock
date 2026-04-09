"""Tests for airlock/fast/circuit_breaker.py env-driven failover-map cache."""

from __future__ import annotations

from airlock.fast import circuit_breaker
from airlock.fast.circuit_breaker import _DEFAULT_FAILOVER_MAP, _load_failover_map


def _reset_cache() -> None:
    circuit_breaker._cached_failover_raw = circuit_breaker._UNSET
    circuit_breaker._cached_failover_map = _DEFAULT_FAILOVER_MAP


def test_loads_from_env(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", '{"gpt-4": ["claude"]}')
    result = _load_failover_map()
    assert result == {"gpt-4": ["claude"]}


def test_cache_identity_same_env(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", '{"gpt-4": ["claude"]}')
    first = _load_failover_map()
    second = _load_failover_map()
    assert first is second


def test_cache_invalidates_on_env_change(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", '{"gpt-4": ["claude"]}')
    assert _load_failover_map() == {"gpt-4": ["claude"]}
    monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", '{"gpt-4": ["claude", "haiku"]}')
    assert _load_failover_map() == {"gpt-4": ["claude", "haiku"]}


def test_invalid_json_falls_back_to_defaults(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", "not json")
    result = _load_failover_map()
    assert result == _DEFAULT_FAILOVER_MAP


def test_unset_env_returns_defaults(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.delenv("AIRLOCK_FAILOVER_MAP", raising=False)
    result = _load_failover_map()
    assert result == _DEFAULT_FAILOVER_MAP
