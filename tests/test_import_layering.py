"""Cycle guard (AC-DECOUPLE): enforce one-way layering.

``airlock.fast`` must NOT import ``airlock.guardrails`` — at module level OR
lazily (function-local). The allowed direction, ``airlock.guardrails`` →
``airlock.fast``, is intentionally NOT checked here.

This is a dependency-free AST walk (import-linter is not installed). It uses
``ast.walk`` so it descends into function bodies, catching lazy/deferred imports
too — e.g. a re-introduced ``from airlock.guardrails.overrides import ...`` inside
a method would still be flagged. ``litellm.types.guardrails`` is correctly ignored
because we match the ``airlock.guardrails`` prefix, not the bare word ``guardrails``.
"""

from __future__ import annotations

import ast
from pathlib import Path

FAST_DIR = Path(__file__).resolve().parent.parent / "airlock" / "fast"
FORBIDDEN_PREFIX = "airlock.guardrails"


def _is_forbidden(module: str | None) -> bool:
    if not module:
        return False
    return module == FORBIDDEN_PREFIX or module.startswith(FORBIDDEN_PREFIX + ".")


def _forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            # node.module is None for relative imports like ``from . import x``;
            # relative imports inside airlock.fast cannot reach airlock.guardrails.
            if _is_forbidden(node.module):
                offenders.append(
                    f"{path.name}:{node.lineno} from {node.module} import ..."
                )
    return offenders


def test_fast_does_not_import_guardrails():
    fast_files = sorted(FAST_DIR.glob("*.py"))
    assert fast_files, f"no python files found under {FAST_DIR}"
    offenders: list[str] = []
    for path in fast_files:
        offenders.extend(_forbidden_imports(path))
    assert not offenders, (
        "airlock.fast must not import airlock.guardrails (one-way layering); "
        f"found: {offenders}"
    )
