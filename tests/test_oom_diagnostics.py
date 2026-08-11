"""Safety properties for the opt-in OOM diagnostic recorder."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from airlock.callbacks.oom_diagnostics import OOMDiagnostics


def test_disabled_diagnostics_do_not_create_an_artifact(monkeypatch, tmp_path):
    monkeypatch.delenv("AIRLOCK_OOM_DIAGNOSTICS", raising=False)
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS_DIR", str(tmp_path))

    diagnostics = OOMDiagnostics()
    data = {"metadata": {}}
    diagnostics.pre_call(data)

    assert data == {"metadata": {}}
    assert list(tmp_path.iterdir()) == []


def test_enabled_records_counters_but_never_request_or_response_content(monkeypatch, tmp_path):
    secret = "do-not-write-this-request-or-response"
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS", "1")
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS_DIR", str(tmp_path))
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS_MAX_RECORDS", "20")

    diagnostics = OOMDiagnostics()
    data = {"messages": [{"content": secret}], "metadata": {"Authorization": secret}}
    diagnostics.pre_call(data)
    diagnostics.post_call(data)
    diagnostics.record_event(
        SimpleNamespace(
            guardrail_meta=dict(data["metadata"]), success=True,
            response_obj=secret, error=secret,
        )
    )

    artifact = next(tmp_path.glob("*.jsonl"))
    content = artifact.read_text()
    records = [json.loads(line) for line in content.splitlines()]
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert secret not in content
    assert {record["phase"] for record in records} >= {
        "diagnostics_started", "request_entry", "provider_response", "callback_complete"
    }
    completion = next(record for record in records if record["phase"] == "callback_complete")
    assert completion["outcome"] == "success"
    assert completion["in_flight"] == 0
    assert "allocator" in completion and "transport" in completion


def test_diagnostics_are_bounded_and_trim_skips_inflight(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS", "true")
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS_DIR", str(tmp_path))
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS_MAX_RECORDS", "4")
    diagnostics = OOMDiagnostics()
    diagnostics.pre_call({"metadata": {}})
    diagnostics._signal_snapshot("signal_usr2", trim=True)
    deadline = time.monotonic() + 2
    while diagnostics._signal_lock.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not diagnostics._signal_lock.locked()
    for _ in range(8):
        diagnostics.snapshot("extra")

    records = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    assert len(records) == 4
    assert any(record["phase"] == "signal_usr2_skipped_in_flight" for record in records)


def test_tracemalloc_can_be_disabled_for_a_low_perturbation_replay(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS", "1")
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS_TRACEMALLOC", "0")
    monkeypatch.setenv("AIRLOCK_OOM_DIAGNOSTICS_DIR", str(tmp_path))

    diagnostics = OOMDiagnostics()
    diagnostics.pre_call({"metadata": {}})

    record = json.loads(next(tmp_path.glob("*.jsonl")).read_text().splitlines()[0])
    assert record["tracemalloc"] == {"current": 0, "peak": 0}
