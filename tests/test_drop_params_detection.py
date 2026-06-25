"""detect_dropped_params — derived drop_params transparency (no network)."""

from __future__ import annotations

from airlock.transparency import detect_dropped_params


def test_flags_unsupported_param_for_provider() -> None:
    # anthropic does not support frequency_penalty / logit_bias.
    data = {"temperature": 0.5, "frequency_penalty": 0.2, "messages": []}
    dropped = detect_dropped_params(
        data, model="claude-3-5-sonnet-20240620", provider="anthropic"
    )
    assert "frequency_penalty" in dropped
    # temperature IS supported by anthropic — not flagged.
    assert "temperature" not in dropped


def test_does_not_flag_metadata_or_internal_keys() -> None:
    data = {
        "frequency_penalty": 0.1,
        "metadata": {"x": 1},
        "airlock_mutations": [],
        "model": "claude-3-5-sonnet-20240620",
        "messages": [],
    }
    dropped = detect_dropped_params(
        data, model="claude-3-5-sonnet-20240620", provider="anthropic"
    )
    assert "metadata" not in dropped
    assert "airlock_mutations" not in dropped
    assert "model" not in dropped
    assert "messages" not in dropped


def test_supported_param_not_flagged_for_openai() -> None:
    data = {"frequency_penalty": 0.2, "temperature": 0.5}
    dropped = detect_dropped_params(data, model="gpt-4o", provider="openai")
    # openai supports both — nothing dropped.
    assert dropped == []


def test_unknown_model_provider_returns_empty_no_raise() -> None:
    data = {"temperature": 0.5, "frequency_penalty": 0.2}
    dropped = detect_dropped_params(
        data, model="totally-made-up-model-xyz", provider="no-such-provider"
    )
    assert dropped == []


def test_return_order_stable() -> None:
    data = {"frequency_penalty": 0.1, "logit_bias": {}, "presence_penalty": 0.2}
    dropped = detect_dropped_params(
        data, model="claude-3-5-sonnet-20240620", provider="anthropic"
    )
    # all three unsupported by anthropic; order follows data insertion order.
    assert dropped == ["frequency_penalty", "logit_bias", "presence_penalty"]
