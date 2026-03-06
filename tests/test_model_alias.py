"""Tests for airlock/fast/model_alias.py — Model alias routing table."""

from __future__ import annotations

import pytest
import yaml

from airlock.fast.model_alias import (
    ModelAliasTable,
    _AliasEntry,
    _infer_provider,
    _score_match,
    _strip_provider_prefix,
    _strip_version,
    _tokenize,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_config(tmp_path):
    """Write a minimal config.yaml and return its path."""
    config = {
        "model_list": [
            {
                "model_name": "claude-sonnet",
                "litellm_params": {
                    "model": "anthropic/claude-sonnet-4-20250514",
                    "api_key": "sk-test",
                },
            },
            {
                "model_name": "claude-haiku",
                "litellm_params": {
                    "model": "anthropic/claude-haiku-4-5-20251001",
                    "api_key": "sk-test",
                },
            },
            {
                "model_name": "claude-opus",
                "litellm_params": {
                    "model": "anthropic/claude-opus-4-20250514",
                    "api_key": "sk-test",
                },
            },
            {
                "model_name": "gpt-4o",
                "litellm_params": {
                    "model": "openai/gpt-4o",
                    "api_key": "sk-test",
                },
            },
            {
                "model_name": "gpt-4o-mini",
                "litellm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-test",
                },
            },
            {
                "model_name": "gemini-flash",
                "litellm_params": {
                    "model": "gemini/gemini-2.5-flash",
                    "api_key": "sk-test",
                },
            },
            {
                "model_name": "gemini-pro",
                "litellm_params": {
                    "model": "gemini/gemini-2.5-pro",
                    "api_key": "sk-test",
                },
            },
            {
                "model_name": "mistral-small",
                "litellm_params": {
                    "model": "mistral/mistral-small-latest",
                    "api_key": "sk-test",
                },
            },
            {
                "model_name": "codestral",
                "litellm_params": {
                    "model": "mistral/codestral-latest",
                    "api_key": "sk-test",
                },
            },
            {
                "model_name": "perplexity-sonar",
                "litellm_params": {
                    "model": "perplexity/sonar",
                    "api_key": "sk-test",
                },
            },
        ],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(config))
    return path


@pytest.fixture
def table(sample_config):
    """Return a loaded ModelAliasTable."""
    t = ModelAliasTable()
    t.load_from_config(sample_config)
    return t


# ---------------------------------------------------------------------------
# Unit tests — low-level helpers
# ---------------------------------------------------------------------------
class TestTokenize:
    def test_simple(self):
        assert _tokenize("claude-sonnet") == {"claude", "sonnet"}

    def test_with_provider(self):
        tokens = _tokenize("anthropic/claude-sonnet-4-20250514")
        assert "anthropic" in tokens
        assert "claude" in tokens
        assert "sonnet" in tokens

    def test_dots_underscores(self):
        tokens = _tokenize("gemini-3.1-pro-preview")
        assert "gemini" in tokens
        assert "pro" in tokens

    def test_empty_string(self):
        assert _tokenize("") == set()


class TestStripVersion:
    def test_date_stamp(self):
        assert _strip_version("claude-sonnet-4-20250514") == "claude-sonnet"

    def test_short_version(self):
        assert _strip_version("claude-sonnet-4-6") == "claude-sonnet"

    def test_latest(self):
        assert _strip_version("mistral-small-latest") == "mistral-small"

    def test_preview(self):
        assert _strip_version("gemini-3-pro-preview") == "gemini-3-pro"

    def test_no_version(self):
        assert _strip_version("claude-sonnet") == "claude-sonnet"

    def test_gpt_4o(self):
        assert _strip_version("gpt-4o") == "gpt-4o"

    def test_empty_string(self):
        assert _strip_version("") == ""


class TestStripProviderPrefix:
    def test_with_prefix(self):
        assert _strip_provider_prefix("anthropic/claude-sonnet-4") == "claude-sonnet-4"

    def test_without_prefix(self):
        assert _strip_provider_prefix("claude-sonnet") == "claude-sonnet"

    def test_multiple_slashes(self):
        assert _strip_provider_prefix("a/b/c") == "b/c"

    def test_leading_slash(self):
        assert _strip_provider_prefix("/leading") == "leading"


