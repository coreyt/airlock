"""Airlock proxy bootstrap — the single caller of the ``install_*`` seams.

Airlock runs on top of LiteLLM's FastAPI app, so each ``install_*_on_proxy_app``
enriches the existing proxy in place. This module centralizes those installs
behind :func:`bootstrap_airlock_proxy`; the LiteLLM-loaded callback module
``airlock.callbacks.model_override_headers`` triggers it once at import time.

The call order is load-bearing and must not change:
``docs → circuit_health → error_handlers → admin → batch → models_capability``
(the admin perimeter mounts BEFORE the batch gateway so the gateway stays the
outermost ASGI layer — umbrella §3 mount order).
"""

from __future__ import annotations

from airlock.admin.http import install_admin_on_proxy_app
from airlock.batch.middleware import install_batch_gateway_on_proxy_app
from airlock.docs import install_airlock_docs_on_proxy_app
from airlock.health import install_circuit_health_on_proxy_app
from airlock.models_seam import install_models_capability_seam_on_proxy_app
from airlock.proxy_errors import install_airlock_error_handlers_on_proxy_app


def bootstrap_airlock_proxy() -> None:
    """Install all Airlock proxy seams onto the live LiteLLM app, in order."""
    install_airlock_docs_on_proxy_app()
    install_circuit_health_on_proxy_app()
    install_airlock_error_handlers_on_proxy_app()
    # Admin perimeter mounts BEFORE the batch gateway so the gateway stays the
    # outermost ASGI layer (umbrella §3 mount order).
    install_admin_on_proxy_app()
    install_batch_gateway_on_proxy_app()
    install_models_capability_seam_on_proxy_app()
