"""Tests for airlock.cli.main — the unified ``airlock`` CLI dispatcher."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from airlock.cli.main import main


# -------------------------------------------------------------------
# No subcommand → help
# -------------------------------------------------------------------


def test_no_subcommand_prints_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "init" in out
    assert "start" in out
    assert "status" in out
    assert "analyze" in out


# -------------------------------------------------------------------
# init subcommand
# -------------------------------------------------------------------


def test_init_routes_to_init_cmd(tmp_path: Path) -> None:
    main(["init", "--dir", str(tmp_path)])
    assert (tmp_path / "config.yaml").exists()
    assert (tmp_path / ".env").exists()
    assert (tmp_path / "logs").is_dir()


# -------------------------------------------------------------------
# start subcommand
# -------------------------------------------------------------------


def test_start_delegates_to_proxy(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model_list: []")
    (tmp_path / ".env").write_text("")
    with mock.patch("airlock.proxy.main") as mock_proxy:
        main(["start", "--config", str(cfg)])
    mock_proxy.assert_called_once()


def test_start_host_port_sets_env(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model_list: []")
    (tmp_path / ".env").write_text("")
    with mock.patch("airlock.proxy.main"):
        main(["start", "--config", str(cfg), "--host", "127.0.0.1", "--port", "8080"])
    assert os.environ.get("AIRLOCK_HOST") == "127.0.0.1"
    assert os.environ.get("AIRLOCK_PORT") == "8080"


def test_start_config_sets_env(tmp_path: Path) -> None:
    cfg = tmp_path / "my-config.yaml"
    cfg.write_text("model_list: []")
    (tmp_path / ".env").write_text("")
    with mock.patch("airlock.proxy.main"):
        main(["start", "--config", str(cfg)])
    assert os.environ.get("AIRLOCK_CONFIG") == str(cfg)


# -------------------------------------------------------------------
# start pre-flight validation
# -------------------------------------------------------------------


def test_start_missing_config_exits_with_error(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AIRLOCK_CONFIG", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        main(["start"])
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "config.yaml" in err
    assert "airlock init" in err


def test_start_missing_config_with_flag_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["start", "--config", "/nonexistent/config.yaml"])
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "config" in err


def test_start_missing_env_prints_warning(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model_list: []")
    # No .env file
    with mock.patch("airlock.proxy.main"):
        main(["start", "--config", str(cfg)])
    err = capsys.readouterr().err
    assert ".env" in err
    assert "Warning" in err


def test_start_with_config_and_env_no_warning(tmp_path: Path, capsys) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model_list: []")
    (tmp_path / ".env").write_text("")
    with mock.patch("airlock.proxy.main"):
        main(["start", "--config", str(cfg)])
    err = capsys.readouterr().err
    assert err == ""


# -------------------------------------------------------------------
# status subcommand
# -------------------------------------------------------------------


def test_status_routes_to_status_cmd() -> None:
    with mock.patch("airlock.cli.status_cmd.run") as mock_run:
        main(["status"])
    mock_run.assert_called_once()


# -------------------------------------------------------------------
# analyze subcommand
# -------------------------------------------------------------------


def test_analyze_delegates_to_slow_cli() -> None:
    with mock.patch("airlock.slow.cli.main") as mock_analyze:
        main(["analyze", "--days", "14"])
    mock_analyze.assert_called_once()
