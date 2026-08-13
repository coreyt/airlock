from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import airlock.reasoning_effort as reasoning_effort
from airlock.callbacks.enterprise_logger import AirlockLogger
from airlock.callbacks.request_event import RequestRecorder, RequestRecorderCallback
from airlock.measurement_report import (
    PII_EGRESS_KIND,
    build_measurement_report,
    build_pii_egress_measurement_report,
    iter_jsonl_records,
    main,
)


def _pii_tool_response(*, tool: str, arguments: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name=tool, arguments=json.dumps(arguments)
                            )
                        )
                    ]
                )
            )
        ]
    )


def _record(
    *,
    timestamp: str = "2026-08-02T12:00:00+00:00",
    client: str | None = "batch-a",
    marker: str = "reasoning_effort_would_reject",
) -> dict:
    record = {
        "timestamp": timestamp,
        "airlock_client": client,
        "model": "gpt-5.4",
        "mutations": [{"field": marker, "before": "none", "after": "minimal"}],
    }
    if marker == "model_alias_would_reject":
        record["airlock_cross_tier_fuzzy_measurement"] = {
            "requested": "gpt-alph",
            "served": "gpt-alpha-1",
            "suggested": "gpt-alpha-2",
            "score": 0.75,
            "from_tier": "low",
            "to_tier": "high",
        }
    return record


def test_effort_report_filters_window_and_requires_explicit_dispositions():
    records = [
        _record(client="batch-a"),
        _record(client=None),
        _record(timestamp="2026-07-01T12:00:00+00:00", client="outside-window"),
        _record(marker="unrelated"),
    ]

    report = build_measurement_report(
        records,
        kind="reasoning-effort",
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-08-21T23:59:59+00:00",
        dispositions={"batch-a": "notify", "<unknown>": "investigate"},
    )

    assert report.total_events == 2
    assert report.affected_clients == ["<unknown>", "batch-a"]
    assert report.unknown_client_events == 1
    assert report.combinations == [
        {"count": 2, "requested": "none", "model": "gpt-5.4"}
    ]
    assert report.dispositions == {"<unknown>": "investigate", "batch-a": "notify"}
    assert report.undisposed_clients == []


def test_cross_tier_report_preserves_candidate_dimensions():
    report = build_measurement_report(
        [_record(marker="model_alias_would_reject")], kind="cross-tier-fuzzy"
    )

    assert report.total_events == 1
    assert report.combinations == [
        {
            "count": 1,
            "requested": "gpt-alph",
            "served": "gpt-alpha-1",
            "suggested": "gpt-alpha-2",
            "from_tier": "low",
            "to_tier": "high",
        }
    ]
    assert report.undisposed_clients == ["batch-a"]


def test_cross_tier_report_makes_pre_structured_records_visible():
    report = build_measurement_report(
        [
            _record(marker="model_alias_would_reject")
            | {"airlock_cross_tier_fuzzy_measurement": None}
        ],
        kind="cross-tier-fuzzy",
    )

    assert report.combinations == [
        {
            "count": 1,
            "requested": "<missing structured measurement>",
            "served": "<missing structured measurement>",
            "suggested": "<missing structured measurement>",
            "from_tier": "<missing structured measurement>",
            "to_tier": "<missing structured measurement>",
        }
    ]


def test_invalid_disposition_is_rejected():
    with pytest.raises(ValueError, match="invalid disposition"):
        build_measurement_report(
            [_record()], kind="reasoning-effort", dispositions={"batch-a": "ship"}
        )


