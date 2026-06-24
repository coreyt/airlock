"""transparency config loader + cached accessor."""

from __future__ import annotations

import pytest

import airlock.transparency as tr
from airlock.transparency import (
    TransparencyConfig,
    configure_transparency,
    get_transparency_config,
    load_transparency_config,
)


@pytest.fixture(autouse=True)
def _reset_config():
    saved = tr._configured
    tr._configured = None
    yield
    tr._configured = saved


def test_missing_block_all_defaults() -> None:
    cfg = load_transparency_config(None)
    assert cfg.mutation_headers == "compact"
    assert cfg.served_headers is True
    assert cfg.explain_body_optin_header == "X-Airlock-Explain"
    assert cfg.attribute_accounting_to_served is True
    assert cfg.mutation_header_budget_bytes == 256


def test_empty_dict_all_defaults() -> None:
    cfg = load_transparency_config({})
    assert cfg == TransparencyConfig()


def test_partial_override_only_given_keys() -> None:
    cfg = load_transparency_config(
        {
            "transparency": {
                "mutation_headers": "off",
                "mutation_header_budget_bytes": 512,
            }
        }
    )
    assert cfg.mutation_headers == "off"
    assert cfg.mutation_header_budget_bytes == 512
    # untouched keys keep defaults
    assert cfg.served_headers is True
    assert cfg.explain_body_optin_header == "X-Airlock-Explain"


def test_invalid_mutation_headers_rejected_falls_back() -> None:
    cfg = load_transparency_config({"transparency": {"mutation_headers": "loud"}})
    assert cfg.mutation_headers == "compact"


def test_non_positive_budget_rejected_falls_back() -> None:
    cfg = load_transparency_config(
        {"transparency": {"mutation_header_budget_bytes": 0}}
    )
    assert cfg.mutation_header_budget_bytes == 256
    cfg2 = load_transparency_config(
        {"transparency": {"mutation_header_budget_bytes": -10}}
    )
    assert cfg2.mutation_header_budget_bytes == 256
    cfg3 = load_transparency_config(
        {"transparency": {"mutation_header_budget_bytes": "notint"}}
    )
    assert cfg3.mutation_header_budget_bytes == 256


def test_bool_coercion() -> None:
    cfg = load_transparency_config({"transparency": {"served_headers": False}})
    assert cfg.served_headers is False


def test_full_value_accepted() -> None:
    cfg = load_transparency_config({"transparency": {"mutation_headers": "full"}})
    assert cfg.mutation_headers == "full"


def test_accessor_returns_defaults_before_configure() -> None:
    # never configured this run (fixture reset _configured to None)
    cfg = get_transparency_config()
    assert cfg == TransparencyConfig()


def test_configure_then_accessor_returns_configured() -> None:
    configure_transparency({"transparency": {"mutation_headers": "off"}})
    assert get_transparency_config().mutation_headers == "off"
