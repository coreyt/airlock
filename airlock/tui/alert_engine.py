"""
Airlock TUI — Alert engine.

Evaluates rules against the shared state store on a timer and produces
alerts for display in the TUI.  No threads, no async — the TUI app
calls ``evaluate()`` on a periodic timer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from airlock.fast.state import McpServerHealth, StateStore, store as _store

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    """A single alert produced by a rule evaluation."""

    rule_name: str
    severity: str  # "critical", "warning", "info"
    title: str  # one-line summary for display
    detail: str  # multi-line explanation
    entity_type: str  # "provider", "model", "client", "mcp"
    entity_id: str  # name of the entity
    timestamp: float
    acknowledged: bool = False


@dataclass
class AlertRule:
    """A named rule that inspects the state store and optionally fires an alert."""

    name: str
    condition: Callable[[StateStore], list[Alert]]
    cooldown_seconds: float
    severity: Literal["critical", "warning", "info"]
    _last_fired: float = field(default=0.0, repr=False)


# ---------------------------------------------------------------------------
# Built-in rule condition functions
# ---------------------------------------------------------------------------

def _check_circuit_open(store: StateStore) -> list[Alert]:
    """Fire a critical alert for any model whose circuit breaker is open."""
    alerts: list[Alert] = []
    try:
        for name, ms in store.all_models().items():
            if ms.circuit.value == "open":
                alerts.append(Alert(
                    rule_name="circuit_open",
                    severity="critical",
                    title=f"{name} circuit OPEN ({ms.consecutive_failures} failures)",
                    detail=(
                        f"Model {name} has tripped its circuit breaker after "
                        f"{ms.consecutive_failures} consecutive failures.  "
                        f"Requests will failover until the recovery timeout elapses."
                    ),
                    entity_type="model",
                    entity_id=name,
                    timestamp=time.time(),
                ))
    except Exception:
        _log.debug("alert rule error", exc_info=True)
    return alerts


def _check_provider_quarantine(store: StateStore) -> list[Alert]:
    """Fire a critical alert for any provider currently quarantined."""
    alerts: list[Alert] = []
    try:
        now = time.time()
        for name, ps in store.all_providers().items():
            if ps.quarantine_until and ps.quarantine_until > now:
                remaining = ps.quarantine_until - now
                alerts.append(Alert(
                    rule_name="provider_quarantine",
                    severity="critical",
                    title=f"{name} quarantined ({remaining:.0f}s remaining)",
                    detail=(
                        f"Provider {name} is quarantined until "
                        f"{remaining:.0f}s from now due to: {ps.last_reason or 'rate limiting'}."
                    ),
                    entity_type="provider",
                    entity_id=name,
                    timestamp=now,
                ))
    except Exception:
        _log.debug("alert rule error", exc_info=True)
    return alerts


def _check_client_threat(store: StateStore) -> list[Alert]:
    """Fire a warning alert for any client with a high threat score."""
    alerts: list[Alert] = []
    try:
        for cid, cs in store.all_clients().items():
            if cs.threat_score > 0.6:
                alerts.append(Alert(
                    rule_name="client_threat",
                    severity="warning",
                    title=f"{cid} threat score {cs.threat_score:.2f}",
                    detail=(
                        f"Client {cid} has a threat score of {cs.threat_score:.2f}, "
                        f"which exceeds the 0.60 threshold.  Review recent request "
                        f"patterns for anomalies."
                    ),
                    entity_type="client",
                    entity_id=cid,
                    timestamp=time.time(),
                ))
    except Exception:
        _log.debug("alert rule error", exc_info=True)
    return alerts


def _check_provider_error_rate(store: StateStore) -> list[Alert]:
    """Fire a warning alert for any provider with a recent error rate above 20%."""
    alerts: list[Alert] = []
    try:
        for name, ps in store.all_providers().items():
            rate = ps.recent_error_rate()
            if rate > 0.20:
                alerts.append(Alert(
                    rule_name="provider_error_rate",
                    severity="warning",
                    title=f"{name} error rate {rate:.0%}",
                    detail=(
                        f"Provider {name} has a recent error rate of {rate:.0%}, "
                        f"exceeding the 20% threshold."
                    ),
                    entity_type="provider",
                    entity_id=name,
                    timestamp=time.time(),
                ))
    except Exception:
        _log.debug("alert rule error", exc_info=True)
    return alerts


def _check_provider_budget(store: StateStore) -> list[Alert]:
    """Fire an info alert for any provider approaching its spend budget.

    NOTE: The state store tracks cumulative spend via ProviderSpend but does
    not yet store per-provider budget limits.  This rule is a no-op until
    budget configuration is wired into the state store.
    """
    # Budget limits are not yet tracked in StateStore — return [].
    return []


def _check_mcp_unhealthy(store: StateStore) -> list[Alert]:
    """Fire a warning alert for any MCP server in an unhealthy state."""
    alerts: list[Alert] = []
    try:
        for name, ss in store.all_mcp_servers().items():
            if ss.health == McpServerHealth.UNHEALTHY:
                alerts.append(Alert(
                    rule_name="mcp_unhealthy",
                    severity="warning",
                    title=f"{name} unhealthy",
                    detail=(
                        f"MCP server {name} is reporting as unhealthy after "
                        f"{ss.consecutive_failures} consecutive failed health "
                        f"checks."
                    ),
                    entity_type="mcp",
                    entity_id=name,
                    timestamp=time.time(),
                ))
    except Exception:
        _log.debug("alert rule error", exc_info=True)
    return alerts


def _check_guardrail_block_spike(store: StateStore) -> list[Alert]:
    """Fire a warning alert if guardrail blocks are spiking.

    NOTE: The state store does not currently track guardrail block counts.
    This rule is a no-op until that data source is wired in.
    """
    # Guardrail block tracking is not yet available — return [].
    return []


# ---------------------------------------------------------------------------
# Default rule set
# ---------------------------------------------------------------------------

_DEFAULT_RULES: list[AlertRule] = [
    AlertRule(
        name="circuit_open",
        condition=_check_circuit_open,
        cooldown_seconds=30.0,
        severity="critical",
    ),
    AlertRule(
        name="provider_quarantine",
        condition=_check_provider_quarantine,
        cooldown_seconds=30.0,
        severity="critical",
    ),
    AlertRule(
        name="client_threat",
        condition=_check_client_threat,
        cooldown_seconds=60.0,
        severity="warning",
    ),
    AlertRule(
        name="provider_error_rate",
        condition=_check_provider_error_rate,
        cooldown_seconds=60.0,
        severity="warning",
    ),
    AlertRule(
        name="provider_budget",
        condition=_check_provider_budget,
        cooldown_seconds=300.0,
        severity="info",
    ),
    AlertRule(
        name="mcp_unhealthy",
        condition=_check_mcp_unhealthy,
        cooldown_seconds=60.0,
        severity="warning",
    ),
    AlertRule(
        name="guardrail_block_spike",
        condition=_check_guardrail_block_spike,
        cooldown_seconds=60.0,
        severity="warning",
    ),
]


# ---------------------------------------------------------------------------
# Alert engine
# ---------------------------------------------------------------------------

class AlertEngine:
    """Evaluates alert rules against the state store and manages active alerts."""

    def __init__(self) -> None:
        self.rules: list[AlertRule] = list(_DEFAULT_RULES)
        self.active: list[Alert] = []

    def evaluate(self, store: StateStore) -> list[Alert]:
        """Run all rules against current state.  Returns newly fired alerts."""
        now = time.time()
        new_alerts: list[Alert] = []

        for rule in self.rules:
            # Respect cooldown
            if now - rule._last_fired < rule.cooldown_seconds:
                continue

            alerts = rule.condition(store)
            if not alerts:
                continue

            fired = False
            for alert in alerts:
                # Dedup by (rule_name, entity_id) — don't stack duplicates
                already_active = any(
                    a.rule_name == alert.rule_name and a.entity_id == alert.entity_id
                    for a in self.active
                )
                if already_active:
                    continue

                fired = True
                self.active.append(alert)
                new_alerts.append(alert)

            if fired:
                rule._last_fired = now

        return new_alerts

    def acknowledge(self, alert: Alert) -> None:
        """Mark an alert as acknowledged (still visible, but muted)."""
        alert.acknowledged = True

    def dismiss(self, alert: Alert) -> None:
        """Remove an alert from the active list entirely."""
        self.active.remove(alert)

    def active_count(self) -> int:
        """Return the number of unacknowledged active alerts."""
        return sum(1 for a in self.active if not a.acknowledged)
