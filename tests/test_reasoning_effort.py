"""Tests for per-provider reasoning_effort normalization (the drop_params fix)."""

from __future__ import annotations

import logging

import pytest

import airlock.reasoning_effort as re_mod
from airlock.reasoning_effort import normalize_reasoning_effort


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv("AIRLOCK_NORMALIZE_REASONING_EFFORT", raising=False)
    re_mod.reset_model_map_cache()
    yield
    re_mod.reset_model_map_cache()


class TestOpenAI:
    @pytest.mark.parametrize(
        "val", ["none", "off", "disable", "disabled", "false", "no", "0"]
    )
    def test_off_intent_maps_to_minimal(self, val):
        data = {"reasoning_effort": val}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == "minimal"  # honour intent, not drop->default

    @pytest.mark.parametrize("val", ["minimal", "low", "medium", "high"])
    def test_valid_values_unchanged(self, val):
        data = {"reasoning_effort": val}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == val

    def test_uppercase_off_intent(self):
        data = {"reasoning_effort": "NONE"}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == "minimal"

    def test_unknown_value_left_for_drop_params(self):
        data = {"reasoning_effort": "ultra"}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == "ultra"  # not our job to guess

    def test_azure_treated_like_openai(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "azure")
        assert data["reasoning_effort"] == "minimal"


class TestGemini:
    @pytest.mark.parametrize("val", ["none", "off", "minimal"])
    def test_off_intent_and_minimal_map_to_disable(self, val):
        data = {"reasoning_effort": val}
        normalize_reasoning_effort(data, "gemini")
        assert data["reasoning_effort"] == "disable"

    @pytest.mark.parametrize("val", ["disable", "low", "medium", "high"])
    def test_valid_gemini_values_unchanged(self, val):
        data = {"reasoning_effort": val}
        normalize_reasoning_effort(data, "gemini")
        assert data["reasoning_effort"] == val


class TestAnthropic:
    def test_off_intent_drops_param(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "anthropic")
        assert "reasoning_effort" not in data  # no extended thinking

    def test_real_value_left(self):
        data = {"reasoning_effort": "low"}
        normalize_reasoning_effort(data, "anthropic")
        assert data["reasoning_effort"] == "low"


class TestMisc:
    def test_absent_is_noop(self):
        data = {"model": "gpt-5.4"}
        normalize_reasoning_effort(data, "openai")
        assert "reasoning_effort" not in data

    def test_unknown_provider_unchanged(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "mistral")
        assert data["reasoning_effort"] == "none"

    def test_none_provider_unchanged(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, None)
        assert data["reasoning_effort"] == "none"

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_NORMALIZE_REASONING_EFFORT", "0")
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == "none"  # toggle off -> no change

    def test_returns_data_for_chaining(self):
        data = {"reasoning_effort": "none"}
        assert normalize_reasoning_effort(data, "openai") is data


def _ledger(data):
    return data.get("metadata", {}).get("airlock_mutations", [])


class TestLedger:
    """OBS-ledger: normalize_reasoning_effort records into airlock_mutations."""

    def test_openai_off_intent_records_set_minimal(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "openai")
        muts = _ledger(data)
        assert len(muts) == 1
        m = muts[0]
        assert m.field == "reasoning_effort"
        assert m.op == "set"
        assert m.before == "none"
        assert m.after == "minimal"
        assert m.stage == "pre_call"
        assert m.source == "reasoning_effort.normalize"

    def test_gemini_off_intent_records_set_disable(self):
        data = {"reasoning_effort": "off"}
        normalize_reasoning_effort(data, "gemini")
        muts = _ledger(data)
        assert len(muts) == 1
        assert muts[0].op == "set"
        assert muts[0].before == "off"
        assert muts[0].after == "disable"

    def test_anthropic_off_intent_records_drop(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "anthropic")
        muts = _ledger(data)
        assert len(muts) == 1
        assert muts[0].op == "drop"
        assert muts[0].before == "none"
        assert muts[0].after is None

    def test_valid_value_records_nothing(self):
        data = {"reasoning_effort": "high"}
        normalize_reasoning_effort(data, "openai")
        assert _ledger(data) == []

    def test_absent_records_nothing(self):
        data = {"model": "gpt-5.4"}
        normalize_reasoning_effort(data, "openai")
        assert _ledger(data) == []

    def test_unknown_provider_records_nothing(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "mistral")
        assert _ledger(data) == []


