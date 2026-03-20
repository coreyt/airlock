"""
Airlock Fast — Shared in-memory state store.

Tracks per-client and per-model metrics in real-time using sliding
windows.  Thread-safe for concurrent async request handlers.

The state store is the single source of truth that the priority scorer,
circuit breaker, threat detector, and intelligent router all read from
and write to.
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
NO_CLIENT_ID = "no_client"
CLIENT_PROVIDER_COOLDOWN_SECONDS = 300.0
PROVIDER_QUARANTINE_SECONDS = 300.0
PROVIDER_ESCALATION_WINDOW_SECONDS = 300.0
PROVIDER_ESCALATION_CLIENT_THRESHOLD = 2


class CircuitState(Enum):
    """Model health states (classic circuit-breaker pattern)."""
    CLOSED = "closed"           # healthy — requests flow normally
    OPEN = "open"               # broken — requests should failover
    HALF_OPEN = "half_open"     # probing — one test request allowed


def normalize_client_id(client_id: str | None) -> str:
    """Return a stable client identifier, collapsing missing values to no_client."""
    text = (client_id or "").strip()
    return text or NO_CLIENT_ID


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
    backoff_until: float = 0.0      # unix ts; 0 → no backoff

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

    def recent_avg_latency(self, window_seconds: float = WINDOW_SECONDS) -> float | None:
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


# ---------------------------------------------------------------------------
# Provider spend tracking
# ---------------------------------------------------------------------------
@dataclass
class ProviderSpend:
    """Tracks cumulative spend for a provider in a rolling window."""

    provider: str
    spend_records: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))

    def record_spend(self, timestamp: float, cost_usd: float) -> None:
        self.spend_records.append((timestamp, cost_usd))

    def recent_spend(self, window_seconds: float = 86400.0) -> float:
        cutoff = time.time() - window_seconds
        return sum(cost for t, cost in self.spend_records if t > cutoff)


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
    last_reason: str = ""
    last_error_type: str = ""
    last_action: str = ""

    def record_request(self, timestamp: float) -> None:
        self.request_times.append(timestamp)

    def record_success(self, timestamp: float) -> None:
        self.success_times.append(timestamp)

    def record_failure(self, timestamp: float) -> None:
        self.failure_times.append(timestamp)

    def record_rate_limit(
        self,
        timestamp: float,
        reason: str,
        error_type: str,
        cooldown_seconds: float = CLIENT_PROVIDER_COOLDOWN_SECONDS,
    ) -> None:
        self.rate_limit_times.append(timestamp)
        self.failure_times.append(timestamp)
        self.quarantine_until = max(self.quarantine_until, timestamp + cooldown_seconds)
        self.last_reason = reason
        self.last_error_type = error_type
        self.last_action = "client_quarantine"

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
        cutoff = time.time() - window_seconds
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
    last_reason: str = ""
    last_error_type: str = ""
    last_action: str = ""

    def record_request(self, timestamp: float) -> None:
        self.request_times.append(timestamp)

    def record_success(self, timestamp: float) -> None:
        self.success_times.append(timestamp)

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
        self, window_seconds: float = PROVIDER_ESCALATION_WINDOW_SECONDS,
    ) -> set[str]:
        cutoff = time.time() - window_seconds
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
# Per-MCP-tool state
# ---------------------------------------------------------------------------
@dataclass
class McpToolState:
    """Tracks a single MCP tool's health (modeled after ModelState)."""

    tool_name: str
    server_name: str = ""
    success_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    failure_times: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    latencies_ms: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))

    def record_success(self, timestamp: float, latency_ms: float) -> None:
        self.success_times.append(timestamp)
        self.latencies_ms.append((timestamp, latency_ms))

    def record_failure(self, timestamp: float) -> None:
        self.failure_times.append(timestamp)

    def recent_avg_latency(self, window_seconds: float = WINDOW_SECONDS) -> float | None:
        cutoff = time.time() - window_seconds
        recent = [lat for t, lat in self.latencies_ms if t > cutoff]
        return sum(recent) / len(recent) if recent else None

    def recent_error_rate(self, window_seconds: float = WINDOW_SECONDS) -> float:
        cutoff = time.time() - window_seconds
        errors = sum(1 for t in self.failure_times if t > cutoff)
        successes = sum(1 for t in self.success_times if t > cutoff)
        total = errors + successes
        return errors / total if total > 0 else 0.0

    def recent_call_count(self, window_seconds: float = WINDOW_SECONDS) -> int:
        cutoff = time.time() - window_seconds
        return (
            sum(1 for t in self.success_times if t > cutoff)
            + sum(1 for t in self.failure_times if t > cutoff)
        )


# ---------------------------------------------------------------------------
# Per-MCP-server state
# ---------------------------------------------------------------------------
class McpServerHealth(Enum):
    """MCP server health states."""
    UNKNOWN = "unknown"         # never probed
    HEALTHY = "healthy"         # last probe succeeded
    UNHEALTHY = "unhealthy"     # last probe failed
    STARTING = "starting"       # managed server launching
    STOPPED = "stopped"         # managed server not running


