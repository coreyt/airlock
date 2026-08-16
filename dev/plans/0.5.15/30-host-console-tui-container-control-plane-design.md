# 0.5.15 design plan — secure host-console TUI administration of containerized Airlock

**Status:** ratified for Slice 50 implementation on 2026-08-16. The release
implements only the host-console topology below, behind an explicit
`admin.remote_tui` profile. It does not make a deployment-default change.

## Slice 50 ratification

The owner authorized implementation after the Slice 50 scope audit. The
following narrow contract resolves the prior B4 admission gate:

- Only a host-console TUI reaching an Airlock container through a published
  `127.0.0.1` host port is supported. IPv6 publication and a peer TUI
  container remain deferred.
- The profile is default-off. It requires native TLS, `admin.enabled: true`,
  `admin.trust_loopback: false`, and a non-loopback Airlock bind. Docker NAT
  is therefore always treated as remote, not as an operator identity.
- Remote TUI access uses only a CA/name-validated HTTPS connection and a
  protected token file. The capability token has a 15-minute ceiling and is
  restricted by the `admin:remote_tui` anchor plus `admin:read`,
  `admin:read_config`, and `admin:clear_quarantine`. The master key is never
  accepted for this profile.
- The remote UI is a small Admin-HTTP-only view. It does not start/manage the
  proxy, read host JSONL/FathomDB/configuration, edit configuration, access
  MCP, or show operational history. Force quarantine and erasure remain
  loopback-only.
- Successful remote mutations retain the token subject as actor and record the
  non-secret `remote_tui_jwt` auth context. No raw IP, header, token, or
  certificate material is audited.
- A separate hardened Compose manifest is opt-in and binds only the host
  loopback port. The existing Compose file remains unchanged and does not
  enable Admin.

Deferred: peer-container topology, mTLS/reverse proxies, forwarded identity,
Docker/CIDR trust, remote history, configuration/virtual-key CRUD, and any
all-interface Admin publication.

## Decision framing

Airlock's Admin API currently authorizes a loopback connection as the operator
(Path A), and the separate-process TUI uses that path. In a Docker deployment,
the host-console TUI reaches the container through a published port. The
connection source observed inside the container is a Docker/network address,
not `127.0.0.1`; a peer container is likewise not loopback. Docker's bridge
membership or a host-loopback port publication is an exposure boundary, **not**
an authentication fact.

The proposed, deliberately narrow future direction is a **direct, TLS-protected
Admin API with short-lived scoped capability credentials**. It reuses Airlock's
existing Admin PDP and audit record rather than adding a second control plane.
The first release would support the two explicitly configured topologies below;
all other container-network sources remain untrusted.

1. A host-console `airlock tui` connects to an Airlock container port published
   only on `127.0.0.1`/`::1`. Airlock runs native TLS, sets
   `admin.trust_loopback: false`, and the TUI presents a short-lived scoped
   capability token and validates a configured CA/hostname.
2. A separately deployed, explicitly approved operator/TUI container connects
   over an isolated bridge network using the same TLS + scoped-token protocol.
   The network limits reachability; it does not grant authorization. An
   unknown peer, even on that network, has no access without a valid token.

The safe current patterns remain a native Airlock/TUI pair or a TUI in the
Airlock container. This document does not make `network_mode: host`, Docker
socket access, bridge-address allowlisting, self-signed-certificate bypass, or
an inference `AIRLOCK_MASTER_KEY` a supported substitute.

## Current-state evidence and constraints

| Existing seam | Evidence | Constraint carried forward |
| --- | --- | --- |
| One Admin PDP | `airlock/admin/policy.py` evaluates loopback, master key, then exact-scope JWT. | Extend scopes and principal facts; do not create TUI-only authorization. |
| One Admin perimeter | `airlock/admin/http.py` extracts `scope["client"]` and mounts ahead of LiteLLM routes. | A Docker source address must remain non-loopback and fail closed. |
| TUI is a client | `airlock/tui/admin_client.py` uses HTTP, presently unauthenticated only for loopback. | Move transport/auth behavior here; never mount Textual in the proxy process. |
| FathomDB ownership | Operational history is proxy-owned and current endpoints are loopback-only. | TUI/container processes never open the embedded store; any remote history view must be separately authorized, bounded, and source-labelled. |
| Existing security posture | Non-loopback bearer Admin refuses plaintext startup unless TLS/proxy assertion or an explicit unsafe override is selected. | Do not weaken this check or treat a Docker network as TLS. |

Docker documents that `-p` exposes a container port through a host mapping and
that binding the published host port to `127.0.0.1` restricts reachability to the
host; it does not say the downstream container receives a loopback peer. Its
bridge model also permits communication among containers on the same network.
This supports the design's separation of reachability from identity.

## Design requirements

### Functional requirements

- **DCR-1 — explicit topology.** Operators can select only the two named
  remote-control topologies through a reviewed configuration/deployment
  profile. A port mapping or container source address alone never creates an
  operator principal.
