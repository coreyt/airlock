"""
Airlock Fast — Shared in-memory state store.

Tracks per-client and per-model metrics in real-time using sliding
windows.  Thread-safe for concurrent async request handlers.

The state store is the single source of truth that the priority scorer,
circuit breaker, and threat detector all read from and write to.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
WINDOW_SECONDS = 300        # default sliding-window duration (5 min)
MAX_SAMPLES = 1000          # cap per deque to bound memory


class CircuitState(Enum):
    """Model health states (classic circuit-breaker pattern)."""
    CLOSED = "closed"           # healthy — requests flow normally
    OPEN = "open"               # broken — requests should failover
    HALF_OPEN = "half_open"     # probing — one test request allowed


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
    threat_score: float = 0.0
    backoff_until: float = 0.0      # unix ts; 0 → no backoff

    # -- writers --------------------------------------------------------

    def record_request(self, timestamp: float) -> None:
        self.request_times.append(timestamp)

    def record_success(self, timestamp: float, latency_ms: float) -> None:
        self.successes.append(timestamp)
        self.latencies_ms.append((timestamp, latency_ms))

    def record_error(self, timestamp: float, error_type: str) -> None:
        self.errors.append((timestamp, error_type))

    # -- readers --------------------------------------------------------

    def recent_request_count(self, window_seconds: float = WINDOW_SECONDS) -> int:
        cutoff = time.time() - window_seconds
        return sum(1 for t in self.request_times if t > cutoff)

    def recent_error_rate(self, window_seconds: float = WINDOW_SECONDS) -> float:
        cutoff = time.time() - window_seconds
        errors = sum(1 for t, _ in self.errors if t > cutoff)
        successes = sum(1 for t in self.successes if t > cutoff)
        total = errors + successes
        return errors / total if total > 0 else 0.0

    def recent_avg_latency(self, window_seconds: float = WINDOW_SECONDS) -> float | None:
        cutoff = time.time() - window_seconds
        recent = [lat for t, lat in self.latencies_ms if t > cutoff]
        return sum(recent) / len(recent) if recent else None

    def is_in_backoff(self) -> bool:
        return time.time() < self.backoff_until


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

    # Thresholds (class-level defaults)
    FAILURE_THRESHOLD: int = 5          # consecutive failures → open
    RECOVERY_TIMEOUT: float = 30.0      # seconds before half-open probe
    SUCCESS_THRESHOLD: int = 3          # half-open successes → close

    def record_success(self, timestamp: float, latency_ms: float) -> None:
        self.success_times.append(timestamp)
        self.latencies_ms.append((timestamp, latency_ms))
        self.consecutive_failures = 0

        if self.circuit == CircuitState.HALF_OPEN:
            recent_ok = sum(
                1 for t in self.success_times if t > self.last_state_change
            )
            if recent_ok >= self.SUCCESS_THRESHOLD:
                self.circuit = CircuitState.CLOSED
                self.last_state_change = timestamp

    def record_failure(self, timestamp: float) -> None:
        self.failure_times.append(timestamp)
        self.consecutive_failures += 1

        if self.circuit == CircuitState.HALF_OPEN:
            self.circuit = CircuitState.OPEN
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
                return True          # allow a single probe
            return False
        # HALF_OPEN → allow (probe in progress)
        return True

    def recent_avg_latency(self, window_seconds: float = WINDOW_SECONDS) -> float | None:
        cutoff = time.time() - window_seconds
        recent = [lat for t, lat in self.latencies_ms if t > cutoff]
        return sum(recent) / len(recent) if recent else None


# ---------------------------------------------------------------------------
# Central state store  (module-level singleton)
# ---------------------------------------------------------------------------
class StateStore:
    """Thread-safe registry of all client and model states."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, ClientState] = {}
        self._models: dict[str, ModelState] = {}

    def get_client(self, client_id: str) -> ClientState:
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

    def all_models(self) -> dict[str, ModelState]:
        with self._lock:
            return dict(self._models)


store = StateStore()
