"""Tests for the canonical health probe surface.

See ``dev/notes/design-health-endpoints.md``. The load-bearing test here is
``TestNoModelCalls`` — the entire change exists to make "a health probe never
calls a model" structural rather than remembered.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from airlock.fast.state import CircuitState
from airlock.health import (
    HealthRouteInstallError,
    install_health_surface,
    install_probe_endpoints,
    replace_health_endpoint,
)
from airlock.health_checks import (
    HEALTH_JSON_MEDIA_TYPE,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_WARN,
    aggregate_report,
    liveness_report,
    readiness_report,
)

CANONICAL_PATHS = [
    "/livez",
    "/readyz",
    "/healthz",
    "/health/live",
    "/health/ready",
]


class FakeModelState:
    def __init__(self, circuit=CircuitState.CLOSED):
        self.circuit = circuit
        self.consecutive_failures = 0
        self.last_state_change = 0.0


class FakeStore:
    """Cached-state stand-in — deliberately has no way to call a model."""

    def __init__(self, models=None):
        self._models = models or {}

    def all_models(self):
        return self._models


def _app_with_litellm_health() -> FastAPI:
    """A FastAPI app carrying a stand-in for LiteLLM's expensive /health."""
    app = FastAPI()

    @app.get("/health")
    async def litellm_health():  # pragma: no cover - must be replaced
        raise AssertionError("LiteLLM's /health should have been removed")

    return app


def _app_with_included_litellm_health() -> FastAPI:
    """Model LiteLLM's FastAPI 0.141 included-router route layout."""
    app = FastAPI()
    router = APIRouter()

    @router.get("/health")
    async def litellm_health():  # pragma: no cover - must be replaced
        raise AssertionError("LiteLLM's /health should have been removed")

    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
class TestCanonicalRoutes:
    @pytest.fixture
    def client(self):
        app = FastAPI()
        install_probe_endpoints(app)
        return TestClient(app)

    @pytest.mark.parametrize("path", CANONICAL_PATHS)
    def test_get_returns_health_json(self, client, path):
        response = client.get(path)
        assert response.status_code in (200, 503)
        assert response.headers["content-type"].startswith(HEALTH_JSON_MEDIA_TYPE)
        body = response.json()
        assert body["status"] in (STATUS_PASS, STATUS_WARN, STATUS_FAIL)
        assert body["serviceId"] == "airlock"

    @pytest.mark.parametrize("path", CANONICAL_PATHS)
    def test_head_matches_get_status(self, client, path):
        """Several uptime checkers default to HEAD; FastAPI will not synthesize it."""
        assert client.head(path).status_code == client.get(path).status_code

    def test_liveness_never_fails_on_provider_trouble(self, client):
        """A liveness failure restarts the container — providers must not cause it."""
        assert client.get("/livez").json()["status"] == STATUS_PASS

    def test_install_is_idempotent(self):
        app = FastAPI()
        install_probe_endpoints(app)
        after_first = self._route_methods(app, "/livez")
        install_probe_endpoints(app)
        assert self._route_methods(app, "/livez") == after_first
        # One GET and one HEAD, registered exactly once each.
        assert sorted(after_first) == ["GET", "HEAD"]

    @staticmethod
    def _route_methods(app: FastAPI, path: str) -> list[str]:
        methods: list[str] = []
        for route in app.router.routes:
            if getattr(route, "path", None) == path:
                methods.extend(getattr(route, "methods", None) or set())
        return sorted(m for m in methods if m in ("GET", "HEAD"))

    def test_probe_payloads_do_not_disclose_model_names(self, client):
        """Endpoints are unauthenticated; per-entity detail stays in /health/circuits."""
        store = FakeStore({"claude-secret-internal": FakeModelState()})
        report = aggregate_report(store, configured_models=1)
        assert "claude-secret-internal" not in json.dumps(report.payload())


class TestHealthReplacement:
    def test_replaces_litellm_route(self):
        app = _app_with_litellm_health()
        replace_health_endpoint(app)
        response = TestClient(app).get("/health")
        assert response.status_code in (200, 503)
        assert response.headers["content-type"].startswith(HEALTH_JSON_MEDIA_TYPE)

    def test_exactly_one_health_route_remains(self):
        app = _app_with_litellm_health()
        replace_health_endpoint(app)
        routes = [
            r
            for r in app.router.routes
            if getattr(r, "path", None) == "/health"
            and "GET" in (getattr(r, "methods", None) or set())
        ]
        assert len(routes) == 1

    async def test_replaces_route_in_an_included_router(self, monkeypatch):
        """FastAPI 0.141 stores LiteLLM routes below ``_IncludedRouter``."""
        app = _app_with_included_litellm_health()
        # The routing regression does not need a LiteLLM import (which can
        # refresh its remote model-price map during a unit test).
        from airlock.health_checks import HealthReport

        monkeypatch.setattr(
            "airlock.health._build_report",
            lambda _kind: HealthReport(status=STATUS_PASS),
        )
        replace_health_endpoint(app)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.get("/health")

        assert response.status_code in (200, 503)
        assert response.headers["content-type"].startswith(HEALTH_JSON_MEDIA_TYPE)

    def test_raises_when_route_absent(self):
        """A silent no-op would leave the expensive endpoint serving."""
        with pytest.raises(HealthRouteInstallError, match="no GET /health route"):
            replace_health_endpoint(FastAPI())

    def test_replacement_is_idempotent(self):
        app = _app_with_litellm_health()
        install_health_surface(app)
        install_health_surface(app)
        assert TestClient(app).get("/health").status_code in (200, 503)


