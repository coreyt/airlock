# Slice 71 — Slice 70 Docker topology verification

**Status:** admitted — verification-only design approved after FIX-3 on
2026-08-16. Slice 70 closed at `e314f15` with unit/static evidence and an
approved local Docker access path; Slice 71 may now begin RED/GREEN work.

## Purpose and scope

Slice 70 delivers a manual, read-only same-host fleet view. Its fake transport,
PDP, and inventory tests prove the policy mechanics, but they do not prove two
real Airlock containers can safely operate through distinct host-loopback TLS
ports. Slice 71 closes that missing evidence only.

It SHALL add a disposable, opt-in Docker integration test and its execution
target. It SHALL NOT add a new control plane, Admin route, fleet action,
inventory format, dependency, deployment manifest, production configuration,
or Docker-derived authorization. The standard Compose deployment and ordinary
`make test` remain unchanged.

## Re-evaluated allocation

| DFR-40/DAC-40 contract | Slice 70 evidence | Slice 71 acceptance evidence |
| --- | --- | --- |
| Explicit isolated fleet mode | CLI isolation tests | Retain regression coverage. |
| Exact remote/fleet capability | PDP unit tests | A live scoped request succeeds; a mutation receives `403`. |
| Per-target secret isolation | In-process signing-secret test | Target-A token is rejected by target B. |
| Protected inventory/refs | Parser unit tests | Add no-follow symlink regression. |
| Same-host loopback topology | Structural Compose test only | Two containers bind distinct random `127.0.0.1` host ports. |
| TLS CA/name verification | Fake transport tests | Correct CA succeeds; a target's other CA produces `tls`. |
| Manual read-only fan-out | Static UI check/fake client | Explicit `refresh(["a", "b"])` returns fresh results only. |

## Design

The test creates all material under `tmp_path`: two minimal configs, two
independent test CAs and server certificates (including `127.0.0.1` SAN), two
distinct signing secrets, two exact-scope 15-minute JWTs, and owner-only
inventory/token/CA files. No model, provider key, real token, prompt, or host
secret is used or logged.

`make test-docker` is the one authoritative flow: it preflights accessible
Docker and `openssl`, freshly builds the current checkout's Dockerfile once,
records the immutable resulting image ID, and passes it to pytest explicitly.
The image build carries a unique Slice-71 run/source-revision label, which the
test asserts before starting containers. It must not use a reusable tag or an
ambient `AIRLOCK_DOCKER_IMAGE`. The Docker CI job provisions Python, uv, and the
locked test dependencies, then runs only `make test-docker`.

The test starts two containers via direct `docker run` with deterministic labels
and unique names. It refuses to run as root, runs containers as the invoking
non-root UID/GID, and creates their config/certificate/private-key mounts in an
owner-only temporary directory with mode `0600`. Each is given a generated
owner-only env-file (never secret-bearing command arguments) with its distinct
`AIRLOCK_JWT_SECRET`, and:

```yaml
admin:
  enabled: true
  trust_loopback: false
  remote_tui: true
  fleet_read_tui: true
```

Each container uses native TLS, a distinct signing secret, and
Docker-assigned `127.0.0.1::4000` publication. The test extracts the assigned
port from a narrow inspect field and only then creates the inventory. It does
not reserve a random port in Python. The generated config is mounted as
`/app/config.yaml:ro`, certificates/keys use the remote-admin mount paths,
`AIRLOCK_HOST=0.0.0.0`, native TLS environment variables are present, and
`AIRLOCK_JWT_SECRET_PREV`/`AIRLOCK_MASTER_KEY` are absent. The generated config
contains exactly `model_list: []` and the required Admin block—no provider
sections. It explicitly sets `AIRLOCK_MCP_STARTUP_MODE=off`,
`AIRLOCK_STARTUP_MODEL_DISCOVERY=0`, `AIRLOCK_ENABLE_MCP_SERVERS=0`, and
`AIRLOCK_LOG_DIR=/tmp/airlock-slice71`. The generated server key is readable by
the invoking non-root UID/GID used for the container, without relying on the
Dockerfile's fixed `airlock` user or a world-readable key. Direct `docker run`
is intentional:
the existing `docker-compose.remote-admin.yml` is a one-target operator
manifest, and a generated second Compose contract would add no production
coverage.

