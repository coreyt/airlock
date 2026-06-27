"""Bootstrap-order guard: ``bootstrap_airlock_proxy`` runs the six install_*
seams in the EXACT load-bearing order (admin BEFORE batch so the gateway stays
the outermost ASGI layer).
"""

from __future__ import annotations

import airlock.proxy_bootstrap as pb

EXPECTED_ORDER = [
    "docs",
    "circuit_health",
    "error_handlers",
    "admin",
    "batch",
    "models_capability",
]


def test_bootstrap_runs_installs_in_order(monkeypatch):
    recorded: list[str] = []

    def _mk(label):
        def _install():
            recorded.append(label)
            return True

        return _install

    monkeypatch.setattr(pb, "install_airlock_docs_on_proxy_app", _mk("docs"))
    monkeypatch.setattr(pb, "install_circuit_health_on_proxy_app", _mk("circuit_health"))
    monkeypatch.setattr(
        pb, "install_airlock_error_handlers_on_proxy_app", _mk("error_handlers")
    )
    monkeypatch.setattr(pb, "install_admin_on_proxy_app", _mk("admin"))
    monkeypatch.setattr(pb, "install_batch_gateway_on_proxy_app", _mk("batch"))
    monkeypatch.setattr(
        pb, "install_models_capability_seam_on_proxy_app", _mk("models_capability")
    )

    pb.bootstrap_airlock_proxy()

    assert recorded == EXPECTED_ORDER
