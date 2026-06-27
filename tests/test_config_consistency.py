"""Cross-cutting consistency tests for config template vs code structures.

These tests load the actual config.yaml template and verify it stays in sync
with the provider registrations, alias tables, router prefixes, and POST checks.
A failure here means a provider was added to one place but not another.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "airlock"
    / "cli"
    / "templates"
    / "config.yaml"
)

_ROOT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture(scope="module")
def root_config() -> dict:
    """Load the deployed root config.yaml as a dict."""
    assert _ROOT_CONFIG_PATH.is_file(), f"Root config not found: {_ROOT_CONFIG_PATH}"
    with open(_ROOT_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def root_model_names(root_config) -> set[str]:
    return {
        e["model_name"] for e in root_config.get("model_list", []) if "model_name" in e
    }


@pytest.fixture(scope="module")
def template_config() -> dict:
    """Load the config.yaml template as a dict."""
    assert _TEMPLATE_PATH.is_file(), f"Template not found: {_TEMPLATE_PATH}"
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def model_entries(template_config) -> list[dict]:
    return template_config.get("model_list", [])


@pytest.fixture(scope="module")
def model_names(model_entries) -> set[str]:
    return {e["model_name"] for e in model_entries if "model_name" in e}


@pytest.fixture(scope="module")
def configured_providers(model_entries) -> set[str]:
    """Set of provider prefixes found in model_list (e.g. {'anthropic', 'openai', ...})."""
    providers = set()
    for entry in model_entries:
        model_str = entry.get("litellm_params", {}).get("model", "")
        if "/" in model_str:
            providers.add(model_str.split("/", 1)[0])
    return providers


# ---------------------------------------------------------------------------
# Config template structure
# ---------------------------------------------------------------------------


class TestConfigTemplateStructure:
    """Validate the template config has required top-level sections."""

    def test_has_model_list(self, template_config):
        assert "model_list" in template_config
        assert len(template_config["model_list"]) > 0

    def test_has_litellm_settings(self, template_config):
        assert "litellm_settings" in template_config

    def test_has_router_settings(self, template_config):
        assert "router_settings" in template_config

    def test_has_guardrails(self, template_config):
        assert "guardrails" in template_config

    def test_has_general_settings(self, template_config):
        assert "general_settings" in template_config


class TestModelListEntries:
    """Every model_list entry should have the required fields."""

    def test_every_entry_has_model_name(self, model_entries):
        for i, entry in enumerate(model_entries):
            assert "model_name" in entry, f"model_list[{i}] missing model_name"

    def test_every_entry_has_litellm_params(self, model_entries):
        for i, entry in enumerate(model_entries):
            assert "litellm_params" in entry, f"model_list[{i}] missing litellm_params"

    def test_every_entry_has_model_string(self, model_entries):
        for i, entry in enumerate(model_entries):
            params = entry.get("litellm_params", {})
            model = params.get("model", "")
            assert model, (
                f"model_list[{i}] ({entry.get('model_name')}) has empty model string"
            )

    def test_every_model_has_provider_prefix(self, model_entries):
        """model string should be provider/model-id format."""
        for entry in model_entries:
            model = entry["litellm_params"]["model"]
            assert "/" in model, (
                f"model_list entry '{entry['model_name']}' model string '{model}' "
                f"missing provider/ prefix"
            )

    def test_every_model_uses_env_var_api_key(self, model_entries):
        """API keys should use os.environ/ syntax, not inline secrets."""
        for entry in model_entries:
            api_key = entry.get("litellm_params", {}).get("api_key", "")
            if api_key:
                assert api_key.startswith("os.environ/"), (
                    f"model '{entry['model_name']}' has inline api_key "
                    f"(should be os.environ/VAR_NAME)"
                )

    def test_no_duplicate_model_names(self, model_entries):
        names = [e["model_name"] for e in model_entries]
        assert len(names) == len(set(names)), (
            f"Duplicate model_names: {[n for n in names if names.count(n) > 1]}"
        )


# ---------------------------------------------------------------------------
# Fallback chain consistency
# ---------------------------------------------------------------------------


class TestFallbackConsistency:
    """Every model referenced in fallbacks must exist in model_list."""

    def test_fallback_sources_are_configured_models(self, template_config, model_names):
        fallbacks = template_config.get("router_settings", {}).get("fallbacks", [])
        for entry in fallbacks:
            for source_model in entry:
                assert source_model in model_names, (
                    f"Fallback source '{source_model}' not in model_list"
                )

    def test_fallback_targets_are_configured_models(self, template_config, model_names):
        fallbacks = template_config.get("router_settings", {}).get("fallbacks", [])
        for entry in fallbacks:
            for source_model, targets in entry.items():
                for target in targets:
                    assert target in model_names, (
                        f"Fallback target '{target}' (for {source_model}) not in model_list"
                    )

    def test_no_orphan_fallback_entries(self, template_config, model_names):
        """Every model in a fallback chain should exist in model_list."""
        fallbacks = template_config.get("router_settings", {}).get("fallbacks", [])
        all_referenced = set()
        for entry in fallbacks:
            all_referenced.update(entry.keys())
            for targets in entry.values():
                all_referenced.update(targets)

        orphans = all_referenced - model_names
        assert not orphans, (
            f"Fallback chain references models not in model_list: {orphans}"
        )


# ---------------------------------------------------------------------------
# Budget config consistency
# ---------------------------------------------------------------------------


class TestBudgetConsistency:
    """Provider budget entries should match configured providers."""

    def test_budget_providers_are_configured(
        self, template_config, configured_providers
    ):
        budget_config = template_config.get("router_settings", {}).get(
            "provider_budget_config", {}
        )
        for provider in budget_config:
            assert provider in configured_providers, (
                f"Budget configured for '{provider}' but no models use that provider"
            )

    def test_budget_entries_have_required_fields(self, template_config):
        budget_config = template_config.get("router_settings", {}).get(
            "provider_budget_config", {}
        )
        for provider, cfg in budget_config.items():
            assert "budget_limit" in cfg, (
                f"Budget for '{provider}' missing budget_limit"
            )
            assert "time_period" in cfg, f"Budget for '{provider}' missing time_period"
            assert isinstance(cfg["budget_limit"], (int, float)), (
                f"Budget for '{provider}' budget_limit is not numeric"
            )


# ---------------------------------------------------------------------------
# MCP server config
# ---------------------------------------------------------------------------


class TestMCPServerConfig:
    """Validate MCP server entries in the template."""

    def test_stdio_servers_have_command(self, template_config):
        mcp = template_config.get("mcp_servers") or {}
        for name, cfg in mcp.items():
            if cfg.get("transport") == "stdio":
                assert "command" in cfg, (
                    f"MCP server '{name}' uses stdio transport but has no command"
                )
                assert "args" in cfg, (
                    f"MCP server '{name}' uses stdio transport but has no args"
                )

    def test_env_vars_use_os_environ_syntax(self, template_config):
        mcp = template_config.get("mcp_servers") or {}
        for name, cfg in mcp.items():
            env = cfg.get("env") or {}
            for var, val in env.items():
                assert str(val).startswith("os.environ/"), (
                    f"MCP server '{name}' env var '{var}' should use os.environ/ syntax"
                )


# ---------------------------------------------------------------------------
# Alias table coverage
# ---------------------------------------------------------------------------


class TestAliasTableCoverage:
    """Every model_name in config should be resolvable by the alias table."""

    def test_all_model_names_resolvable(self, model_names):
        """Every configured model_name should be resolvable (not None)."""
        from airlock.fast.model_alias import ModelAliasTable

        table = ModelAliasTable()
        table.load_from_config(_TEMPLATE_PATH)
        for name in model_names:
            resolved = table.resolve(name)
            assert resolved is not None, f"Model alias table cannot resolve '{name}'"

    @pytest.mark.xfail(
        reason="gemini-3.1-pro collides with gemini-3.1-pro-tools in version-stripped index",
        strict=False,
    )
    def test_all_model_names_resolve_to_self(self, model_names):
        """Exact model_name should resolve to itself (not a sibling variant)."""
        from airlock.fast.model_alias import ModelAliasTable

        table = ModelAliasTable()
        table.load_from_config(_TEMPLATE_PATH)
        for name in model_names:
            resolved = table.resolve(name)
            assert resolved == name, (
                f"Model '{name}' resolves to '{resolved}' instead of itself — "
                f"the alias table's version-stripped index has a collision"
            )

    def test_provider_prefixed_forms_resolve(self, model_entries):
        """Sending 'anthropic/claude-sonnet' should resolve to 'claude-sonnet'."""
        from airlock.fast.model_alias import ModelAliasTable

        table = ModelAliasTable()
        table.load_from_config(_TEMPLATE_PATH)
        for entry in model_entries:
            alias = entry["model_name"]
            provider_model = entry["litellm_params"]["model"]
            resolved = table.resolve(provider_model)
            assert resolved == alias, (
                f"Provider-prefixed '{provider_model}' resolved to '{resolved}', "
                f"expected '{alias}'"
            )


# ---------------------------------------------------------------------------
# Provider prefix maps stay in sync
# ---------------------------------------------------------------------------


class TestProviderPrefixSync:
    """router.py and model_alias.py _PROVIDER_PREFIXES must cover the same providers."""

    def test_router_covers_alias_providers(self):
        """Every provider value in model_alias should appear as a value in router."""
        from airlock.fast.model_alias import _PROVIDER_PREFIXES as alias_prefixes
        from airlock.fast.router import _PROVIDER_PREFIXES as router_prefixes

        alias_providers = set(alias_prefixes.values())
        router_providers = set(router_prefixes.values())

        # Router must cover all providers that alias table knows about
        # (except purely non-LLM providers like tavily that don't need routing)
        non_routable = {"tavily"}  # custom handler, no budget/routing needed
        missing = (alias_providers - non_routable) - router_providers
        assert not missing, (
            f"Providers in model_alias.py but missing from router.py: {missing}. "
            f"infer_provider() in router.py will return None for these."
        )

    def test_router_prefixes_subset_of_alias(self):
        """Every prefix key in router should exist in alias table."""
        from airlock.fast.model_alias import _PROVIDER_PREFIXES as alias_prefixes
        from airlock.fast.router import _PROVIDER_PREFIXES as router_prefixes

        missing = set(router_prefixes.keys()) - set(alias_prefixes.keys())
        assert not missing, (
            f"Prefix keys in router.py but missing from model_alias.py: {missing}"
        )

    def test_configured_providers_have_prefix_entries(self, configured_providers):
        """Every provider prefix in config.yaml should be routable."""
        from airlock.fast.router import _PROVIDER_PREFIXES as router_prefixes

        router_providers = set(router_prefixes.values())
        # Custom providers (tavily) don't need router entries
        non_routable = {"tavily"}
        for provider in configured_providers - non_routable:
            assert provider in router_providers, (
                f"Provider '{provider}' is in config.yaml but has no prefix "
                f"mapping in router.py — infer_provider() can't route it"
            )


# ---------------------------------------------------------------------------
# POST check registration completeness
# ---------------------------------------------------------------------------


class TestPostCheckRegistration:
    """Every configured provider should have a registered POST check."""

    def test_every_provider_has_post_check(self, configured_providers):
        from airlock.cli.post_cmd import _CHECKS

        registered_names = {c.name for c in _CHECKS}
        for provider in configured_providers:
            check_name = f"provider_{provider}"
            assert check_name in registered_names, (
                f"Provider '{provider}' is in config.yaml but has no "
                f"registered POST check (expected check named '{check_name}')"
            )

    def test_check_functions_are_callable(self):
        from airlock.cli.post_cmd import _CHECKS

        provider_checks = [c for c in _CHECKS if c.name.startswith("provider_")]
        assert len(provider_checks) >= 7, (
            f"Expected at least 7 provider checks, found {len(provider_checks)}: "
            f"{[c.name for c in provider_checks]}"
        )
        for check in provider_checks:
            assert callable(check.fn), f"Check '{check.name}' fn is not callable"

    def test_check_names_match_function_pattern(self):
        """Check names should follow provider_<name> pattern."""
        from airlock.cli.post_cmd import _CHECKS

        provider_checks = [c for c in _CHECKS if c.group == "Providers"]
        for check in provider_checks:
            assert check.name.startswith("provider_"), (
                f"Provider check '{check.name}' doesn't follow provider_<name> pattern"
            )


# ---------------------------------------------------------------------------
# Custom provider map consistency
# ---------------------------------------------------------------------------


class TestCustomProviderMap:
    """Custom providers in litellm_settings should have matching model entries."""

    def test_custom_providers_have_model_entries(
        self, template_config, configured_providers
    ):
        custom_map = (
            template_config.get("litellm_settings", {}).get("custom_provider_map") or []
        )
        for entry in custom_map:
            provider = entry.get("provider", "")
            assert provider in configured_providers, (
                f"Custom provider '{provider}' in custom_provider_map but no "
                f"models configured with that provider prefix"
            )

    def test_custom_handlers_are_importable(self, template_config):
        import importlib

        custom_map = (
            template_config.get("litellm_settings", {}).get("custom_provider_map") or []
        )
        for entry in custom_map:
            handler_path = entry.get("custom_handler", "")
            module_path, _, attr_name = handler_path.rpartition(".")
            assert module_path, f"Invalid handler path: {handler_path}"
            mod = importlib.import_module(module_path)
            assert hasattr(mod, attr_name), (
                f"Custom handler '{handler_path}': module '{module_path}' "
                f"has no attribute '{attr_name}'"
            )


# ---------------------------------------------------------------------------
# Router cost tier consistency
# ---------------------------------------------------------------------------


class TestRootConfigReferenceIntegrity:
    """Root config.yaml fallbacks/cost_tiers targets must be live model_names."""

    def test_fallback_sources_and_targets_live(self, root_config, root_model_names):
        fallbacks = root_config.get("router_settings", {}).get("fallbacks", [])
        for entry in fallbacks:
            for source, targets in entry.items():
                assert source in root_model_names, (
                    f"Fallback source '{source}' not in root model_list"
                )
                for target in targets:
                    assert target in root_model_names, (
                        f"Fallback target '{target}' (for {source}) not in root model_list"
                    )

    def test_cost_tier_members_live(self, root_config, root_model_names):
        cost_tiers = root_config.get("cost_tiers", {})
        for tier, models in cost_tiers.items():
            for model in models:
                assert model in root_model_names, (
                    f"cost_tiers['{tier}'] references '{model}' not in root model_list"
                )

    def test_no_duplicate_root_model_names(self, root_config):
        names = [
            e["model_name"]
            for e in root_config.get("model_list", [])
            if "model_name" in e
        ]
        dupes = [n for n in names if names.count(n) > 1]
        assert not dupes, f"Duplicate model_names in root config: {set(dupes)}"


class TestRootConfigBatchKeys:
    """Consolidated prefixed batch aliases AND legacy twins both resolve via the
    Airlock Batch Gateway with the right backend/provider_model."""

    @pytest.fixture
    def aliases(self, root_config) -> dict:
        from airlock.batch.gateway import load_batch_aliases

        return load_batch_aliases(root_config)

    @pytest.mark.parametrize(
        "alias,backend,provider_model",
        [
            ("aistudio/gemini-3.5-flash", "aistudio", "gemini-3.5-flash"),
            ("aistudio/gemini-3.1-pro", "aistudio", "gemini-3.1-pro-preview"),
            ("mistral/mistral-large", "mistral", "mistral-large-latest"),
            ("mistral/mistral-small", "mistral", "mistral-small-latest"),
            ("vllm/qwen3.6-27b", "vllm", "qwen3.6-27b"),
        ],
    )
    def test_consolidated_keys(self, aliases, alias, backend, provider_model):
        assert alias in aliases, f"Consolidated batch alias '{alias}' missing"
        assert aliases[alias]["backend"] == backend
        assert aliases[alias]["provider_model"] == provider_model

    @pytest.mark.parametrize(
        "alias,backend,provider_model",
        [
            ("gemini-3.5-flash-aistudio", "aistudio", "gemini-3.5-flash"),
            ("gemini-3.1-pro-aistudio", "aistudio", "gemini-3.1-pro-preview"),
            ("mistral-large-batch", "mistral", "mistral-large-latest"),
            ("mistral-small-batch", "mistral", "mistral-small-latest"),
            ("qwen36-27b-vllm-batch", "vllm", "qwen3.6-27b"),
        ],
    )
    def test_legacy_twin_keys(self, aliases, alias, backend, provider_model):
        assert alias in aliases, f"Legacy batch twin '{alias}' missing"
        assert aliases[alias]["backend"] == backend
        assert aliases[alias]["provider_model"] == provider_model


class TestCostTierConsistency:
    """Default cost tiers should reference models that exist in config."""

    def test_tier_models_exist_in_config(self, model_names):
        from airlock.fast.router import _DEFAULT_COST_TIERS

        for tier, models in _DEFAULT_COST_TIERS.items():
            for model in models:
                assert model in model_names, (
                    f"Cost tier '{tier}' references model '{model}' "
                    f"which is not in config.yaml model_list"
                )

    def test_no_hidden_default_provider_budgets(self):
        """SET-unify: there is no hidden default provider-budget map. With no config
        and no env override, budgets are empty (no proactive swap / no warn)."""
        import airlock.fast.settings as settings_mod
        from airlock.fast.settings import load_airlock_settings

        settings_mod._configured = None
        assert load_airlock_settings({}).provider_budgets == {}
