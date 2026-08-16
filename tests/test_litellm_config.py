"""Parity tests for the deliberately narrow LiteLLM include resolver."""

from __future__ import annotations

import pytest

from airlock.litellm_config import resolve_litellm_direct_config


def test_nested_include_extends_the_active_litellm_include_list_in_order(tmp_path):
    grandchild = tmp_path / "grandchild.yaml"
    grandchild.write_text("admin:\n  enabled: true\n")
    first = tmp_path / "first.yaml"
    first.write_text(
        "include: [grandchild.yaml]\nadmin:\n  enabled: false\nitems: [first]\n"
    )
    second = tmp_path / "second.yaml"
    second.write_text("admin:\n  enabled: false\nitems: [second]\n")
    root = tmp_path / "config.yaml"
    root.write_text("include: [first.yaml, second.yaml]\nitems: [root]\n")

    resolved = resolve_litellm_direct_config(root)

    assert resolved["admin"] == {"enabled": True}
    assert resolved["items"] == ["root", "first", "second"]
    assert "include" not in resolved


def test_direct_include_replaces_dict_and_scalar_but_extends_lists(tmp_path):
    included = tmp_path / "included.yaml"
    included.write_text("settings:\n  child: true\nmode: included\nitems: [two]\n")
    root = tmp_path / "config.yaml"
    root.write_text(
        "include: [included.yaml]\nsettings:\n  root: true\nmode: root\nitems: [one]\n"
    )

    assert resolve_litellm_direct_config(root) == {
        "settings": {"child": True},
        "mode": "included",
        "items": ["one", "two"],
    }


def test_direct_include_preserves_litellm_malformed_list_target_failure(tmp_path):
    from litellm.proxy.proxy_server import ProxyConfig

    included = tmp_path / "included.yaml"
    included.write_text("items: [child]\n")
    root = tmp_path / "config.yaml"
    root.write_text("include: [included.yaml]\nitems: not-a-list\n")

    import yaml

    with pytest.raises(AttributeError, match="extend"):
        ProxyConfig()._process_includes(yaml.safe_load(root.read_text()), str(tmp_path))
    with pytest.raises(AttributeError, match="extend"):
        resolve_litellm_direct_config(root)


@pytest.mark.parametrize("include", ["child.yaml", "true"])
def test_include_must_be_a_list(tmp_path, include):
    root = tmp_path / "config.yaml"
    root.write_text(f"include: {include}\n")
    with pytest.raises(ValueError, match="include"):
        resolve_litellm_direct_config(root)


def test_resolver_matches_pinned_litellm_for_overlay_and_nested_include(
    tmp_path,
):
    from litellm.proxy.proxy_server import ProxyConfig

    nested = tmp_path / "nested.yaml"
    nested.write_text("admin:\n  enabled: true\n")
    first = tmp_path / "first.yaml"
    first.write_text(
        "include: [nested.yaml]\nadmin:\n  enabled: false\nitems: [first]\n"
    )
    second = tmp_path / "second.yaml"
    second.write_text("mode: second\nitems: [second]\n")
    root = tmp_path / "config.yaml"
    root.write_text("include: [first.yaml, second.yaml]\nmode: root\nitems: [root]\n")

    import yaml

    upstream_input = yaml.safe_load(root.read_text())
    expected = ProxyConfig()._process_includes(upstream_input, str(tmp_path))
    assert resolve_litellm_direct_config(root) == expected
