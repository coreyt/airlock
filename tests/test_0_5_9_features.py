"""RED/GREEN acceptance tests for the 0.5.9 internal milestone."""

from __future__ import annotations

from pathlib import Path
import time
from unittest.mock import MagicMock

import pytest

from airlock.callbacks.request_event import build_request_event
from airlock.admin.http import handle_admin_request
from airlock.admin.policy import Principal
from airlock.fast.state import store
from airlock.guardrails.code_inspection import inspect_code
from airlock.guardrails.response_scanner import (
    _code_inspection_should_block,
    response_scanner,
)
from airlock.guardrails.schemas import GuardrailSignal, default_knobs
from airlock.guardrails.orchestrator import evaluate
from airlock.guardrails.semantic import (
    ClassifierMetadata,
    ClassifierResult,
    run_classifiers,
)
from airlock.guardrails.semantic import corpus_equivalence_report
from airlock.slow.analyzer import find_semantic_insights
from airlock.slow.analyzer_llm import _run_tool_loop, analyze_with_llm, reduced_dataset
from airlock.slow.analyzer_llm import AnthropicSandboxExecutor, remote_dataset
from airlock.slow import analyzer as analyzer_module
from airlock.tui.mcp_manager import McpServerManager, _startup_timeout
from airlock.tui.screens.guards import FlowEntry, _parse_entry, _render_mutations
from airlock.tui.screens.logs import _analysis_days, _is_blocked_request
from airlock.tui.alert_engine import _check_provider_budget


class _Classifier:
    def __init__(
        self, name: str, blocked: bool, metadata: ClassifierMetadata | None = None
    ):
        self.name, self.blocked, self.metadata = name, blocked, metadata
        self.calls = 0

    async def classify(self, text: str) -> ClassifierResult:
        self.calls += 1
        return ClassifierResult(
            self.name, 1.0 if self.blocked else 0.0, 0.5, self.blocked, "test", 1
        )


def test_startup_timeout_validation_is_scoped_to_managed_health_servers():
    assert _startup_timeout(
        {"startup_timeout": 30, "health_url": "http://localhost/ready"}
    ) == (30, None)
    assert "health_url" in _startup_timeout({"startup_timeout": 30})[1]
    assert (
        "positive integer"
        in _startup_timeout({"startup_timeout": 0, "health_url": "x"})[1]
    )
    assert _startup_timeout({"health_url": "x"}) == (None, None)


def test_invalid_managed_timeout_is_visible_and_prevents_spawn(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "mcp_servers:\n  owned:\n    airlock_managed:\n      command: echo\n      cwd: /tmp\n      startup_timeout: 301\n      health_url: http://localhost/ready\n"
    )
    manager = McpServerManager()
    manager.load_config(path)
    assert "positive integer" in manager.start_server("owned")


@pytest.mark.asyncio
async def test_adaptive_light_block_records_only_executed_as_selected(monkeypatch):
    monkeypatch.setenv("AIRLOCK_SEMANTIC_SELECTION", "adaptive")
    light = _Classifier("light", True, ClassifierMetadata(cost_class="light"))
    heavy = _Classifier("heavy", False, ClassifierMetadata(cost_class="heavy"))
    verdict = await run_classifiers([light, heavy], "sufficient text")
    assert light.calls == 1 and heavy.calls == 0
    assert verdict.selected == ["light"]
    assert verdict.short_circuited == [
        {"name": "heavy", "reason": "blocking_light_tier"}
    ]


def test_code_inspection_never_retains_matched_source_or_pii():
    secret = "alice@example.com"
    result = inspect_code(f"```python\nopen('/etc/passwd'); email = '{secret}'\n```")
    assert result["findings"]["pii:EMAIL_ADDRESS"] == 1
    assert result["findings"]["resource_access"] == 1
    assert secret not in str(result)
    assert result["enforcement_weight"] == 0.0