class TestNoModelCalls:
    """The invariant the whole change exists to establish."""

    @pytest.mark.parametrize("path", CANONICAL_PATHS + ["/health"])
    def test_no_health_path_triggers_a_completion(self, path, monkeypatch):
        called: list[str] = []

        def _explode(*args, **kwargs):
            called.append(path)
            raise AssertionError(f"{path} attempted a model call")

        import litellm

        monkeypatch.setattr(litellm, "completion", _explode, raising=False)
        monkeypatch.setattr(litellm, "acompletion", _explode, raising=False)
        monkeypatch.setattr(litellm, "ahealth_check", _explode, raising=False)

        app = _app_with_litellm_health()
        install_health_surface(app)
        response = TestClient(app).get(path)

        assert response.status_code in (200, 503)
        assert called == [], f"{path} made a model call"


class TestReadinessStates:
    def test_all_models_available_passes(self):
        store = FakeStore({"a": FakeModelState(), "b": FakeModelState()})
        report = readiness_report(store, configured_models=2)
        assert report.status == STATUS_PASS
        assert report.http_status == 200

    def test_partial_circuit_open_warns_but_stays_in_rotation(self):
        """Withdrawing an instance over one bad provider turns a partial
        outage into a total one."""
        store = FakeStore(
            {"a": FakeModelState(), "b": FakeModelState(CircuitState.OPEN)}
        )
        report = readiness_report(store, configured_models=2)
        assert report.status == STATUS_WARN
        assert report.http_status == 200

    def test_all_circuits_open_fails(self):
        store = FakeStore(
            {
                "a": FakeModelState(CircuitState.OPEN),
                "b": FakeModelState(CircuitState.OPEN),
            }
        )
        report = readiness_report(store, configured_models=2)
        assert report.status == STATUS_FAIL
        assert report.http_status == 503

    def test_no_models_configured_fails(self):
        report = readiness_report(FakeStore(), configured_models=0)
        assert report.status == STATUS_FAIL

    def test_unobserved_models_count_as_available(self):
        """A model the store has never seen has no recorded failure."""
        store = FakeStore()
        report = readiness_report(store, configured_models=5)
        assert report.status == STATUS_PASS

    def test_absent_database_is_not_a_failure(self):
        """Airlock runs without a database; the inherited endpoint's
        'db: Not connected' made every DB-less deployment look broken."""
        report = readiness_report(
            FakeStore({"a": FakeModelState()}), configured_models=1
        )
        assert report.status == STATUS_PASS
        assert any("no database configured" in n for n in report.notes)

    def test_configured_but_unreachable_database_fails(self):
        report = readiness_report(
            FakeStore({"a": FakeModelState()}),
            configured_models=1,
            db_configured=True,
            db_reachable=False,
        )
        assert report.status == STATUS_FAIL

    def test_configured_and_reachable_database_passes(self):
        report = readiness_report(
            FakeStore({"a": FakeModelState()}),
            configured_models=1,
            db_configured=True,
            db_reachable=True,
        )
        assert report.status == STATUS_PASS


class TestReportFormat:
    def test_liveness_has_single_trivial_check(self):
        report = liveness_report()
        assert report.status == STATUS_PASS
        assert [c.name for c in report.checks] == ["proxy:responsive"]

    def test_output_omitted_on_pass(self):
        """The spec reserves `output` for fail/warn."""
        payload = liveness_report().payload()
        assert "output" not in payload["checks"]["proxy:responsive"][0]

    def test_output_present_on_failure(self):
        report = readiness_report(FakeStore(), configured_models=0)
        entry = report.payload()["checks"]["router:configured"][0]
        assert entry["output"] == "no models configured"

    def test_payload_carries_version_and_links(self):
        payload = liveness_report().payload()
        assert payload["links"]["circuits"] == "/health/circuits"
        assert "version" in payload and "releaseId" in payload

    def test_checks_render_as_spec_arrays(self):
        """IETF format: each check name maps to a list of check objects."""
        payload = aggregate_report(
            FakeStore({"a": FakeModelState()}), configured_models=1
        ).payload()
        for name, entries in payload["checks"].items():
            assert isinstance(entries, list), name
            assert "status" in entries[0] and "time" in entries[0]
