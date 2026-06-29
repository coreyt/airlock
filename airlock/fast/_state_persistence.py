"""
Airlock Fast — State persistence sub-module.

Contains checkpoint_spend, restore_spend, checkpoint_state, restore_state.
"""

from __future__ import annotations

import time

from airlock.fast._state_core import CircuitState, StateStore

_CB_STATE_MAX_AGE_SECONDS = 300.0  # 5 minutes — BREAKER ONLY (not spend)

SPEND_STATE_VERSION = 1  # versioned spend checkpoint schema (FIX-7)


def checkpoint_spend(state_store: StateStore, path: str) -> None:
    """Atomically snapshot rolling provider spend (FIX-7).

    Versioned schema, prune-before-checkpoint (out-of-window buckets dropped), and
    an atomic temp-file + ``os.replace`` write so a crash mid-write cannot corrupt the
    on-disk checkpoint. Separate sibling file from ``cb_state.json`` so the breaker's
    5-minute freshness gate stays breaker-only.
    """
    import json
    import logging
    import os
    import tempfile

    spend = state_store._spend_store
    data = {
        "version": SPEND_STATE_VERSION,
        "timestamp": time.time(),
        "bucket_width_seconds": spend._bucket_width,
        "window_seconds": spend._window,
        "providers": spend.export_buckets(),
    }
    try:
        dirpath = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(dirpath, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".spend_state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError:
        logging.getLogger("airlock.fast.state").error(
            "Failed to checkpoint spend state", exc_info=True
        )


def restore_spend(state_store: StateStore, path: str) -> None:
    """Rehydrate in-window provider spend from a checkpoint (FIX-1 child-side).

    Idempotent (absolute set, not append) and **age-bounded** by bucket age — NOT
    gated by the breaker's 5-minute freshness window. A version mismatch is skipped.
    """
    import json
    import logging

    log = logging.getLogger("airlock.fast.state")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.debug("No valid spend state to restore from %s", path)
        return

    version = data.get("version")
    if version != SPEND_STATE_VERSION:
        log.warning(
            "Spend checkpoint version mismatch (got %r, expected %d); skipping",
            version,
            SPEND_STATE_VERSION,
        )
        return

    providers = data.get("providers") or {}
    touched = state_store._spend_store.import_buckets(providers)
    # Register restored providers so advisor/tools.py (which iterates
    # store._provider_spend directly) and the router see them after a restart.
    for provider in touched:
        state_store.get_provider_spend(provider)
    log.info("Restored spend for %d providers from %s", len(touched), path)


def checkpoint_state(state_store: StateStore, path: str) -> None:
    """Snapshot circuit breaker state to a JSON file for restart recovery."""
    import json

    models = state_store.all_models()
    data = {
        "timestamp": time.time(),
        "models": {
            name: {
                "circuit": ms.circuit.value,
                "consecutive_failures": ms.consecutive_failures,
            }
            for name, ms in models.items()
            if ms.circuit != CircuitState.CLOSED  # only persist non-healthy
        },
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        import logging

        logging.getLogger("airlock.fast.state").error(
            "Failed to checkpoint circuit breaker state", exc_info=True
        )


def restore_state(state_store: StateStore, path: str) -> None:
    """Restore circuit breaker state from a JSON checkpoint if recent (< 5 min)."""
    import json
    import logging

    log = logging.getLogger("airlock.fast.state")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        log.debug("No valid circuit breaker state to restore from %s", path)
        return

    ts = data.get("timestamp", 0)
    if time.time() - ts > _CB_STATE_MAX_AGE_SECONDS:
        log.info("Stale circuit breaker state (%.0fs old), ignoring", time.time() - ts)
        return

    models = data.get("models", {})
    for name, state_dict in models.items():
        model = state_store.get_model(name)
        circuit_val = state_dict.get("circuit", "closed")
        try:
            model.circuit = CircuitState(circuit_val)
        except ValueError:
            model.circuit = CircuitState.CLOSED
        model.consecutive_failures = state_dict.get("consecutive_failures", 0)
        model.last_state_change = ts

    log.info("Restored circuit breaker state for %d models from %s", len(models), path)