# ---------------------------------------------------------------------------
# 0.5.8 P-2 / P-6c — WARN-ONLY strict validation (`effort_would_reject`)
#
# Warn-only means: routing is byte-identical to the pre-0.5.8 behaviour. The
# module only *computes* what strict validation would decide and reports it.
# Everything here is mocked — no real config, no network, no 5.6 map entries.
# ---------------------------------------------------------------------------

# Synthetic litellm model map. Mirrors the real flag shape (see design §2):
# low/medium/high are always present; none/minimal/xhigh/max come from flags,
# and an absent-or-None flag means "not supported".
_FAKE_MODEL_MAP = {
    # 5.6-style: `none` is valid, `minimal` is REJECTED, `xhigh` valid.
    "openai/gpt-5.6-sol": {
        "supports_none_reasoning_effort": True,
        "supports_minimal_reasoning_effort": False,
        "supports_xhigh_reasoning_effort": True,
        "supports_max_reasoning_effort": None,
    },
    # 5.4-style: `minimal` is valid, `none` is REJECTED.
    "openai/gpt-5.4": {
        "supports_none_reasoning_effort": False,
        "supports_minimal_reasoning_effort": True,
        "supports_xhigh_reasoning_effort": False,
        "supports_max_reasoning_effort": None,
    },
    "gemini/gemini-3-pro": {
        "supports_none_reasoning_effort": None,
        "supports_minimal_reasoning_effort": None,
        "supports_xhigh_reasoning_effort": None,
        "supports_max_reasoning_effort": None,
    },
    "anthropic/claude-opus-4-8": {
        "supports_none_reasoning_effort": None,
        "supports_minimal_reasoning_effort": None,
        "supports_xhigh_reasoning_effort": None,
        "supports_max_reasoning_effort": None,
    },
}

_FAKE_MODEL_LIST = [
    {"model_name": "gpt-5.6-sol", "litellm_params": {"model": "openai/gpt-5.6-sol"}},
    # Semantic alias: `gpt-5` deliberately stays on 5.4 (design §3.1). Its body
    # is only discoverable through model_list — prefix-stripping cannot find it.
    {"model_name": "gpt-5", "litellm_params": {"model": "openai/gpt-5.4"}},
    {"model_name": "gpt-5.4", "litellm_params": {"model": "openai/gpt-5.4"}},
    {"model_name": "gemini-3-pro", "litellm_params": {"model": "gemini/gemini-3-pro"}},
    {
        "model_name": "claude-opus-4-8",
        "litellm_params": {"model": "anthropic/claude-opus-4-8"},
    },
]


@pytest.fixture
def fake_map(monkeypatch):
    """Mock ``litellm.get_model_info`` + inject a synthetic ``model_list``."""
    import litellm

    def _get_model_info(model, *args, **kwargs):
        try:
            return dict(_FAKE_MODEL_MAP[model])
        except KeyError:
            raise Exception(f"model {model!r} not in map") from None

    monkeypatch.setattr(litellm, "get_model_info", _get_model_info)
    monkeypatch.setattr(re_mod, "_load_model_list", lambda: list(_FAKE_MODEL_LIST))
    re_mod.reset_model_map_cache()
    return _FAKE_MODEL_LIST


def _warn_events(caplog):
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "effort_would_reject" in r.getMessage()
    ]


def _would_reject_muts(data):
    return [m for m in _ledger(data) if m.field == "reasoning_effort_would_reject"]


@pytest.fixture
def warns(caplog):
    caplog.set_level(logging.WARNING, logger="airlock.reasoning_effort")
    return caplog