- **DCR-2 — transport authentication.** A non-process-loopback Admin request
  requires TLS with certificate validation and a bearer capability JWT. The
  TUI must not disable TLS verification merely because its destination hostname
  is `localhost`.
- **DCR-3 — least privilege.** Read snapshots, live-protection mutations,
  operational history, and destructive erasure each use distinct explicit
  scopes. The TUI asks only for the scopes required by the enabled screen/action;
  it never sends the master key or an inference credential.
- **DCR-4 — bounded reads.** Remote operational history, if ratified, remains
  proxy-owned, capped, paged/bounded, source-labelled, and visibly degraded.
  It must not silently switch from FathomDB to JSONL or disclose request
  content in a denial response.
- **DCR-5 — credential lifecycle.** Tokens have a maximum TTL, an identifiable
  non-secret subject, rotation/revocation instructions, and an operator-provided
  local secret-file mechanism. Neither token values nor TLS private material
  appear in config, process diagnostics, TUI snapshots, URLs, logs, or audit
  targets.
- **DCR-6 — auditable mutations.** Successful mutations retain the existing
  `admin_action` contract and add a stable auth/topology descriptor (for
  example `token_remote`), never an IP address or bearer material by default.
- **DCR-7 — failure behavior.** Missing/expired/wrong-scope credentials return
  the existing bounded 401/403 JSON shape. TLS failure, unavailable Admin API,
  and unavailable operational reads are presented to the TUI as unavailable,
  without retry storms or inference-path effects.

### Security and compatibility requirements

- No control endpoint becomes reachable on an all-interface published port by
  default. The standard Compose deployment remains inference-only in behavior.
- `trust_loopback` remains exactly process-network-loopback semantics. There is
  no `trust_docker_bridge`, `trust_host_gateway`, or CIDR-based operator mode.
- The inference request path, model routing, guardrails, provider credentials,
  LiteLLM virtual-key behavior, and JSONL record format remain unchanged.
- Existing native-TUI Path A behavior remains backward compatible when this
  future profile is disabled.
- A reverse proxy may be considered only as an operator-managed deployment
  integration; Airlock must not accept forwarded identity headers unless a
  separately designed, mutually authenticated proxy channel establishes their
  provenance.

## Acceptance criteria for a future implementation proposal

1. A host TUI can read the allowed snapshots and execute only an explicitly
   granted live-state action through a `127.0.0.1`-published, TLS-enabled
   container endpoint; valid certificate/CA verification is demonstrated.
2. The same host route with no token, expired token, wrong scope, plaintext,
   or invalid CA/certificate fails without returning state or secret material.
3. An approved peer TUI container succeeds only with its scoped credential;
   another container on the same bridge fails for all Admin and operational
   endpoints. Test both IPv4 and configured IPv6 publication if enabled.
4. Operational history is either explicitly denied for remote clients or, if
   the separate history scope is approved, is proxy-owned, bounded, source and
   truncation labelled, and cannot disclose records on 401/403/404 paths.
5. Host-loopback publishing is verified as a reachability restriction, not an
   authorization shortcut: `trust_loopback` does not admit the NATed request.
6. Every successful control mutation emits exactly one redacted audit record;
   reads emit no mutation audit record; token, certificate, private key,
   request content, and raw client IP do not enter test fixtures, JSONL, or TUI
   snapshots.
7. Existing direct-loopback TUI, Admin JWT, plaintext refusal, and FathomDB
   local-bridge regression suites retain their prior contracts.
8. A security reviewer signs off on threat model, token handling, certificate
   provisioning, topology manifests, and rollback before enablement.

## Proposed architecture (not an implementation instruction)

```
host TUI / approved TUI container
  └─ TLS + scoped bearer token ─▶ Airlock AdminMiddleware
                                  ├─ authenticated Principal
                                  ├─ existing PDP (exact scope)
                                  └─ existing proxy-owned handlers / audit writer

Docker publish / isolated bridge: reachability only; never Principal.loopback
```

### Authorization matrix

| Surface | Proposed scope / condition | Data/action boundary |
| --- | --- | --- |
| Provider/client/circuit/telemetry snapshots | `admin:read` | existing bounded live state |
| Clear quarantine/reset circuit/clear backoff | existing action scopes | existing state-only mutations + audit |
| Remote operational history (optional) | new `admin:operational_read`, explicitly enabled | bounded proxy-owned read; no direct DB |
| Erasure and manual force-quarantine | remain loopback-only unless a later design explicitly revises them | destructive/break-glass controls |
| TUI key management | out of scope; depends on 0.6.0 virtual-key authority | no key API exposure here |

The token issuer remains the existing locally signing `airlock admin mint-token`
path, but the future proposal must define a non-secret token distribution
workflow (for example, a root-readable file delivered by the operator's secret
manager). A TUI never mints its own authority. TLS server identity is pinned to
an operator-managed CA/name rather than using the current loopback-only
self-signed verification bypass.

### Alternatives researched

