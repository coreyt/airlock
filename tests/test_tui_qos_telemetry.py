"""Slice 90 contracts for QoS and exporter-health operator diagnostics."""

from __future__ import annotations

from airlock.admin.http import _view_clients
from airlock.tui.screens.overview import (
    _render_client_priority,
    _render_telemetry_health,
)
from airlock import telemetry_health


def test_priority_snapshot_is_bounded_and_labels_disabled_admission(
    fresh_state_store, monkeypatch
) -> None:
    monkeypatch.setattr(
        "airlock.admin.http.get_settings",
        lambda: type(
            "Settings", (), {"admission": type("Admission", (), {"enabled": False})()}
        )(),
    )
    fresh_state_store.record_client_priority(
        "alice", 0.8765, True, ["interactive_session(avg_gap=1.0s)"] * 8, 123.0
    )

    payload = _view_clients()
    priority = payload["clients"]["alice"]["priority"]

    assert payload["source"] == "live_admin"
    assert priority == {
        "score": 0.876,
        "boost": True,
        "reasons": ["interactive_session(avg_gap=1.0s)"] * 4,
        "observed_at": 123.0,
        "admission_enabled": False,
    }


def test_telemetry_endpoint_and_error_are_safe(monkeypatch) -> None:
    monkeypatch.setattr(telemetry_health, "_health", {})
    telemetry_health.configure_exporter(
        "otlp", enabled=True, endpoint="https://secret:pw@trace.example:4318/path?q=key"
    )
    telemetry_health.record_signal("otlp")
    raw_error = "provider response sentinel must not be shown"
    telemetry_health.record_export_failure("otlp", raw_error)

    snapshot = telemetry_health.telemetry_snapshot()

    assert snapshot["otlp"]["endpoint"] == "https://trace.example:4318"
    assert snapshot["otlp"]["signals"] == 1
    assert snapshot["otlp"]["failures"] == 1
    assert snapshot["otlp"]["last_error"] == "export_error"
    assert raw_error not in str(snapshot)
    assert "secret" not in str(snapshot)
    assert "?" not in str(snapshot)


def test_malformed_telemetry_endpoint_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(telemetry_health, "_health", {})

    telemetry_health.configure_exporter(
        "otlp", enabled=True, endpoint="https://trace.example:bad/path"
    )

    assert telemetry_health.telemetry_snapshot()["otlp"]["endpoint"] is None


def test_telemetry_renderer_labels_unavailable_and_never_claims_delivery() -> None:
    assert "unavailable" in _render_telemetry_health(None)

    rendered = _render_telemetry_health(
        {"prometheus": {"enabled": True, "signals": 4, "endpoint": "http://in-process"}}
    )

    assert "prometheus: enabled" in rendered
    assert "signals=4" in rendered
    assert "successfully exported" not in rendered


def test_stale_priority_is_not_rendered_as_active() -> None:
    line, signals = _render_client_priority(
        {"score": 0.9, "boost": True, "observed_at": 1.0, "admission_enabled": True},
        now=200.0,
    )

    assert "stale" in line
    assert "active boost" not in line
    assert signals == ""
