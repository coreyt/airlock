from __future__ import annotations

import json

import pytest

from airlock.measurement_report import build_measurement_report, iter_jsonl_records, main


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
    assert json.loads(capsys.readouterr().out)["dispositions"] == {
        "batch-a": "enforce"
    }


def test_iter_jsonl_records_skips_invalid_json(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text(
        '{"model": "good"}\nnot-json\n["not", "an object"]\n', encoding="utf-8"
    )

    assert list(iter_jsonl_records([path])) == [{"model": "good"}]