class TestWouldRejectDetection:
    """What strict validation *would* decide, computed but not enforced."""

    def test_none_on_5_6_is_not_flagged(self, fake_map, warns):
        # `none` is VALID on 5.6, so enforcement will ACCEPT it — nothing to warn
        # about. (Today's translation to `minimal` is a separate, real bug that
        # P-2 fixes; it is not something the caller must change.)
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "none"}
        normalize_reasoning_effort(data, "openai")
        assert _warn_events(warns) == []
        assert _would_reject_muts(data) == []

    def test_none_on_5_4_IS_flagged(self, fake_map, warns):
        # THE headline cohort. Enforcement validates what the CLIENT sent, and
        # `none` is not in 5.4's set — so this caller gets a 400. Today the
        # translation to `minimal` hides that. Measuring the emitted value would
        # report this group as fine and under-count the breaking change by
        # exactly the population it most affects.
        data = {"model": "gpt-5.4", "reasoning_effort": "none"}
        normalize_reasoning_effort(data, "openai")
        events = _warn_events(warns)
        assert len(events) == 1
        msg = events[0].getMessage()
        assert "requested=none" in msg
        assert "model=gpt-5.4" in msg
        assert "supported=high,low,medium,minimal" in msg
        assert len(_would_reject_muts(data)) == 1

    def test_minimal_on_5_6_is_flagged(self, fake_map, warns):
        # 5.6 rejects `minimal` outright.
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "minimal"}
        normalize_reasoning_effort(data, "openai")
        events = _warn_events(warns)
        assert len(events) == 1
        assert "requested=minimal" in events[0].getMessage()

    def test_minimal_on_5_4_is_not_flagged(self, fake_map, warns):
        data = {"model": "gpt-5.4", "reasoning_effort": "minimal"}
        normalize_reasoning_effort(data, "openai")
        assert _warn_events(warns) == []

    @pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.4"])
    def test_max_is_flagged(self, fake_map, warns, model):
        # DECIDED: litellm sets no supports_max_reasoning_effort flag, so `max`
        # counts as would-reject. See _supported_efforts for the rationale.
        data = {"model": model, "reasoning_effort": "max"}
        normalize_reasoning_effort(data, "openai")
        events = _warn_events(warns)
        assert len(events) == 1
        assert "requested=max" in events[0].getMessage()

    def test_highest_is_flagged_and_never_mapped(self, fake_map, warns):
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "highest"}
        normalize_reasoning_effort(data, "openai")
        # No synonym mapping, ever: `highest` is not `max` and not `high`.
        assert data["reasoning_effort"] == "highest"
        events = _warn_events(warns)
        assert len(events) == 1
        assert "requested=highest" in events[0].getMessage()
        assert "translated_to=highest" in events[0].getMessage()

    def test_xhigh_on_5_6_is_not_flagged(self, fake_map, warns):
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"}
        normalize_reasoning_effort(data, "openai")
        assert _warn_events(warns) == []

    def test_xhigh_on_5_4_is_flagged(self, fake_map, warns):
        data = {"model": "gpt-5.4", "reasoning_effort": "xhigh"}
        normalize_reasoning_effort(data, "openai")
        assert len(_warn_events(warns)) == 1

    @pytest.mark.parametrize("val", ["low", "medium", "high"])
    def test_always_present_levels_never_flagged(self, fake_map, warns, val):
        data = {"model": "gpt-5.6-sol", "reasoning_effort": val}
        normalize_reasoning_effort(data, "openai")
        assert _warn_events(warns) == []

    def test_semantic_alias_resolves_through_model_list(self, fake_map, warns):
        # `gpt-5` has no 5.x suffix to strip — only model_list knows its body is
        # the 5.4 one, where `none` and `xhigh` are both invalid. If the alias
        # were not resolved through model_list, neither would be flagged.
        data = {"model": "gpt-5", "reasoning_effort": "none"}
        normalize_reasoning_effort(data, "openai")
        assert len(_warn_events(warns)) == 1
        warns.clear()
        data = {"model": "gpt-5", "reasoning_effort": "xhigh"}
        normalize_reasoning_effort(data, "openai")
        assert len(_warn_events(warns)) == 1

    def test_unknown_model_logs_nothing_and_changes_nothing(self, fake_map, warns):
        data = {"model": "my-self-hosted-llama", "reasoning_effort": "highest"}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == "highest"
        assert _warn_events(warns) == []
        assert _ledger(data) == []

    def test_absent_model_key_logs_nothing(self, fake_map, warns):
        data = {"reasoning_effort": "highest"}
        normalize_reasoning_effort(data, "openai")
        assert _warn_events(warns) == []

    def test_get_model_info_raising_is_not_fatal(self, fake_map, warns, monkeypatch):
        import litellm

        def _boom(*a, **kw):
            raise RuntimeError("map exploded")

        monkeypatch.setattr(litellm, "get_model_info", _boom)
        re_mod.reset_model_map_cache()
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "max"}
        normalize_reasoning_effort(data, "openai")  # must not raise
        assert data["reasoning_effort"] == "max"
        assert _warn_events(warns) == []

    def test_client_id_included_when_known(self, fake_map, warns):
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "max"}
        normalize_reasoning_effort(data, "openai", client_id="acme-42")
        assert "client_id=acme-42" in _warn_events(warns)[0].getMessage()

    def test_client_id_omitted_when_unknown(self, fake_map, warns):
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "max"}
        normalize_reasoning_effort(data, "openai")
        assert "client_id=" not in _warn_events(warns)[0].getMessage()

    def test_disabled_via_env_logs_nothing(self, fake_map, warns, monkeypatch):
        monkeypatch.setenv("AIRLOCK_NORMALIZE_REASONING_EFFORT", "0")
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "max"}
        normalize_reasoning_effort(data, "openai")
        assert _warn_events(warns) == []


