"""Tests for advisor data-gathering tools."""

import json
import time
import datetime

from airlock.advisor.tools import (
    get_state_snapshot,
    get_recent_errors,
    get_config,
    get_guard_signals,
    get_client_profile,
    get_model_profile,
    get_knobs,
    TOOL_REGISTRY,
)
from airlock.fast.state import CircuitState


# ---------------------------------------------------------------------------
# 1. get_state_snapshot
# ---------------------------------------------------------------------------


def test_get_state_snapshot_populated(fresh_state_store):
    """Populated store returns client, model, provider, and spend dicts."""
    store = fresh_state_store
    now = time.time()

    client = store.get_client("client-1")
    client.record_request(now)
    client.record_request(now)
    client.record_success(now, 400.0)
    client.record_error(now, "TimeoutError")
    client.threat_score = 0.5

    model = store.get_model("claude-sonnet")
    model.record_success(now, 300.0)

    provider = store.get_provider("anthropic")
    provider.record_request(now)
    provider.record_success(now)

    spend = store.get_provider_spend("anthropic")
    spend.record_spend(now, 12.50)

    result = get_state_snapshot(store)

    assert "client-1" in result["clients"]
    c = result["clients"]["client-1"]
    assert c["request_count"] == 2
    assert isinstance(c["error_rate"], float)
    assert isinstance(c["avg_latency_ms"], float)
    assert c["threat_score"] == 0.5
    assert isinstance(c["in_backoff"], bool)

    assert "claude-sonnet" in result["models"]
    m = result["models"]["claude-sonnet"]
    assert m["circuit"] == "closed"
    assert m["consecutive_failures"] == 0

    assert "anthropic" in result["providers"]
    p = result["providers"]["anthropic"]
    assert p["request_count"] >= 1
    assert isinstance(p["error_rate"], float)
    assert isinstance(p["quarantined"], bool)
    assert isinstance(p["impacted_clients"], list)

    assert "anthropic" in result["provider_spend"]
    assert result["provider_spend"]["anthropic"]["daily_spend_usd"] >= 0


def test_get_state_snapshot_empty(fresh_state_store):
    """Empty store returns empty sub-dicts."""
    result = get_state_snapshot(fresh_state_store)
    assert result["clients"] == {}
    assert result["models"] == {}
    assert result["providers"] == {}
    assert result["provider_spend"] == {}


# ---------------------------------------------------------------------------
# 2. get_recent_errors
# ---------------------------------------------------------------------------


