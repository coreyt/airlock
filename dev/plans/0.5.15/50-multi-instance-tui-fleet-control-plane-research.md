# 0.5.15 research hypothesis — one TUI for multiple Airlock instances

**Status:** re-evaluated on 2026-08-16: **conditional, planning/design only;
not admitted for implementation.** This document adds no deployment agent,
remote-execution capability, or configuration write endpoint. The Slice 70
audit and independent design review confirmed that the fleet authority/HITL
decisions below remain unresolved.

## Slice 70 admission update

Slices 40 and 50 now provide a redacted child-owned Admin configuration
projection and a secure **single-target** host-console transport respectively.
They do not establish fleet inventory ownership, target identity, hardened
fan-out transport, audience-bound credentials, selection semantics, or a
cross-target audit model. The ordinary `urllib` Admin client must not be
generalized into fleet access without those controls.

The reviewed minimum **hypothesis** for a later owner decision is read-only
v1: an owner-only static local inventory with opaque IDs, display names, literal
HTTPS origins, and per-target CA/token-file references; explicit selections
only; no discovery, deployment/lifecycle/configuration actions, shared
credentials, mutations, redirects, proxy-from-environment, or local fleet
operation log. Proposed engineering limits are at most 20 selected targets,
four concurrent requests, 2-second connection/5-second request deadlines, and
64 KiB response bodies. They are not an approved product contract.

Before implementation, the owner/HITL record must decide:

1. inventory publisher, format, permissions, reload/lifecycle, and
   secret-manager reference posture;
2. supported systemd/container/Kubernetes topology and per-instance
   CA/SAN/issuer/audience/token-rotation contract, including whether every
   target requires a distinct credential;
3. whether RFC1918/private targets are permitted and the DNS-rebinding/egress
   enforcement rule; HTTPS-only, redirect, proxy, metadata, link-local, and
   loopback policy;
4. read-only v1 versus an exact fleet-safe mutation allowlist, target/count
   limits, confirmation/dry-run/retry semantics, and any two-person workflow;
5. correlation/audit retention and the explicit boundary that desired-state
   deployment remains external.

Until that record is ratified, the appropriate Slice 70 outcome is a status
record and a decision-ready design, not code, tests, user documentation, or
deployment changes.

## Outcome and hypothesis

An operator should be able to open one `airlock tui`, select one or more named
Airlock instances, inspect each instance through its authenticated Admin API,
and perform an explicitly scoped *runtime* action on the chosen instance(s).
This must work whether an instance is launched under systemd or in a container.

The narrow, Airlock-aligned hypothesis is:

> **The multi-instance TUI is an authenticated, fan-out Admin API client with a
> local, non-secret inventory of endpoints and trust anchors. Each Airlock
> instance remains the authority for its runtime state, authorization, audit,
> virtual keys, and effective configuration snapshot. Deployment configuration
> is owned by the existing reviewed deployment mechanism (file + restart,
> Ansible, GitOps, etc.), never by a TUI-side state store.**

“System-wide” therefore means an intentionally selected *fleet/target group*,
not a hidden global Airlock singleton. A system-wide change is either:

1. a bounded runtime Admin action fanned out to an explicitly selected target
   set, after every target authorizes it independently; or
2. a desired-state deployment change committed/applied by an approved external
   configuration owner, followed by each instance reporting its observed,
   redacted effective-config fingerprint through its Admin API.

The first can be an Airlock feature. The second is an integration/documentation
contract, not an embedded control plane.

## Current Airlock constraints carried forward

| Existing seam | Constraint for the fleet feature |
| --- | --- |
| `airlock/admin/policy.py` and `airlock/admin/http.py` | Each destination retains one Admin PDP/perimeter. A fleet selection never substitutes for the instance’s TLS, token, exact scope, or audit decision. |
| `airlock/tui/admin_client.py` | Extend the TUI client to use named endpoint profiles and bounded concurrent calls. Do not mount a Textual UI in the proxy process. |
| Existing host-console plan | Remote/container access is TLS plus scoped credentials; a Docker bridge, host port, DNS name, or private subnet is reachability only, not operator identity. |
| Provider-config design | The first provider panel is a redacted read-only effective-startup projection. Neither a fleet file nor a TUI cache becomes a second provider-routing authority. |
| Virtual-key design | `VirtualKeyStore` is the one per-instance key authority. The TUI only calls its scoped Admin adapter and never synchronizes key secrets or opens another instance’s store. |

