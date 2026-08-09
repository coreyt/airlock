# Design — PII Rehydration (PRIMARY / consolidated)

Status: **DRAFT, do not commit without owner review.**

This is the **authoritative** design for Airlock's PII redaction→rehydration
feature. It reconciles two working notes, which remain as detail references:

- `design-pii-rehydration-goldmine.md` — the verified threat model (file:line),
  the "no goldmine" data-handling design, graceful-degrade. **Detail for Part A.**
- `design-rehydration-authorization-maintainability.md` — the research survey and
  the layered authorization gate that avoids a decaying list. **Detail for Part B.**
- `../../dev/design-note-pii-rehydration.md` (main checkout) — the original
  tool-call hydration mechanism this hardens.

Where this primary conflicts with either, **this primary wins**. It adds four
owner directives (2026-08-08): a **required known-bad blocklist**, the
**Cedar/CaMeL tiering**, and a **validation plan for deny-plus-telemetry**.

---

## 1. The problem, whole

Airlock redacts outbound PII with Presidio (placeholder `<EMAIL_ADDRESS_1>`) and
**re-hydrates** the original value into the model's tool-call arguments so the
client can execute the tool. Two distinct risks, often conflated:

1. **The goldmine (data at rest / in memory).** To reverse redaction, Airlock
   holds a placeholder→PII map. Verified finding: that map currently reaches the
   always-on enterprise JSONL log on every PII-bearing request, 30-day retention
   — a concentrated store one file-read turns into a mass-PII breach. *This is a
   live defect; it already sits in `logs/airlock-2026-08-04.jsonl`.*
2. **The egress primitive (authorization).** Because a model can route a
   placeholder into *any* tool argument, and Airlock (client today, executor
   under 0.6.1) executes it, rehydration reconstructs real PII into whatever sink
   the model names. Restricting *which* sink may receive PII is an egress-control
   decision — and a naive static `(tool, path)` allowlist **decays** (too narrow
   → bloat or disablement).

Part A closes the goldmine. Part B authorizes egress without a decaying list.
They are independent and compose.

---

## 2. Part A — no goldmine (data handling)

