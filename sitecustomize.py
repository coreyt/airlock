"""Runtime startup patches for Airlock-owned subprocesses.

Python imports ``sitecustomize`` automatically on interpreter startup when the
module is importable on ``sys.path``. Airlock uses this hook to suppress
LiteLLM's eager MCP tool discovery when the proxy is launched in lazy mode.
"""

from __future__ import annotations

import os


def _lazy_mcp_mapping_noop(*args, **kwargs) -> None:
    """Skip LiteLLM's startup-wide MCP list_tools() sweep in lazy mode."""
    return None


def _apply_patches_for_testing(manager) -> None:
    if os.getenv("AIRLOCK_MCP_STARTUP_MODE", "").strip().lower() != "lazy":
        return
    manager.initialize_tool_name_to_mcp_server_name_mapping = _lazy_mcp_mapping_noop


def _patch_litellm_lazy_mcp_startup() -> None:
    if os.getenv("AIRLOCK_MCP_STARTUP_MODE", "").strip().lower() != "lazy":
        return

    try:
        from litellm.proxy._experimental.mcp_server.mcp_server_manager import (
            global_mcp_server_manager,
        )
    except Exception:
        return

    _apply_patches_for_testing(global_mcp_server_manager)


_patch_litellm_lazy_mcp_startup()
