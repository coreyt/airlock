"""
Airlock Advisor — config proposal handling.

Parses ACTION blocks from LLM output into structured ``ConfigProposal``
objects, generates unified diffs, classifies risk (low / medium / high),
and applies changes with safety rails (.bak backup, YAML validation).
"""

from __future__ import annotations

import difflib
import shutil
from dataclasses import dataclass
from typing import Literal

import yaml

RiskLevel = Literal["low", "medium", "high"]


@dataclass
class ConfigProposal:
    """A proposed configuration change from the advisor."""

    description: str
    config_path: str
    original_yaml: str
    modified_yaml: str
    diff_preview: str
    risk_level: RiskLevel
    requires_restart: bool


def parse_action_block(
    raw: dict, config_path: str | None = None
) -> ConfigProposal | None:
    """Parse an ACTION dict from LLM output into a ConfigProposal.

    Expected format:
        {"type": "config_change", "description": "...", "changes": {"model_list": [...]}}

    Returns None if malformed.
    """
    if not isinstance(raw, dict):
        return None

    if raw.get("type") != "config_change":
        return None

    changes = raw.get("changes")
    if not isinstance(changes, dict):
        return None

    description = raw.get("description", "")

    # Load current config
    original_dict = {}
    original_yaml = ""
    if config_path:
        try:
            with open(config_path) as f:
                original_yaml = f.read()
            original_dict = yaml.safe_load(original_yaml) or {}
        except (OSError, yaml.YAMLError):
            pass

    # Merge changes into original
    merged = dict(original_dict)
    merged.update(changes)
    modified_yaml = yaml.dump(merged, default_flow_style=False)

    diff_preview = generate_diff(original_yaml, modified_yaml)
    risk_level = classify_risk(changes, original_dict or None)
    requires_restart = _requires_restart(changes)

    return ConfigProposal(
        description=description,
        config_path=config_path or "",
        original_yaml=original_yaml,
        modified_yaml=modified_yaml,
        diff_preview=diff_preview,
        risk_level=risk_level,
        requires_restart=requires_restart,
    )


def generate_diff(original: str, modified: str) -> str:
    """Generate a unified diff between two YAML strings."""
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile="original",
        tofile="modified",
    )
    return "".join(diff_lines)


def classify_risk(changes: dict, original: dict | None = None) -> RiskLevel:
    """Classify the risk level of proposed changes.

    - guardrails, general_settings → "high"
    - Model removal (model_list shrinks) → "high"
    - litellm_settings, router_settings → "medium"
    - Everything else → "low"
    """
    high_keys = {"guardrails", "general_settings"}
    medium_keys = {"litellm_settings", "router_settings"}

    # Check for high-risk keys
    if high_keys & set(changes.keys()):
        return "high"

    # Check for model removal
    if "model_list" in changes and original and "model_list" in original:
        new_count = len(changes.get("model_list", []))
        old_count = len(original.get("model_list", []))
        if new_count < old_count:
            return "high"

    # Check for medium-risk keys
    if medium_keys & set(changes.keys()):
        return "medium"

    return "low"


def apply_proposal(proposal: ConfigProposal) -> str:
    """Apply a config proposal, creating a .bak backup first.

    Returns the backup file path.
    Raises ValueError if the modified YAML is invalid.
    """
    # Validate YAML before applying
    try:
        yaml.safe_load(proposal.modified_yaml)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in proposal: {e}") from e

    # Create backup
    backup_path = proposal.config_path + ".bak"
    shutil.copy2(proposal.config_path, backup_path)

    # Write new config
    with open(proposal.config_path, "w") as f:
        f.write(proposal.modified_yaml)

    return backup_path


def _requires_restart(changes: dict) -> bool:
    """Determine if changes require a service restart."""
    restart_keys = {"model_list", "litellm_settings", "router_settings"}
    return bool(restart_keys & set(changes.keys()))