class TestInferProvider:
    def test_claude(self):
        assert _infer_provider("claude-sonnet") == "anthropic"

    def test_gpt(self):
        assert _infer_provider("gpt-4o") == "openai"

    def test_gemini(self):
        assert _infer_provider("gemini-flash") == "gemini"

    def test_unknown(self):
        assert _infer_provider("llama-3") is None

    def test_o1(self):
        assert _infer_provider("o1-preview") == "openai"

    def test_o3(self):
        assert _infer_provider("o3-mini") == "openai"

    def test_magistral(self):
        assert _infer_provider("magistral-medium") == "mistral"

    def test_codestral(self):
        assert _infer_provider("codestral-latest") == "mistral"

    def test_sonar(self):
        assert _infer_provider("sonar") == "perplexity"


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------
class TestScoreMatch:
    def test_exact_alias_match(self):
        entry = _AliasEntry(
            alias="claude-sonnet",
            provider_model="anthropic/claude-sonnet-4-20250514",
            provider="anthropic",
        )
        assert _score_match("claude-sonnet", entry) == 1.0

    def test_exact_bare_model_match(self):
        entry = _AliasEntry(
            alias="claude-sonnet",
            provider_model="anthropic/claude-sonnet-4-20250514",
            provider="anthropic",
        )
        assert _score_match("claude-sonnet-4-20250514", entry) == 1.0

    def test_version_variant(self):
        """claude-sonnet-4-6 should score high against claude-sonnet."""
        entry = _AliasEntry(
            alias="claude-sonnet",
            provider_model="anthropic/claude-sonnet-4-20250514",
            provider="anthropic",
        )
        score = _score_match("claude-sonnet-4-6", entry)
        assert score >= 0.50, f"Expected >= 0.50, got {score}"

    def test_cross_provider_penalty(self):
        """claude-sonnet-4-6 should NOT match gpt-4o."""
        entry = _AliasEntry(
            alias="gpt-4o",
            provider_model="openai/gpt-4o",
            provider="openai",
        )
        score = _score_match("claude-sonnet-4-6", entry)
        assert score < 0.20, f"Expected < 0.20, got {score}"

    def test_same_family_different_variant(self):
        """claude-haiku-4-5-20251001 should match claude-haiku."""
        entry = _AliasEntry(
            alias="claude-haiku",
            provider_model="anthropic/claude-haiku-4-5-20251001",
            provider="anthropic",
        )
        score = _score_match("claude-haiku-4-5-20251001", entry)
        assert score == 1.0  # exact bare model match

    def test_gemini_version_stripped(self):
        """gemini-2.5-flash should match gemini-flash."""
        entry = _AliasEntry(
            alias="gemini-flash",
            provider_model="gemini/gemini-2.5-flash",
            provider="gemini",
        )
        score = _score_match("gemini-2.5-flash", entry)
        assert score == 1.0  # exact bare model match

    def test_mistral_latest(self):
        """mistral-small-latest should match mistral-small."""
        entry = _AliasEntry(
            alias="mistral-small",
            provider_model="mistral/mistral-small-latest",
            provider="mistral",
        )
        score = _score_match("mistral-small-latest", entry)
        assert score == 1.0  # exact bare model match

    def test_empty_query(self):
        entry = _AliasEntry(
            alias="claude-sonnet",
            provider_model="anthropic/claude-sonnet-4-20250514",
            provider="anthropic",
        )
        score = _score_match("", entry)
        assert score < 0.50

    def test_provider_bonus(self):
        """Provider match + version core match should trigger the bonus."""
        entry = _AliasEntry(
            alias="claude-sonnet",
            provider_model="anthropic/claude-sonnet-4-20250514",
            provider="anthropic",
        )
        score = _score_match("claude-sonnet-4-6", entry)
        assert score >= 0.85, f"Expected bonus to kick in, got {score}"


