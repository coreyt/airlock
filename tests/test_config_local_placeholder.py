"""Keep the committed local-config fallback safe for fresh checkouts."""

from __future__ import annotations

import pathlib

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_config_includes_a_mapping_local_fallback():
    config = yaml.safe_load((ROOT / "config.yaml").read_text())
    fallback = yaml.safe_load((ROOT / "config.local.yaml").read_text())

    assert config["include"] == ["config.local.yaml"]
    # Fresh checkouts get the committed ``{}`` fallback; deployments may replace
    # it locally with a mapping of machine-specific MCP servers.
    assert isinstance(fallback, dict)
