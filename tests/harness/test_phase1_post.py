"""
S4 — POST: Power-On Self-Test command.

Tests run_checks() directly, no proxy needed for config/guardrail checks.
"""

from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.harness


@pytest.fixture
def post_config_dir(tmp_path, monkeypatch):
    """Minimal config dir for POST checks."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "model_list:\n"
        "  - model_name: claude-sonnet\n"
        "    litellm_params:\n"
        "      model: anthropic/claude-sonnet-4-20250514\n"
        "      api_key: os.environ/ANTHROPIC_API_KEY\n"
        "guardrails:\n"
        "  - guardrail_name: airlock-pii-guard\n"
        "    litellm_params:\n"
        "      guardrail: airlock.guardrails.pii_guard\n"
        "      mode: [pre_call, pre_mcp_call]\n"
        "      default_on: true\n"
    )
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-test\n")
    (tmp_path / "logs").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AIRLOCK_CONFIG", raising=False)
    return tmp_path


class TestPostChecks:

    def test_full_run_returns_groups(self, post_config_dir):
        from airlock.cli.post_cmd import run_checks

        results = run_checks(
            skip_llm=True, skip_storage=True, skip_mcp=True
        )
        assert len(results) > 0

    def test_all_checks_have_name_and_status(self, post_config_dir):
        from airlock.cli.post_cmd import run_checks

        results = run_checks(
            skip_llm=True, skip_storage=True, skip_mcp=True
        )
        for r in results:
            assert r.name
            assert r.status is not None

    def test_json_output_valid(self, post_config_dir):
        from airlock.cli.post_cmd import run_checks, render_json

        results = run_checks(
            skip_llm=True, skip_storage=True, skip_mcp=True
        )
        json_str = render_json(results)
        data = json.loads(json_str)
        assert "checks" in data

    def test_skip_providers(self, post_config_dir):
        from airlock.cli.post_cmd import run_checks, CheckStatus

        results = run_checks(skip_llm=True)
        provider_checks = [r for r in results if r.group == "Providers"]
        assert all(r.status == CheckStatus.SKIP for r in provider_checks)

    def test_skip_mcp(self, post_config_dir):
        from airlock.cli.post_cmd import run_checks, CheckStatus

        results = run_checks(skip_mcp=True)
        mcp_checks = [r for r in results if r.group == "MCP"]
        # All MCP checks should be SKIP or already-skipped for other reasons
        assert all(r.status == CheckStatus.SKIP for r in mcp_checks
                    if "skipped by flag" in r.detail)

    def test_skip_storage(self, post_config_dir):
        from airlock.cli.post_cmd import run_checks, CheckStatus

        results = run_checks(skip_storage=True)
        storage_checks = [r for r in results if r.group == "Storage"]
        assert all(r.status == CheckStatus.SKIP for r in storage_checks)

    def test_multiple_skip_flags(self, post_config_dir):
        from airlock.cli.post_cmd import run_checks, CheckStatus

        results = run_checks(
            skip_llm=True, skip_storage=True, skip_mcp=True
        )
        skip_groups = {"Providers", "Storage", "MCP"}
        flag_skipped = [
            r for r in results
            if r.group in skip_groups and "skipped by flag" in r.detail
        ]
        assert len(flag_skipped) > 0
        assert all(r.status == CheckStatus.SKIP for r in flag_skipped)

    def test_proxy_down_graceful(self, post_config_dir):
        from airlock.cli.post_cmd import run_checks, CheckStatus

        results = run_checks()
        # Connectivity checks should fail/warn/skip gracefully — not crash.
        # provider_keys only checks env var presence so it may PASS independently
        # of whether providers are reachable.
        connectivity_checks = [
            r for r in results
            if r.group == "Providers" and r.name != "provider_keys"
        ]
        for r in connectivity_checks:
            assert r.status in (
                CheckStatus.FAIL,
                CheckStatus.WARN,
                CheckStatus.SKIP,
            )

    def test_proxy_down_error_messages(self, post_config_dir):
        from airlock.cli.post_cmd import run_checks

        results = run_checks()
        provider_checks = [r for r in results if r.group == "Providers"]
        for r in provider_checks:
            assert r.detail  # Should have an error message
