"""AirlockSettings loader — precedence matrix (env > config > default).

Covers every field: default / config / env precedence, malformed-input
fallback, `0`-budget preservation, fallbacks list-of-dicts -> dict, and the
"1d" -> 86400 window parse. Also the configure_settings/get_settings singleton.
"""

from __future__ import annotations

import json

import pytest

import airlock.fast.settings as settings_mod
from airlock.fast.settings import (
    AirlockSettings,
    configure_settings,
    get_settings,
    load_airlock_settings,
)

# SET-unify removed the hidden value-carrying budget/failover defaults: with no
# config (and no env override) the provider-budget and failover maps are EMPTY.
_EXPECTED_DEFAULT_BUDGETS: dict[str, float] = {}
_EXPECTED_DEFAULT_SESSION_TTL = 3600
_EXPECTED_DEFAULT_SMART_THRESHOLDS = (0.30, 0.60)
_EXPECTED_DEFAULT_WARN_RATIO = 0.8


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for var in (
        "AIRLOCK_PROVIDER_BUDGETS",
        "AIRLOCK_FAILOVER_MAP",
        "AIRLOCK_COST_TIERS",
        "AIRLOCK_SESSION_TTL",
        "AIRLOCK_SMART_THRESHOLDS",
        "AIRLOCK_BUDGET_WARN_RATIO",
    ):
        monkeypatch.delenv(var, raising=False)
    saved = settings_mod._configured
    settings_mod._configured = None
    yield
    settings_mod._configured = saved


# ---------------------------------------------------------------------------
# All-defaults
# ---------------------------------------------------------------------------
def test_none_config_all_defaults() -> None:
    s = load_airlock_settings(None)
    assert isinstance(s, AirlockSettings)
    assert s.provider_budgets == _EXPECTED_DEFAULT_BUDGETS
    assert s.session_ttl == _EXPECTED_DEFAULT_SESSION_TTL
    assert s.smart_thresholds == _EXPECTED_DEFAULT_SMART_THRESHOLDS
    assert s.budget_warn_ratio == _EXPECTED_DEFAULT_WARN_RATIO
    assert s.cost_tiers["low"]  # populated default tiers
    assert s.failover_map == {}  # no hidden failover default (SET-unify)


def test_empty_dict_all_defaults() -> None:
    s = load_airlock_settings({})
    assert s.provider_budgets == _EXPECTED_DEFAULT_BUDGETS
    assert s.session_ttl == _EXPECTED_DEFAULT_SESSION_TTL


def test_empty_config_reproduces_nonbudget_defaults_only() -> None:
    """FIX-5 scoped golden: empty config keeps NON-budget defaults; the hidden
    provider-budget / failover defaults are gone (operator-confirmed change)."""
    s = load_airlock_settings({"airlock_settings": {}})
    # Non-budget defaults preserved verbatim.
    assert s.session_ttl == _EXPECTED_DEFAULT_SESSION_TTL
    assert s.smart_thresholds == _EXPECTED_DEFAULT_SMART_THRESHOLDS
    assert s.budget_warn_ratio == _EXPECTED_DEFAULT_WARN_RATIO
    assert s.cost_tiers["low"]
    # Budget / failover defaults explicitly NOT preserved.
    assert s.provider_budgets == {}
    assert s.budget_windows == {}
    assert s.failover_map == {}


# ---------------------------------------------------------------------------
# provider_budgets — config / env / precedence / 0-preserve / window
# ---------------------------------------------------------------------------
def test_provider_budgets_from_config() -> None:
    cfg = {
        "router_settings": {
            "provider_budget_config": {
                "anthropic": {"budget_limit": 12.5, "time_period": "1d"},
                "openai": {"budget_limit": 7, "time_period": "30d"},
            }
        }
    }
    s = load_airlock_settings(cfg)
    assert s.provider_budgets["anthropic"] == 12.5
    assert s.provider_budgets["openai"] == 7.0


def test_provider_budget_zero_preserved() -> None:
    cfg = {
        "router_settings": {
            "provider_budget_config": {
                "gemini": {"budget_limit": 0, "time_period": "1d"},
            }
        }
    }
    s = load_airlock_settings(cfg)
    assert "gemini" in s.provider_budgets
    assert s.provider_budgets["gemini"] == 0.0


def test_budget_window_1d_is_86400() -> None:
    cfg = {
        "router_settings": {
            "provider_budget_config": {
                "anthropic": {"budget_limit": 10, "time_period": "1d"},
            }
        }
    }
    s = load_airlock_settings(cfg)
    assert s.budget_windows["anthropic"] == 86400


def test_budget_native_max_budget_spelling_tolerated() -> None:
    cfg = {
        "router_settings": {
            "provider_budget_config": {
                "anthropic": {"max_budget": 33, "time_period": "1d"},
            }
        }
    }
    s = load_airlock_settings(cfg)
    assert s.provider_budgets["anthropic"] == 33.0