The earlier design also found that the launcher starts LiteLLM as a separate
process. A configuration snapshot must therefore be initialized in that child
from the exact resolved runtime configuration; a TUI must never work around the
boundary by reading remote config files.

## Requirements for a future implementation

1. **Named inventory, no discovery by default.** A local TUI profile contains
   an opaque `instance_id`, display name, HTTPS Admin base URL, server-name/CA
   reference, and a **reference** to a local credential file or secret-manager
   item. It contains no bearer token, provider credential, virtual-key secret,
   SSH key, Docker socket, systemd unit, or provider YAML value. Auto-discovery
   through Docker, host scans, Consul, Kubernetes, or cloud APIs is out of the
   first implementation.
2. **Per-instance authentication.** The client makes independent TLS-verified
   Admin calls and sends only that instance’s short-lived scoped credential.
   A fleet credential must not be silently replayed to arbitrary endpoints;
   shared issuer/audience is a separately ratified identity design.
3. **Read aggregation is bounded and truthful.** Fetch redacted health/live
   snapshots and the proposed config-warning/config-inventory projection with
   bounded concurrency, request/time/size caps, source and freshness labels.
   Show `unreachable`, `TLS failed`, `unauthorized`, `forbidden`, and `stale`
   per instance; do not collapse partial failure into fleet success.
4. **Two-person semantics for mutations.** A user first selects explicit
   `instance_id`s, sees the exact action and the distinct target count, then
   confirms. The TUI submits one request per instance, records a local
   non-secret operation correlation ID, and reports a matrix of success/failure.
   It must not offer an “all instances” default, wildcard target, or best-effort
   retry that can turn a partial operation into an unbounded one.
5. **Local actions only initially.** Fan-out is eligible only for existing,
   idempotent, explicitly fleet-safe, non-destructive Admin actions after the
   Admin action contract says so. Provider/routing/config writes, Admin-token
   issuance, token revocation, virtual-key creation/reveal, key deletion,
   master-key changes, process restarts, and upgrades are excluded pending
   individual designs.
6. **Configuration truth.** The TUI may display each destination’s immutable,
   redacted effective-config fingerprint/version and configured policy view. A
   restart-required change must be visibly `desired` vs `observed` and is not
   called applied until the destination reports the new fingerprint.
7. **Audit and secrets.** Every receiving instance produces its existing
   redacted `admin_action` record with the authenticated subject and optional
   correlation ID. The TUI’s audit summary includes only target IDs, action,
   outcome, and timestamps. It never logs token/certificate/key material,
   provider secrets, request contents, raw config, or unredacted target errors.
8. **No inference dependency.** A failed/unavailable fleet console, inventory,
   external deployment controller, or one failed instance cannot affect another
   instance’s inference path.

## Candidate architecture

```text
operator
  |
  v
one `airlock tui` -- local profile: target IDs, URLs, CA/token *references*
  |                         |                  |
  | HTTPS + scoped token    | HTTPS + scoped token
  v                         v                  v
Airlock A Admin PDP      Airlock B Admin PDP  Airlock C Admin PDP
  |                         |                  |
  +-- local runtime state    +-- local state    +-- local state
  +-- config fingerprint     +-- fingerprint    +-- fingerprint

reviewed desired-state owner (optional, external)
  Git/Ansible/Kubernetes/Nomad/etc. --> config/secrets + lifecycle per target
  TUI reads post-apply state only; it neither invokes nor impersonates it.
```

The optional lower path is explicitly **not** between the TUI and Airlock.
That prevents an operator-console compromise from acquiring host/root, Docker,
or orchestrator credentials merely to view an Admin page.

## Is a library/control-plane needed?

### Embedded Airlock dependencies

No external fleet-control library is suitable as a baseline dependency. The
smallest aligned implementation is an Airlock-owned `FleetAdminClient` built on
the TUI’s existing typed Admin client/async HTTP substrate, plus Textual’s
existing selection/table/worker facilities. It needs application code rather
than a new fleet SDK:

