"""Tests for advisor config proposals."""

import yaml

from airlock.advisor.proposals import (
    ConfigProposal,
    apply_proposal,
    classify_risk,
    generate_diff,
    parse_action_block,
)


# --- parse_action_block ---


def test_parse_action_block_valid(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({"model_list": [{"model_name": "gpt-4"}]}))

    raw = {
        "type": "config_change",
        "description": "Add gpt-3.5 model",
        "changes": {
            "model_list": [
                {"model_name": "gpt-4"},
                {"model_name": "gpt-3.5-turbo"},
            ]
        },
    }
    proposal = parse_action_block(raw, config_path=str(config_file))
    assert proposal is not None
    assert isinstance(proposal, ConfigProposal)
    assert proposal.description == "Add gpt-3.5 model"
    assert proposal.config_path == str(config_file)


def test_parse_action_block_wrong_type():
    raw = {"type": "other", "description": "nope", "changes": {}}
    result = parse_action_block(raw)
    assert result is None


def test_parse_action_block_malformed():
    raw = {"type": "config_change", "description": "missing changes key"}
    result = parse_action_block(raw)
    assert result is None


def test_parse_action_block_not_dict():
    result = parse_action_block("not a dict")
    assert result is None


# --- generate_diff ---


def test_generate_diff_produces_unified():
    original = "model_list:\n  - gpt-4\n"
    modified = "model_list:\n  - gpt-4\n  - gpt-3.5\n"
    diff = generate_diff(original, modified)
    assert "---" in diff
    assert "+++" in diff


def test_generate_diff_identical():
    text = "model_list:\n  - gpt-4\n"
    diff = generate_diff(text, text)
    assert diff == ""


# --- classify_risk ---


def test_classify_risk_add_model_low():
    changes = {
        "model_list": [
            {"model_name": "gpt-4"},
            {"model_name": "gpt-3.5-turbo"},
        ]
    }
    original = {"model_list": [{"model_name": "gpt-4"}]}
    result = classify_risk(changes, original)
    assert result == "low"


def test_classify_risk_settings_medium():
    changes = {"litellm_settings": {"drop_params": True}}
    result = classify_risk(changes)
    assert result == "medium"


def test_classify_risk_remove_model_high():
    changes = {"model_list": []}
    original = {"model_list": [{"model_name": "gpt-4"}]}
    result = classify_risk(changes, original)
    assert result == "high"


def test_classify_risk_guardrails_high():
    changes = {"guardrails": [{"prompt_injection": {"enabled": True}}]}
    result = classify_risk(changes)
    assert result == "high"


# --- requires_restart ---


def test_requires_restart_model_list(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({"model_list": [{"model_name": "gpt-4"}]}))

    raw = {
        "type": "config_change",
        "description": "Add model",
        "changes": {
            "model_list": [
                {"model_name": "gpt-4"},
                {"model_name": "gpt-3.5-turbo"},
            ]
        },
    }
    proposal = parse_action_block(raw, config_path=str(config_file))
    assert proposal is not None
    assert proposal.requires_restart is True


# --- apply_proposal ---


def test_apply_creates_backup(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model_list:\n  - gpt-4\n")

    proposal = ConfigProposal(
        description="test",
        config_path=str(config_file),
        original_yaml="model_list:\n  - gpt-4\n",
        modified_yaml="model_list:\n  - gpt-4\n  - gpt-3.5\n",
        diff_preview="...",
        risk_level="low",
        requires_restart=True,
    )
    backup_path = apply_proposal(proposal)
    assert backup_path.endswith(".bak")
    from pathlib import Path

    assert Path(backup_path).exists()


def test_apply_writes_new_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model_list:\n  - gpt-4\n")

    modified = "model_list:\n  - gpt-4\n  - gpt-3.5\n"
    proposal = ConfigProposal(
        description="test",
        config_path=str(config_file),
        original_yaml="model_list:\n  - gpt-4\n",
        modified_yaml=modified,
        diff_preview="...",
        risk_level="low",
        requires_restart=True,
    )
    apply_proposal(proposal)
    assert config_file.read_text() == modified


def test_apply_invalid_yaml_raises(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model_list:\n  - gpt-4\n")

    proposal = ConfigProposal(
        description="test",
        config_path=str(config_file),
        original_yaml="model_list:\n  - gpt-4\n",
        modified_yaml=": invalid: yaml: [[[",
        diff_preview="...",
        risk_level="low",
        requires_restart=True,
    )
    import pytest

    with pytest.raises(ValueError):
        apply_proposal(proposal)
