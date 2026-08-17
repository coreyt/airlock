"""Slice 80 contracts for bounded routing/operator diagnostics."""

from __future__ import annotations

from airlock.fast.router import apply_routing
from airlock.tui.screens.guards import _parse_entry, _render_routing


def _routing_record(complexity: str, *, model: str = "claude-haiku") -> dict:
    return {
        "timestamp": "2026-08-12T12:00:00+00:00",
        "model": model,
        "airlock_routing": {
            "smart_classify": {"complexity": complexity, "score": 0.2},
            "routed_model": model,
            "reasons": ["cost_tier(low)"],
        },
    }


def test_routing_only_record_is_visible_without_prompt_content() -> None:
    entry = _parse_entry(_routing_record("simple"))

    assert entry is not None
    assert entry.routing is not None
    assert entry.routing["smart_classify"]["complexity"] == "simple"


def test_routing_panel_labels_bounded_source_and_distribution() -> None:
    entries = [
        _parse_entry(_routing_record("simple")),
        _parse_entry(_routing_record("complex")),
        _parse_entry(_routing_record("complex")),
    ]
    assert all(entries)

    rendered = _render_routing(entries[0], entries)  # type: ignore[arg-type]

    assert "simple: 1" in rendered
    assert "complex: 2" in rendered
    assert "bounded JSONL window" in rendered
    assert "persisted request metadata" in rendered


def test_session_identifier_is_not_recorded_in_routing_metadata(
    fresh_state_store,
) -> None:
    data = {
        "model": "claude-sonnet",
        "metadata": {"airlock": {"session_id": "do-not-log"}},
    }

    result = apply_routing(data, client_id="alice")

    assert "do-not-log" not in str(result["metadata"]["airlock_routing"])


def test_routing_panel_survives_malformed_persisted_metadata() -> None:
    entry = _parse_entry(
        {
            "timestamp": "2026-08-12T12:00:00+00:00",
            "model": "claude-haiku",
            "airlock_routing": {"smart_classify": "not-a-mapping", "reasons": 1},
        }
    )
    assert entry is not None

    rendered = _render_routing(entry, [entry])

    assert "No smart-routing classifications" in rendered
