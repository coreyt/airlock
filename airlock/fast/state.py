"""
Airlock Fast — Shared in-memory state store (public re-export facade).

All public names are re-exported from the focused sub-modules below so that
the 56+ existing callers using ``from airlock.fast.state import X`` continue
to resolve correctly without any change to those importers.

Sub-module layout:
  _state_core.py        — BreakerPolicy, CircuitState, ClientState, SessionRecord,
                          ProviderRateLimitState, ClientProviderState, ProviderState,
                          ModelState, StateStore (core registry)
  _state_spend.py       — SpendStore, ProviderSpend
  _state_mcp.py         — McpToolState, McpServerHealth, McpServerState
  _state_persistence.py — checkpoint_spend, restore_spend, checkpoint_state,
                          restore_state

The following stay here (not moved to sub-modules):
  get_store / set_store / _StoreProxy / store  — from ENABLE-stateprovider seam
  tail_jsonl                                   — TUI-specific JSONL tailer
  normalize_client_id / NO_CLIENT_ID           — re-exported from client_identity
"""

from __future__ import annotations

import threading

# ---------------------------------------------------------------------------
# Sub-module re-exports — _state_core
# ---------------------------------------------------------------------------
from airlock.fast._state_core import (  # noqa: F401
    MAX_SAMPLES,
    PROVIDER_ESCALATION_CLIENT_THRESHOLD,
    PROVIDER_ESCALATION_WINDOW_SECONDS,
    PROVIDER_QUARANTINE_SECONDS,
    CLIENT_PROVIDER_COOLDOWN_SECONDS,
    WINDOW_SECONDS,
    BreakerPolicy,
    CircuitState,
    ClientProviderState,
    ClientState,
    ModelState,
    NO_CLIENT_ID,
    ProviderRateLimitState,
    ProviderState,
    SessionRecord,
    StateStore,
    _policy_from_mapping,
    configure_breaker,
    normalize_client_id,
    policy_for,
)

# ---------------------------------------------------------------------------
# Sub-module re-exports — _state_spend
# ---------------------------------------------------------------------------
from airlock.fast._state_spend import (  # noqa: F401
    DEFAULT_SPEND_BUCKET_SECONDS,
    DEFAULT_SPEND_WINDOW_SECONDS,
    ProviderSpend,
    SpendStore,
)

# ---------------------------------------------------------------------------
# Sub-module re-exports — _state_mcp
# ---------------------------------------------------------------------------
from airlock.fast._state_mcp import (  # noqa: F401
    McpServerHealth,
    McpServerState,
    McpToolState,
)

# ---------------------------------------------------------------------------
# Sub-module re-exports — _state_persistence
# ---------------------------------------------------------------------------
from airlock.fast._state_persistence import (  # noqa: F401
    SPEND_STATE_VERSION,
    _CB_STATE_MAX_AGE_SECONDS,
    checkpoint_spend,
    checkpoint_state,
    restore_spend,
    restore_state,
)

# ---------------------------------------------------------------------------
# State-provider injection seam (ENABLE-stateprovider; stays in state.py)
# ---------------------------------------------------------------------------

_default_store: "StateStore | None" = None


def get_store() -> "StateStore":
    """Return the active StateStore (injected or default singleton)."""
    global _default_store
    if _default_store is None:
        _default_store = StateStore()
    return _default_store


def set_store(s: "StateStore | None") -> None:
    """Inject (or clear) the active StateStore. Pass None to reset to default."""
    global _default_store
    _default_store = s


class _StoreProxy:
    """Transparent proxy so `from .state import store` callers need no changes.

    All attribute access is forwarded to the active store returned by get_store().
    `set_store(fresh)` re-routes all existing `store.X` calls immediately.
    """

    def __getattr__(self, name: str):
        return getattr(get_store(), name)

    def __repr__(self) -> str:
        return f"<_StoreProxy wrapping {get_store()!r}>"


# The proxy forwards every attribute to the live StateStore, so it *is* one
# behaviourally — but mypy only sees `_StoreProxy` and rejects it wherever a
# StateStore is expected. Declaring the public symbol as StateStore for type
# checkers keeps call sites honest without weakening them with scattered
# `# type: ignore`s. Runtime binding is unchanged.
store: StateStore = _StoreProxy()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# JSONL log tailer for TUI cross-process state sync (stays in state.py)
# ---------------------------------------------------------------------------


def tail_jsonl(
    log_dir: str,
    stop_event: threading.Event,
    poll_interval: float = 2.0,
) -> None:
    """Tail today's JSONL log file and feed records into the global store.

    Designed to run in a daemon thread started by the TUI.  Picks up the
    current day's log, seeks to the end, then polls for new lines.  Rolls
    over to a new file at midnight.
    """
    import json
    import os
    from datetime import date
    from pathlib import Path

    log_path = Path(log_dir)
    current_date = ""
    fh = None
    pos = 0

    try:
        while not stop_event.is_set():
            today = date.today().isoformat()
            target = log_path / f"airlock-{today}.jsonl"

            # Roll over to new day's file
            if today != current_date:
                if fh is not None:
                    fh.close()
                current_date = today
                if target.is_file():
                    fh = open(target, encoding="utf-8")  # noqa: SIM115
                    fh.seek(0, os.SEEK_END)  # skip existing entries
                    pos = fh.tell()
                else:
                    fh = None
                    pos = 0

            # File may have been created since last check
            if fh is None and target.is_file():
                fh = open(target, encoding="utf-8")  # noqa: SIM115
                fh.seek(0, os.SEEK_END)
                pos = fh.tell()

            if fh is not None:
                fh.seek(pos)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        store.ingest_jsonl_record(record)
                    except (json.JSONDecodeError, Exception):
                        pass
                pos = fh.tell()

            stop_event.wait(poll_interval)
    finally:
        if fh is not None:
            fh.close()
