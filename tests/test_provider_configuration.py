"""Redaction and bounds for Slice 40's startup configuration snapshot."""

from __future__ import annotations

import json

from airlock.provider_configuration import (
    configure_provider_configuration,
    provider_configuration_snapshot,
)


def _entry(name: str, model: str, **params):
    return {
        "model_name": name,
        "litellm_params": {"model": model, **params},
    }


def test_snapshot_is_redacted_stable_and_models_capability_truth():
    sentinel = "sk-SLICE40-DO-NOT-LEAK"
    config = {
        "model_list": [
            _entry(
                "vertex/chat",
                "vertex_ai/gemini-2.5-pro",
                vertex_location="us-central1",
                api_key="os.environ/VERTEX_SECRET_NAME",
                api_base=f"https://user:{sentinel}@api.example.test/v1?secret={sentinel}",
            ),
            _entry(
                "enhanced",
                "enhanced/profile",
                enhanced_profile={"target_model": "anthropic/claude"},
                api_key=sentinel,
            ),
            _entry("no-key", "openai/gpt-test"),
            _entry(
                "vertex-credential",
                "vertex_ai/gemini-2.5-flash",
                vertex_credentials="os.environ/VERTEX_CREDENTIALS",
                api_base="https://[",
            ),
        ]
    }

    configure_provider_configuration(
        config,
        getenv=lambda name: (
            sentinel if name in {"VERTEX_SECRET_NAME", "VERTEX_CREDENTIALS"} else None
        ),
        loaded_at="2026-08-16T12:00:00Z",
    )
    snapshot = provider_configuration_snapshot()
    encoded = json.dumps(snapshot, sort_keys=True)

    assert snapshot["source"] == "startup_config"
    assert snapshot["restart_required"] is True
    assert snapshot["schema_version"] == 1
    assert snapshot["loaded_at"] == "2026-08-16T12:00:00Z"
    assert sentinel not in encoded
    assert "VERTEX_SECRET_NAME" not in encoded
    assert "/v1" not in encoded and "user:" not in encoded
    vertex = next(p for p in snapshot["providers"] if p["provider"] == "vertex_ai")
    alias = next(
        alias for alias in vertex["aliases"] if alias["alias"] == "vertex/chat"
    )
    assert alias["api_base_host"] == "api.example.test"
    assert alias["credential"] == {"kind": "env_ref", "configured": True}
    assert alias["endpoints"] == ["chat", "batch"]
    assert alias["region"] == "us-central1"
    assert next(
        a for p in snapshot["providers"] for a in p["aliases"] if a["alias"] == "no-key"
    )["credential"] == {"kind": "none", "configured": False}
    vertex_credential = next(
        a
        for p in snapshot["providers"]
        for a in p["aliases"]
        if a["alias"] == "vertex-credential"
    )
    assert vertex_credential["credential"] == {"kind": "env_ref", "configured": True}
    assert vertex_credential["api_base_host"] is None
    configure_provider_configuration(
        {"model_list": [config["model_list"][-1]]},
        getenv=lambda _name: None,
        loaded_at="2026-08-16T12:00:00Z",
    )
    assert provider_configuration_snapshot()["providers"][0]["aliases"][0][
        "credential"
    ] == {"kind": "env_ref", "configured": False}


def test_snapshot_bounds_and_fingerprint_do_not_oracle_literal_secret():
    provider_bound = {
        "model_list": [_entry(f"p{i}/alias", f"p{i}/model") for i in range(65)]
    }
    configure_provider_configuration(provider_bound, loaded_at="t")
    first = provider_configuration_snapshot()
    assert len(first["providers"]) == 64
    assert first["truncated"]["providers"] is True

    def build(secret: str):
        return {
            "model_list": [
                _entry(f"p0/alias{i}", f"p0/model{i}", api_key=secret)
                for i in range(201)
            ]
        }

    configure_provider_configuration(build("first-secret"), loaded_at="t")
    first = provider_configuration_snapshot()
    configure_provider_configuration(build("different-secret"), loaded_at="t")
    second = provider_configuration_snapshot()

    assert sum(len(p["aliases"]) for p in second["providers"]) == 200
    assert second["truncated"] == {"providers": False, "aliases": True}
    assert first["fingerprint"] == second["fingerprint"]

    configure_provider_configuration(
        {"model_list": [_entry("env", "openai/env", api_key="os.environ/KEY")]},
        getenv=lambda _name: None,
        loaded_at="t",
    )
    absent = provider_configuration_snapshot()
    configure_provider_configuration(
        {"model_list": [_entry("env", "openai/env", api_key="os.environ/KEY")]},
        getenv=lambda _name: "now-present",
        loaded_at="t",
    )
    present = provider_configuration_snapshot()
    assert absent["fingerprint"] == present["fingerprint"]