def test_provider_budgets_env_overrides_config() -> None:
    cfg = {
        "router_settings": {
            "provider_budget_config": {
                "anthropic": {"budget_limit": 12.5, "time_period": "1d"},
            }
        }
    }
    s = load_airlock_settings(
        cfg,
        env={"AIRLOCK_PROVIDER_BUDGETS": json.dumps({"anthropic": 99.0})},
    )
    assert s.provider_budgets == {"anthropic": 99.0}


def test_provider_budgets_malformed_env_falls_back_to_config() -> None:
    cfg = {
        "router_settings": {
            "provider_budget_config": {
                "anthropic": {"budget_limit": 12.5, "time_period": "1d"},
            }
        }
    }
    s = load_airlock_settings(cfg, env={"AIRLOCK_PROVIDER_BUDGETS": "{bad json"})
    assert s.provider_budgets["anthropic"] == 12.5


def test_provider_budgets_malformed_config_falls_back_to_default() -> None:
    cfg = {"router_settings": {"provider_budget_config": "not-a-mapping"}}
    s = load_airlock_settings(cfg)
    # No hidden default to fall back to (SET-unify) — empty means no enforcement.
    assert s.provider_budgets == _EXPECTED_DEFAULT_BUDGETS
    assert s.provider_budgets == {}


def test_provider_budgets_malformed_entry_skipped() -> None:
    cfg = {
        "router_settings": {
            "provider_budget_config": {
                "anthropic": {"budget_limit": 10, "time_period": "1d"},
                "openai": "garbage",
            }
        }
    }
    s = load_airlock_settings(cfg)
    assert s.provider_budgets["anthropic"] == 10.0
    assert "openai" not in s.provider_budgets


