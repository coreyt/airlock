"""
Airlock Fast — Core state classes sub-module.

Contains BreakerPolicy, CircuitState, ClientState, SessionRecord,
ProviderRateLimitState, ClientProviderState, ProviderState, ModelState,
StateStore, and the breaker configuration helpers.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

# Canonical client-identity helpers live in ``airlock.client_identity``; re-export
# here so the many ``from airlock.fast.state import normalize_client_id`` callers
# (and ``state.NO_CLIENT_ID``) keep working against the single implementation.
from airlock.client_identity import NO_CLIENT_ID as NO_CLIENT_ID  # re-export
from airlock.client_identity import normalize_client_id

# Sub-module imports (no cycle: these modules do not import from _state_core)
from airlock.fast._state_mcp import McpServerState, McpToolState
from airlock.fast._state_spend import ProviderSpend, SpendStore

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
WINDOW_SECONDS = 300  # default sliding-window duration (5 min)
MAX_SAMPLES = 1000  # cap per deque to bound memory
CLIENT_PROVIDER_COOLDOWN_SECONDS = 300.0
PROVIDER_QUARANTINE_SECONDS = 300.0
PROVIDER_ESCALATION_WINDOW_SECONDS = 300.0
PROVIDER_ESCALATION_CLIENT_THRESHOLD = 2


# ---------------------------------------------------------------------------
# Per-client circuit-breaker policy (A1 + E)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BreakerPolicy:
    """Resolved breaker tuning for one client key.

    Defaults reproduce the historical one-strike / 300 s behaviour, so a deploy
    with no ``airlock_settings.circuit_breaker`` config and no
    ``AIRLOCK_BREAKER_OVERRIDES`` env behaves exactly as before (CC-3).
    """

    rate_limit_threshold: int = 1
    rate_limit_window_seconds: float = float(WINDOW_SECONDS)
    client_cooldown_seconds: float = CLIENT_PROVIDER_COOLDOWN_SECONDS
    provider_cooldown_seconds: float = PROVIDER_QUARANTINE_SECONDS
    provider_escalation_client_threshold: int = PROVIDER_ESCALATION_CLIENT_THRESHOLD
    escalation_exempt: bool = False
    disabled: bool = False


_DEFAULT_BREAKER_POLICY = BreakerPolicy()
# Module-level resolved config: a default policy plus per-client overrides.
_breaker_default: BreakerPolicy = _DEFAULT_BREAKER_POLICY
_breaker_clients: dict[str, BreakerPolicy] = {}


def _policy_from_mapping(base: BreakerPolicy, raw: dict) -> BreakerPolicy:
    """Build a BreakerPolicy from ``base`` overlaid with the keys present in raw."""
    import logging

    if not isinstance(raw, dict):
        return base
    fields = {
        "rate_limit_threshold": int,
        "rate_limit_window_seconds": float,
        "client_cooldown_seconds": float,
        "provider_cooldown_seconds": float,
        "provider_escalation_client_threshold": int,
        "escalation_exempt": bool,
        "disabled": bool,
    }
    values = {f: getattr(base, f) for f in fields}
    for key, caster in fields.items():
        if key in raw and raw[key] is not None:
            try:
                values[key] = caster(raw[key])
            except (TypeError, ValueError):
                logging.getLogger("airlock.fast.state").warning(
                    "Invalid circuit_breaker value for %s; ignoring", key
                )
    return BreakerPolicy(**values)


def configure_breaker(config: dict | None) -> None:
    """Load breaker policy from config + ``AIRLOCK_BREAKER_OVERRIDES`` env (CC-2).

    Read once at startup. Precedence: per-client override → global default →
    hard-coded constant. Malformed env JSON falls back to config/defaults with a
    logged warning (never crashes startup).
    """
    import json
    import logging
    import os

    log = logging.getLogger("airlock.fast.state")
    global _breaker_default, _breaker_clients

    # Collect raw override mappings first so precedence can be applied once at the
    # end: per-client > default, and env > config. Building policies eagerly (as a
    # prior version did) leaked the *config* default into config-defined clients
    # even when env overrode the default.
    default_raw: dict = {}
    client_raw: dict[str, dict] = {}

    def _merge_clients(raw_clients: object) -> None:
        if not isinstance(raw_clients, dict):
            if raw_clients is not None:
                log.warning("circuit_breaker.clients is not a mapping, ignoring")
            return
        for cid, raw in raw_clients.items():
            if isinstance(raw, dict):
                key = normalize_client_id(str(cid))
                client_raw.setdefault(key, {}).update(raw)

    block = ((config or {}).get("airlock_settings") or {}).get("circuit_breaker") or {}
    if isinstance(block, dict):
        default_raw.update({k: v for k, v in block.items() if k != "clients"})
        _merge_clients(block.get("clients"))

    raw_env = os.environ.get("AIRLOCK_BREAKER_OVERRIDES")
    if raw_env:
        try:
            parsed = json.loads(raw_env)
        except (json.JSONDecodeError, TypeError):
            log.warning("Invalid AIRLOCK_BREAKER_OVERRIDES JSON, using defaults")
            parsed = None
        if isinstance(parsed, dict):
            if isinstance(parsed.get("defaults"), dict):
                default_raw.update(parsed["defaults"])  # env > config
            _merge_clients(parsed.get("clients"))
        elif parsed is not None:
            log.warning("AIRLOCK_BREAKER_OVERRIDES has wrong shape, ignoring")

    default = _policy_from_mapping(_DEFAULT_BREAKER_POLICY, default_raw)
    clients: dict[str, BreakerPolicy] = {
        cid: _policy_from_mapping(default, raw) for cid, raw in client_raw.items()
    }

    _breaker_default = default
    _breaker_clients = clients


def policy_for(client_id: str) -> BreakerPolicy:
    """Resolve the breaker policy for a client key (per-client → default)."""
    return _breaker_clients.get(normalize_client_id(client_id), _breaker_default)


class CircuitState(Enum):
    """Model health states (classic circuit-breaker pattern)."""

    CLOSED = "closed"  # healthy — requests flow normally
    OPEN = "open"  # broken — requests should failover
    HALF_OPEN = "half_open"  # probing — one test request allowed


# ---------------------------------------------------------------------------
# Per-client state
# ---------------------------------------------------------------------------
@dataclass
class ClientState:
    """Tracks a single client's recent behaviour."""

    client_id: str
    request_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    latencies_ms: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    errors: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    successes: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    gemini_outcomes: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    threat_score: float = 0.0
    backoff_until: float = 0.0  # unix ts; 0 → no backoff

    # -- writers --------------------------------------------------------

    def record_request(self, timestamp: float) -> None:
        self.request_times.append(timestamp)

    def record_success(self, timestamp: float, latency_ms: float) -> None:
        self.successes.append(timestamp)
        self.latencies_ms.append((timestamp, latency_ms))

    def record_error(self, timestamp: float, error_type: str) -> None:
        self.errors.append((timestamp, error_type))

    def record_gemini_outcome(self, timestamp: float, output_shape: str) -> None:
        self.gemini_outcomes.append((timestamp, output_shape))

    # -- readers --------------------------------------------------------

    def recent_request_count(self, window_seconds: float = WINDOW_SECONDS) -> int:
        cutoff = time.time() - window_seconds
        return sum(1 for t in self.request_times if t > cutoff)

    def recent_success_count(self, window_seconds: float = WINDOW_SECONDS) -> int:
        cutoff = time.time() - window_seconds
        return sum(1 for t in self.successes if t > cutoff)

    def recent_error_count(self, window_seconds: float = WINDOW_SECONDS) -> int:
        cutoff = time.time() - window_seconds
        return sum(1 for t, _ in self.errors if t > cutoff)

    def recent_error_rate(self, window_seconds: float = WINDOW_SECONDS) -> float:
        cutoff = time.time() - window_seconds
        errors = sum(1 for t, _ in self.errors if t > cutoff)
        successes = sum(1 for t in self.successes if t > cutoff)
        total = errors + successes
        return errors / total if total > 0 else 0.0

    def recent_avg_latency(
        self, window_seconds: float = WINDOW_SECONDS
    ) -> float | None:
        cutoff = time.time() - window_seconds
        recent = [lat for t, lat in self.latencies_ms if t > cutoff]
        return sum(recent) / len(recent) if recent else None

    def is_in_backoff(self) -> bool:
        return time.time() < self.backoff_until

    def recent_gemini_outcome_count(
        self,
        output_shape: str,
        window_seconds: float = WINDOW_SECONDS,
    ) -> int:
        cutoff = time.time() - window_seconds
        return sum(
            1
            for t, shape in self.gemini_outcomes
            if t > cutoff and shape == output_shape
        )