| Candidate | Appropriate use | Decision |
| --- | --- | --- |
| Existing Textual + Airlock Admin client | Named-target selection, bounded concurrent reads, action-result matrix, local credential-file references. | **Adopt/extend.** Maintains TUI-as-client and avoids new infrastructure. |
| `httpx` (already used by Airlock provider/transport code) | TLS-verified async, pooled, bounded Admin API calls if the TUI client does not already expose equivalent transport. | **Partial; no new service.** Pin/cap only if a direct import is required. It is transport, not fleet authority. [HTTPX](https://www.python-httpx.org/async/) |
| `kubernetes` Python client / Docker SDK / Paramiko / systemd D-Bus client | Would make a user-facing TUI control the orchestrator/host/daemon directly. | **Reject.** Violates the no direct SSH/Docker/systemd access boundary and greatly expands credential and blast-radius scope. |
| Ansible Runner Python library | Useful only to an operator-owned automation runner, not linked into `airlock tui`. | **Do not adopt in Airlock.** A runner would need SSH/WinRM keys and playbook authority; it is a separate deployment platform. [Ansible Runner](https://github.com/ansible/ansible-runner) |

An instance registry/server, agent, message bus, or Airlock-specific Terraform
provider is not justified for 0.5.15. Those would introduce a new durable
authority, cross-instance identity and lifecycle requirements, and HA/recovery
work before they improve the basic Admin-client use case.

### External systems that can own configuration/deployment

These are viable *operator-selected integrations*, not Airlock dependencies.
Their role is to make desired deployment state available; Airlock must expose
only the observed effective result via Admin.

| System | What official docs establish | Fit and boundary |
| --- | --- | --- |
| **Ansible / Automation Controller** | Inventories and playbooks are the normal automation project model; `ansible.builtin.systemd_service` manages systemd units on remote hosts, including idempotent start/stop/restart. [Getting started](https://docs.ansible.com/projects/ansible/latest/getting_started/get_started_ansible.html), [systemd module](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/systemd_service_module.html) | **Best general VM/systemd or Docker-host deployment integration.** It can template Airlock config, provision secret references, and restart a selected service/container. Airlock must not embed it or receive its SSH/control credentials. |
| **OpenTofu / Terraform** | Declarative providers manage remote resource types but state maps configuration to real resources; official docs warn state can contain sensitive values and needs secure/locked storage. [providers](https://opentofu.org/docs/v1.11/language/providers/), [sensitive state](https://opentofu.org/docs/language/state/sensitive-data/), [state locking](https://opentofu.org/docs/language/state/backends/) | **Conditional infrastructure provisioning only.** Good for VMs, networks, KMS/Vault policy, or Kubernetes resources. Poor default for mutable Airlock provider policy: creating an Airlock provider would require a custom provider, token/state-secret design, and produces a competing config API. Do not build one in 0.5.15. |
| **Kubernetes + Flux or Argo CD** | Kubernetes Deployments declaratively reconcile Pod state and support controlled rollout/rollback; ConfigMaps can be volume-mounted but env values require restart; Secrets require encryption at rest/RBAC and are not safely "just base64". [Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/), [ConfigMaps](https://kubernetes.io/docs/concepts/configuration/configmap/), [Secrets](https://kubernetes.io/docs/concepts/configuration/secret/). Flux syncs versioned sources to Kubernetes with RBAC and multi-cluster capabilities; Argo CD is declarative GitOps CD for Kubernetes. [Flux](https://fluxcd.io/flux/), [Argo CD](https://argo-cd.readthedocs.io/en/stable/) | **Best supported configuration-owner model for Kubernetes-only adopters.** Git is desired state; Flux/Argo applies it and the workload restarts/reloads according to the deployment design. Do not bundle an operator, CRD, Flux/Argo client, or cluster credential in the TUI. |
| **Salt** | Salt is a master/minion remote-execution and configuration-management system; state files can enforce configuration, services, and files across targeted minions. [overview](https://docs.saltproject.io/salt/user-guide/en/latest/topics/overview.html), [state system](https://docs.saltproject.io/en/latest/ref/states/index.html) | **Possible existing-enterprise integration, not recommended baseline.** It creates a master/minion control plane and remote-execution authority beyond Airlock’s needs. |
| **Nomad** | Nomad jobs declare desired workload state, which Nomad reconciles on clients; job files are normally version-controlled. [jobs](https://developer.hashicorp.com/nomad/docs/concepts/job), [job submission](https://developer.hashicorp.com/nomad/docs/job-declare/create-job) | **Conditional for existing Nomad installations.** Use its jobspec/ACL/update process; do not give the TUI Nomad credentials or make Airlock schedule workloads. |
| **Consul** | Consul is service discovery/networking; registered services provide health/address information via catalog/DNS/API. [service discovery](https://developer.hashicorp.com/consul/docs/discover) | **Optional discovery source only, and deferred.** It may resolve pre-approved `instance_id`s in a Consul-operated estate, but catalog membership is not Airlock-admin authorization and should not cause automatic target enrollment. |
| **FleetDM** | Fleet is device management/endpoint control, not an application desired-state or per-service Admin plane. | **Reject.** It is the wrong layer: a host/device agent would expand endpoint-management authority and cannot replace TLS/scoped Airlock Admin calls. |
| **Vault** | An external secrets/KMS system can protect deployment secrets and native-store encryption material, but it is neither an instance registry nor a fleet config controller. [Vault Transit](https://developer.hashicorp.com/vault/docs/secrets/transit) | **Optional existing-enterprise secret integration only.** Retain Airlock’s native virtual-key authority and never put virtual-key plaintext, Admin tokens, or TUI inventory secrets in a second Airlock-owned Vault schema. |

## systemd versus containers: why it matters

The Admin API contract is deliberately deployment-neutral—both forms need TLS,
per-instance credentials, authorization, audit, and a truthfully reported
effective configuration. The *delivery and lifecycle* layer is not neutral.

| Concern | systemd-native service | Docker/Compose container | Kubernetes/orchestrated container |
| --- | --- | --- | --- |
| Transport/reachability | Local loopback can safely support a same-host native TUI; remote uses an explicitly exposed TLS Admin endpoint. | A published port is host firewall/NAT exposure, not container-loopback identity. Docker notes bridge-network ports are reachable from host/peer containers and `-p` exposes them outside the host. [Docker](https://docs.docker.com/engine/network/port-publishing/) | Service/DNS/load balancers and network policy determine reachability; Pod IPs are ephemeral. Endpoint identity must be stable DNS plus TLS, never an IP. |
| Discovery | Static hostnames/inventory (or an external CMDB/Ansible inventory) is natural. | Compose service/container names are local deployment details; do not scan Docker socket. | Labels, Services, or an approved registry can map IDs to URLs, but automatic enrollment expands trust and must be separately designed. |
| Config delivery | Reviewed files, systemd environment/credentials, unit drop-ins. | Mounted files/env/secret files and image/Compose versions. Environment updates normally require container recreation. | ConfigMap volume propagation and Secrets/POD templates; ConfigMap environment values do not update automatically. Deployment rolls out a changed Pod template at a controlled rate. [ConfigMaps](https://kubernetes.io/docs/concepts/configuration/configmap/), [Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/) |
| Identity/auth | OS permissions protect local files; remote endpoint still needs TLS and scoped Admin credentials. | Avoid Docker-socket access and bridge-address trust. Token/CA materials must be mounted/read with least privilege. | Cluster RBAC/service accounts are distinct from Airlock Admin subjects; Kubernetes Secrets require encryption at rest and least-privilege RBAC. [Secrets](https://kubernetes.io/docs/concepts/configuration/secret/) |
| Lifecycle/upgrades | `systemd` does daemon reload/restart/enable; Ansible’s systemd module can do it remotely. | Deployment system replaces/restarts a container; TUI must not run Docker commands. | Controller rollout/rollback semantics and availability gates apply; TUI must not call `kubectl` or reconciliation APIs. |
| Blast radius | A privileged service manager or SSH account can own the whole host. | Docker socket often controls the host/container estate; giving it to the TUI is a high-risk escalation. | Namespace/cluster credentials can modify many workloads/secrets; GitOps controller scope must be RBAC-constrained. |

So deployment form matters greatly for *configuration ownership and blast
radius*, but it should not lead to three TUI implementations. The TUI speaks a
single Admin API protocol; installation-specific configuration is delivered by
the platform that already owns the installation.

## Acceptance criteria for the narrow first slice

1. A configured TUI profile with two to a bounded number of distinct HTTPS
   Airlock Admin endpoints shows per-instance source, version/fingerprint,
   fresh/stale/unavailable state, without contacting an unconfigured endpoint.
2. Invalid CA/hostname, expired/wrong-audience/wrong-scope token, missing token,
   one slow target, and one malformed response fail only that row; they do not
   leak response content, secret material, or block other bounded calls.
3. A read requires the proposed exact Admin read scope on every target; a
   mutation request is made only after explicit target review/confirmation and
   results report each instance separately.
4. A malicious/incorrect inventory cannot cause calls to link-local,
   loopback-unapproved, metadata, Unix-socket, or arbitrary redirected URLs.
   Enforce HTTPS, allowlisted configured origins, no redirects, DNS/IP
   re-validation at connection time, response/request caps, and proxy-from-env
   disabled unless a reviewed proxy profile is selected.
5. The TUI never opens SSH, Docker, Kubernetes, systemd, FathomDB, or another
   instance’s files/storage. Test instrumentation verifies no such dependency.
6. A deployment configuration change is represented as external desired state;
   after the approved deployment controller restarts/rolls out an instance, the
   TUI observes the new redacted fingerprint through Admin. It never writes a
   provider credential/config file nor claims completion from a local desired
   value.
7. Existing single-instance loopback TUI, container host-console TLS behavior,
   inference routing/guardrails, and per-instance Admin actions retain current
   behavior when fleet mode is disabled.

## Architectural alignment and blast radius

**Aligned:** this design preserves Airlock’s gateway ownership (LiteLLM remains
the transport substrate), a per-instance Airlock Admin PDP, local native
virtual-key store, and TUI-as-client boundary. It contains the new code to TUI
profile parsing, typed client fan-out, selection UX, Admin API version/capability
descriptors, tests, docs, and small additive Admin DTO fields.

**Misaligned/rejected:** a central Airlock fleet database, central virtual-key
replication, LiteLLM database UI, TUI writing YAML/env files, SSH/Docker/systemd
execution, an embedded Ansible/Salt/Nomad/Consul/Flux/Argo control plane,
unreviewed automatic discovery, broad bearer-token reuse, and provider config
CRUD with no durable owner.

The direct blast radius is high at the Admin authorization/transport edge and
medium in TUI UX; it is intentionally zero on the inference hot path. Fan-out
turns a valid but mistaken action into several independently authorized actions,
so target selection, limits, concurrency, dry-run/confirmation, audit
correlation, and partial-failure semantics are the primary safety controls.

## HITL decisions before code

1. Ratify the above **Admin-client-only** baseline versus authorizing a new
   Airlock-owned centralized fleet/configuration service (recommended: baseline
   only; defer the latter).
2. Select the initial deployment support matrix: native/systemd and
   Docker/Compose profiles, Kubernetes GitOps integration guidance, or all
   three. Do not claim generic remote lifecycle management without a selected
   owner.
3. Ratify the instance identity model: who publishes inventory profiles; how
   TLS CAs/SANs and per-instance token audiences/issuers are provisioned;
   whether a shared credential is ever allowed; rotation/revocation/incident
   response; and the operator secret-file/secret-manager contract.
4. Approve the exact fleet-safe action allowlist, maximum target count,
   concurrency/timeouts, confirmation/dry-run rule, retry policy, and whether
   an action requires a second operator/approval workflow.
5. Decide whether desired-state system-wide configuration is **documentation
   only** in 0.5.15 (recommended) or whether Airlock needs a formally versioned
   deployment-provider plug-in contract later. This decision must precede any
   CRUD surface.
6. Approve SSRF/egress policy for inventory endpoints and the audit-retention
   model for local fleet-operation summaries.

## Research method and sources

This is a web-research hypothesis using primary project documentation and
official GitHub project pages where a Python integration was assessed. It does
not infer vendor adoption as a substitute for Airlock’s security boundary. The
candidate table links each source directly; especially relevant primary sources
are the [Ansible systemd module](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/systemd_service_module.html),
[OpenTofu sensitive-state guidance](https://opentofu.org/docs/language/state/sensitive-data/),
[Kubernetes Secret guidance](https://kubernetes.io/docs/concepts/configuration/secret/),
[Flux architecture overview](https://fluxcd.io/flux/),
[Argo CD overview](https://argo-cd.readthedocs.io/en/stable/),
[Nomad job model](https://developer.hashicorp.com/nomad/docs/concepts/job),
and [Consul discovery overview](https://developer.hashicorp.com/consul/docs/discover).