def test_provider_budgets_via_monkeypatched_env(monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_PROVIDER_BUDGETS", json.dumps({"openai": 5.0}))
    s = load_airlock_settings({})
    assert s.provider_budgets == {"openai": 5.0}


# ---------------------------------------------------------------------------
# failover_map — list-of-dicts conversion / env / precedence
# ---------------------------------------------------------------------------
def test_failover_map_from_config_list_of_dicts() -> None:
    cfg = {
        "router_settings": {
            "fallbacks": [
                {"claude-opus": ["claude-sonnet", "gpt-5-pro"]},
                {"claude-sonnet": ["claude-haiku"]},
            ]
        }
    }
    s = load_airlock_settings(cfg)
    assert s.failover_map == {
        "claude-opus": ["claude-sonnet", "gpt-5-pro"],
        "claude-sonnet": ["claude-haiku"],
    }


def test_failover_map_env_overrides_config() -> None:
    cfg = {"router_settings": {"fallbacks": [{"a": ["b"]}]}}
    s = load_airlock_settings(
        cfg, env={"AIRLOCK_FAILOVER_MAP": json.dumps({"x": ["y", "z"]})}
    )
    assert s.failover_map == {"x": ["y", "z"]}


def test_failover_map_malformed_env_falls_back_to_config() -> None:
    cfg = {"router_settings": {"fallbacks": [{"a": ["b"]}]}}
    s = load_airlock_settings(cfg, env={"AIRLOCK_FAILOVER_MAP": "{bad"})
    assert s.failover_map == {"a": ["b"]}


def test_failover_map_empty_when_absent() -> None:
    # SET-unify: no hidden failover default — absent fallbacks => empty map.
    s = load_airlock_settings({})
    assert s.failover_map == {}


def test_failover_map_env_wrong_inner_shape_falls_back_to_config() -> None:
    # A string value must NOT be iterated char-by-char into ["b", "c"].
    cfg = {"router_settings": {"fallbacks": [{"x": ["y"]}]}}
    s = load_airlock_settings(cfg, env={"AIRLOCK_FAILOVER_MAP": '{"a": "bc"}'})
    assert s.failover_map == {"x": ["y"]}
    assert "a" not in s.failover_map


def test_failover_map_env_wrong_inner_shape_falls_back_to_default() -> None:
    s = load_airlock_settings({}, env={"AIRLOCK_FAILOVER_MAP": '{"a": "bc"}'})
    assert s.failover_map.get("a") != ["b", "c"]
    # No config fallbacks + no hidden default => empty map (SET-unify).
    assert s.failover_map == {}


# ---------------------------------------------------------------------------
# cost_tiers
# ---------------------------------------------------------------------------
def test_cost_tiers_from_config() -> None:
    cfg = {"cost_tiers": {"low": ["m1"], "medium": ["m2"], "high": ["m3"]}}
    s = load_airlock_settings(cfg)
    assert s.cost_tiers == {"low": ["m1"], "medium": ["m2"], "high": ["m3"]}


def test_cost_tiers_env_overrides_config() -> None:
    cfg = {"cost_tiers": {"low": ["from-config"]}}
    s = load_airlock_settings(
        cfg, env={"AIRLOCK_COST_TIERS": json.dumps({"low": ["from-env"]})}
    )
    assert s.cost_tiers == {"low": ["from-env"]}


def test_cost_tiers_malformed_env_falls_back_to_config() -> None:
    cfg = {"cost_tiers": {"low": ["from-config"]}}
    s = load_airlock_settings(cfg, env={"AIRLOCK_COST_TIERS": "not-json{"})
    assert s.cost_tiers == {"low": ["from-config"]}


def test_cost_tiers_malformed_config_falls_back_to_default() -> None:
    cfg = {"cost_tiers": {"low": "not-a-list"}}
    s = load_airlock_settings(cfg)
    assert s.cost_tiers["low"] == [
        "claude-haiku",
        "gemini-flash",
        "gemini-flash-lite",
        "gpt-5-nano",
        "mistral-small",
    ]


# ---------------------------------------------------------------------------
# session_ttl
# ---------------------------------------------------------------------------
def test_session_ttl_from_config() -> None:
    s = load_airlock_settings({"airlock_settings": {"session_ttl": 7200}})
    assert s.session_ttl == 7200


def test_session_ttl_env_overrides_config() -> None:
    s = load_airlock_settings(
        {"airlock_settings": {"session_ttl": 7200}},
        env={"AIRLOCK_SESSION_TTL": "1234"},
    )
    assert s.session_ttl == 1234


def test_session_ttl_malformed_env_falls_back_to_config() -> None:
    s = load_airlock_settings(
        {"airlock_settings": {"session_ttl": 7200}},
        env={"AIRLOCK_SESSION_TTL": "not-an-int"},
    )
    assert s.session_ttl == 7200


def test_session_ttl_malformed_config_falls_back_to_default() -> None:
    s = load_airlock_settings({"airlock_settings": {"session_ttl": "nope"}})
    assert s.session_ttl == _EXPECTED_DEFAULT_SESSION_TTL


# ---------------------------------------------------------------------------
# smart_thresholds
# ---------------------------------------------------------------------------
def test_smart_thresholds_from_config() -> None:
    s = load_airlock_settings({"airlock_settings": {"smart_thresholds": [0.2, 0.7]}})
    assert s.smart_thresholds == (0.2, 0.7)


def test_smart_thresholds_env_overrides_config() -> None:
    s = load_airlock_settings(
        {"airlock_settings": {"smart_thresholds": [0.2, 0.7]}},
        env={"AIRLOCK_SMART_THRESHOLDS": json.dumps([0.4, 0.8])},
    )
    assert s.smart_thresholds == (0.4, 0.8)


def test_smart_thresholds_malformed_env_falls_back_to_config() -> None:
    s = load_airlock_settings(
        {"airlock_settings": {"smart_thresholds": [0.2, 0.7]}},
        env={"AIRLOCK_SMART_THRESHOLDS": "[1]"},
    )
    assert s.smart_thresholds == (0.2, 0.7)


def test_smart_thresholds_malformed_config_falls_back_to_default() -> None:
    s = load_airlock_settings({"airlock_settings": {"smart_thresholds": [1, 2, 3]}})
    assert s.smart_thresholds == _EXPECTED_DEFAULT_SMART_THRESHOLDS


# ---------------------------------------------------------------------------
# budget_warn_ratio
# ---------------------------------------------------------------------------
def test_budget_warn_ratio_from_config() -> None:
    s = load_airlock_settings({"airlock_settings": {"budget_warn_ratio": 0.95}})
    assert s.budget_warn_ratio == 0.95


def test_budget_warn_ratio_env_overrides_config() -> None:
    s = load_airlock_settings(
        {"airlock_settings": {"budget_warn_ratio": 0.95}},
        env={"AIRLOCK_BUDGET_WARN_RATIO": "0.5"},
    )
    assert s.budget_warn_ratio == 0.5


def test_budget_warn_ratio_malformed_env_falls_back_to_config() -> None:
    s = load_airlock_settings(
        {"airlock_settings": {"budget_warn_ratio": 0.95}},
        env={"AIRLOCK_BUDGET_WARN_RATIO": "high"},
    )
    assert s.budget_warn_ratio == 0.95


def test_budget_warn_ratio_malformed_config_falls_back_to_default() -> None:
    s = load_airlock_settings({"airlock_settings": {"budget_warn_ratio": "nope"}})
    assert s.budget_warn_ratio == _EXPECTED_DEFAULT_WARN_RATIO


# ---------------------------------------------------------------------------
# frozen dataclass + singleton seam
# ---------------------------------------------------------------------------
def test_settings_is_frozen() -> None:
    s = load_airlock_settings({})
    with pytest.raises(Exception):
        s.session_ttl = 1  # type: ignore[misc]


def test_get_settings_returns_defaults_when_unconfigured() -> None:
    settings_mod._configured = None
    s = get_settings()
    assert s.session_ttl == _EXPECTED_DEFAULT_SESSION_TTL


def test_get_settings_unconfigured_windows_match_budgets() -> None:
    # The unconfigured accessor must be internally consistent: every provider in
    # provider_budgets must have a matching budget_windows entry. SET-unify removed
    # the hidden defaults, so both maps are empty (and therefore still consistent).
    settings_mod._configured = None
    s = get_settings()
    assert set(s.budget_windows) == set(s.provider_budgets)
    assert s.provider_budgets == {}
    assert s.budget_windows == {}


def test_configure_then_get_round_trip() -> None:
    configure_settings({"airlock_settings": {"session_ttl": 4242}})
    assert get_settings().session_ttl == 4242
