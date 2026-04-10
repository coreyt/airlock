"""Advisor data-gathering tools for querying Airlock operational state."""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from airlock.fast.state import StateStore


# ---------------------------------------------------------------------------
# Log loading (copied from analyzer pattern — not importing private fn)
# ---------------------------------------------------------------------------


def _load_logs(log_dir: str, days: int = 7) -> list[dict[str, Any]]:
    """Load JSONL records from the last *days* days."""
    records: list[dict[str, Any]] = []
    today = datetime.utcnow().date()
    log_path = Path(log_dir)

    for i in range(days):
        day = today - timedelta(days=i)
        file_path = log_path / f"airlock-{day.isoformat()}.jsonl"
        if not file_path.exists():
            continue
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return records


# ---------------------------------------------------------------------------
# Config redaction helper
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = {"api_key", "key", "secret"}


def _redact(obj: Any) -> Any:
    """Recursively redact sensitive keys in nested dicts/lists."""
    if isinstance(obj, dict):
        return {
            k: "***REDACTED***"
            if any(s in k.lower() for s in _SENSITIVE_KEYS)
            else _redact(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Tool 1: get_state_snapshot
# ---------------------------------------------------------------------------


def get_state_snapshot(store: StateStore) -> dict:
    """Serialize the StateStore into a compact dict."""
    clients: dict[str, dict] = {}
    for name, cs in store.all_clients().items():
        clients[name] = {
            "request_count": cs.recent_request_count(),
            "error_rate": cs.recent_error_rate(),
            "avg_latency_ms": cs.recent_avg_latency(),
            "threat_score": cs.threat_score,
            "in_backoff": cs.is_in_backoff(),
        }

    models: dict[str, dict] = {}
    for name, ms in store.all_models().items():
        models[name] = {
            "circuit": ms.circuit.value,
            "consecutive_failures": ms.consecutive_failures,
            "last_state_change": ms.last_state_change,
        }

    providers: dict[str, dict] = {}
    for name, ps in store.all_providers().items():
        providers[name] = {
            "request_count": ps.recent_request_count(),
            "error_rate": ps.recent_error_rate(),
            "quarantined": ps.is_quarantined(),
            "impacted_clients": list(ps.impacted_clients()),
        }

    provider_spend: dict[str, dict] = {}
    # Access internal _provider_spend since there's no all_provider_spend()
    with store._lock:
        for name, spend in store._provider_spend.items():
            provider_spend[name] = {
                "daily_spend_usd": spend.recent_spend(),
            }

    return {
        "clients": clients,
        "models": models,
        "providers": providers,
        "provider_spend": provider_spend,
    }


# ---------------------------------------------------------------------------
# Tool 2: get_recent_errors
# ---------------------------------------------------------------------------


def get_recent_errors(log_dir: str, days: int = 2) -> dict:
    """Load JSONL logs, filter to failures, group by model and error_type."""
    records = _load_logs(log_dir, days=days)
    failures = [r for r in records if not r.get("success")]

    by_model: Counter = Counter()
    by_error_type: Counter = Counter()
    by_client: Counter = Counter()

    for r in failures:
        by_model[r.get("model", "unknown")] += 1
        by_error_type[r.get("error_type", "unknown")] += 1
        by_client[r.get("airlock_client", "unknown")] += 1

    recent_samples = [
        {
            "timestamp": r.get("timestamp", ""),
            "model": r.get("model", ""),
            "error": r.get("error", ""),
            "airlock_client": r.get("airlock_client", ""),
        }
        for r in failures[-10:]
    ]

    return {
        "total_errors": len(failures),
        "by_model": dict(by_model),
        "by_error_type": dict(by_error_type),
        "by_client": dict(by_client),
        "recent_samples": recent_samples,
    }


# ---------------------------------------------------------------------------
# Tool 3: get_analysis_report
# ---------------------------------------------------------------------------


def get_analysis_report(days: int = 7) -> dict:
    """Run analyzer.analyze() and serialize to dict."""
    try:
        from airlock.slow.analyzer import analyze

        report = analyze(days=days)
        return dataclasses.asdict(report)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 4: get_circuit_health
# ---------------------------------------------------------------------------


def get_circuit_health(store: StateStore) -> dict:
    """Wrap health.get_circuit_health()."""
    from airlock.health import get_circuit_health as _get_circuit_health

    return _get_circuit_health(store)


# ---------------------------------------------------------------------------
# Tool 5: get_config
# ---------------------------------------------------------------------------


def get_config(config_path: str) -> dict:
    """Read and parse config.yaml with sensitive key redaction."""
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return _redact(data)
    except FileNotFoundError:
        return {"error": f"Config file not found: {config_path}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 6: get_guard_signals
# ---------------------------------------------------------------------------


def get_guard_signals(
    log_dir: str,
    days: int = 2,
    guardrail: str | None = None,
    client: str | None = None,
) -> dict:
    """Load JSONL logs, extract airlock_observation fields."""
    records = _load_logs(log_dir, days=days)

    observations: list[dict] = []
    for r in records:
        obs = r.get("airlock_observation")
        if not obs:
            continue
        # Filter by client if specified
        if client and obs.get("client_id") != client:
            continue
        observations.append(obs)

    # Flatten signals from all observations
    all_signals: list[dict] = []
    for obs in observations:
        for sig in obs.get("signals", []):
            all_signals.append(sig)

    # Filter by guardrail name if specified
    if guardrail:
        all_signals = [s for s in all_signals if s.get("guardrail_name") == guardrail]
        observations = [
            obs
            for obs in observations
            if any(s.get("guardrail_name") == guardrail for s in obs.get("signals", []))
        ]

    # Aggregate by guardrail_name
    by_guardrail: dict[str, list[float]] = {}
    for sig in all_signals:
        name = sig.get("guardrail_name", "unknown")
        by_guardrail.setdefault(name, []).append(sig.get("score", 0.0))

    signals_summary = []
    for name, scores in by_guardrail.items():
        signals_summary.append(
            {
                "guardrail_name": name,
                "detected_count": len(scores),
                "avg_score": sum(scores) / len(scores) if scores else 0.0,
            }
        )

    filtered_samples = observations[-10:]

    return {
        "total_observations": len(observations),
        "signals": signals_summary,
        "filtered_samples": filtered_samples,
    }


# ---------------------------------------------------------------------------
# Tool 7: get_client_profile
# ---------------------------------------------------------------------------


def get_client_profile(store: StateStore, log_dir: str, client_id: str) -> dict:
    """Combine StateStore data with log analysis for one client."""
    # Realtime from StateStore
    cs = store.get_client(client_id)
    realtime = {
        "request_count": cs.recent_request_count(),
        "error_rate": cs.recent_error_rate(),
        "avg_latency_ms": cs.recent_avg_latency(),
        "threat_score": cs.threat_score,
        "in_backoff": cs.is_in_backoff(),
    }

    # Historical from logs
    records = _load_logs(log_dir, days=7)
    client_records = [r for r in records if r.get("airlock_client") == client_id]
    failures = [r for r in client_records if not r.get("success")]

    models_used: Counter = Counter()
    error_types: Counter = Counter()
    for r in client_records:
        models_used[r.get("model", "unknown")] += 1
    for r in failures:
        error_types[r.get("error_type", "unknown")] += 1

    historical = {
        "total_requests": len(client_records),
        "total_errors": len(failures),
        "models_used": dict(models_used),
        "error_types": dict(error_types),
    }

    return {
        "client_id": client_id,
        "realtime": realtime,
        "historical": historical,
    }


# ---------------------------------------------------------------------------
# Tool 8: get_model_profile
# ---------------------------------------------------------------------------


def get_model_profile(store: StateStore, log_dir: str, model_name: str) -> dict:
    """Combine StateStore data with log analysis for one model."""
    ms = store.get_model(model_name)
    realtime = {
        "circuit": ms.circuit.value,
        "consecutive_failures": ms.consecutive_failures,
        "avg_latency_ms": ms.recent_avg_latency(),
    }

    records = _load_logs(log_dir, days=7)
    model_records = [r for r in records if r.get("model") == model_name]
    successes = [r for r in model_records if r.get("success")]
    failures = [r for r in model_records if not r.get("success")]

    durations = [r.get("duration_ms", 0) for r in model_records if r.get("duration_ms")]
    avg_duration = sum(durations) / len(durations) if durations else 0.0

    error_types: Counter = Counter()
    for r in failures:
        error_types[r.get("error_type", "unknown")] += 1

    total = len(model_records)
    historical = {
        "total_requests": total,
        "successes": len(successes),
        "failures": len(failures),
        "error_rate": len(failures) / total if total > 0 else 0.0,
        "avg_duration_ms": avg_duration,
        "error_types": dict(error_types),
    }

    return {
        "model_name": model_name,
        "realtime": realtime,
        "historical": historical,
    }


# ---------------------------------------------------------------------------
# Tool 9: get_knobs
# ---------------------------------------------------------------------------


def get_knobs(log_dir: str) -> dict:
    """Read airlock-knobs.json from the log dir."""
    knobs_path = Path(log_dir) / "airlock-knobs.json"
    if not knobs_path.exists():
        return {"error": "not found"}
    try:
        with open(knobs_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool 10: TOOL_REGISTRY
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, tuple[Callable, dict]] = {
    "get_state_snapshot": (
        get_state_snapshot,
        {
            "type": "object",
            "properties": {},
            "required": [],
            "description": "Get real-time state snapshot of all clients, models, and providers",
        },
    ),
    "get_recent_errors": (
        get_recent_errors,
        {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back",
                    "default": 2,
                },
            },
            "required": [],
            "description": "Get recent error records grouped by model, client, and error type",
        },
    ),
    "get_analysis_report": (
        get_analysis_report,
        {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to analyze",
                    "default": 7,
                },
            },
            "required": [],
            "description": "Run full analysis pipeline and return report",
        },
    ),
    "get_circuit_health": (
        get_circuit_health,
        {
            "type": "object",
            "properties": {},
            "required": [],
            "description": "Get circuit breaker health status for all models",
        },
    ),
    "get_config": (
        get_config,
        {
            "type": "object",
            "properties": {
                "config_path": {
                    "type": "string",
                    "description": "Path to config.yaml file",
                },
            },
            "required": ["config_path"],
            "description": "Read and parse config.yaml with sensitive key redaction",
        },
    ),
    "get_guard_signals": (
        get_guard_signals,
        {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back",
                    "default": 2,
                },
                "guardrail": {
                    "type": "string",
                    "description": "Filter by guardrail name",
                },
                "client": {
                    "type": "string",
                    "description": "Filter by client ID",
                },
            },
            "required": [],
            "description": "Get guardrail observation signals from logs",
        },
    ),
    "get_client_profile": (
        get_client_profile,
        {
            "type": "object",
            "properties": {
                "client_id": {
                    "type": "string",
                    "description": "Client ID to profile",
                },
            },
            "required": ["client_id"],
            "description": "Get combined realtime and historical profile for a client",
        },
    ),
    "get_model_profile": (
        get_model_profile,
        {
            "type": "object",
            "properties": {
                "model_name": {
                    "type": "string",
                    "description": "Model name to profile",
                },
            },
            "required": ["model_name"],
            "description": "Get combined realtime and historical profile for a model",
        },
    ),
    "get_knobs": (
        get_knobs,
        {
            "type": "object",
            "properties": {},
            "required": [],
            "description": "Read current guardrail tuning knobs",
        },
    ),
}
