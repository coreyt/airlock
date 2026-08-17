# 0.5.15 design candidate — provider configuration visibility in Admin/TUI

**Status:** ratified for Slice 40 implementation, 2026-08-16. This supersedes
the earlier conditional draft after the Slice 40 audit resolved B2 with the
child-owned runtime-config handoff below. It does not authorize
provider/model/credential CRUD. CRUD requires its own durable
configuration-owner decision.

## Decision proposed for review

Make the initial candidate **read-only provider-configuration visibility**.
Do not put provider CRUD in 0.5.15 unless HITL later approves a separate durable
configuration-owner design. Airlock's deployment policy remains reviewed YAML
and environment/secret-manager references followed by restart.

This is deliberately narrower than LiteLLM's database-backed admin UI: its
documented `store_model_in_db` makes database models additional deployments to
YAML models, which would create a second routing-policy authority and can load
balance both. That conflicts with Airlock's explicit-alias and reviewed-policy
contract. [LiteLLM config model](https://docs.litellm.ai/docs/proxy/configs)

## Current facts and research

* `airlock/proxy.py` reads YAML at startup, builds a temporary runtime config
  only for startup rewrites, then starts LiteLLM. `config.local.yaml` may be an
  included deployment overlay. There is no live config store or reload owner.
* `model_list` is the served-alias allowlist. `capability_record()` and
  `airlock_provider_for()` are the shared truth for the served provider and
  capability metadata. Discovery is optional, informational, and cannot add an
  alias (`AIRLOCK_STARTUP_MODEL_DISCOVERY=0` by default).
* The installed Admin perimeter (`airlock/admin/http.py`) is a proxy-process,
  authenticated, bounded read/mutation seam. Its existing `GET .../providers`
  snapshot is operational state (quarantine, limits, spend), not configuration.
  The separate-process TUI consumes it through `tui/admin_client.py`.
* `tui/screens/config.py` already labels provider-key inputs as `applies=False`;
  it masks process environment but must not be mistaken for a secret editor.
* LiteLLM documents `model_name` as the client alias and
  `litellm_params.model` as the routed model, including `os.environ/NAME`
  secret references. [LiteLLM config reference](https://docs.litellm.ai/docs/proxy/configs)
  This matches Airlock's model-list ownership but not its policy controls.
* Existing dependencies already cover the client and validation needs: Textual
  supplies tables, inputs, workers, and tests
  ([repository](https://github.com/Textualize/textual)); Pydantic is already
  transitive through LiteLLM and is suitable for a pure DTO/validation boundary
  ([repository](https://github.com/pydantic/pydantic)). PyYAML is already
  pinned, but safe-load/safe-dump would discard comments/formatting and must not
  be used to implement editing ([repository](https://github.com/yaml/pyyaml)).
* `ruamel.yaml` is a possible round-trip YAML library
  ([repository](https://github.com/pycontribs/ruamel.yaml)), but adding it now
  would solve syntax preservation without solving configuration ownership,
  secret references, concurrent deploys, or reload. **Reject for 0.5.15.**

## Requirements

### Functional requirements

1. A new authenticated `GET /airlock/admin/config/providers` returns a bounded,
   source-labelled, **redacted effective startup configuration inventory**:
   provider token, aliases, underlying model identifier, allowed endpoints,
   region, deprecated marker, stable API-base host only, opaque credential
   reference kind and boolean presence, and whether a setting is static or
   derived. The fixed limits are 64 providers, 200 aliases total, and 256
   characters for every displayed metadata string; deterministic truncation is
   labelled in the response.
2. The response uses the same resolved include/config semantics that Airlock
   uses for launch, but reports `source: startup_config`, a config fingerprint
   (non-secret hash of the canonical credential-blind redacted DTO), load timestamp, schema
   version, and `restart_required: true`. It is not a
   promise that a later disk edit is active.
3. The Admin scope is new and least-privilege: `admin:read_config`; master key
   and trusted loopback retain their existing authority. Disabled Admin remains
   404. No config read becomes remotely visible through `admin:read` by default.
4. The TUI gains a read-only Provider Configuration panel that calls only this
   endpoint in a background worker. It shows freshness/source, aliases and
   capability truth alongside (not mixed with) the current live operational
   snapshot. An unavailable/unauthorized endpoint degrades to an explicit
   unavailable state; it never falls back to reading another process's files.
5. The projection never exposes credential values, literal secret length/prefix,
   request headers, full `api_base` path/query/userinfo, included-file path,
   unrecognised environment variable, or a provider error body. `api_base` is
   normalized to a host only; secret values are represented only as
   `credential: {kind: env_ref|credential_ref|none|redacted_literal,
   configured: bool}`.
6. Discovery output and live provider health stay distinct: discovered models
   cannot appear as configured aliases; operational state may be absent for an
   otherwise configured provider.

### Explicit non-goals

No create/update/delete/reorder model aliases; no editing YAML, `.env`, Docker
environment, Kubernetes Secret/ConfigMap, provider credentials, router
fallbacks, budget or guardrail policy; no automatic discovery-to-enable; no
live reload; no generic file browser; no adoption of LiteLLM's DB config/model
management as an Airlock control plane.

## Acceptance criteria

1. Fixture configurations with direct includes, provider-prefixed aliases, enhanced
   profiles, regional Vertex, missing credential refs, and duplicate provider
   aliases produce a deterministic inventory whose alias/provider/endpoints
   agree with `capability_record()`.
2. Snapshot tests prove no sentinel secret, raw API base path, include path, or
   arbitrary environment variable can reach JSON, TUI render, JSONL, exception,
   metrics, or response headers.
3. `admin:read` alone is 403; `admin:read_config`, master key, and trusted
   loopback succeed only when Admin is enabled. Disabled remains 404; bad/missing
   credentials reveal no inventory.
4. TUI tests prove bounded background refresh, table/detail rendering, stale or
   unavailable source indication, and no local-file fallback. Existing TUI
   operational-provider and config-screen behaviour remains unchanged.
5. `/v1/models`, `/model/info`, routing, guardrails, and model discovery have
   byte-for-byte/behavioural regression coverage showing the new snapshot is
   off the inference hot path.
6. Documentation says an edit still requires the deployment workflow and
   restart; an operator cannot infer that the UI applied an edit.

## Draft design

### Ratified data flow and B2 decision

```text
reviewed YAML + direct deployment overlay
        | (pinned LiteLLM include-list expansion; no independent resolver)
        v
materialized runtime config ---- `--config` ----> LiteLLM child
        |                                      |
        +---- `AIRLOCK_CONFIG` ----------------+
                                               | (child startup only)
                                               v
                         Admin policy + immutable redacted snapshot
                                               |
                         GET + admin:read_config, Cache-Control: no-store
                                               v
                           TUI background HTTP client / read-only panel
```

1. Add `airlock/litellm_config.py`, a narrow shared loader that exactly applies
   the pinned LiteLLM include contract: includes must be a list of paths;
   paths resolve from the root file and are processed in active-list order; an
   included list extends an existing root key (including `include`, which queues
   descendants after existing entries and preserves LiteLLM's malformed-target
   failure); scalar/dict values replace the root value; `include` is removed
   after expansion.
   It must preserve sensible file/parser errors and deep-copy values so it
   never aliases the caller's YAML structure.
2. Always materialize exactly one private runtime config file and pass its path
   to LiteLLM and through `AIRLOCK_CONFIG`; this is the sole Slice-40
   configuration authority. The parent performs CC-12 validation and all its
   startup consumers use the same direct-resolved mapping. The child configures
   its Admin policy and builds the snapshot from that same runtime path before
   mounting the perimeter. The models capability seam consumes that materialized
   path too.
3. Add a small pure `airlock/provider_configuration.py` projector. It receives
   the direct-resolved child configuration and a controlled environment lookup,
   calls existing `capability_record`, and returns immutable redacted DTOs. It
   never returns source YAML, calls a provider, names a secret or environment
   variable, or makes a secret change observable through its fingerprint.
4. Add an Admin read route through the existing `AdminMiddleware`/pure-handler
   pattern. Keep the response bounded (for example 200 aliases, 64 providers,
   fixed field lengths) and add `Cache-Control: no-store`.
5. Extend `tui/admin_client.py` with a typed fetch and a narrowly scoped panel
   or Provider-detail subview. Use existing `@work(thread=True)` style and a
   refresh interval no faster than the operational snapshot. Never put any
   credential entry field on the view.
6. Only after separate approval for CRUD, author a new design that selects one
   durable configuration owner (GitOps/file handoff, a secret-manager-aware
   desired-state store, or a specifically accepted LiteLLM DB integration),
   optimistic concurrency/versioning, semantic validation on a copy, atomic
   activation/restart, audit/rollback, and an approval flow. That is not an
   extension point to silently add now.

## Architectural alignment and blast radius

| Concern | Alignment / containment |
| --- | --- |
| Gateway ownership | Reuses LiteLLM for transport and Airlock's ASGI Admin seam; does not replace routing or model resolution. |
| Explicit aliases | Uses `model_list` and `capability_record`; no discovery or credential creates a route. |
| Process boundary | TUI remains an HTTP client, never shares state or reads proxy files. |
| Secret boundary | Projects presence/reference only; redaction is tested across all durable/remote surfaces. |
| Hot path | Startup snapshot plus authenticated admin GET only; no pre-call hook, proxy middleware for inference, or provider request. |
| Authorization | New read-specific scope avoids widening operational-history or write scopes. |
| Blast radius | Additive Admin/TUI/docs/tests; risks are config/secret disclosure and semantic mismatch, contained by typed projection, response bounds, and no-store responses. |

The principal risk is presenting configuration as live policy when it is not.
`source`, fingerprint, timestamp, and `restart_required` are contractual, and
the TUI must render them prominently.

## Rollout and verification plan

1. **Design gate (closed):** remote capability access is explicitly scoped;
   the response uses 64 providers, 200 aliases, 256-character safe strings,
   opaque credential kinds, and a canonical-redacted fingerprint.
2. **RED:** pure-projection and redaction tests first, then Admin authorization,
   include-resolution parity, TUI, and full proxy regressions.
3. **Canary:** Admin remains disabled by default. Enable its existing explicit
   `admin.enabled` switch only on a local test deployment; compare snapshot
   aliases/capabilities against `/v1/models` and `/model/info`, not provider
   discovery.
4. **Operational verification:** inspect captured output and JSONL using
   sentinel credentials, exercise absent/disabled/admin-denied paths, restart
   after a reviewed config change, and verify the fingerprint changes only after
   restart.
5. **Release gate:** independent security/architecture review confirms no second
   config authority or secret egress; HITL accepts or rejects promotion.

## Accepted security answers

* Remote use requires the new explicit `admin:read_config` scope; loopback and
  the master key retain their existing full-Admin authority.
* Reference names and handles are not safe output. The API reports only an
  opaque kind and `configured` boolean.
* The launcher, child policy, snapshot, and capability seam share the pinned
  LiteLLM include-list expansion and runtime-path handoff. A separate Airlock
  resolver with different recursion/order semantics is expressly rejected.