class TestWouldRejectLedger:
    """The event must reach the 0.5.4 observability path, not just the log file."""

    def test_ledger_entry_recorded_for_would_reject(self, fake_map, warns):
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "max"}
        normalize_reasoning_effort(data, "openai", client_id="acme-42")
        muts = _would_reject_muts(data)
        assert len(muts) == 1
        m = muts[0]
        assert m.op == "inject"  # advisory marker; no request field was changed
        assert m.before == "max"
        assert m.after == "max"
        assert m.stage == "pre_call"
        assert m.source == "reasoning_effort.validate"
        assert "gpt-5.6-sol" in m.reason
        assert "high,low,medium,none,xhigh" in m.reason

    def test_translation_mutation_still_recorded_alongside(self, fake_map, warns):
        # The pre-existing `none -> minimal` ledger entry must survive untouched;
        # the advisory is additive. Uses 5.4, where `none` is genuinely a
        # would-reject, so both entries appear together.
        data = {"model": "gpt-5.4", "reasoning_effort": "none"}
        normalize_reasoning_effort(data, "openai")
        muts = _ledger(data)
        assert [m.field for m in muts] == [
            "reasoning_effort",
            "reasoning_effort_would_reject",
        ]
        assert muts[0].op == "set"
        assert muts[0].before == "none"
        assert muts[0].after == "minimal"

    def test_no_ledger_entry_when_supported(self, fake_map, warns):
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "high"}
        normalize_reasoning_effort(data, "openai")
        assert _ledger(data) == []

    def test_advisory_header_token_leaks_no_value(self, fake_map, warns):
        from airlock.transparency import mutations_header

        data = {"model": "gpt-5.6-sol", "reasoning_effort": "max"}
        normalize_reasoning_effort(data, "openai")
        header = mutations_header(_ledger(data))
        assert "reasoning_effort_would_reject=inject" in header