def test_code_inspection_has_an_explicit_zero_default_knob_and_can_be_weighted():
    knobs = default_knobs()
    signal = GuardrailSignal("code_inspection", True, 1.0, {}, 1.0)
    assert knobs.weights["code_inspection"] == 0.0
    assert evaluate([signal], knobs) == 0.0
    knobs.weights["code_inspection"] = 1.0
    assert evaluate([signal], knobs) == 1.0


def test_code_inspection_enforcement_is_disabled_until_an_operator_weights_it(
    monkeypatch,
):
    inspection = {"score": 1.0}
    monkeypatch.setattr(
        "airlock.guardrails.response_scanner._get_knobs",
        lambda: MagicMock(weights={"code_inspection": 0.0}, threshold=0.5),
    )
    assert not _code_inspection_should_block(inspection)
    monkeypatch.setattr(
        "airlock.guardrails.response_scanner._get_knobs",
        lambda: MagicMock(weights={"code_inspection": 0.75}, threshold=0.5),
    )
    assert _code_inspection_should_block(inspection)


def test_request_event_normalizes_programmatic_tool_identifiers():
    event = build_request_event(
        {"metadata": {"allowed_callers": ["  My.Tool ", {"name": "OTHER"}]}},
        None,
        None,
        None,
        success=True,
    )
    assert event.programmatic_tools == ["my.tool", "other"]


def test_request_event_merges_top_level_code_inspection_with_nested_metadata():
    event = build_request_event(
        {
            "metadata": {
                "airlock_code_inspection": {"findings": {"resource_access": 1}}
            },
            "litellm_params": {"metadata": {"existing": "value"}},
        },
        None,
        None,
        None,
        success=True,
    )
    assert event.code_inspection["findings"]["resource_access"] == 1


@pytest.mark.asyncio
async def test_mcp_code_inspection_is_persisted_as_safe_observation():
    response = MagicMock()
    response.mcp_tool_call_response = [
        MagicMock(text="```python\nopen('/etc/passwd')\n```")
    ]
    kwargs: dict = {"litellm_params": {"metadata": {}}}
    await response_scanner.async_post_mcp_tool_call_hook(kwargs, response, None, None)
    inspection = kwargs["litellm_params"]["metadata"]["airlock_code_inspection"]
    assert inspection["findings"]["resource_access"] == 1
    assert "passwd" not in str(inspection)


def test_analyzer_llm_receives_aggregates_only_and_falls_back_without_credentials(
    monkeypatch,
):
    report = MagicMock()
    report.summary = {"total_requests": 1}
    report.optimizations = []
    report.semantic_insights = None
    report.hypotheses = []
    report.guardrail_tuning = {}
    assert "messages" not in reduced_dataset(report)
    monkeypatch.delenv("AIRLOCK_ANALYZER_MODEL", raising=False)
    assert analyze_with_llm(report, "ops") is None


def test_analyzer_tool_loop_allows_only_named_aggregate_queries():
    class Client:
        def __init__(self):
            self.calls = 0

        def complete_with_tools(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "tool_calls": [
                        {"id": "1", "function": {"name": "summary", "arguments": "{}"}}
                    ]
                }
            return {
                "content": '[{"narrative":"ok","evidence_references":["summary"],'
                '"confidence":0.5,"proposed_actions":[]}]'
            }

    client = Client()
    raw = _run_tool_loop(
        client,
        model="local/test",
        audience="ops",
        payload={
            "summary": {"n": 1},
            "optimizations": [],
            "semantic_insights": None,
            "hypotheses": [],
        },
    )
    assert raw and '"summary"' in raw
    assert client.calls == 2


def test_llm_analysis_path_never_writes_knobs(monkeypatch):
    monkeypatch.setattr(analyzer_module, "_load_logs", lambda days: [])
    write = MagicMock()
    monkeypatch.setattr("airlock.slow.tuner.write_knobs", write)
    analyzer_module.analyze(days=1, write_knobs=False)
    write.assert_not_called()


