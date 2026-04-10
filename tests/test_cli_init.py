"""Tests for airlock.cli.init_cmd — the ``airlock init`` command."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from airlock.cli.init_cmd import run


def _make_args(target_dir: str, force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(dir=target_dir, force=force)


# -------------------------------------------------------------------
# File creation
# -------------------------------------------------------------------


def test_creates_config_yaml(tmp_path: Path) -> None:
    run(_make_args(str(tmp_path)))
    cfg = tmp_path / "config.yaml"
    assert cfg.exists()
    data = yaml.safe_load(cfg.read_text())
    assert "model_list" in data
    assert "guardrails" in data
    assert "litellm_settings" in data


def test_creates_dot_env(tmp_path: Path) -> None:
    run(_make_args(str(tmp_path)))
    env = tmp_path / ".env"
    assert env.exists()
    content = env.read_text()
    assert "ANTHROPIC_API_KEY" in content
    assert "OPENAI_API_KEY" in content
    assert "AIRLOCK_MASTER_KEY" in content


def test_creates_logs_directory(tmp_path: Path) -> None:
    run(_make_args(str(tmp_path)))
    assert (tmp_path / "logs").is_dir()


# -------------------------------------------------------------------
# Idempotent behaviour (no --force)
# -------------------------------------------------------------------


def test_does_not_overwrite_existing_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("original")
    run(_make_args(str(tmp_path)))
    assert cfg.read_text() == "original"


def test_does_not_overwrite_existing_env(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("original")
    run(_make_args(str(tmp_path)))
    assert env.read_text() == "original"


# -------------------------------------------------------------------
# --force
# -------------------------------------------------------------------


def test_force_overwrites_existing_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("original")
    run(_make_args(str(tmp_path), force=True))
    assert cfg.read_text() != "original"
    data = yaml.safe_load(cfg.read_text())
    assert "model_list" in data


def test_force_overwrites_existing_env(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("original")
    run(_make_args(str(tmp_path), force=True))
    content = env.read_text()
    assert content != "original"
    assert "ANTHROPIC_API_KEY" in content


# -------------------------------------------------------------------
# Edge cases
# -------------------------------------------------------------------


def test_existing_logs_dir_skipped(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    marker = logs / "marker.txt"
    marker.write_text("keep")
    run(_make_args(str(tmp_path)))
    assert logs.is_dir()
    assert marker.read_text() == "keep"


def test_nonexistent_target_dir_exits(tmp_path: Path) -> None:
    bad = str(tmp_path / "no-such-dir")
    with pytest.raises(SystemExit) as exc_info:
        run(_make_args(bad))
    assert exc_info.value.code == 1


# -------------------------------------------------------------------
# Summary output
# -------------------------------------------------------------------


def test_summary_shows_created(tmp_path: Path, capsys) -> None:
    run(_make_args(str(tmp_path)))
    out = capsys.readouterr().out
    assert "created" in out.lower()


def test_summary_shows_skipped(tmp_path: Path, capsys) -> None:
    (tmp_path / "config.yaml").write_text("x")
    (tmp_path / ".env").write_text("x")
    run(_make_args(str(tmp_path)))
    out = capsys.readouterr().out
    assert "skipped" in out.lower()


def test_summary_shows_overwritten(tmp_path: Path, capsys) -> None:
    (tmp_path / "config.yaml").write_text("x")
    run(_make_args(str(tmp_path), force=True))
    out = capsys.readouterr().out
    assert "overwritten" in out.lower()


def test_next_steps_printed(tmp_path: Path, capsys) -> None:
    run(_make_args(str(tmp_path)))
    out = capsys.readouterr().out
    assert "Next steps" in out
    assert "airlock start" in out


# -------------------------------------------------------------------
# Template content validation
# -------------------------------------------------------------------


def test_generated_config_has_guardrails(tmp_path: Path) -> None:
    run(_make_args(str(tmp_path)))
    data = yaml.safe_load((tmp_path / "config.yaml").read_text())
    names = [g["guardrail_name"] for g in data["guardrails"]]
    assert "airlock-pii-guard" in names
    assert "airlock-keyword-guard" in names
    assert "airlock-fast-guardian" in names


def test_generated_config_uses_env_var_syntax(tmp_path: Path) -> None:
    run(_make_args(str(tmp_path)))
    content = (tmp_path / "config.yaml").read_text()
    assert "os.environ/" in content
