"""Health check computation, independent of HTTP routing.

Produces reports in the [IETF health check response
format](https://datatracker.ietf.org/doc/html/draft-inadarei-api-health-check-06):
media type ``application/health+json``, a ``status`` of ``pass``/``warn``/``fail``,
2xx for pass and warn, 5xx for fail.

**The invariant this module exists to hold: no check may make a live model
call.** Every value is read from configuration or cached ``StateStore`` state.
``GET /health`` historically fired a completion to every configured model, which
made the single most-probed path in the ecosystem the most expensive one — see
``dev/notes/design-health-endpoints.md``. Keeping the computations here, away
from any provider client, makes that structural rather than remembered.

Three probes answer three different questions:

``liveness``
    Is the process responsive? Failure means *restart the container*, so this
    must not be able to fail for any reason short of a wedged process. A
    liveness check that consults providers restarts a healthy proxy because
    someone else's API is down.

``readiness``
    Can it serve traffic now? Failure means *stop routing to this instance*.

``aggregate``
    The human- and uptime-checker-facing summary, served at ``/health`` and
    ``/healthz``.

Disclosure: probe payloads carry status and aggregate counts only — never model
names or provider identities. Endpoints are unauthenticated (owner decision
2026-08-04), and per-entity detail stays in ``/health/circuits`` and
``/health/latest`` so authentication can be added there later without reshaping
these payloads.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

HEALTH_JSON_MEDIA_TYPE = "application/health+json"

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

#: Worst-first, for combining component statuses.
_SEVERITY = {STATUS_PASS: 0, STATUS_WARN: 1, STATUS_FAIL: 2}

SERVICE_ID = "airlock"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _airlock_version() -> str:
    try:
        from airlock import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001 - version reporting must never break a probe
        return "unknown"


@dataclass
class Check:
    """One component check, rendered into the ``checks`` map."""

    name: str
    status: str
    observed_value: Any = None
    observed_unit: str | None = None
    output: str | None = None

    def render(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"status": self.status, "time": _now_iso()}
        if self.observed_value is not None:
            entry["observedValue"] = self.observed_value
        if self.observed_unit:
            entry["observedUnit"] = self.observed_unit
        # The spec reserves `output` for fail/warn; omit it on pass.
        if self.output and self.status != STATUS_PASS:
            entry["output"] = self.output
        return entry


@dataclass
class HealthReport:
    """A complete health response prior to serialization."""

    status: str
    checks: list[Check] = field(default_factory=list)
    description: str = "Airlock LLM proxy"
    notes: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        version = _airlock_version()
        body: dict[str, Any] = {
            "status": self.status,
            "serviceId": SERVICE_ID,
            "description": self.description,
            "version": version,
            "releaseId": version,
        }
        if self.checks:
            body["checks"] = {c.name: [c.render()] for c in self.checks}
        if self.notes:
            body["notes"] = list(self.notes)
        body["links"] = {"circuits": "/health/circuits"}
        return body

    @property
    def http_status(self) -> int:
        # 503 rather than a 4xx: failure here is server-side unavailability, and
        # load balancers treat 503 as "try elsewhere".
        return 503 if self.status == STATUS_FAIL else 200


def _worst(statuses: list[str]) -> str:
    if not statuses:
        return STATUS_PASS
    return max(statuses, key=lambda s: _SEVERITY.get(s, 0))


def liveness_report() -> HealthReport:
    """Is the process responsive?

    Deliberately trivial. Reaching this code *is* the check — if the event loop
    were wedged the request would not have been served. Nothing here may consult
    configuration, providers, or the database, because a liveness failure
    restarts the container.
    """
    return HealthReport(
        status=STATUS_PASS,
        checks=[Check("proxy:responsive", STATUS_PASS)],
    )


def _model_availability(state_store: Any) -> tuple[int, int]:
    """Return ``(total_models, available_models)`` from cached circuit state."""
    from airlock.fast.state import CircuitState

    models = state_store.all_models()
    total = len(models)
    available = sum(
        1 for m in models.values() if getattr(m, "circuit", None) == CircuitState.CLOSED
    )
    return total, available


def readiness_report(
    state_store: Any | None = None,
    *,
    configured_models: int | None = None,
    db_configured: bool = False,
    db_reachable: bool | None = None,
) -> HealthReport:
    """Can this instance serve traffic right now?

    Reads cached state only. The status ladder:

    ==========================================  ========  ====
    Condition                                   Status    HTTP
    ==========================================  ========  ====
    Router configured, at least one model open  ``pass``  200
    Some circuits open, at least one closed     ``warn``  200
    No models configured, or all circuits open  ``fail``  503
    Database configured but unreachable         ``fail``  503
    No database configured                      ``pass``  200
    ==========================================  ========  ====

    Partial circuit-open is ``warn`` rather than ``fail`` on purpose. Pulling an
    instance out of the load balancer because one provider is rate-limited would
    withdraw capacity that can still serve every other model — turning a partial
    provider outage into a total one. Only total unavailability justifies
    removing traffic.
    """
    if state_store is None:
        from airlock.fast.state import store as default_store

        state_store = default_store

    checks: list[Check] = []
    notes: list[str] = []

    tracked_total, available = _model_availability(state_store)
    # `configured_models` is the router's view; the state store only knows about
    # models it has observed. Prefer the router count when supplied.
    total = configured_models if configured_models is not None else tracked_total

    if total <= 0:
        checks.append(
            Check(
                "router:configured",
                STATUS_FAIL,
                observed_value=0,
                observed_unit="models",
                output="no models configured",
            )
        )
    else:
        checks.append(
            Check(
                "router:configured",
                STATUS_PASS,
                observed_value=total,
                observed_unit="models",
            )
        )

    if total > 0:
        # A model the store has never seen has no recorded failure, so it counts
        # as available: absence of evidence is not a circuit-open.
        unobserved = max(0, total - tracked_total)
        effective_available = available + unobserved
        if effective_available <= 0:
            checks.append(
                Check(
                    "models:available",
                    STATUS_FAIL,
                    observed_value=0,
                    observed_unit="models",
                    output="all model circuits are open",
                )
            )
        elif effective_available < total:
            checks.append(
                Check(
                    "models:available",
                    STATUS_WARN,
                    observed_value=effective_available,
                    observed_unit="models",
                    output=f"{total - effective_available} of {total} model circuits are open",
                )
            )
        else:
            checks.append(
                Check(
                    "models:available",
                    STATUS_PASS,
                    observed_value=effective_available,
                    observed_unit="models",
                )
            )

    if db_configured:
        if db_reachable:
            checks.append(Check("database:reachable", STATUS_PASS))
        else:
            checks.append(
                Check(
                    "database:reachable",
                    STATUS_FAIL,
                    output="configured database is not reachable",
                )
            )
    else:
        # Airlock runs without a database. Reporting that as a failure — as the
        # inherited readiness endpoint's "db: Not connected" implied — would make
        # every DB-less deployment look permanently broken.
        checks.append(Check("database:reachable", STATUS_PASS))
        notes.append("no database configured; Airlock does not require one")

    return HealthReport(
        status=_worst([c.status for c in checks]), checks=checks, notes=notes
    )


def aggregate_report(
    state_store: Any | None = None,
    *,
    configured_models: int | None = None,
    db_configured: bool = False,
    db_reachable: bool | None = None,
) -> HealthReport:
    """Human- and uptime-checker-facing summary served at ``/health``.

    This is the payload that replaced LiteLLM's per-model live sweep. It makes
    no model calls; per-model detail lives in ``/health/circuits`` and, when
    ``background_health_checks`` is enabled, ``/health/latest``.
    """
    live = liveness_report()
    ready = readiness_report(
        state_store,
        configured_models=configured_models,
        db_configured=db_configured,
        db_reachable=db_reachable,
    )
    checks = live.checks + ready.checks
    return HealthReport(
        status=_worst([c.status for c in checks]),
        checks=checks,
        notes=ready.notes,
    )


def render(report: HealthReport) -> tuple[dict[str, Any], int, str]:
    """Return ``(payload, http_status, media_type)`` for a report."""
    return report.payload(), report.http_status, HEALTH_JSON_MEDIA_TYPE


def probe_timestamp() -> float:
    """Monotonic-ish wall clock for callers that record probe times."""
    return time.time()