# ---------------------------------------------------------------------------
# Full table resolution tests
# ---------------------------------------------------------------------------
class TestModelAliasTable:
    def test_exact_alias(self, table):
        assert table.resolve("claude-sonnet") == "claude-sonnet"

    def test_exact_bare_provider_model(self, table):
        assert table.resolve("claude-sonnet-4-20250514") == "claude-sonnet"

    def test_version_variant_resolves(self, table):
        """The actual bug: claude-sonnet-4-6 should resolve to claude-sonnet."""
        assert table.resolve("claude-sonnet-4-6") == "claude-sonnet"

    def test_haiku_variant(self, table):
        assert table.resolve("claude-haiku-4-5-20251001") == "claude-haiku"

    def test_opus_variant(self, table):
        assert table.resolve("claude-opus-4-20250514") == "claude-opus"

    def test_gemini_bare(self, table):
        assert table.resolve("gemini-2.5-flash") == "gemini-flash"

    def test_mistral_latest(self, table):
        assert table.resolve("mistral-small-latest") == "mistral-small"

    def test_codestral_latest(self, table):
        assert table.resolve("codestral-latest") == "codestral"

    def test_gpt_exact(self, table):
        assert table.resolve("gpt-4o") == "gpt-4o"

    def test_gpt_mini(self, table):
        assert table.resolve("gpt-4o-mini") == "gpt-4o-mini"

    def test_unknown_model_returns_none(self, table):
        assert table.resolve("llama-3-70b") is None

    def test_cross_provider_no_match(self, table):
        """Should resolve claude-sonnet-4-6 to claude-sonnet, not openai."""
        assert table.resolve("claude-sonnet-4-6") == "claude-sonnet"

    def test_case_insensitive(self, table):
        assert table.resolve("Claude-Sonnet") == "claude-sonnet"

    def test_cached_after_first_resolve(self, table):
        """Second resolve for same name should hit cache with correct value."""
        result = table.resolve("claude-sonnet-4-6")
        assert result == "claude-sonnet"
        assert table._exact["claude-sonnet-4-6"] == "claude-sonnet"

    def test_empty_config(self, tmp_path):
        """Empty config should produce empty table."""
        path = tmp_path / "empty.yaml"
        path.write_text("{}")
        t = ModelAliasTable()
        t.load_from_config(path)
        assert t.resolve("claude-sonnet") is None

    def test_missing_config(self, tmp_path):
        """Missing config file should not crash."""
        t = ModelAliasTable()
        t.load_from_config(tmp_path / "nonexistent.yaml")
        assert t.resolve("anything") is None

    def test_perplexity(self, table):
        assert table.resolve("perplexity-sonar") == "perplexity-sonar"

    def test_sonar_bare(self, table):
        """sonar (bare provider model) should resolve to perplexity-sonar."""
        assert table.resolve("sonar") == "perplexity-sonar"

    def test_empty_string_resolve(self, table):
        """Empty string should not crash."""
        result = table.resolve("")
        # Should return None or an alias, but not crash
        assert result is None or isinstance(result, str)

    def test_provider_prefixed_query(self, table):
        """Query with provider/ prefix should still resolve."""
        result = table.resolve("anthropic/claude-sonnet-4-20250514")
        assert result == "claude-sonnet"

    def test_ambiguous_claude_resolves_to_one(self, table):
        """Bare 'claude' alone should resolve to a claude model, not crash."""
        result = table.resolve("claude")
        if result is not None:
            assert result.startswith("claude")

    def test_reload_clears_cache(self, sample_config):
        """Reloading config should clear cached fuzzy results."""
        t = ModelAliasTable()
        t.load_from_config(sample_config)
        t.resolve("claude-sonnet-4-6")
        assert "claude-sonnet-4-6" in t._exact
        # Reload should clear
        t.load_from_config(sample_config)
        assert "claude-sonnet-4-6" not in t._exact

    def test_malformed_entry_missing_model_name(self, tmp_path):
        """Entry without model_name should be skipped."""
        config = {
            "model_list": [
                {"litellm_params": {"model": "anthropic/claude-sonnet-4"}},
                {
                    "model_name": "gpt-4o",
                    "litellm_params": {"model": "openai/gpt-4o"},
                },
            ],
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(config))
        t = ModelAliasTable()
        t.load_from_config(path)
        assert t.resolve("gpt-4o") == "gpt-4o"

    def test_malformed_entry_missing_litellm_params(self, tmp_path):
        """Entry without litellm_params should be skipped."""
        config = {
            "model_list": [
                {"model_name": "broken-model"},
                {
                    "model_name": "gpt-4o",
                    "litellm_params": {"model": "openai/gpt-4o"},
                },
            ],
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(config))
        t = ModelAliasTable()
        t.load_from_config(path)
        assert t.resolve("gpt-4o") == "gpt-4o"

    def test_malformed_entry_empty_model(self, tmp_path):
        """Entry with empty litellm_params.model should be skipped."""
        config = {
            "model_list": [
                {
                    "model_name": "broken",
                    "litellm_params": {"model": ""},
                },
                {
                    "model_name": "gpt-4o",
                    "litellm_params": {"model": "openai/gpt-4o"},
                },
            ],
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(config))
        t = ModelAliasTable()
        t.load_from_config(path)
        assert t.resolve("gpt-4o") == "gpt-4o"

    def test_corrupt_yaml(self, tmp_path):
        """Corrupt YAML should not crash, just produce empty table."""
        path = tmp_path / "bad.yaml"
        path.write_text(":::not yaml{{{}}")
        t = ModelAliasTable()
        t.load_from_config(path)
        assert t.resolve("anything") is None


class TestAllLoggedModels:
    """Test every model name observed in production JSONL logs resolves correctly."""

    @pytest.mark.parametrize(
        "logged_name,expected_alias",
        [
            ("claude-haiku-4-5-20251001", "claude-haiku"),
            ("claude-opus-4-20250514", "claude-opus"),
            ("claude-sonnet-4-20250514", "claude-sonnet"),
            ("claude-sonnet-4-6", "claude-sonnet"),
            ("codestral-latest", "codestral"),
            ("gemini-2.5-flash", "gemini-flash"),
            ("gemini-2.5-pro", "gemini-pro"),
            ("gpt-4o", "gpt-4o"),
            ("gpt-4o-mini", "gpt-4o-mini"),
            ("mistral-small-latest", "mistral-small"),
        ],
    )
    def test_logged_model_resolves(self, table, logged_name, expected_alias):
        result = table.resolve(logged_name)
        assert result == expected_alias, (
            f"{logged_name} resolved to {result!r}, expected {expected_alias!r}"
        )