The test uses two generated CAs and leaf certificates with `IP:127.0.0.1` SAN,
and inventory origins of `https://127.0.0.1:<port>`. It mints each exact
two-scope, ≤15-minute JWT with that target's signing secret. It waits with a
monotonic bounded deadline only for `https://127.0.0.1:<port>/livez`, using the
generated CA and an environment-free proxy configuration; it never probes
`/health`, a provider, or an inference endpoint. It then uses
`FleetAdminClient` with explicit IDs, and verifies Docker inspect port bindings.
For cross-target replay it retains valid inventory references but replaces only
target B's in-memory token with target A's token, which must be `forbidden`.
It sends a direct CA-verified request to
`POST /airlock/admin/providers/slice71/clear-quarantine` and asserts `403`
before any handler-side mutation. The third unselected target is a bounded
host-loopback listener whose accept event must remain unset, proving that the
client does not contact an unselected inventory target.

All subprocess error output is bounded and redacted. The fixture records only
the returned container IDs. Before removal in `finally`, it inspects each
recorded ID and verifies the exact UUID run label; cleanup supports partial
starts, preserves the primary failure, and removes no discovered/filter-matched
containers. Narrow inspect assertions prove `HostIp == 127.0.0.1`, distinct
ports, no host network, non-privileged mode, and no Docker-socket mount.
`docker system prune`, broad container deletion, host-network mode, privileged
containers, Docker socket mounts, and all-interface port publication are
prohibited.

## Verification execution contract

Register `@pytest.mark.docker` in `pyproject.toml`, add `make test-docker`, and
change ordinary `make test` plus the normal CI test job to select
`not live and not docker`. `make test-docker` builds the labelled local image,
captures its ID, then selects only the Docker marker. The Docker CI job must
first provision Python, uv, and locked test dependencies, then run only that
target. Explicit `make test-docker` fails if Docker is unavailable or
inaccessible—it must not skip and falsely claim topology evidence. CI uses only
the disposable local image and generated material.

## TDD and acceptance criteria

1. **RED:** no-follow inventory symlink regression; the marker/Make target; a
   three-target inventory (two real containers plus one unselected listener
   trap) that cannot pass until real TLS containers and bounded cleanup exist.
2. **GREEN:** correct CA + exact token yields `fresh` for both selected targets;
   Docker inspect proves two distinct loopback-only bindings.
3. **Negative proof:** target-A token at target B is `forbidden`; target-B CA
   at target A is `tls`; fleet token mutation is `403`; no target is contacted
   without being selected.
4. **Safety proof:** generated secret sentinels are absent from assertion
   failures/test logs; cleanup removes only known containers; image/container
   labels are unique; no provider/inference call is made.
5. **Regression:** Slice 70 focused tests, Admin policy tests, Docker marker,
   Ruff/format, strict docs, `make sync && make verify`, and ordinary
   `make test` all pass. Record Docker engine/image versions and result in a
   Slice 71 status record.

## Blast radius and rollback

The product blast radius is nil: changes are test harness, Make/CI selection,
and verification documentation only. Docker resource use and accidental broad
cleanup are the material risks; named labels, generated temporary resources,
loopback publication, bounded output, and explicit cleanup mitigate them.
Rollback is removal of the test target and test-only resources; no deployed
operator configuration is changed.

## Documentation and durable evidence

Update the [README Developer setup](../../../README.md#developer-setup) section
with `make test-docker`, Docker and `openssl` prerequisites, its opt-in
behavior, generated-material and no-provider-network guarantees, and exact
cleanup boundary. The final Slice 71 status record SHALL name the image ID,
Docker engine version, command and result, plus any skipped/not-applicable
condition—never secret material.

## Admission gates

Before implementation, an independent design review must approve the generated
certificate/token approach, image provenance, Docker cleanup selectors,
marker/CI separation, liveness behavior, and secret-safe diagnostics. Docker
daemon access is now owner-approved only for Airlock development verification;
it does not authorize product code to use Docker as identity or control-plane
authority.