def test_cli_can_gate_on_all_dispositions(tmp_path, capsys):
    records = tmp_path / "airlock-2026-08-02.jsonl"
    records.write_text(json.dumps(_record()) + "\nnot-json\n", encoding="utf-8")
    dispositions = tmp_path / "dispositions.json"
    dispositions.write_text(json.dumps({"batch-a": "enforce"}), encoding="utf-8")

    assert main(["reasoning-effort", str(records), "--require-dispositions"]) == 2
    capsys.readouterr()
    assert (
        main(
            [
                "reasoning-effort",
                str(records),
                "--dispositions",
                str(dispositions),
                "--require-dispositions",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["dispositions"] == {"batch-a": "enforce"}


def test_iter_jsonl_records_skips_invalid_json(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(
        '{"model": "good"}\nnot-json\n["not", "an object"]\n', encoding="utf-8"
    )

    assert list(iter_jsonl_records([path])) == [{"model": "good"}]


def test_pii_egress_report_is_value_free_and_requires_human_decision():
    records = [
        _record()
        | {
            "airlock_pii_egress": {
                "mode": "observe",
                "hydrated": 1,
                "would_suppress": 1,
                "decisions": [
                    {
                        "allow": False,
                        "reason": "unknown_tool",
                        "entity_type": "EMAIL_ADDRESS",
                        "tool": "unregistered_tool",
                        "path": "/recipient",
                    }
                ],
            }
        }
    ]

    report = build_pii_egress_measurement_report(records)

    assert report.kind == PII_EGRESS_KIND
    assert report.egress_events == 1
    assert report.decision_count == 1
    assert report.hydrated == 1
    assert report.would_suppress == 1
    assert report.decisions == [
        {
            "count": 1,
            "mode": "observe",
            "reason": "unknown_tool",
            "entity_type": "EMAIL_ADDRESS",
            "tool": "unregistered_tool",
            "path": "/recipient",
            "allow": False,
        }
    ]
    assert report.as_dict()["human_decision_required"] is True


def test_pii_egress_cli_rejects_automatic_disposition(tmp_path, capsys):
    records = tmp_path / "airlock-2026-08-02.jsonl"
    records.write_text(json.dumps(_record()) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["pii-egress", str(records), "--require-dispositions"])

    assert exc_info.value.code == 2
    assert "documented human DECIDE" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_pii_egress_observe_canary_round_trips_through_jsonl_and_report(
    tmp_path, monkeypatch
):
    """Exercise PII post-call → canonical event → JSONL → report, without PII sinks."""
    from airlock.guardrails.pii_guard import AirlockPIIGuard, _pii_map_store

    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("AIRLOCK_PII_EGRESS_MODE", "observe")
    # This unknown tool would be denied in shadow/enforce. Observe preserves
    # behavior while recording the would-suppress decision.
    monkeypatch.delenv("AIRLOCK_PII_EGRESS_TOOL_BANDS", raising=False)
    canary = "canary-person@example.test"
    data = {
        "model": "gpt-5.4",
        "metadata": {
            "airlock_client": "pii-dogfood-canary",
            "airlock_pii_handle": _pii_map_store.put({"<EMAIL_ADDRESS_1>": canary}),
        },
    }
    response = _pii_tool_response(
        tool="unregistered_tool", arguments={"recipient": "<EMAIL_ADDRESS_1>"}
    )

    client_response = await AirlockPIIGuard().async_post_call_success_hook(
        data, None, response
    )
    assert (
        json.loads(client_response.choices[0].message.tool_calls[0].function.arguments)[
            "recipient"
        ]
        == canary
    )
    # The response passed to telemetry remains redacted, and reverse mapping has
    # been consumed before the recorder sees metadata.
    assert (
        json.loads(response.choices[0].message.tool_calls[0].function.arguments)[
            "recipient"
        ]
        == "<EMAIL_ADDRESS_1>"
    )
    assert "airlock_pii_handle" not in data["metadata"]

    recorder = RequestRecorder()
    recorder.register(AirlockLogger().record_event, name="enterprise")
    RequestRecorderCallback(recorder).log_success_event(data, response, None, None)

    records = list(iter_jsonl_records(sorted(tmp_path.glob("airlock-*.jsonl"))))
    assert len(records) == 1
    record = records[0]
    assert "airlock_pii_map" not in record
    assert canary not in json.dumps(record)
    assert record["airlock_pii_egress"]["would_suppress"] == 1
    report = build_pii_egress_measurement_report(records)
    assert report.egress_events == 1
    assert report.decision_count == 1
    assert report.hydrated == 1
    assert report.would_suppress == 1
    assert report.decisions[0]["reason"] == "unknown_tool"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "success"),
    [
        ("reasoning-effort", True),
        ("reasoning-effort", False),
    ],
)
async def test_measurement_markers_round_trip_through_real_enterprise_jsonl(
    kind,
    success,
    tmp_path,
    monkeypatch,
):
    """Exercise producer → recorder callback → AirlockLogger → JSONL → report."""
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    client = "measurement-client"
    monkeypatch.setattr(
        reasoning_effort, "_supported_efforts", lambda _model: frozenset({"minimal"})
    )
    data = {
        "model": "gpt-5.4",
        "reasoning_effort": "none",
        "metadata": {"airlock_client": client},
    }
    reasoning_effort.normalize_reasoning_effort(data, "openai", client)

    recorder = RequestRecorder()
    recorder.register(AirlockLogger().record_event, name="enterprise")
    callback = RequestRecorderCallback(recorder)
    if success:
        await callback.async_log_success_event(data, None, None, None)
    else:
        data["exception"] = RuntimeError("synthetic callback failure")
        await callback.async_log_failure_event(data, None, None, None)

    records = list(iter_jsonl_records(sorted(tmp_path.glob("airlock-*.jsonl"))))
    report = build_measurement_report(records, kind=kind)

    assert len(records) == 1
    assert records[0]["success"] is success
    assert report.total_events == 1
    assert report.affected_clients == [client]