Detail + file:line: `design-pii-rehydration-goldmine.md` §1–§4, §6.1–§6.3.
Four **stages** (data-handling axis — do not confuse with Part B's *layers*):

| Stage | Invariant | Ship |
|---|---|---|
| **A0 — sink-boundary scrub** | `airlock_pii_map` never reaches a sink. Denylist it at the event-builder snapshot (`request_event.py:176`) + `write_precall_block_record`. | **Emergency, now** (~10 lines). Plus purge the existing Aug-04 log. |
| **A1 — opaque handle (durable target)** | Cleartext map never touches `metadata`; only a random, request-scoped handle does. Map lives in a bounded process-local store, deleted in `finally` on every exit path, TTL-swept for abandoned requests. | Durable design. Closes the debug-dump surface A0 cannot (LiteLLM logs `metadata` wholesale). |
| **A2 — bounded lifetime** | Map dies when hydration ends; ops rule: no LiteLLM detailed-debug with PII on; `ulimit -c 0`. | With A0/A1. |
| **A3 — hydrate a copy** | Telemetry sees the pre-hydration (redacted) response; only the client sees the hydrated one. Stop mutating the shared response object in place. | Soon. |

Irreducible floor: cleartext exists transiently in the store for the request
duration (Python `str`, unzeroizable). Mitigated by scope + lifetime + core-dump
disable — **not** claimed away. No map encryption, no FPE/deterministic tokens
(deterministic tokens are the goldmine, time-shifted — rejected with evidence in
the goldmine note §2-E).

---

## 3. Part B — authorize egress without a decaying list

Detail + citations: `design-rehydration-authorization-maintainability.md`. The
reframe: **do not enumerate `(tool, path)` pairs as the primary structure.**
Decide by `data-class × sink-egress-trust`, use the tool schema to auto-handle
the safe direction, and let explicit lists hold only the residual. Default-deny.

### 3.1 The evaluation order (with the required blocklist)

Per placeholder, per argument, first matching rule wins:

```
0. KNOWN-BAD BLOCKLIST (deny-override)   → DENY, always. [§3.3 — required]
1. Coarse taint: is a placeholder present? → if no PII, nothing to gate.
2. Type-compatibility (asymmetric):
     - type MISMATCH  → SUPPRESS (keep placeholder). zero entries.
     - type MATCH     → eligible; fall through.
3. Sink egress-trust band:
     - round-trip / internal sink → ALLOW.            zero entries.
     - exfil-capable sink         → require step 4.
     - unknown / runtime-discovered → DENY + telemetry. (fail-safe)
4. Residual allowlist: (tool, path, class) listed? → ALLOW; else DENY + telemetry.
```

Layers 1–3 are the maintainability engine: the **common case (well-typed,
round-trip tool) needs zero list entries**, type-mismatches auto-suppress with
zero entries, and the residual allowlist (step 4) only ever accumulates
exfil-capable exceptions — which is exactly where a human *should* make an
explicit least-privilege call. Residual entries carry owner + justification +
expiry + recertification, and are **auto-populated from suppressed-hydration
telemetry** (learn-then-enforce), so the list is built from observed events, not
user complaints.

### 3.2 Why default-deny handles unknown-bad

Anything not affirmatively allowed by steps 2–4 is denied. Unknown/newly
discovered tools default to the exfil band ⇒ deny + telemetry. So **unknown-bad
is safe by construction** — the operator never has to have anticipated it.

### 3.3 The required known-bad blocklist (owner directive)

**A deny-override blocklist for known-bad is required, and it is *not* the
allow-by-default "blocklist fallback."** Two different things share the word:

- **Rejected (allow-by-default fallback):** replace the allowlist entirely, allow
  PII into any sink unless blocked. Still rejected — it is default-*open* on
  every sink nobody has thought about, the exact decay mirror. (Documented as a
  lean-default interim only, `…maintainability.md` §2.8.)
- **Required (deny-override layer, step 0 above):** a small, high-signal veto that
  denies known-dangerous `(tool | path | class)` combos **even if a lower layer
  would allow them.** Its jobs:
  - Globally-dangerous sinks that must never receive PII regardless of band or
    tenant allow-entry (shell exec, arbitrary webhook/HTTP post, unbounded
    `send-to-any-recipient`).
  - Fast incident response: a tool found compromised is blocked immediately,
    without waiting to re-classify its band or prove a negative.
  - Class-specific vetoes (never hydrate `SSN`/`CREDIT_CARD` into *any* tool,
    even a round-trip one).

**It does not have the fallback-blocklist's decay problem, because it is not
trying to be exhaustive.** Default-deny (step 4) already catches everything the
blocklist misses; the blocklist only adds belt-and-suspenders vetoes for things
that are *known* bad and might otherwise be auto-allowed by band. It carries the
same owner/justification/expiry/recert discipline as the residual allowlist.
Net: **allowlist (default-deny) handles unknown-bad; blocklist (deny-override)
handles known-bad; neither must be exhaustive.**

### 3.4 Honest boundaries

- Depends on the tool→egress-band classification (a small `O(tools)` list, not
  `O(tools×args)`), kept fail-safe by unknown ⇒ deny.
- Type-compatibility depends on schema quality; `format` is annotation-only in
  JSON Schema, so Layer 2 is a *narrowing heuristic*, never a proof — used only
  asymmetrically (mismatch→deny; match→merely eligible).
- Does **not** fix a compromised *approved* tool (supply chain). That trust seam
  is 0.6.1 MCP-governance's problem.

---

## 4. Cedar and CaMeL — tiering (owner directive)

Neither is a v1 dependency; both are **later tiers**, for different triggers.
v1 requires no external PDP and no dual-LLM rebuild — it is a modest extension of
the existing `mcp_tool_guard` allowlist + argument-sanitization posture.

| Tier | Mechanism | What it buys | Trigger to adopt | Do NOT |
|---|---|---|---|---|
| **v1 (initial)** | Layered gate §3 as plain in-process config: band map + residual allowlist + known-bad blocklist + suppressed-hydration telemetry | Maintainable authorization, zero-entry common case, lean default (NFR-12) | now | — |
| **Later — Cedar** | Express the *residual* policy (allow + block lists, per-tenant, templated) in AWS Cedar | Cedar's restricted grammar is SMT-decidable, so the Cedar Analysis toolkit can answer **"does this policy change grant any new PII-egress path?"** and "will this refactor break an existing grant?" — the property that keeps a rule set auditable *as it grows* | **Multi-tenant scale** where policy audit becomes a compliance requirement and the residual/tenant policy is large enough that human review can't prove non-regression. Still in-process, opt-in. | Never OPA/Rego (analysis undecidable; built-ins can themselves exfiltrate). Never a required network hop. |
| **Later — CaMeL** | Capability-tag PII at redaction; a taint-tracking interpreter checks the capability at each tool-call sink (dual-LLM: privileged planner + quarantined reader) | Executor-grade injection defense: capability-at-sink instead of a heuristic band — the principled version of §3's Layer 2 | **0.6.1 Airlock-as-executor**, when rehydration→execution is a single in-proxy egress step with no downstream client backstop and the dual-LLM cost is justified | Treat as v1. It is a rebuild with real utility/coverage costs (~⅔ attack coverage on AgentDojo, thin adoption). |

Framing: **the v1 egress-trust band is a pragmatic, legible approximation of
CaMeL's capability check** that ships without the rebuild; **Cedar is how the v1
lists stay provably auditable** if they ever grow past human review. Adopt each
only when its trigger fires; record the trigger so it isn't adopted speculatively.

---

## 5. How to test deny-plus-telemetry before committing (owner directive)

The approach is falsifiable, and it should be **measured in a non-enforcing mode
before enforcement is turned on** — reusing Airlock's existing
**observe → shadow → enforce** guardrail lifecycle, in the dogfood/test
environment with synthetic + canary PII (prod PII is off, so this is isolated and
safe). The gate is built mode-aware from day one.

### 5.1 The modes

- **Observe.** The gate computes its per-layer decision for every rehydration and
  emits value-free telemetry, but **does not change behavior**. Pure measurement
  of what it *would* do — zero user impact, zero new leak.
- **Shadow.** The gate **enforces the deny/suppress side** (fail-safe: no PII to a
  disallowed sink) while **recording would-have-allowed**, so the frustrating
  false-deny side is measured without risking leaks.
- **Enforce.** Full gate.

### 5.2 Pre-registered metrics (from the value-free telemetry)

Register these and the thresholds **before** the run, so enforce-vs-interim is a
data decision, not a post-hoc rationalization:

| # | Metric | Validates | Target |
|---|---|---|---|
| M1 | **Zero-entry rate** — % of rehydrations auto-resolved by Layers 1–3 with no residual entry | the core "common case needs zero entries" claim | high (e.g. ≥90%) |
| M2 | **False-deny on canary-legit** — canary PII seeded in known-legitimate slots that gets suppressed | user-frustration / broken-tool rate | 0 |
| M3 | **Residual growth curve** — distinct exfil-exceptions over the window | maintainability (plateau vs. linear decay) | plateaus below N |
| M4 | **False-allow into exfil** — would-allow hydrations into exfil-band tools | band-classification correctness | ~0 |
| M5 | **Canary egress** — honey-PII rehydrated + routed to an exfil sink | high-fidelity leak signal | 0 |
| M6 | **Unknown-tool rate** — fraction of tools unclassified (→ deny) | classification coverage / operator burden | trending down |

Canary/honey-PII is the load-bearing instrument: seed the corpus with fake PII in
both legitimate slots (M2 must not suppress them) and watch for any egress of
those exact values into exfil sinks (M5 must be 0).

### 5.3 Decision criteria

- **Enforce the layered allowlist** if M1 ≥ threshold, M2 = 0, M3 plateaus < N,
  M4 = 0, M5 = 0. (The claim held; ship §3.)
- **Fall back to the blocklist-interim** (allow-by-default + §2.8 compensating
  controls) *only* if M1 is low or M2 is unacceptable — i.e., the zero-entry
  claim genuinely fails for this deployment's tool mix — and then on a stated
  migration path back to §3.
- **The known-bad blocklist (step 0) and the suppressed-hydration telemetry run
  in every mode and every outcome** — they are not part of what is being A/B'd;
  they are always on.

This is exactly Airlock's "unavailable is never clean / observe→shadow→enforce"
posture applied to the egress gate: never enable enforcement on faith; enable it
on the canary + telemetry evidence.

---

## 6. Build order (reconciled)

1. **A0 sink-boundary scrub + purge the Aug-04 log** (emergency; the live leak).
2. **A1 opaque handle** + A2 lifetime + A3 copy-response (close the goldmine
   properly).
3. **Part B v1 in observe mode**: layered gate + known-bad blocklist +
   suppressed-hydration telemetry, mode-aware, not enforcing. Run the §5
   validation in dogfood.
4. **Flip to shadow, then enforce** per the §5 decision criteria.
5. **Graceful-degrade policy** (goldmine §6.3): `AIRLOCK_PII_FAIL_MODE`
   open|closed for the *redaction-unavailable* case (loud + `airlock_pii_unavailable`
   audit marker); rehydration-unavailable always degrades to the placeholder.
6. **Later tiers** (Cedar / CaMeL) only when §4 triggers fire.
7. **Streaming hydration** inherits every invariant (map never in a sink; handle
   deleted on stream close incl. disconnect; scanner before hydrator).

The whole feature stays **opt-in** (`AIRLOCK_PII_ENABLED`, off in prod today).
A0 is a prerequisite gate on ever enabling redaction in production.

---

## 7. Verification (merged)

From the two source notes, plus the new directives:

- **T1/T2/T4** — canary sweep: `airlock_pii_map` and a canary PII string never
  appear in any serialized sink output or block record (proves A0).
- **T3** — map absent from `metadata` after post-call (A2).
- **T5** — telemetry response stays redacted; returned response has the value (A3).
- **T7** — handle (not map) on the bus; handle not derivable from PII (A1).
- **T8** — handle store cleanup on success / exception / disconnect / TTL; bounded
  under load (A1).
- **T9** — layered gate: mismatch → suppress; round-trip match → allow; exfil →
  deny unless residual-listed; **known-bad blocklist vetoes an otherwise-allowed
  round-trip sink**; every suppression emits a value-free audit event (Part B).
- **T10** — graceful-degrade markers: `open` serves + stamps
  `airlock_pii_unavailable` (survives to audit); `closed` blocks (§5/§6.3).
- **T11 (new)** — mode semantics: observe changes no behavior but emits full
  telemetry; shadow enforces denies but records would-allows; enforce does both.
- **T12 (new)** — canary-legit PII in an allowlisted round-trip slot is **not**
  suppressed (guards M2 / false-deny).

---

## 8. Open owner decisions

1. **`AIRLOCK_PII_FAIL_MODE` default** — `open` (availability, per the graceful-
   degrade directive) vs. `closed` (egress-safety for the compliance persona).
2. **v1 allowlist/blocklist scope** — global first, or per-tenant from the start
   (templated).
3. **Enforce thresholds** — the concrete M1/M3/N numbers in §5.2, ratified before
   the validation run.
4. **Cedar/CaMeL triggers** — accept §4's triggers, or defer the note-only.

Everything above is design only. No source or service has been touched.
