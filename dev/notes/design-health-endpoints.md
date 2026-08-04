# Health endpoint alignment design

**Status:** Proposed — 2026-08-04
**Scope:** 0.5.9 internal milestone. Contains a **breaking change** to `GET /health`.
**Driver:** The current endpoint surface has caused operational confusion. The
conventional path is the dangerous one, and the safe path is spelled unusually.

## Problem

Airlock inherits LiteLLM's health surface. Three things are misaligned with what
monitoring software expects.

**1. `GET /health` is the expensive one.** It fires a live completion to every
configured model when `background_health_checks` is off. `/health` and
`/healthz` are the first paths every monitoring default reaches for — uptime
checkers, Prometheus blackbox exporter, load-balancer target groups, Helm chart
templates. Point any standard tool at Airlock with stock configuration and it
bills the account and consumes provider rate limits on every probe interval.

LiteLLM knows this: its own handler docstring opens with
`🚨 USE /health/liveliness to health check the proxy 🚨`. The mitigation is a
warning, and warnings do not survive a new Grafana synthetic check or a
teammate's Kubernetes manifest.

Airlock currently enforces this as a *documentation* hard constraint. A
documented constraint cannot bind tooling that never reads the documentation.

**2. The safe path is spelled unusually.** `/health/liveliness` is not a name any
convention uses. `/livez`, `/readyz`, `/healthz`, `/health/live`, and
`/health/ready` all return 404.

**3. Responses are not machine-checkable.** `/health/liveliness` returns the bare
JSON string `"I'm alive!"` — no `status` field, no health media type. Tools that
assert on a response body field cannot.

## Current surface, measured

Probed against the running proxy 2026-08-04:

| Path | Status | Body | Cost |
|---|---|---|---|
| `GET /health` | 200 | per-model results | **live call per model** |
| `GET /health/liveliness` | 200 | `"I'm alive!"` | none |
| `GET /health/liveness` | 200 | `"I'm alive!"` | none |
| `GET /health/readiness` | 200 | `{"status":"healthy","db":"Not connected"}` | none |
| `GET /health/circuits` | 200 | Airlock circuit state | none |
| `GET /health/latest`, `/health/history` | 200 | cached background results | none |
| `/healthz` `/livez` `/readyz` `/health/live` `/health/ready` `/ping` | **404** | — | — |

`/health` is registered by LiteLLM at
`litellm/proxy/health_endpoints/_health_endpoints.py:901` with
`dependencies=[Depends(user_api_key_auth)]`. `/health/liveliness` and
`/health/liveness` are two decorators on one handler.

## Dependency audit

Every in-repo consumer of a health path:

| Consumer | Path used | Notes |
|---|---|---|
| `airlock/tui/screens/overview.py:389` | `/health/liveliness` | TUI proxy-status probe |
| `airlock/hooks/_common.py:48` | `/health/liveliness?client=` | Claude Code hook probe |
| `airlock/cli/status_cmd.py:27` | `/health/liveliness` | `airlock status` |
| `airlock/cli/dogfood_cmd.py:13` | `/health/liveliness` | `airlock dogfood` |
| `docker-compose.yml:15` | `/health/liveliness` | container healthcheck |
| `deploy/k8s/deployment.yaml:46,53` | `/health/liveliness` | **both** liveness and readiness probes |
| `airlock/docs.py:250` | `/health` | special-cases the path for API-doc enrichment |
| `airlock/health.py` | `/health/circuits` | Airlock's own route, install pattern to reuse |

Nothing in Airlock calls `GET /health`.

**The LiteLLM admin UI does not call `GET /health` either.** Its compiled bundles
reference `health/readiness`, `health/latest`, `health/test`, `health/services`,
and `health/license` — never bare `/health`. This was the open risk in the
earlier recommendation, and it is now closed: **redefining `/health` does not
break the LiteLLM dashboard.**