def test_llm_cli_selects_the_no_write_analysis_path(monkeypatch):
    from airlock.slow.cli import main

    report = MagicMock()
    report.summary = {}
    monkeypatch.setattr("airlock.slow.cli.analyze", MagicMock(return_value=report))
    monkeypatch.setattr("airlock.slow.cli._format_text", lambda _: "ok")
    monkeypatch.setattr("sys.argv", ["airlock-analyze", "--llm"])
    main()
    from airlock.slow import cli

    cli.analyze.assert_called_once_with(days=7, write_knobs=False)


def test_analysis_days_replaces_removed_days_input():
    assert _analysis_days("1h") == 1
    assert _analysis_days("6h") == 1
    assert _analysis_days("today") == 1
    assert _analysis_days("24h") == 1
    assert _analysis_days("7d") == 7
    assert _analysis_days("custom") == 31


def test_log_aggregation_distinguishes_airlock_blocks_from_provider_errors():
    assert _is_blocked_request({"airlock_enforcement": {"should_block": True}})
    assert not _is_blocked_request({"success": False, "error_type": "RateLimitError"})


def test_mutation_renderer_never_displays_raw_values_from_ledger():
    secret = "sk-live-should-never-render"
    entry = FlowEntry(
        "",
        "",
        "",
        "",
        True,
        None,
        None,
        None,
        [],
        None,
        None,
        {
            "mutations": [
                {"field": "prompt", "op": "rewrite", "before": secret, "after": secret}
            ]
        },
    )
    output = _render_mutations(entry)
    assert secret not in output
    assert "[hidden]" in output


def test_mutation_renderer_uses_canonical_redaction_count():
    entry = FlowEntry(
        "",
        "",
        "",
        "",
        True,
        None,
        None,
        None,
        [],
        None,
        None,
        {
            "mutations": [{"field": "messages", "category": "EMAIL", "count": 3}],
        },
    )
    assert "messages  EMAIL  3" in _render_mutations(entry)


def test_guards_accepts_a_mutation_ledger_without_observer_metadata():
    entry = _parse_entry({"mutations": [{"field": "messages", "count": 1}]})
    assert entry is not None


def test_admin_provider_view_includes_read_only_headroom_and_spend(monkeypatch):
    monkeypatch.setattr("airlock.admin.http.admin_enabled", lambda: True)
    monkeypatch.setattr(
        "airlock.admin.http.get_settings",
        lambda: MagicMock(provider_budgets={"openai": 10.0}),
        raising=False,
    )
    # get_settings is imported by the implementation under test in GREEN.
    store.get_provider("openai")
    rate = store.get_provider_ratelimit("openai")
    now = time.time()
    rate.update({"remaining_requests": 25, "limit_requests": 100}, now)
    store.get_provider_spend("openai").record_spend(now, 2.5)
    status, payload, _ = handle_admin_request(
        "GET",
        "/airlock/admin/providers",
        b"",
        Principal(loopback=True, bearer=None, actor="test"),
    )
    assert status == 200
    provider = payload["providers"]["openai"]
    assert provider["remaining_requests"] == 25
    assert provider["spend_usd"] == pytest.approx(2.5)
    assert provider["budget_cap_usd"] == 10.0


def test_remote_analyzer_dataset_redacts_and_caps_evidence():
    report = MagicMock()
    report.summary = {"note": "x" * 2_000, "credential": "sk-live-secret"}
    report.optimizations = []
    report.semantic_insights = None
    report.hypotheses = []
    report.guardrail_tuning = {}
    dataset = remote_dataset(report)
    encoded = str(dataset)
    assert "sk-live-secret" not in encoded
    assert len(dataset["summary"]["note"]) <= 500


