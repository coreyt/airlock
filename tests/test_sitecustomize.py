from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock


def _load_local_sitecustomize():
    module_name = "sitecustomize"
    module_path = Path(__file__).resolve().parent.parent / "sitecustomize.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_apply_patches_for_lazy_mcp_startup_noops_when_not_lazy(monkeypatch):
    sitecustomize = _load_local_sitecustomize()

    monkeypatch.setenv("AIRLOCK_MCP_STARTUP_MODE", "eager")
    fake_manager = MagicMock()

    sitecustomize._apply_patches_for_testing(fake_manager)

    assert not hasattr(fake_manager, "initialize_tool_name_to_mcp_server_name_mapping") or fake_manager.initialize_tool_name_to_mcp_server_name_mapping != sitecustomize._lazy_mcp_mapping_noop


def test_apply_patches_for_lazy_mcp_startup_replaces_initializer(monkeypatch):
    sitecustomize = _load_local_sitecustomize()

    monkeypatch.setenv("AIRLOCK_MCP_STARTUP_MODE", "lazy")
    fake_manager = MagicMock()
    fake_manager.initialize_tool_name_to_mcp_server_name_mapping = MagicMock()

    sitecustomize._apply_patches_for_testing(fake_manager)

    assert (
        fake_manager.initialize_tool_name_to_mcp_server_name_mapping
        is sitecustomize._lazy_mcp_mapping_noop
    )