# ---------------------------------------------------------------------------
# Session affinity
# ---------------------------------------------------------------------------
@dataclass
class SessionRecord:
    """Tracks which model a session is pinned to."""

    session_id: str
    model: str
    created_at: float = 0.0
    last_used: float = 0.0


@dataclass
class ProviderRateLimitState:
    """Latest upstream quota headroom for a provider (workstream C, observe-only).

    Updated every call from parsed ``x-ratelimit-*`` headers; fields stay ``None``
    until a header is observed. Cheap, in-memory, mirrors ``ProviderSpend``.
    """

    provider: str
    remaining_tokens: int | None = None
    remaining_requests: int | None = None
    limit_tokens: int | None = None
    limit_requests: int | None = None
    reset_tokens_seconds: float | None = None
    reset_requests_seconds: float | None = None
    observed_at: float = 0.0

    def update(self, parsed: dict, timestamp: float) -> None:
        """Overlay the non-None parsed fields; record when it was observed."""
        for key in (
            "remaining_tokens",
            "remaining_requests",
            "limit_tokens",
            "limit_requests",
            "reset_tokens_seconds",
            "reset_requests_seconds",
        ):
            value = parsed.get(key)
            if value is not None:
                setattr(self, key, value)
        self.observed_at = timestamp


# ---------------------------------------------------------------------------
# Provider protection state
# ---------------------------------------------------------------------------
@dataclass
class ClientProviderState:
    """Tracks a single client's health against a specific provider."""

    client_id: str
    provider: str
    request_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    success_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    failure_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    rate_limit_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    quarantine_until: float = 0.0
    # CC-6: floor for the rate-limit window; an operator clear sets this so the
    # threshold counter cannot re-arm off pre-clear 429s. Owned here; written by
    # the admin clear mutators (Pack ADM-state).
    cleared_at: float = 0.0
    # CC-7: one-probe gate after a probe-mode clear (mirrors ModelState half-open).
    _half_open_probe: bool = False
    last_reason: str = ""
    last_error_type: str = ""
    last_action: str = ""

    def record_request(self, timestamp: float) -> None:
        self.request_times.append(timestamp)

    def record_success(self, timestamp: float) -> None:
        self.success_times.append(timestamp)
        if self._half_open_probe:
            # Successful probe after an admin clear → close the breaker (CC-7).
            self._half_open_probe = False
            self.quarantine_until = 0.0

    def record_failure(self, timestamp: float) -> None:
        self.failure_times.append(timestamp)

    def record_rate_limit(
        self,
        timestamp: float,
        reason: str,
        error_type: str,
        cooldown_seconds: float = CLIENT_PROVIDER_COOLDOWN_SECONDS,
        *,
        rate_limit_threshold: int = 1,
        rate_limit_window_seconds: float = WINDOW_SECONDS,
    ) -> bool:
        """Record a 429; arm the quarantine only if the threshold is met (A1).

        The event is always recorded (for logging + provider escalation). The
        quarantine is armed when the count of recent 429s reaches
        ``rate_limit_threshold`` (default 1 reproduces today's one-strike
        behaviour, CC-3) or immediately when a half-open probe fails (CC-7).
        Returns whether the quarantine was armed.
        """
        self.rate_limit_times.append(timestamp)
        self.failure_times.append(timestamp)
        self.last_reason = reason
        self.last_error_type = error_type
        probe = self._half_open_probe
        self._half_open_probe = False
        armed = probe or (
            self.recent_rate_limit_count(rate_limit_window_seconds)
            >= rate_limit_threshold
        )
        if armed:
            self.quarantine_until = max(
                self.quarantine_until, timestamp + cooldown_seconds
            )
            self.last_action = "client_quarantine"
        return armed

    def is_quarantined(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now < self.quarantine_until

    def cooldown_remaining(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, self.quarantine_until - now)

    def recent_request_count(self, window_seconds: float = WINDOW_SECONDS) -> int:
        cutoff = time.time() - window_seconds
        return sum(1 for t in self.request_times if t > cutoff)

    def recent_rate_limit_count(self, window_seconds: float = WINDOW_SECONDS) -> int:
        # CC-6: events before an operator clear are hidden from threshold logic.
        cutoff = max(time.time() - window_seconds, self.cleared_at)
        return sum(1 for t in self.rate_limit_times if t > cutoff)


@dataclass
class ProviderState:
    """Tracks provider-wide health and escalated quarantine state."""

    provider: str
    request_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    success_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    failure_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    rate_limit_events: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    gemini_outcomes: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    gemini_modes: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    quarantine_until: float = 0.0
    # CC-6: floor for impacted_clients() so a provider clear is not undone by
    # pre-clear client history. CC-7: one-probe half-open gate. Both owned here;
    # written by the admin clear mutators (Pack ADM-state).
    cleared_at: float = 0.0
    _half_open_probe: bool = False
    last_reason: str = ""
    last_error_type: str = ""
    last_action: str = ""

    def record_request(self, timestamp: float) -> None:
        self.request_times.append(timestamp)

    def record_success(self, timestamp: float) -> None:
        self.success_times.append(timestamp)
        if self._half_open_probe:
            # Successful probe after an admin clear → close the breaker (CC-7).
            self._half_open_probe = False
            self.quarantine_until = 0.0

    def record_failure(self, timestamp: float) -> None:
        self.failure_times.append(timestamp)

    def record_gemini_outcome(
        self,
        timestamp: float,
        output_shape: str,
        reasoning_mode: str,
    ) -> None:
        self.gemini_outcomes.append((timestamp, output_shape))
        self.gemini_modes.append((timestamp, reasoning_mode))

    def record_rate_limit(
        self,
        timestamp: float,
        client_id: str,
        reason: str,
        error_type: str,
    ) -> None:
        self.rate_limit_events.append((timestamp, client_id))
        self.failure_times.append(timestamp)
        self.last_reason = reason
        self.last_error_type = error_type

    def quarantine(
        self,
        timestamp: float,
        reason: str,
        error_type: str,
        cooldown_seconds: float = PROVIDER_QUARANTINE_SECONDS,
    ) -> None:
        self.quarantine_until = max(self.quarantine_until, timestamp + cooldown_seconds)
        self.last_reason = reason
        self.last_error_type = error_type
        self.last_action = "provider_quarantine"

    def is_quarantined(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now < self.quarantine_until

    def cooldown_remaining(self, now: float | None = None) -> float:
        now = time.time() if now is None else now
        return max(0.0, self.quarantine_until - now)

    def recent_request_count(self, window_seconds: float = WINDOW_SECONDS) -> int:
        cutoff = time.time() - window_seconds
        return sum(1 for t in self.request_times if t > cutoff)

    def recent_error_rate(self, window_seconds: float = WINDOW_SECONDS) -> float:
        cutoff = time.time() - window_seconds
        errors = sum(1 for t in self.failure_times if t > cutoff)
        successes = sum(1 for t in self.success_times if t > cutoff)
        total = errors + successes
        return errors / total if total > 0 else 0.0

    def impacted_clients(
        self,
        window_seconds: float = PROVIDER_ESCALATION_WINDOW_SECONDS,
    ) -> set[str]:
        # CC-6: floor on cleared_at so a provider clear is not undone by pre-clear
        # client history at the next 429 (escalation reads this set).
        cutoff = max(time.time() - window_seconds, self.cleared_at)
        return {client_id for ts, client_id in self.rate_limit_events if ts > cutoff}

    def recent_gemini_outcome_count(
        self,
        output_shape: str,
        window_seconds: float = WINDOW_SECONDS,
    ) -> int:
        cutoff = time.time() - window_seconds
        return sum(
            1
            for t, shape in self.gemini_outcomes
            if t > cutoff and shape == output_shape
        )

    def recent_gemini_mode(self, window_seconds: float = WINDOW_SECONDS) -> str | None:
        cutoff = time.time() - window_seconds
        recent = [mode for t, mode in self.gemini_modes if t > cutoff]
        return recent[-1] if recent else None


# ---------------------------------------------------------------------------
# Per-model state
# ---------------------------------------------------------------------------
@dataclass
class ModelState:
    """Tracks a single model/provider's health for circuit-breaking."""

    model_name: str
    circuit: CircuitState = CircuitState.CLOSED
    failure_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    success_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    latencies_ms: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    last_state_change: float = 0.0
    consecutive_failures: int = 0
    _half_open_admitted: bool = False  # gate: only one probe in half-open

    # Thresholds (class-level defaults)
    FAILURE_THRESHOLD: int = 5  # consecutive failures → open
    RECOVERY_TIMEOUT: float = 30.0  # seconds before half-open probe
    SUCCESS_THRESHOLD: int = 3  # half-open successes → close

    def record_success(self, timestamp: float, latency_ms: float) -> None:
        self.success_times.append(timestamp)
        self.latencies_ms.append((timestamp, latency_ms))
        self.consecutive_failures = 0

        if self.circuit == CircuitState.HALF_OPEN:
            self._half_open_admitted = False
            recent_ok = sum(1 for t in self.success_times if t > self.last_state_change)
            if recent_ok >= self.SUCCESS_THRESHOLD:
                self.circuit = CircuitState.CLOSED
                self.last_state_change = timestamp

    def record_failure(self, timestamp: float) -> None:
        self.failure_times.append(timestamp)
        self.consecutive_failures += 1

        if self.circuit == CircuitState.HALF_OPEN:
            self.circuit = CircuitState.OPEN
            self._half_open_admitted = False
            self.last_state_change = timestamp
        elif self.circuit == CircuitState.CLOSED:
            if self.consecutive_failures >= self.FAILURE_THRESHOLD:
                self.circuit = CircuitState.OPEN
                self.last_state_change = timestamp

    def should_allow_request(self) -> bool:
        if self.circuit == CircuitState.CLOSED:
            return True
        if self.circuit == CircuitState.OPEN:
            if time.time() - self.last_state_change >= self.RECOVERY_TIMEOUT:
                self.circuit = CircuitState.HALF_OPEN
                self.last_state_change = time.time()
                self._half_open_admitted = True
                return True  # allow a single probe
            return False
        # HALF_OPEN → only allow if no probe is already in flight
        if not self._half_open_admitted:
            self._half_open_admitted = True
            return True
        return False

    def recent_avg_latency(
        self, window_seconds: float = WINDOW_SECONDS
    ) -> float | None:
        cutoff = time.time() - window_seconds
        recent = [lat for t, lat in self.latencies_ms if t > cutoff]
        return sum(recent) / len(recent) if recent else None


# ---------------------------------------------------------------------------
# Central state store  (module-level singleton)
# ---------------------------------------------------------------------------
class StateStore:
    """Thread-safe registry of all client and model states."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._clients: dict[str, ClientState] = {}
        self._models: dict[str, ModelState] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._provider_spend: dict[str, ProviderSpend] = {}
        # Single seam-backed accumulator shared by all provider handles; shares the
        # store lock so compound read-modify-write stays atomic (FIX-3).
        self._spend_store = SpendStore(lock=self._lock)
        self._provider_ratelimit: dict[str, ProviderRateLimitState] = {}
        self._providers: dict[str, ProviderState] = {}
        self._client_provider: dict[tuple[str, str], ClientProviderState] = {}
        self._mcp_servers: dict[str, McpServerState] = {}
        self._mcp_tools: dict[str, McpToolState] = {}
        self._mcp_call_count: int = 0
        self._llm_call_count: int = 0

    def get_client(self, client_id: str) -> ClientState:
        client_id = normalize_client_id(client_id)
        with self._lock:
            if client_id not in self._clients:
                self._clients[client_id] = ClientState(client_id=client_id)
            return self._clients[client_id]

    def get_model(self, model_name: str) -> ModelState:
        with self._lock:
            if model_name not in self._models:
                self._models[model_name] = ModelState(model_name=model_name)
            return self._models[model_name]

    def all_clients(self) -> dict[str, ClientState]:
        with self._lock:
            return dict(self._clients)

    def record_threat_score(
        self,
        client: ClientState,
        score: float,
        now: float,
        *,
        decay_base: float,
        threshold: float,
        base_backoff_s: float,
        max_backoff_s: float,
    ) -> tuple[float, bool, float]:
        """Atomically blend ``score`` into ``client.threat_score`` under the lock.

        The heuristic ``score`` is computed lock-free by the caller; this mutator
        performs only the read-modify-write so concurrent same-client requests
        cannot lose an update. Snapshots the request deque, decays the accumulated
        score, applies ``combined = max(score, prev * decay)``, writes
        ``threat_score`` and (when blocked) ``backoff_until``, and returns
        ``(combined, blocked, backoff_seconds)``.
        """
        with self._lock:
            request_times = list(client.request_times)
            if len(request_times) >= 2:
                # guardian records the current request BEFORE assessing, so [-1] is
                # "now"; use [-2] for the previous request's timestamp.
                elapsed = now - request_times[-2]
            else:
                elapsed = 1.0
            elapsed = max(elapsed, 0.01)  # guard against zero/negative
            decay_factor = decay_base**elapsed
            combined = max(score, client.threat_score * decay_factor)
            client.threat_score = combined

            blocked = combined >= threshold
            backoff_seconds = 0.0
            if blocked:
                exponent = min(10, int(combined * 10))
                backoff_seconds = min(max_backoff_s, base_backoff_s * (2**exponent))
                client.backoff_until = now + backoff_seconds
            return combined, blocked, backoff_seconds

    def all_models(self) -> dict[str, ModelState]:
        with self._lock:
            return dict(self._models)

    # -- Session affinity --------------------------------------------------

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            return self._sessions.get(session_id)

    def set_session(self, session_id: str, model: str) -> SessionRecord:
        now = time.time()
        with self._lock:
            if session_id in self._sessions:
                rec = self._sessions[session_id]
                rec.model = model
                rec.last_used = now
            else:
                rec = SessionRecord(
                    session_id=session_id,
                    model=model,
                    created_at=now,
                    last_used=now,
                )
                self._sessions[session_id] = rec
            return rec

    # -- Provider spend ----------------------------------------------------

    def get_provider_spend(self, provider: str) -> ProviderSpend:
        with self._lock:
            if provider not in self._provider_spend:
                self._provider_spend[provider] = ProviderSpend(
                    provider=provider, _store=self._spend_store
                )
            return self._provider_spend[provider]

    def get_provider_ratelimit(self, provider: str) -> ProviderRateLimitState:
        with self._lock:
            if provider not in self._provider_ratelimit:
                self._provider_ratelimit[provider] = ProviderRateLimitState(
                    provider=provider
                )
            return self._provider_ratelimit[provider]

    def record_provider_ratelimit(
        self, provider: str, parsed: dict, timestamp: float
    ) -> None:
        """Update a provider's headroom from parsed ``x-ratelimit-*`` headers.

        No-op when ``parsed`` carries no usable values, so callers can pass the
        tolerant parser output unconditionally.
        """
        if not parsed or all(v is None for v in parsed.values()):
            return
        with self._lock:
            self.get_provider_ratelimit(provider).update(parsed, timestamp)

    def all_provider_ratelimits(self) -> dict[str, ProviderRateLimitState]:
        with self._lock:
            return dict(self._provider_ratelimit)

    def get_provider(self, provider: str) -> ProviderState:
        with self._lock:
            if provider not in self._providers:
                self._providers[provider] = ProviderState(provider=provider)
            return self._providers[provider]

    def all_providers(self) -> dict[str, ProviderState]:
        with self._lock:
            return dict(self._providers)

    def get_client_provider(self, client_id: str, provider: str) -> ClientProviderState:
        client_id = normalize_client_id(client_id)
        key = (client_id, provider)
        with self._lock:
            if key not in self._client_provider:
                self._client_provider[key] = ClientProviderState(
                    client_id=client_id,
                    provider=provider,
                )
            return self._client_provider[key]

    def all_client_provider_states(self) -> dict[tuple[str, str], ClientProviderState]:
        with self._lock:
            return dict(self._client_provider)

    def record_provider_request(
        self, client_id: str, provider: str, timestamp: float
    ) -> None:
        with self._lock:
            self.get_provider(provider).record_request(timestamp)
            self.get_client_provider(client_id, provider).record_request(timestamp)

    def record_provider_success(
        self, client_id: str, provider: str, timestamp: float
    ) -> None:
        with self._lock:
            self.get_provider(provider).record_success(timestamp)
            self.get_client_provider(client_id, provider).record_success(timestamp)

    def record_provider_failure(
        self, client_id: str, provider: str, timestamp: float
    ) -> None:
        with self._lock:
            self.get_provider(provider).record_failure(timestamp)
            self.get_client_provider(client_id, provider).record_failure(timestamp)

    def _escalation_impacted(
        self,
        provider: str,
        provider_state: ProviderState,
        window_seconds: float,
        now: float,
    ) -> set[str]:
        """Distinct clients eligible to drive provider-wide escalation.

        Applies both CC-6 floors (the provider's ``cleared_at`` and each
        client→provider bucket's ``cleared_at``) and excludes clients whose policy
        is ``escalation_exempt`` or ``disabled``. Caller holds ``self._lock``.
        """
        cutoff = max(now - window_seconds, provider_state.cleared_at)
        impacted: set[str] = set()
        for ts, cid in provider_state.rate_limit_events:
            if ts <= cutoff:
                continue
            pol = policy_for(cid)
            if pol.escalation_exempt or pol.disabled:
                continue
            bucket = self._client_provider.get((normalize_client_id(cid), provider))
            if bucket is not None and ts <= bucket.cleared_at:
                continue  # this client's bucket was cleared after the event
            impacted.add(cid)
        return impacted

    def record_provider_rate_limit(
        self,
        client_id: str,
        provider: str,
        timestamp: float,
        reason: str,
        error_type: str,
    ) -> dict[str, float | str | bool]:
        client_id = normalize_client_id(client_id)
        with self._lock:
            policy = policy_for(client_id)
            client_provider = self.get_client_provider(client_id, provider)
            provider_state = self.get_provider(provider)

            # The provider event is always recorded so escalation can see it.
            provider_state.record_rate_limit(timestamp, client_id, reason, error_type)

            if policy.disabled:
                # Breaker off for this client: record the 429 for logging but never
                # arm and never contribute to escalation (E).
                client_provider.rate_limit_times.append(timestamp)
                client_provider.failure_times.append(timestamp)
                client_provider.last_reason = reason
                client_provider.last_error_type = error_type
                return {
                    "client_quarantined": False,
                    "provider_quarantined": False,
                    "client_cooldown_seconds": 0.0,
                    "provider_cooldown_seconds": provider_state.cooldown_remaining(
                        timestamp
                    ),
                    "impacted_clients": 0,
                }

            client_quarantined = client_provider.record_rate_limit(
                timestamp,
                reason,
                error_type,
                cooldown_seconds=policy.client_cooldown_seconds,
                rate_limit_threshold=policy.rate_limit_threshold,
                rate_limit_window_seconds=policy.rate_limit_window_seconds,
            )

            # Escalation set: distinct clients with a recent 429, with the CC-6
            # floors applied (provider cleared_at AND each client bucket's
            # cleared_at) and escalation_exempt / disabled clients excluded (E).
            impacted = self._escalation_impacted(
                provider,
                provider_state,
                policy.rate_limit_window_seconds,
                timestamp,
            )
            provider_quarantined = False
            if provider_state._half_open_probe:
                # CC-7: a failed probe after a provider half-open clear re-arms
                # immediately, regardless of the escalation threshold.
                provider_state._half_open_probe = False
                provider_state.quarantine(
                    timestamp,
                    reason,
                    error_type,
                    cooldown_seconds=policy.provider_cooldown_seconds,
                )
                provider_quarantined = True
            elif len(impacted) >= policy.provider_escalation_client_threshold:
                provider_state.quarantine(
                    timestamp,
                    reason,
                    error_type,
                    cooldown_seconds=policy.provider_cooldown_seconds,
                )
                provider_quarantined = True

            return {
                "client_quarantined": client_quarantined,
                "provider_quarantined": provider_quarantined,
                "client_cooldown_seconds": client_provider.cooldown_remaining(
                    timestamp
                ),
                "provider_cooldown_seconds": provider_state.cooldown_remaining(
                    timestamp
                ),
                "impacted_clients": len(impacted),
            }

    # -- Admin control plane (CC-8) ---------------------------------------
    # Mutators clear/arm protection state and RETURN the ``admin_action`` JSONL
    # payload — one object that is the audit record AND the channel by which the
    # separate TUI process converges its read-replica (CC-9). The breaker pack
    # owns ``cleared_at`` / ``_half_open_probe``; these methods only write them.

    @staticmethod
    def _admin_iso(now: float) -> str:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(now, timezone.utc).isoformat()

    def clear_provider_quarantine(
        self,
        provider: str,
        *,
        mode: str = "probe",
        actor: str = "",
        now: float | None = None,
    ) -> dict:
        """Clear a provider quarantine and cascade to its client buckets (R12).

        ``mode="probe"`` drops to half-open (one probe admitted; a success closes,
        a failure re-arms). ``mode="force"`` hard-clears. Sets ``cleared_at`` on the
        provider AND every ``(client, provider)`` bucket so the threshold counter
        and escalation cannot re-arm off pre-clear history (CC-6).
        """
        if mode not in ("probe", "force"):
            raise ValueError(f"unknown clear mode: {mode!r}")
        now = time.time() if now is None else now
        probe = mode == "probe"
        with self._lock:
            ps = self.get_provider(provider)
            ps.cleared_at = now
            ps.quarantine_until = now if probe else 0.0
            ps._half_open_probe = probe
            cascaded = 0
            for (_cid, prov), cp in self._client_provider.items():
                if prov == provider:
                    cp.cleared_at = now
                    cp.quarantine_until = now if probe else 0.0
                    cp._half_open_probe = probe
                    cascaded += 1
        return {
            "record_type": "admin_action",
            "timestamp": self._admin_iso(now),
            "op": "clear_provider_quarantine",
            "actor": actor,
            "provider": provider,
            "mode": mode,
            "cascaded_clients": cascaded,
        }

    def clear_client_provider_quarantine(
        self,
        client_id: str,
        provider: str,
        *,
        mode: str = "probe",
        actor: str = "",
        now: float | None = None,
    ) -> dict:
        """Clear one client→provider quarantine bucket — the precise UN-10 op."""
        if mode not in ("probe", "force"):
            raise ValueError(f"unknown clear mode: {mode!r}")
        now = time.time() if now is None else now
        client_id = normalize_client_id(client_id)
        probe = mode == "probe"
        with self._lock:
            cp = self.get_client_provider(client_id, provider)
            cp.cleared_at = now
            cp.quarantine_until = now if probe else 0.0
            cp._half_open_probe = probe
        return {
            "record_type": "admin_action",
            "timestamp": self._admin_iso(now),
            "op": "clear_client_provider_quarantine",
            "actor": actor,
            "client_id": client_id,
            "provider": provider,
            "mode": mode,
        }

    def clear_client_backoff(
        self, client_id: str, *, actor: str = "", now: float | None = None
    ) -> dict:
        """Clear a client's threat backoff (no breaker history involved)."""
        now = time.time() if now is None else now
        client_id = normalize_client_id(client_id)
        with self._lock:
            self.get_client(client_id).backoff_until = 0.0
        return {
            "record_type": "admin_action",
            "timestamp": self._admin_iso(now),
            "op": "clear_client_backoff",
            "actor": actor,
            "client_id": client_id,
        }

    def reset_model_circuit(
        self, model: str, *, actor: str = "", now: float | None = None
    ) -> dict:
        """Drop a model circuit to half-open so the next call probes."""
        now = time.time() if now is None else now
        with self._lock:
            ms = self.get_model(model)
            ms.circuit = CircuitState.HALF_OPEN
            ms.consecutive_failures = 0
            ms._half_open_admitted = False
            ms.last_state_change = now
        return {
            "record_type": "admin_action",
            "timestamp": self._admin_iso(now),
            "op": "reset_model_circuit",
            "actor": actor,
            "model": model,
        }

    def quarantine_provider(
        self,
        provider: str,
        *,
        actor: str = "",
        now: float | None = None,
        cooldown: float | None = None,
    ) -> dict:
        """Manually arm a provider quarantine (operator/loopback-only op)."""
        now = time.time() if now is None else now
        cooldown = PROVIDER_QUARANTINE_SECONDS if cooldown is None else cooldown
        with self._lock:
            self.get_provider(provider).quarantine(now, "manual", "admin", cooldown)
        return {
            "record_type": "admin_action",
            "timestamp": self._admin_iso(now),
            "op": "quarantine_provider",
            "actor": actor,
            "provider": provider,
            "cooldown_seconds": cooldown,
        }

    def _ingest_admin_action(self, record: dict) -> None:
        """Replay an admin_action JSONL record into this replica store (CC-9).

        Replays as a hard ``force`` clear: the half-open probe is a live-traffic
        concern; the replica only needs to reflect that the operator cleared the
        state. A subsequent failed probe arrives as a normal rate-limit record and
        re-quarantines the replica through the usual path.
        """
        op = record.get("op")
        actor = record.get("actor", "")
        # Use the record's own timestamp so the replica's cleared_at matches the
        # original event (matters for cold-start replay of older JSONL, not just
        # live tailing); fall back to wall-clock if it can't be parsed.
        try:
            from datetime import datetime

            now = datetime.fromisoformat(record.get("timestamp", "")).timestamp()
        except (ValueError, TypeError):
            now = time.time()
        if op == "clear_provider_quarantine":
            self.clear_provider_quarantine(
                record.get("provider", ""), mode="force", actor=actor, now=now
            )
        elif op == "clear_client_provider_quarantine":
            self.clear_client_provider_quarantine(
                record.get("client_id", ""),
                record.get("provider", ""),
                mode="force",
                actor=actor,
                now=now,
            )
        elif op == "clear_client_backoff":
            self.clear_client_backoff(record.get("client_id", ""), actor=actor, now=now)
        elif op == "reset_model_circuit":
            self.reset_model_circuit(record.get("model", ""), actor=actor, now=now)
        elif op == "quarantine_provider":
            self.quarantine_provider(
                record.get("provider", ""),
                actor=actor,
                now=now,
                cooldown=record.get("cooldown_seconds"),
            )

    def record_gemini_outcome(
        self,
        client_id: str,
        provider: str,
        timestamp: float,
        output_shape: str,
        reasoning_mode: str,
    ) -> None:
        client_id = normalize_client_id(client_id)
        with self._lock:
            self.get_client(client_id).record_gemini_outcome(timestamp, output_shape)
            self.get_provider(provider).record_gemini_outcome(
                timestamp,
                output_shape,
                reasoning_mode,
            )

    # -- MCP server tracking -----------------------------------------------

    def get_mcp_server(self, name: str) -> McpServerState:
        with self._lock:
            if name not in self._mcp_servers:
                self._mcp_servers[name] = McpServerState(name=name)
            return self._mcp_servers[name]

    def all_mcp_servers(self) -> dict[str, McpServerState]:
        with self._lock:
            return dict(self._mcp_servers)

    def set_mcp_server(self, name: str, state: McpServerState) -> None:
        with self._lock:
            self._mcp_servers[name] = state

    # -- MCP tool tracking -------------------------------------------------

    def get_mcp_tool(self, tool_name: str, server_name: str = "") -> McpToolState:
        key = f"{server_name}/{tool_name}" if server_name else tool_name
        with self._lock:
            if key not in self._mcp_tools:
                self._mcp_tools[key] = McpToolState(
                    tool_name=tool_name,
                    server_name=server_name,
                )
            return self._mcp_tools[key]

    def all_mcp_tools(self) -> dict[str, McpToolState]:
        with self._lock:
            return dict(self._mcp_tools)

    def record_call_type(self, is_mcp: bool) -> None:
        with self._lock:
            if is_mcp:
                self._mcp_call_count += 1
            else:
                self._llm_call_count += 1

    def traffic_split(self) -> tuple[int, int]:
        with self._lock:
            return self._llm_call_count, self._mcp_call_count

    def should_allow_request(self, model_name: str) -> bool:
        """Thread-safe circuit breaker check for a model.

        Protects _half_open_admitted flag from race conditions where
        multiple concurrent requests could all enter HALF_OPEN probe state.
        """
        with self._lock:
            if model_name not in self._models:
                self._models[model_name] = ModelState(model_name=model_name)
            return self._models[model_name].should_allow_request()

    # -- JSONL log ingestion (for TUI cross-process visibility) ---------------

    def ingest_jsonl_record(self, record: dict) -> None:
        """Populate state from a JSONL log entry written by enterprise logger.

        This bridges the process gap: the proxy subprocess writes JSONL, and
        the TUI process reads it to populate the same StateStore interface.
        """
        # CC-9: route by record_type BEFORE the model check below (admin_action
        # records carry no model and would otherwise be dropped). Absent
        # record_type is treated as "request" for back-compat with older logs.
        if record.get("record_type") == "admin_action":
            self._ingest_admin_action(record)
            return

        model = record.get("model")
        if not model:
            return

        success = record.get("success", True)
        duration_ms = record.get("duration_ms", 0.0) or 0.0
        ts_str = record.get("timestamp", "")

        # Parse ISO timestamp to epoch
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(ts_str)
            now = dt.timestamp()
        except (ValueError, TypeError):
            now = time.time()

        with self._lock:
            model_state = self.get_model(model)
            if success:
                model_state.record_success(now, duration_ms)
            else:
                model_state.record_failure(now)

            client_id = normalize_client_id(record.get("airlock_client"))
            client_state = self.get_client(client_id)
            client_state.record_request(now)
            if success:
                client_state.record_success(now, duration_ms)
            else:
                client_state.record_error(now, record.get("error_type") or "Error")

            provider = record.get("airlock_provider")
            if not provider:
                from airlock.fast.router import infer_provider

                provider = infer_provider(model)
            if provider:
                self.record_provider_request(client_id, provider, now)
                if success:
                    self.record_provider_success(client_id, provider, now)
                else:
                    self.record_provider_failure(client_id, provider, now)

                protection = record.get("airlock_provider_protection") or {}
                if protection.get("action") in {
                    "client_quarantine",
                    "provider_quarantine",
                }:
                    reason = (
                        protection.get("reason")
                        or record.get("error")
                        or "rate_limited"
                    )
                    error_type = record.get("error_type") or "RateLimitError"
                    self.record_provider_rate_limit(
                        client_id, provider, now, reason, error_type
                    )

                gemini_response = record.get("airlock_gemini_response") or {}
                gemini_request = record.get("airlock_gemini") or {}
                if provider == "gemini" and gemini_response:
                    self.record_gemini_outcome(
                        client_id,
                        provider,
                        now,
                        str(gemini_response.get("output_shape") or "unknown"),
                        str(gemini_request.get("mode") or "balanced"),
                    )

            # Track call type
            call_type = record.get("call_type", "")
            is_mcp = call_type == "call_mcp_tool" or "mcp_tool_name" in record
            self.record_call_type(is_mcp)

            if is_mcp:
                tool_name = record.get("mcp_tool_name", "unknown")
                server_name = record.get("mcp_server_name", "")
                tool_state = self.get_mcp_tool(tool_name, server_name)
                if success:
                    tool_state.record_success(now, duration_ms)
                else:
                    tool_state.record_failure(now)