def test_remote_sandbox_requires_explicit_opt_in_and_never_receives_raw_report(
    monkeypatch,
):
    executor = MagicMock()
    report = MagicMock()
    report.summary = {"messages": "raw secret"}
    report.optimizations = []
    report.semantic_insights = None
    report.hypotheses = []
    report.guardrail_tuning = {}
    monkeypatch.delenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX", raising=False)
    assert AnthropicSandboxExecutor(executor).run(report, "ops") is None
    executor.assert_not_called()
    monkeypatch.setenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX", "anthropic")
    monkeypatch.setenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY", "code_execution")
    AnthropicSandboxExecutor(executor).run(report, "ops")
    assert "raw secret" not in str(executor.call_args)


def test_remote_sandbox_structured_output_is_validated_and_advisory(monkeypatch):
    report = MagicMock()
    report.summary = {}
    report.optimizations = []
    report.semantic_insights = None
    report.hypotheses = []
    report.guardrail_tuning = {}
    monkeypatch.setenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX", "anthropic")
    monkeypatch.setenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY", "code_execution")
    monkeypatch.setattr(
        "airlock.slow.analyzer_llm.AnthropicSandboxExecutor.run",
        lambda *_: (
            '[{"narrative":"check capacity","evidence_references":["summary"],"confidence":0.8,"proposed_actions":["review"]}]'
        ),
    )
    findings = analyze_with_llm(report, "ops")
    assert findings and findings[0].proposed_actions == ["review"]


def test_llm_rejects_malformed_structured_output(monkeypatch):
    report = MagicMock()
    report.summary = {}
    report.optimizations = []
    report.semantic_insights = None
    report.hypotheses = []
    report.guardrail_tuning = {}
    monkeypatch.setenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX", "anthropic")
    monkeypatch.setenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY", "code_execution")
    monkeypatch.setattr(
        "airlock.slow.analyzer_llm.AnthropicSandboxExecutor.run",
        lambda *_: (
            '[{"narrative":"x","evidence_references":[1],'
            '"confidence":0.5,"proposed_actions":"review"}]'
        ),
    )
    assert analyze_with_llm(report, "ops") is None


@pytest.mark.asyncio
async def test_corpus_equivalence_reports_skips_latency_and_mismatches():
    light = _Classifier("light", False, ClassifierMetadata(cost_class="light"))
    heavy = _Classifier(
        "heavy", True, ClassifierMetadata(cost_class="heavy", min_content_length=20)
    )
    report = await corpus_equivalence_report(
        [light, heavy], ["short", "this corpus sample is long enough"]
    )
    assert report["total_samples"] == 2
    assert report["skipped_classifiers"]["heavy"] == 1
    assert report["mismatch_count"] == 1
    assert "adaptive_p95_ms" in report["latency_ms"]


def test_slow_semantic_analysis_reports_selection_and_skip_recommendations():
    semantic = find_semantic_insights(
        [
            {
                "airlock_semantic": {
                    "status": "passed",
                    "selection": "adaptive",
                    "selected": ["light"],
                    "skipped": [
                        {"name": "heavy", "reason": "below_min_content_length"}
                    ],
                    "short_circuited": [],
                    "results": [
                        {
                            "name": "light",
                            "score": 0.0,
                            "duration_ms": 2,
                            "blocked": False,
                        }
                    ],
                }
            }
        ]
    )
    assert semantic.selection_stats["skipped"]["heavy"] == 1
    assert semantic.skip_recommendations


def test_provider_budget_alert_uses_configured_cap_and_spend(monkeypatch):
    from airlock.fast.state import StateStore

    local = StateStore()
    local.get_provider_spend("openai").record_spend(time.time(), 9.0)
    monkeypatch.setattr(
        "airlock.tui.alert_engine.get_settings",
        lambda: MagicMock(provider_budgets={"openai": 10.0}, budget_warn_ratio=0.8),
        raising=False,
    )
    alerts = _check_provider_budget(local)
    assert alerts and alerts[0].entity_id == "openai"