class TestNonOpenAIProvidersUnchanged:
    """Gemini/Anthropic keep today's behaviour; no level set is knowable there.

    litellm's per-level flags encode the OpenAI enum only — a Gemini model
    reports every flag as None, which would yield {low,medium,high} and falsely
    flag the legitimate `disable`. So validation is computed for the OpenAI
    family alone.
    """

    @pytest.mark.parametrize("val", ["none", "off", "minimal"])
    def test_gemini_still_translates_to_disable(self, fake_map, warns, val):
        data = {"model": "gemini-3-pro", "reasoning_effort": val}
        normalize_reasoning_effort(data, "gemini")
        assert data["reasoning_effort"] == "disable"
        assert _warn_events(warns) == []

    def test_gemini_disable_not_flagged(self, fake_map, warns):
        data = {"model": "gemini-3-pro", "reasoning_effort": "disable"}
        normalize_reasoning_effort(data, "gemini")
        assert data["reasoning_effort"] == "disable"
        assert _warn_events(warns) == []
        assert _ledger(data) == []

    def test_gemini_bogus_value_not_flagged_and_untouched(self, fake_map, warns):
        data = {"model": "gemini-3-pro", "reasoning_effort": "highest"}
        normalize_reasoning_effort(data, "gemini")
        assert data["reasoning_effort"] == "highest"
        assert _warn_events(warns) == []

    def test_anthropic_still_drops_off_intent(self, fake_map, warns):
        data = {"model": "claude-opus-4-8", "reasoning_effort": "none"}
        normalize_reasoning_effort(data, "anthropic")
        assert "reasoning_effort" not in data
        assert _warn_events(warns) == []

    def test_anthropic_real_value_untouched(self, fake_map, warns):
        data = {"model": "claude-opus-4-8", "reasoning_effort": "max"}
        normalize_reasoning_effort(data, "anthropic")
        assert data["reasoning_effort"] == "max"
        assert _warn_events(warns) == []

    def test_unknown_provider_untouched(self, fake_map, warns):
        data = {"model": "gpt-5.6-sol", "reasoning_effort": "none"}
        normalize_reasoning_effort(data, "mistral")
        assert data["reasoning_effort"] == "none"
        assert _warn_events(warns) == []


# Frozen snapshot of pre-0.5.8 routing. Every cell is what Airlock emitted
# BEFORE warn-only validation existed. `...` means "key absent from data".
_LEGACY_MATRIX = [
    # (provider, model, requested, emitted)
    ("openai", "gpt-5.6-sol", "none", "minimal"),
    ("openai", "gpt-5.6-sol", "off", "minimal"),
    ("openai", "gpt-5.6-sol", "disable", "minimal"),
    ("openai", "gpt-5.6-sol", "0", "minimal"),
    ("openai", "gpt-5.6-sol", "NONE", "minimal"),
    ("openai", "gpt-5.6-sol", "minimal", "minimal"),
    ("openai", "gpt-5.6-sol", "low", "low"),
    ("openai", "gpt-5.6-sol", "medium", "medium"),
    ("openai", "gpt-5.6-sol", "high", "high"),
    ("openai", "gpt-5.6-sol", "xhigh", "xhigh"),
    ("openai", "gpt-5.6-sol", "max", "max"),
    ("openai", "gpt-5.6-sol", "highest", "highest"),
    ("openai", "gpt-5.4", "none", "minimal"),
    ("openai", "gpt-5.4", "minimal", "minimal"),
    ("openai", "gpt-5.4", "xhigh", "xhigh"),
    ("openai", "gpt-5.4", "max", "max"),
    ("openai", "gpt-5", "none", "minimal"),
    ("openai", "unknown-model", "none", "minimal"),
    ("openai", "unknown-model", "highest", "highest"),
    ("azure", "gpt-5.6-sol", "none", "minimal"),
    ("gemini", "gemini-3-pro", "none", "disable"),
    ("gemini", "gemini-3-pro", "minimal", "disable"),
    ("gemini", "gemini-3-pro", "disable", "disable"),
    ("gemini", "gemini-3-pro", "high", "high"),
    ("gemini", "gemini-3-pro", "highest", "highest"),
    ("anthropic", "claude-opus-4-8", "none", ...),
    ("anthropic", "claude-opus-4-8", "off", ...),
    ("anthropic", "claude-opus-4-8", "low", "low"),
    ("anthropic", "claude-opus-4-8", "max", "max"),
    ("mistral", "gpt-5.6-sol", "none", "none"),
    (None, "gpt-5.6-sol", "none", "none"),
]


class TestWarnOnlyRoutingIsByteIdentical:
    """THE critical warn-only assertion: nothing about what we send changes."""

    @pytest.mark.parametrize(
        "provider,model,requested,emitted",
        _LEGACY_MATRIX,
        ids=[f"{p}-{m}-{r}" for p, m, r, _ in _LEGACY_MATRIX],
    )
    def test_emitted_value_matches_legacy(
        self, fake_map, warns, provider, model, requested, emitted
    ):
        data = {"model": model, "reasoning_effort": requested}
        out = normalize_reasoning_effort(data, provider, client_id="c1")
        assert out is data
        assert data.get("reasoning_effort", ...) == emitted
        assert data["model"] == model  # routing target never touched
