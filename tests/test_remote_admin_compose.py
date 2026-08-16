"""Structural guard for the opt-in host-console remote Admin Compose profile."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_remote_admin_compose_is_complete_and_loopback_only():
    root = Path(__file__).parents[1]
    profile = yaml.safe_load((root / "docker-compose.remote-admin.yml").read_text())
    service = profile["services"]["airlock"]
    assert service["ports"] == ["127.0.0.1:${AIRLOCK_PORT:-4000}:4000"]
    assert service["environment"] == {
        "AIRLOCK_HOST": "0.0.0.0",
        "AIRLOCK_SSL_CERTFILE": "/run/airlock-tls/server.crt",
        "AIRLOCK_SSL_KEYFILE": "/run/airlock-tls/server.key",
    }
    assert (
        "docker-compose.yml"
        not in (root / "docker-compose.remote-admin.yml").read_text()
    )
    assert "./config.yaml:/app/config.yaml:ro" in service["volumes"]
    assert "./certs/server.crt:/run/airlock-tls/server.crt:ro" in service["volumes"]
    assert "./certs/server.key:/run/airlock-tls/server.key:ro" in service["volumes"]