Note `deploy/k8s/deployment.yaml` currently points its *readiness* probe at a
*liveness* endpoint. Readiness and liveness answer different questions, and
conflating them means Kubernetes will route traffic to a pod that is up but not
able to serve.

## Canonical references

- Kubernetes uses [`/livez` and `/readyz`](https://www.kubernetes.io/docs/reference/using-api/health-checks/)
  ("z-pages"); `/healthz` is deprecated for the API server itself since v1.16 but
  remains the common general-purpose name.
- MicroProfile/Quarkus use `/health/live` and `/health/ready`.
- Spring Boot uses `/actuator/health/{liveness,readiness}`.
- The [IETF health-check draft](https://datatracker.ietf.org/doc/html/draft-inadarei-api-health-check-06)
  specifies media type `application/health+json`, `status` of `pass`/`fail`/`warn`,
  HTTP 2xx–3xx for pass, 4xx–5xx for fail.

The three probe kinds, and what each must mean here:

| Probe | Question | Consequence of failure | Cost budget |
|---|---|---|---|
| Liveness | Is the process responsive? | container restart | must be trivial |
| Readiness | Can it serve traffic now? | removed from load balancer | cached state only |
| Health | Aggregate for humans/uptime tools | alert | cached state only |

**No probe may ever make a live model call.** That is the invariant this design
exists to make structural.

---

# Tier 1 — Canonical aliases and `health+json`

Add the names monitoring software already looks for, and make responses
machine-checkable. Purely additive; no existing behavior changes.

### New routes

All unauthenticated, all cached-state only, `GET` and `HEAD`:

| Path | Semantics |
|---|---|
| `/livez` | liveness |
| `/readyz` | readiness |
| `/healthz` | aggregate |
| `/health/live` | liveness (MicroProfile spelling) |
| `/health/ready` | readiness (MicroProfile spelling) |

`HEAD` matters: several uptime checkers default to it, and FastAPI does not
synthesize it from `GET`.

### Response format

Media type `application/health+json`.

```json
{
  "status": "pass",
  "serviceId": "airlock",
  "version": "0.5.8",
  "releaseId": "0.5.8",
  "description": "Airlock LLM proxy",
  "checks": {
    "proxy:responsive": [{"status": "pass", "time": "2026-08-04T15:00:00Z"}],
    "router:configured": [{"status": "pass", "observedValue": 81, "observedUnit": "models"}],
    "models:available": [{"status": "pass", "observedValue": 79, "observedUnit": "models"}]
  },
  "links": {"circuits": "/health/circuits"}
}
```

HTTP status: `pass`/`warn` → 200, `fail` → 503. 503 rather than a 4xx because
failure here is server-side unavailability, and load balancers treat 503 as
"try elsewhere".

Liveness returns only `proxy:responsive` — it must not be able to fail for any
reason short of a wedged process, or Kubernetes will restart a healthy container
because a provider is down.

### Legacy paths

`/health/liveliness`, `/health/liveness`, and `/health/readiness` keep their
**exact current bodies**, byte for byte. Six in-repo consumers and every
user-authored manifest depend on them. They are documented as legacy with the
canonical name given alongside; they are not deprecated with a removal date in
0.5.9.

---

# Tier 2 — `GET /health` becomes cheap

**This is a breaking change, made deliberately and without a compatibility
escape hatch**, per owner decision 2026-08-04: the old behavior is removed, not
gated behind a flag. A flag would preserve exactly the footgun the change
exists to eliminate, and any deployment that flipped it would silently return to
billing itself on every probe.

### Change

`GET /health` returns the same aggregate payload as `/healthz`. It never makes a
model call.

### Implementation

FastAPI resolves routes in registration order, so registering a second `/health`
after LiteLLM's would never match. The route must be **removed and replaced**:

```python
def _replace_health_route(app) -> bool:
    removed = [
        r for r in app.router.routes
        if getattr(r, "path", None) == "/health" and "GET" in getattr(r, "methods", set())
    ]
    for route in removed:
        app.router.routes.remove(route)
    # register Airlock's /health here
    return bool(removed)
```

This runs in the existing `airlock/health.py` install path, which already
registers `/health/circuits` on the proxy app and is therefore a proven
insertion point. If the route is not found — because a LiteLLM upgrade renamed
or restructured it — the installer must **log an error and fail loudly** rather
than silently leaving the expensive endpoint in place. A silent failure here
reintroduces the hazard invisibly, which is worse than a noisy startup.

### What replaces deep checking

Nothing request-triggered, by design. Per-model results remain available via
LiteLLM's **background** health-check loop
(`general_settings.background_health_checks: true`), read from cache through the
existing `/health/latest` and `/health/history` endpoints. Those make no live
calls at request time.

The result: **request-triggered live model calls are eliminated from the HTTP
surface entirely.** Deep results come from a loop whose rate the operator
controls, not from whoever last pointed a probe at the proxy. This is a better
answer than relocating the deep sweep to a new path, because a new path is a new
footgun for the next person who finds it.

### Auth

LiteLLM's `/health` required an API key. The replacement is unauthenticated but
returns **status only** — no model names, no provider identities, no counts that
would disclose deployment shape to an unauthenticated caller. Detailed
per-check output requires auth, consistent with the IETF `links` pattern.
`/health/liveliness` is already unauthenticated today, so this exposes nothing
new in kind.

---

# Tier 3 — Readiness that means something

### Current behavior

`/health/readiness` returns `{"status":"healthy","db":"Not connected"}`. It
reports healthy while simultaneously reporting a component as not connected,
which reads as broken to a human and is ambiguous to a naive JSON assertion. It
also does not reflect whether Airlock can actually serve a request.

### Definition

Readiness is `pass` when all of:

- configuration is loaded and the router is initialized;
- at least one model is configured;
- at least one model is not circuit-open (from cached `StateStore` state — never a live call);
- the database, **if configured**, is reachable. When no DB is configured this
  is reported as `pass` with a note, not as a failure. Airlock runs without one.

Readiness is `warn` (still 200) when some models are circuit-open but at least
one remains available. It is `fail` (503) only when **no** model can serve.

The warn/fail split is deliberate. Removing a pod from the load balancer because
one provider is rate-limited would take down capacity that could still serve
every other model — converting a partial provider outage into a full outage.
Only total unavailability justifies pulling traffic.

### Probe correction

`deploy/k8s/deployment.yaml` must point its readiness probe at `/readyz`, not at
the liveness endpoint. Liveness stays on `/livez`.

---

## Risks

| Risk | Mitigation |
|---|---|
| A LiteLLM upgrade restructures `/health`, silently breaking removal | Installer fails loudly; a test asserts the route was actually replaced |
| An operator depended on `GET /health` doing a deep sweep | Documented breaking change in CHANGELOG; background checks + `/health/latest` cover the need |
| Readiness flapping pulls pods during transient provider errors | Circuit-open on *some* models is `warn`, not `fail` |
| Unauthenticated `/health` discloses deployment shape | Status-only body; details require auth |
| Route removal runs before LiteLLM registers `/health` | Install at the same point as `/health/circuits`, which is already correctly ordered; test asserts post-conditions |

## Testing

- Route-replacement test: after install, exactly one `GET /health` route exists
  and it is Airlock's; a synthetic app missing the LiteLLM route makes the
  installer raise.
- Contract tests per path: status code, `content-type: application/health+json`,
  `status` field, and `HEAD` parity with `GET`.
- **A test asserting no health path can trigger a model call** — the point of the
  whole change. Assert with a router mock that fails the test if called.
- Legacy-body tests: `/health/liveliness` returns exactly `"I'm alive!"`.
- Readiness state matrix: no models, all circuits open, some open, DB
  configured/absent.
- Existing consumers (`airlock status`, hooks, TUI, dogfood) keep passing
  unchanged.