@dataclass
class McpServerState:
    """Tracks an MCP server's health and lifecycle."""

    name: str
    transport: str = ""                   # "sse", "http", "stdio"
    url: str = ""
    is_managed: bool = False
    health: McpServerHealth = McpServerHealth.UNKNOWN
    last_health_check: float = 0.0
    last_health_latency_ms: float = 0.0
    consecutive_failures: int = 0
    started_at: float = 0.0               # unix ts; 0 = not started by Airlock
    pid: int = 0                          # PID of managed process; 0 = none
    health_history: deque = field(
        default_factory=lambda: deque(maxlen=50),
    )

    def record_health_check(
        self, timestamp: float, healthy: bool, latency_ms: float,
    ) -> None:
        self.last_health_check = timestamp
        self.last_health_latency_ms = latency_ms
        self.health_history.append((timestamp, healthy))
        if healthy:
            self.health = McpServerHealth.HEALTHY
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
            if self.health != McpServerHealth.STARTING:
                self.health = McpServerHealth.UNHEALTHY

    def uptime_seconds(self) -> float:
        if self.started_at > 0:
            return time.time() - self.started_at
        return 0.0

    def recent_success_rate(self) -> float:
        """Fraction of recent health checks that succeeded."""
        if not self.health_history:
            return 0.0
        ok = sum(1 for _, healthy in self.health_history if healthy)
        return ok / len(self.health_history)


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
    FAILURE_THRESHOLD: int = 5          # consecutive failures → open
    RECOVERY_TIMEOUT: float = 30.0      # seconds before half-open probe
    SUCCESS_THRESHOLD: int = 3          # half-open successes → close

    def record_success(self, timestamp: float, latency_ms: float) -> None:
        self.success_times.append(timestamp)
        self.latencies_ms.append((timestamp, latency_ms))
        self.consecutive_failures = 0

        if self.circuit == CircuitState.HALF_OPEN:
            self._half_open_admitted = False
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
                return True          # allow a single probe
            return False
        # HALF_OPEN → only allow if no probe is already in flight
        if not self._half_open_admitted:
            self._half_open_admitted = True
            return True
        return False

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
        self._sessions: dict[str, SessionRecord] = {}
        self._provider_spend: dict[str, ProviderSpend] = {}
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
                self._provider_spend[provider] = ProviderSpend(provider=provider)
            return self._provider_spend[provider]

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

    def record_provider_request(self, client_id: str, provider: str, timestamp: float) -> None:
        self.get_provider(provider).record_request(timestamp)
        self.get_client_provider(client_id, provider).record_request(timestamp)

    def record_provider_success(self, client_id: str, provider: str, timestamp: float) -> None:
        self.get_provider(provider).record_success(timestamp)
        self.get_client_provider(client_id, provider).record_success(timestamp)

    def record_provider_failure(self, client_id: str, provider: str, timestamp: float) -> None:
        self.get_provider(provider).record_failure(timestamp)
        self.get_client_provider(client_id, provider).record_failure(timestamp)

    def record_provider_rate_limit(
        self,
        client_id: str,
        provider: str,
        timestamp: float,
        reason: str,
        error_type: str,
    ) -> dict[str, float | str | bool]:
        client_id = normalize_client_id(client_id)
        client_provider = self.get_client_provider(client_id, provider)
        provider_state = self.get_provider(provider)

        client_provider.record_rate_limit(timestamp, reason, error_type)
        provider_state.record_rate_limit(timestamp, client_id, reason, error_type)

        provider_quarantined = False
        impacted_clients = provider_state.impacted_clients()
        if len(impacted_clients) >= PROVIDER_ESCALATION_CLIENT_THRESHOLD:
            provider_state.quarantine(timestamp, reason, error_type)
            provider_quarantined = True

        return {
            "client_quarantined": True,
            "provider_quarantined": provider_quarantined,
            "client_cooldown_seconds": client_provider.cooldown_remaining(timestamp),
            "provider_cooldown_seconds": provider_state.cooldown_remaining(timestamp),
            "impacted_clients": len(impacted_clients),
        }

    def record_gemini_outcome(
        self,
        client_id: str,
        provider: str,
        timestamp: float,
        output_shape: str,
        reasoning_mode: str,
    ) -> None:
        client_id = normalize_client_id(client_id)
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
                    tool_name=tool_name, server_name=server_name,
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


    # -- JSONL log ingestion (for TUI cross-process visibility) ---------------

    def ingest_jsonl_record(self, record: dict) -> None:
        """Populate state from a JSONL log entry written by enterprise logger.

        This bridges the process gap: the proxy subprocess writes JSONL, and
        the TUI process reads it to populate the same StateStore interface.
        """
        model = record.get("model")
        if not model:
            return

        success = record.get("success", True)
        duration_ms = record.get("duration_ms", 0.0) or 0.0
        ts_str = record.get("timestamp", "")

        # Parse ISO timestamp to epoch
        try:
            from datetime import datetime, timezone

            dt = datetime.fromisoformat(ts_str)
            now = dt.timestamp()
        except (ValueError, TypeError):
            now = time.time()

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
            if protection.get("action") in {"client_quarantine", "provider_quarantine"}:
                reason = protection.get("reason") or record.get("error") or "rate_limited"
                error_type = record.get("error_type") or "RateLimitError"
                self.record_provider_rate_limit(client_id, provider, now, reason, error_type)

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


store = StateStore()


# ---------------------------------------------------------------------------
# JSONL log tailer for TUI cross-process state sync
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