| Alternative | Finding | Decision |
| --- | --- | --- |
| Direct TLS + scoped JWT | Fits the existing Admin policy, token signer, and audit seam; no second proxy or process needed. | **Recommended design baseline.** |
| Host-local trusted reverse proxy | Can work only with a mutually authenticated isolated upstream channel and strict forwarded-header handling. It adds an identity translation and operational lifecycle. | Defer; require a dedicated threat model if a user needs SSO/proxy integration. |
| Docker bridge/CIDR/host-gateway trust | A bridge peer can be another container; NAT source is not cryptographic identity. | Rejected. |
| `network_mode: host` | Removes network namespace isolation and Docker ignores port publication in host mode. | Rejected as an authorization solution. |
| Reuse `AIRLOCK_MASTER_KEY` | Broad inference/admin authority and poor rotation/audit granularity. | Rejected. |

## Web research and GitHub-library assessment

| Source/library | What it establishes or could do | Airlock alignment / decision |
| --- | --- | --- |
| [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/) | `127.0.0.1` publication limits host reachability; published ports otherwise can be exposed; bridge/direct routing caveats matter. | Deployment evidence; no runtime dependency. |
| [Docker bridge networks](https://docs.docker.com/engine/network/drivers/bridge/) | Bridge networks are connectivity domains, not an operator identity mechanism. | Supports the no-CIDR-trust rule. |
| [Docker host networking](https://docs.docker.com/engine/network/drivers/host/) | Host networking shares the host network namespace and makes port mappings ineffective. | Explicitly not a supported authority shortcut. |
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | Existing substrate already hosts Airlock's ASGI Admin middleware. | Partial, existing dependency only; do not delegate Airlock Admin policy to LiteLLM. |
| [Traefik](https://github.com/traefik/traefik) | Could terminate TLS/perform forward auth for an operator-managed edge. | Not adopted: extra control-plane hop, header-trust risks, and broader deployment blast radius; retain only as a later integration option. |
| [Envoy](https://github.com/envoyproxy/envoy) | Could provide mTLS/proxy policy. | Not adopted: operationally disproportionate for a single-container default and would duplicate Airlock's PDP boundary. |

The relevant upstream security warning is concrete: Traefik has recently fixed
forward-auth header-handling issues. That does not prohibit it, but reinforces
that a proxy path cannot be treated as a free identity proof and needs a
versioned, separately reviewed deployment contract.

## Architectural alignment and blast radius

**Alignment verdict: conditionally aligned.** The proposal keeps Airlock's
authority and state ownership where they are: the proxy Admin perimeter is the
policy-enforcement point; the existing PDP decides exact scopes; the TUI stays a
pulling client; the proxy remains the sole FathomDB owner; JSONL remains a
bounded fallback; and the inference hot path receives no TUI dependency. It is
not aligned if it adds Docker-source trust, an unaudited proxy identity header,
or a parallel admin/key store.

| Area | Expected future change | Risk / blast radius | Mitigation |
| --- | --- | --- | --- |
| Admin policy and HTTP perimeter | remote token principal, new optional history scope | High: authorization boundary | pure PDP tests, default-off profile, security review |
| TUI Admin client | token-file/CA loading and accurate unavailable state | Medium: operator UX and secret handling | no env echo, no verification bypass, snapshot tests |
| Deployment docs/Compose | opt-in TLS/topology examples | High: accidental endpoint exposure | separate hardened profile, bind-address assertions, rollback instructions |
| Operational reads | optional scoped remote view | High: content disclosure | default deny, bounds/source labels, no direct DB |
| Inference/guardrails/routing | none | Must be zero | regression and request-path performance checks |

## Rollout, verification, and rollback plan

1. **Pre-implementation HITL:** approve threat model, exact scopes, certificate
   trust/distribution, token delivery, and whether remote history is allowed.
2. **Red:** write unit/perimeter tests for both topologies and every negative
   case before a transport/client change. Add Compose integration tests with a
   hostile bridge peer and a host TUI process.
3. **Green:** enable only a dedicated disabled-by-default profile, with an
   explicit bind address, TLS material references, token-file path, and no
   credential values in YAML. Preserve existing native loopback tests.
4. **Canary:** an operator validates a non-sensitive snapshot first, then one
   reversible protection action, and verifies a redacted audit record. Do not
   use production request history as a smoke test.
5. **Rollback:** disable the profile/remove the published Admin mapping and
   restart. Existing direct-process loopback access stays available. Rotate the
   JWT signing secret or credential if compromise is suspected; certificate
   rotation follows the operator CA process.

## Open HITL questions

- Is remote operational history necessary in 0.5.15, or should the remote TUI
  initially expose only live snapshots and state controls?
- What secret-manager/file-permission contract is acceptable for a host TUI
  token, and which CA is authoritative in each supported deployment?
- Is an operator-managed mTLS reverse-proxy integration a product requirement,
  or should it stay an unsupported deployment composition until a separate
  design is approved?
