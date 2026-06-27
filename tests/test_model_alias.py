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

    def test_perplexity(self):
        assert _infer_provider("perplexity-sonar-pro") == "perplexity"

    def test_tavily(self):
        assert _infer_provider("tavily-search") == "tavily"


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

    def test_none_resolve(self, table):
        """None model (batch/file routes carry no top-level model) must not crash."""
        assert table.resolve(None) is None

    def test_non_str_resolve(self, table):
        """Non-string model must not crash (defensive)."""
        assert table.resolve(123) is None

    def test_provider_prefixed_query(self, table):
        """Query with provider/ prefix should still resolve."""
        result = table.resolve("anthropic/claude-sonnet-4-20250514")
        assert result == "claude-sonnet"

    def test_openai_prefix_alias(self, table):
        """openai/claude-haiku should fast-path to claude-haiku alias."""
        result = table.resolve("openai/claude-haiku")
        assert result == "claude-haiku"

    def test_anthropic_prefix_alias(self, table):
        """anthropic/claude-haiku should fast-path to claude-haiku alias."""
        result = table.resolve("anthropic/claude-haiku")
        assert result == "claude-haiku"

    def test_openai_prefix_alias_cached(self, table):
        """Second call with provider prefix should use O(1) cached result."""
        table.resolve("openai/claude-sonnet")  # populate cache
        assert "openai/claude-sonnet" in table._exact
        result = table.resolve("openai/claude-sonnet")
        assert result == "claude-sonnet"

    def test_openai_prefix_gpt(self, table):
        """openai/gpt-4o should resolve to gpt-4o alias."""
        result = table.resolve("openai/gpt-4o")
        assert result == "gpt-4o"

    def test_unknown_provider_prefix_falls_through_to_fuzzy(self, table):
        """An unrecognised prefix/model still fuzzy-resolves when possible."""
        # "custom/claude-haiku" — prefix unknown but bare name is an alias
        result = table.resolve("custom/claude-haiku")
        assert result == "claude-haiku"

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


@pytest.fixture
def collision_config(tmp_path):
    """Config exercising provider-prefixed aliases that share a stripped body."""
    config = {
        "model_list": [
            # bare AI-Studio entry — owns "gemini-3.5-flash"
            {
                "model_name": "gemini-3.5-flash",
                "litellm_params": {
                    "model": "gemini/gemini-3.5-flash",
                    "api_key": "sk-test",
                },
            },
            # new prefixed AI-Studio consolidated batch alias
            {
                "model_name": "aistudio/gemini-3.5-flash",
                "litellm_params": {
                    "model": "gemini/gemini-3.5-flash",
                    "api_key": "sk-test",
                },
                "airlock_batch": {
                    "backend": "aistudio",
                    "provider_model": "gemini-3.5-flash",
                },
            },
            # legacy AI-Studio batch twin
            {
                "model_name": "gemini-3.5-flash-aistudio",
                "litellm_params": {
                    "model": "gemini/gemini-3.5-flash",
                    "api_key": "sk-test",
                },
                "airlock_batch": {
                    "backend": "aistudio",
                    "provider_model": "gemini-3.5-flash",
                },
            },
            # legacy Vertex entry
            {
                "model_name": "gemini-3.5-flash-vertex",
                "litellm_params": {
                    "model": "vertex_ai/gemini-3.5-flash",
                    "vertex_project": "proj",
                    "vertex_location": "global",
                },
            },
            # new prefixed Vertex alias
            {
                "model_name": "vertex/gemini-3.5-flash",
                "litellm_params": {
                    "model": "vertex_ai/gemini-3.5-flash",
                    "vertex_project": "proj",
                    "vertex_location": "global",
                },
            },
            # single-provider entry for prefix-ignored resolution
            {
                "model_name": "claude-haiku",
                "litellm_params": {
                    "model": "anthropic/claude-haiku-4-5-20251001",
                    "api_key": "sk-test",
                },
            },
        ],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(config))
    return path


def _alias_to_model(path) -> dict[str, str]:
    cfg = yaml.safe_load(path.read_text())
    return {e["model_name"]: e["litellm_params"]["model"] for e in cfg["model_list"]}


class TestCollisionSafeResolution:
    @pytest.fixture
    def ctable(self, collision_config):
        t = ModelAliasTable()
        t.load_from_config(collision_config)
        return t

    def test_bare_stays_ai_studio(self, ctable):
        """Bare gemini-3.5-flash must NEVER repoint to vertex/aistudio."""
        assert ctable.resolve("gemini-3.5-flash") == "gemini-3.5-flash"

    def test_native_vertex_ai_prefix_resolves_to_vertex(self, ctable, collision_config):
        resolved = ctable.resolve("vertex_ai/gemini-3.5-flash")
        models = _alias_to_model(collision_config)
        assert resolved in {"gemini-3.5-flash-vertex", "vertex/gemini-3.5-flash"}
        assert models[resolved].startswith("vertex_ai/")

    def test_vertex_prefix_resolves_to_vertex(self, ctable, collision_config):
        resolved = ctable.resolve("vertex/gemini-3.5-flash")
        models = _alias_to_model(collision_config)
        assert models[resolved].startswith("vertex_ai/")

    def test_aistudio_prefix_resolves_to_ai_studio(self, ctable, collision_config):
        resolved = ctable.resolve("aistudio/gemini-3.5-flash")
        models = _alias_to_model(collision_config)
        assert models[resolved].startswith("gemini/")

    def test_gemini_prefix_resolves_to_ai_studio(self, ctable, collision_config):
        resolved = ctable.resolve("gemini/gemini-3.5-flash")
        models = _alias_to_model(collision_config)
        assert models[resolved].startswith("gemini/")

    def test_single_provider_prefix_ignored(self, ctable):
        """openai/claude-haiku resolves to claude-haiku (prefix ignored)."""
        assert ctable.resolve("openai/claude-haiku") == "claude-haiku"

    def test_contradictory_multi_provider_prefix_returns_none(self, ctable):
        """A multi-provider body with a contradictory prefix -> None, no fuzzy."""
        assert ctable.resolve("mistral/gemini-3.5-flash") is None

    def test_contradictory_prefix_not_cached(self, ctable):
        ctable.resolve("mistral/gemini-3.5-flash")
        assert "mistral/gemini-3.5-flash" not in ctable._exact

    def test_legacy_aistudio_resolves_to_self(self, ctable):
        assert (
            ctable.resolve("gemini-3.5-flash-aistudio") == "gemini-3.5-flash-aistudio"
        )

    def test_legacy_vertex_resolves_to_self(self, ctable):
        assert ctable.resolve("gemini-3.5-flash-vertex") == "gemini-3.5-flash-vertex"


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
