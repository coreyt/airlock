"""Bootstrap-order guard: ``bootstrap_airlock_proxy`` runs the six install_*
seams in the EXACT load-bearing order (admin BEFORE batch so the gateway stays
the outermost ASGI layer).
"""

from __future__ import annotations

import airlock.proxy_bootstrap as pb
from airlock.admin.policy import admin_enabled
from airlock.provider_configuration import provider_configuration_snapshot

EXPECTED_ORDER = [
    "docs",
    "circuit_health",
    "error_handlers",
    "embedding_boundary",
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
    monkeypatch.setattr(
        pb, "install_circuit_health_on_proxy_app", _mk("circuit_health")
    )
    monkeypatch.setattr(
        pb, "install_airlock_error_handlers_on_proxy_app", _mk("error_handlers")
    )
    monkeypatch.setattr(
        pb, "install_embedding_request_boundary_on_proxy_app", _mk("embedding_boundary")
    )
    monkeypatch.setattr(pb, "install_admin_on_proxy_app", _mk("admin"))
    monkeypatch.setattr(pb, "_configure_child_startup_configuration", lambda: None)
    monkeypatch.setattr(pb, "install_batch_gateway_on_proxy_app", _mk("batch"))
    monkeypatch.setattr(
        pb, "install_models_capability_seam_on_proxy_app", _mk("models_capability")
    )

    pb.bootstrap_airlock_proxy()

    assert recorded == EXPECTED_ORDER


def test_child_configuration_uses_runtime_file_with_litellm_nested_include_order(
    tmp_path, monkeypatch
):
    grandchild = tmp_path / "grandchild.yaml"
    grandchild.write_text("admin:\n  enabled: true\n")
    direct = tmp_path / "direct.yaml"
    direct.write_text(
        "include: [grandchild.yaml]\nadmin:\n  enabled: false\nmodel_list:\n"
        "  - model_name: direct\n    litellm_params:\n      model: openai/direct\n"
    )
    runtime = tmp_path / "runtime.yaml"
    runtime.write_text("include: [direct.yaml]\nmodel_list: []\n")
    monkeypatch.setenv("AIRLOCK_CONFIG", str(runtime))

    pb._configure_child_startup_configuration()

    assert admin_enabled() is True
    assert (
        provider_configuration_snapshot()["providers"][0]["aliases"][0]["alias"]
        == "direct"
    )
