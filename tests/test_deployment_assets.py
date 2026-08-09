"""Regression checks for deployable production assets."""

from __future__ import annotations

import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_user_systemd_unit_limits_restart_storms():
    unit = (ROOT / "deploy" / "airlock.service").read_text()

    assert "StartLimitIntervalSec=5min" in unit
    assert "StartLimitBurst=3" in unit
    assert "Restart=on-failure" in unit
    assert "MemoryHigh=3G" in unit
    assert "MemoryMax=4G" in unit