def test_get_recent_errors_filters_failures(log_dir):
    """Only failure records are counted."""
    today = datetime.date.today()
    log_file = log_dir / f"airlock-{today.isoformat()}.jsonl"

    records = [
        {
            "timestamp": "2025-01-01T10:00:00Z",
            "success": True,
            "model": "gpt-4o",
            "error": None,
            "error_type": None,
            "airlock_client": "c1",
        },
        {
            "timestamp": "2025-01-01T10:01:00Z",
            "success": False,
            "model": "gpt-4o",
            "error": "boom",
            "error_type": "RateLimitError",
            "airlock_client": "c1",
        },
        {
            "timestamp": "2025-01-01T10:02:00Z",
            "success": False,
            "model": "claude-sonnet",
            "error": "timeout",
            "error_type": "TimeoutError",
            "airlock_client": "c2",
        },
    ]
    with open(log_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    result = get_recent_errors(str(log_dir), days=2)
    assert result["total_errors"] == 2


def test_get_recent_errors_groups_by_model(log_dir):
    """Failures are grouped by model."""
    today = datetime.date.today()
    log_file = log_dir / f"airlock-{today.isoformat()}.jsonl"

    records = [
        {
            "timestamp": "2025-01-01T10:01:00Z",
            "success": False,
            "model": "gpt-4o",
            "error": "boom",
            "error_type": "RateLimitError",
            "airlock_client": "c1",
        },
        {
            "timestamp": "2025-01-01T10:02:00Z",
            "success": False,
            "model": "gpt-4o",
            "error": "boom2",
            "error_type": "RateLimitError",
            "airlock_client": "c1",
        },
        {
            "timestamp": "2025-01-01T10:03:00Z",
            "success": False,
            "model": "claude-sonnet",
            "error": "timeout",
            "error_type": "TimeoutError",
            "airlock_client": "c2",
        },
    ]
    with open(log_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    result = get_recent_errors(str(log_dir), days=2)
    assert result["by_model"]["gpt-4o"] == 2
    assert result["by_model"]["claude-sonnet"] == 1
    assert result["by_error_type"]["RateLimitError"] == 2
    assert result["by_error_type"]["TimeoutError"] == 1
    assert result["by_client"]["c1"] == 2
    assert result["by_client"]["c2"] == 1
    assert len(result["recent_samples"]) == 3


# ---------------------------------------------------------------------------
# 3. get_config
# ---------------------------------------------------------------------------


def test_get_config_redacts_keys(tmp_path):
    """Sensitive keys are redacted."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model_list:\n"
        "  - model_name: claude-sonnet\n"
        "    litellm_params:\n"
        "      model: anthropic/claude-sonnet-4-20250514\n"
        "      api_key: sk-secret-123\n"
        "general_settings:\n"
        "  master_key: super-secret\n"
    )

    result = get_config(str(config_path))
    params = result["model_list"][0]["litellm_params"]
    assert params["api_key"] == "***REDACTED***"
    assert params["model"] == "anthropic/claude-sonnet-4-20250514"
    assert result["general_settings"]["master_key"] == "***REDACTED***"


def test_get_config_missing_file():
    """Missing config returns error dict."""
    result = get_config("/nonexistent/config.yaml")
    assert "error" in result


# ---------------------------------------------------------------------------
# 4. get_guard_signals
# ---------------------------------------------------------------------------


def test_get_guard_signals_filters_by_guardrail(log_dir):
    """Observations can be filtered by guardrail name."""
    today = datetime.date.today()
    log_file = log_dir / f"airlock-{today.isoformat()}.jsonl"

    records = [
        {
            "timestamp": "2025-01-01T10:00:00Z",
            "success": True,
            "model": "gpt-4o",
            "airlock_client": "c1",
            "airlock_observation": {
                "signals": [
                    {
                        "guardrail_name": "pii_scan",
                        "detected": True,
                        "score": 0.8,
                        "details": {},
                        "duration_ms": 1.0,
                    },
                ],
                "client_id": "c1",
                "model": "gpt-4o",
            },
        },
        {
            "timestamp": "2025-01-01T10:01:00Z",
            "success": True,
            "model": "claude-sonnet",
            "airlock_client": "c2",
            "airlock_observation": {
                "signals": [
                    {
                        "guardrail_name": "keyword_scan",
                        "detected": False,
                        "score": 0.0,
                        "details": {},
                        "duration_ms": 0.5,
                    },
                ],
                "client_id": "c2",
                "model": "claude-sonnet",
            },
        },
    ]
    with open(log_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    result = get_guard_signals(str(log_dir), days=2, guardrail="pii_scan")
    assert result["total_observations"] == 1
    assert len(result["signals"]) == 1
    assert result["signals"][0]["guardrail_name"] == "pii_scan"


# ---------------------------------------------------------------------------
# 5. get_client_profile
# ---------------------------------------------------------------------------


def test_get_client_profile_combines_sources(fresh_state_store, log_dir):
    """Combines realtime state and historical logs."""
    store = fresh_state_store
    now = time.time()

    client = store.get_client("c1")
    client.record_request(now)
    client.record_success(now, 500.0)
    client.threat_score = 0.2

    today = datetime.date.today()
    log_file = log_dir / f"airlock-{today.isoformat()}.jsonl"
    records = [
        {
            "timestamp": "2025-01-01T10:00:00Z",
            "success": True,
            "model": "gpt-4o",
            "airlock_client": "c1",
            "duration_ms": 300,
        },
        {
            "timestamp": "2025-01-01T10:01:00Z",
            "success": False,
            "model": "gpt-4o",
            "error_type": "TimeoutError",
            "airlock_client": "c1",
            "duration_ms": 500,
        },
    ]
    with open(log_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    result = get_client_profile(store, str(log_dir), "c1")
    assert result["client_id"] == "c1"
    assert "realtime" in result
    assert result["realtime"]["request_count"] == 1
    assert "historical" in result
    assert result["historical"]["total_requests"] == 2
    assert result["historical"]["total_errors"] == 1
    assert "gpt-4o" in result["historical"]["models_used"]


# ---------------------------------------------------------------------------
# 6. get_model_profile
# ---------------------------------------------------------------------------


def test_get_model_profile_returns_circuit_state(fresh_state_store, log_dir):
    """Model profile includes circuit state."""
    store = fresh_state_store
    now = time.time()

    model = store.get_model("claude-sonnet")
    model.circuit = CircuitState.OPEN
    model.consecutive_failures = 5
    model.last_state_change = now
    model.record_success(now, 200.0)

    today = datetime.date.today()
    log_file = log_dir / f"airlock-{today.isoformat()}.jsonl"
    records = [
        {
            "timestamp": "2025-01-01T10:00:00Z",
            "success": True,
            "model": "claude-sonnet",
            "duration_ms": 300,
            "airlock_client": "c1",
        },
        {
            "timestamp": "2025-01-01T10:01:00Z",
            "success": False,
            "model": "claude-sonnet",
            "error_type": "RateLimitError",
            "duration_ms": 100,
            "airlock_client": "c1",
        },
    ]
    with open(log_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    result = get_model_profile(store, str(log_dir), "claude-sonnet")
    assert result["model_name"] == "claude-sonnet"
    # record_success resets consecutive_failures to 0 and may change circuit
    assert result["realtime"]["circuit"] in ("closed", "open", "half_open")
    assert "historical" in result
    assert result["historical"]["total_requests"] == 2
    assert result["historical"]["total_errors"] == 1


# ---------------------------------------------------------------------------
# 7. get_knobs
# ---------------------------------------------------------------------------


def test_get_knobs_missing_file(log_dir):
    """Missing knobs file returns error dict."""
    result = get_knobs(str(log_dir))
    assert "error" in result


def test_get_knobs_reads_file(log_dir):
    """Valid knobs JSON is returned."""
    knobs_path = log_dir / "airlock-knobs.json"
    knobs_data = {
        "version": "2025-01-01",
        "weights": {"pii_scan": 1.0},
        "threshold": 0.5,
    }
    knobs_path.write_text(json.dumps(knobs_data))

    result = get_knobs(str(log_dir))
    assert result["version"] == "2025-01-01"
    assert result["weights"]["pii_scan"] == 1.0


# ---------------------------------------------------------------------------
# 8. TOOL_REGISTRY
# ---------------------------------------------------------------------------


def test_tool_registry_complete():
    """Registry has entries for all tools with callable + schema."""
    expected_tools = [
        "get_state_snapshot",
        "get_recent_errors",
        "get_analysis_report",
        "get_circuit_health",
        "get_config",
        "get_guard_signals",
        "get_client_profile",
        "get_model_profile",
        "get_knobs",
    ]
    for name in expected_tools:
        assert name in TOOL_REGISTRY, f"{name} missing from TOOL_REGISTRY"
        func, schema = TOOL_REGISTRY[name]
        assert callable(func)
        assert isinstance(schema, dict)
        assert "type" in schema
        assert "description" in schema
