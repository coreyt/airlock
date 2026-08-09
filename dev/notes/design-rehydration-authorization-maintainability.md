# Design — Rehydration Authorization Without a Decaying List

Status: **DRAFT, do not commit without owner review.**
**Folded into `design-pii-rehydration-primary.md` (2026-08-08) — that note is
authoritative. This remains the Part-B detail reference (mechanism survey with
citations, layered-gate rationale, Cedar/CaMeL/blocklist evidence).**

Scope: the *authorization* decision inside `airlock/guardrails/pii_guard.py`
rehydration — "may this PII placeholder be re-hydrated into *this* tool-call
argument?" — and specifically how to express it so it is **not** an unwieldy
static allowlist.

Feeds: **§6.4 of `dev/notes/design-pii-rehydration-goldmine.md`** (the
hydration-allowlist trade-off analysis and owner decision D-2). That note chose
"(a) tool+path, default-deny, per-tenant-ready, paired with suppressed-hydration
telemetry." This note stress-tests that choice against the owner's maintainability
critique, surveys the alternatives with citations, and refines the chosen design
into one whose **common case needs zero list entries**. It does not overturn
D-2; it makes D-2 survivable.

Research date: 2026-08-08. All external URLs accessed that day; publication dates
noted where the source exposes one. Evidence-thinness is flagged inline.

---

## 1. The decay problem, stated crisply

Rehydration is a **PII-egress capability**: because a model can route a
placeholder (`<EMAIL_ADDRESS_1>`) into *any* tool argument, and Airlock (today
via the client, under 0.6.1 as the executor) then *executes* that tool, the
guardrail itself reconstructs real PII into whatever sink the model names. The
control must gate release like an egress control, not merely restrict a surface.

The proposed control is a per-`(tool, JSON-Pointer argument-path)` **allowlist**,
default-deny (`design-pii-rehydration-goldmine.md` §6.2, D-2). The owner's
critique — which is correct — is that a static enumerated allowlist **decays**
along a predictable arc:

1. **Starts too narrow.** Cold-start with an empty list means legitimate tool
   calls receive placeholders instead of real values — re-creating the exact
   "unusable tool argument" defect rehydration was built to fix (§6.4 #2). Users
   hit friction the moment they use any not-yet-listed tool.
2. **Grows to relieve the friction.** Each break adds an entry. In an MCP/agent
   world with many or *runtime-discovered* tools (0.6.1), the list cannot even
   pre-name the tools it must cover.
3. **Ends in one of two failure states, both of which defeat the control:**
   - an **unwieldy, un-auditable** list nobody can reason about ("where can PII
     flow now?" becomes unanswerable), or
   - the administrator **disables it entirely** to stop the complaints.

The symmetric fallback — allow-by-default with a **blocklist** of dangerous
sinks — has a mirror-image decay: you cannot enumerate every dangerous sink,
new exfil tools leak until someone blocks them, and the blocklist is default-open
on exactly the tools you have not thought about yet.

The design question is therefore not "which list?" but **"can the authorization
decision be expressed so that the list holds only rare exceptions — so it stays
short and auditable by construction?"** The specific hypothesis to validate or
refute: *is there a mechanism where the common case needs zero list entries*
(e.g. schema-type matching auto-authorizes email→email-typed-arg), so the list
never accumulates the routine cases?

---

## 2. Mechanism survey (with maintainability verdict + sources)

The single most important cross-cutting finding, and it recurs in every
literature below: **no mechanism eliminates maintenance — each relocates it**,
from "curating a list" to "curating attributes / a scoring model / a classifier
/ recertification workflow." The useful question is therefore *where* each
mechanism relocates the cost and whether the relocated form stays **auditable**
("can we still answer *where can PII flow?*"). NIST names this exact trap for
attribute rules: auditing "who has access" becomes a *simulation* you must run,
not a list you can read (SP 800-162 §3.1.2.3, below).

### 2.1 Policy-as-code / ABAC / ReBAC — express the rule, not the list

| Mechanism | Replaces the list with | Maintainability verdict |
|---|---|---|
| **OPA / Rego** | expressive Datalog rules | **Reject as the primary gate.** Rules compress N entries, but Rego is Turing-adjacent and **not soundly analyzable** — program equivalence is undecidable, so you cannot prove "this policy edit opens no new PII-release path." Perf is worst-case exponential; and OPA built-ins (`http.send`) can themselves exfiltrate, which is disqualifying for a control whose job is to *stop* egress. |
| **AWS Cedar** | small, non-Turing-complete grammar (RBAC+ABAC+ReBAC) | **The one bright spot for auditability-at-scale.** Cedar's restricted expressiveness buys a **decidable, sound, complete SMT encoding** (Lean-proved); the Cedar Analysis toolkit answers exactly the anti-decay questions — "does this change grant any new permission?", "will this refactor break existing access?". This is the property that keeps a rule set auditable *as it grows*. Caveat: the released CLI is "proof-of-concept," and analyzability is *bought* by giving up expressiveness (rich computation over the argument value may not encode). **Optional-heavier tier only** (see §4), never a default dependency. |
| **Zanzibar / ReBAC** | relationship tuples + rewrite rules | Powerful when release-eligibility is naturally a *graph* (sink belongs to a tool owned by a team cleared for purpose P). Per-request decisions are traceable; but reverse enumeration ("list every sink that can now receive PII") needs graph expansion, and there is no soundness proof over the config language. Operationally heavy (consistency tokens). **Overkill for a self-hosted proxy.** |
| **ABAC generally (NIST SP 800-162)** | Boolean attribute rules | The best-cited statement of *why the naive rule approach loses auditability.* ABAC gives "more efficient administration" and handles the "unexpected user," but "an ABAC system may not lend itself well to conducting [before-the-fact] audits efficiently … requires … a simulation of the access control request for every known subject." **The cautionary primary source, not a recommendation.** |

Sources: Cedar — PACMPL/OOPSLA https://dl.acm.org/doi/10.1145/3649835, extended
https://arxiv.org/abs/2403.04651; Cedar Analysis (AWS Open Source Blog,
2025-06-16) https://aws.amazon.com/blogs/opensource/introducing-cedar-analysis-open-source-tools-for-verifying-authorization-policies/.
OPA hardening (CNCF, 2025-03-18)
https://www.cncf.io/blog/2025/03/18/open-policy-agent-best-practices-for-a-secure-deployment/.
Zanzibar (USENIX ATC '19)
https://research.google/pubs/zanzibar-googles-consistent-global-authorization-system/.
NIST SP 800-162 (pub Jan 2014, upd 2019-08-02)
https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-162.pdf.

### 2.2 Type- and purpose-based authorization — "allowlist by construction"

This is where the "zero-entry common case" hypothesis lives.

- **Schema-type matching (the candidate):** hydrate an `EMAIL_ADDRESS`
  placeholder only into an argument the tool's own JSON Schema types as
  `format: "email"`. When the tool is well-typed, this needs **zero list
  entries** — the tool schema *is* the policy. **Verdict: a valid default-deny
  *narrowing heuristic*, but not a sound authorization gate.** It rests on two
  weak legs:
  1. **`format` is annotation-only by default in JSON Schema.** Draft 2020-12
     §7 makes the Format-Annotation vocabulary REQUIRED-as-annotation and the
     Format-*Assertion* vocabulary OPTIONAL and "disabled by default"; assertion
     support is thinly implemented. So a slot typed `format:"email"` may never
     have been validated as one, and the *majority* of real tool args are
     `type:"string"` with no `format` at all — for which the heuristic either
     denies (breaks legit hydration) or falls back to a list (defeats the goal).
     https://json-schema.org/draft/2020-12/json-schema-validation
  2. **"Right type, wrong field" is invisible to type.** An attacker-steered
     `from_address` / `reply_to` / `webhook_url` is *also* a valid email/URI, so
     type-match happily hydrates the real value into the exfil slot. Type says
     nothing about *whose* value or *which* sink. The most on-point recent paper
     (Liao, "Auditing Provenance Sensitivity in LLM Agent Action Selection,"
     arXiv:2607.20827, 2026-07-23) makes this precise with an email example that
     mirrors ours: "Authority is target-specific … a stale address … directly
     competes with the **recipient argument**"; topical/type relatedness "alone
     is … insufficient." https://arxiv.org/html/2607.20827v1 (recent, largely
     un-peer-reviewed — cite as framing, not established practice).

  **Net:** schema-type matching genuinely dissolves the list *in the safe
  direction* (type-mismatch → auto-suppress; well-typed round-trip → auto-allow)
  but **must not be used to auto-authorize hydration into an exfil-capable sink
  just because the type matches.** Used asymmetrically it is powerful; used
  symmetrically it is unsound. This asymmetry is the crux of the §3
  recommendation.

- **Data-class matching (Google DLP):** classify by infoType, then de-identify.
  Tellingly, DLP does **not** gate *re-identification* by data class — it gates
  by *key possession + IAM role*. This is direct evidence *against* "data-type
  alone authorizes egress": even the most mature reversible-PII system treats
  the class as necessary-not-sufficient and authorizes the *reveal* by
  role/capability. (last reviewed 2024-06-07)
  https://docs.cloud.google.com/architecture/de-identification-re-identification-pii-using-cloud-dlp

- **Purpose-Based Access Control (PBAC):** bind each release to a declared
  *purpose* checked against the data's *intended purpose* in a hierarchy
  (Byun & Li, VLDB Journal 2008 https://link.springer.com/article/10.1007/s00778-006-0023-0;
  SACMAT 2005 https://dl.acm.org/doi/10.1145/1063979.1063998). Conceptually the
  best fit for a PII-egress gate and it aligns the control with the legal basis
  (GDPR Art. 5(1)(b) purpose limitation, https://gdpr-text.com/read/article-5/).
  **But purpose is unverifiable declared intent** — the model/tool *asserts* a
  purpose; nothing proves it. So PBAC is a useful *labeling/audit* layer for the
  multi-tenant compliance persona, **not** the enforcement mechanism. (Real
  implementations — Immuta, Velotix — are vendor-documented, not peer-reviewed.)

### 2.3 Provenance / taint / information-flow control

- **The clean conceptual answer, and why it does not deploy inline.** IFC tags
  the PII once and lets a lattice decide the sink (Denning 1976,
  https://dl.acm.org/doi/10.1145/360051.360056; DIFC / Jif, Myers & Liskov,
  https://www.cs.cornell.edu/andru/papers/iflow-tosem.pdf). Rehydration *is a
  declassification*, and DIFC's lesson is that declassification must be an
  explicit, owner-authorized act — which supports default-deny. **The blocker:
  fine-grained taint does not survive an LLM transformation.** Microsoft's FIDES
  ("Securing AI Agents with Information-Flow Control," arXiv:2505.23643,
  2025-05-29) treats the model as a black box and assigns outputs the *join
  (union)* of all input labels — sound but **over-taints**, so it cannot say
  "*this* `<EMAIL_1>` landed in *this* arg," which is exactly the slot decision
  we need. Fine-grained provenance-through-LLM work (NeuroTaint,
  arXiv:2604.23374, 2026-04-25) is **offline/post-hoc audit, not an inline
  gate.** (Both 2026 arXiv, un-peer-reviewed; FIDES quotes were via a fetch
  summarizer — verify wording before quoting verbatim.)

- **CaMeL — the closest architectural analog, and it validates the reframe.**
  Google DeepMind's "Defeating Prompt Injections by Design" (arXiv:2503.18813,
  2025-03-24, rev 2025-06-24): a privileged LLM emits a plan and never sees
  untrusted data; a quarantined LLM sees untrusted data but *cannot call tools*;
  a **taint-tracking interpreter enforces capability policies at the point of
  each tool call**, blocking sensitive data from flowing to unauthorized sinks.
  This is precisely "tag the value with a capability, check it at the sink" — no
  enumerated sink list. Honest limits: utility cost, ~two-thirds attack coverage
  on AgentDojo, and thin real-world adoption. **The north star for 0.6.1
  (Airlock-as-executor), not a v1 dependency.**

- **What Airlock already has for free:** the *placeholder token itself is a
  coarse taint tag that survives the LLM* — the model echoes
  `<EMAIL_ADDRESS_1>` verbatim into an argument, so Airlock always knows a
  PII value is being placed (that is what makes any gate possible). What does
  *not* survive is fine-grained "which source field → which sink." So Airlock
  can do coarse taint (present today, implicitly) but must not pretend it has
  per-value provenance.

### 2.4 Learn-then-enforce / policy mining — auto-populate the list

- **AWS IAM Access Analyzer** generates least-privilege policies from CloudTrail
  activity (launched 2021-04-07,
  https://aws.amazon.com/blogs/security/iam-access-analyzer-makes-it-easier-to-implement-least-privilege-permissions-by-generating-iam-policies-based-on-access-activity).
  The honest, AWS-stated caveat: the output is a **draft** — it emits resource
  *placeholders* a human must fill, coverage is uneven, and data-plane events
  are missed. Academic policy/role mining agrees (Sanders et al., ACSAC 2019,
  https://www.acsac.org/2019/program/final/1/92.pdf; least-privilege assignment
  is NP-hard). K8s/eBPF profile generators (Cilium/Tetragon observe-mode ≥2 wks,
  https://tetragon.io/docs/concepts/tracing-policy/mode/; seccomp generators)
  carry the same "you must exercise every legitimate path or enforcement breaks
  it later" burden.
- **Verdict: strongest *auditability* win of the survey, and the operational
  key to a fail-closed allowlist.** It does **not** reduce net maintenance — it
  *relocates* it to "re-mine on drift + ratify drafts" — but it converts the
  list from folklore into **evidence-backed entries traceable to observed
  events.** For Airlock this is the §6.4 #2 "suppressed-hydration telemetry"
  idea, correctly identified: build the residual list from a suppressed-events
  feed, not from user complaints.

### 2.5 Risk-adaptive / context-aware — gate only the high-risk minority

- **NIST RAdAC** (Risk-Adaptable Access Control) — authorization "takes into
  account operational need, risk, and heuristics" (CSRC glossary, sourced to
  SP 800-95 / SP 800-160 Vol.2 Rev.1,
  https://csrc.nist.gov/glossary/term/risk_adaptive_adaptable_access_control;
  ABAC formalization, Kandala/Sandhu, ARES 2011,
  https://profsandhu.com/cs6393_s19/ARES11-RAdAC-final.pdf). BeyondCorp /
  Gartner CARTA push the same idea: allow low-risk by default, gate only
  high-risk. (CARTA is Gartner-paywalled — secondary sources only; treat as
  directional.)
- **Verdict: the family that most directly shrinks the list** — if most flows
  are low-risk they need *no entry*, only high-sensitivity × low-trust flows do.
  But maintenance moves into the **scoring model**, and the recurring weakness is
  **auditability/opacity** ("why blocked?") plus gaming (stay under the
  threshold) and mislabeled-sensitivity silent release. For Airlock, use a
  *coarse, legible* risk axis (sink egress-trust), not an opaque ML score, so we
  keep the auditability we would otherwise lose.

### 2.6 Just-in-time / graduated-trust — first-use approve, then persist

- Mobile runtime permissions are the best-documented analog **including its
  failure**: ask-on-first-use avoids the giant upfront list, but Felt et al.
  (SOUPS 2012) found only **17% of users attend** to prompts and **~3%
  comprehend** them, and Wijesekera et al. (USENIX Security 2015,
  arXiv:1504.03747) found the same permission is invasive in ~⅓ of *contexts* —
  so "approve once, persist forever" is context-blind and TOCTOU-prone. PAM JIT
  (Azure PIM) mitigates with **time-bound activation + expiry**.
- **Verdict: best at cold-start / not-breaking-tools**, good provenance per
  entry, but the dominant, empirically-quantified failure is **prompt fatigue**
  → reflexive approvals → the allowlist fills with rubber-stamps and degrades to
  allow-all (this *is* the owner's "admins disable it" endpoint, arrived at one
  click at a time). Usable in Airlock only if approvals are **rare** (which the
  §3 design ensures by auto-handling the common case) and **expire**.

### 2.7 How real detokenization / egress systems fight list decay (the analog to mine)

This is the closest real-world analog and the survey's most actionable finding.
Mature reversible-PII and egress systems **do not keep a smarter list** — they
**reframe** so there is little to enumerate:

- **Authorize by `role × data-class`, reference the class not the sink.**
  HashiCorp Vault Transform: detokenize = `decode`, gated by `role →
  allowed_roles → transformation` (a small two-sided allowlist), under
  path-policy (https://developer.hashicorp.com/vault/docs/secrets/transform).
  Skyflow: column/row PBAC (RBAC+ABAC), reusable named policies attached to
  roles, plus **graduated reveal** — `detokenize()` returns full / masked /
  redacted by role, so reveal is not binary
  (https://docs.skyflow.com/docs/governance/overview). Microsoft Purview DLP:
  policy references a **Sensitive Information Type** (the class), one SIT drives
  blocking across all channels — you enumerate *channels*, never *destinations*
  (updated 2026-06-26,
  https://learn.microsoft.com/en-us/purview/dlp-learn-about-dlp).
- **Collapse the list with trust-domain boundaries.** PCI-DSS scopes cleartext
  PAN by **CDE membership**, not an endpoint list — anything that can detokenize
  is *in* the sensitive domain and inherits its controls (practitioner summaries
  of v4.0; normative doc paywalled). This is the regulatory statement of "a sink
  that can receive PII is, by that fact, inside the trust boundary."
- **Fight decay with per-entry owner + justification + expiry + mandatory
  recertification.** Firewall rule-lifecycle tooling (Tufin: "recertification is
  the only required workflow," rules carry expiry/owner,
  https://www.tufin.com/blog/automating-rule-recertification-management;
  AlgoSec ties each rule to a business application so decommissioning the app
  auto-revokes the rule). OAuth scope-creep is the anti-pattern to design
  against — an append-only grant list that only grows because removal is scary
  (https://auth0.com/blog/oauth2-access-tokens-and-principle-of-least-privilege/).
- **Templated policies** replace N per-principal entries with one rule over a
  stable identity attribute (Vault policy templating,
  https://developer.hashicorp.com/vault/tutorials/policies/policy-templating) —
  the anti-sprawl primitive for the multi-tenant case (§4).

### 2.8 Making default-allow safe (the blocklist fallback's compensating controls)

If the allowlist is judged too heavy and Airlock stays allow-by-default, the
mature answer is a **detection layer instead of enumeration**:

- **Anomaly/behavioral detection on egress (UEBA):** baseline normal PII-egress
  per client/tool, flag deviations; consolidates DLP egress alerts
  (Palo Alto/Exabeam UEBA primers). Fails on low-and-slow ("boiling frog") and
  cold-start.
- **Rate-limit sensitive-data egress per client/tool:** a single per-principal
  budget replaces per-sink rules; positioned explicitly against exfiltration
  (Cloudflare advanced rate limiting,
  https://developers.cloudflare.com/waf/rate-limiting-rules/best-practices/).
  Fails on low-and-slow; threshold trades leak-cap vs legitimate-burst breakage.
- **Taint / cross-trust-domain flow blocking:** CaMeL (§2.3) — block the
  *known-dangerous class* (PII into an exfil-domain sink) without enumerating
  every sink.
- **Canary / honey-PII:** seed the corpus with fake PII; any rehydration+egress
  of those exact values is high-fidelity proof of misuse (Acalvio/CounterCraft
  canary-token references). Purely detective; misses a careful attacker.

**Verdict on the blocklist fallback: it is strictly weaker** (new exfil tools
leak until noticed; detection is post-hoc), and is defensible only as an
*interim lean-default* with the compensating controls above **plus** the same
suppressed-/emitted-hydration telemetry, on a stated migration path to §3. It is
*not* the recommendation.

---

## 3. Recommendation — a maintainable allowlist (chosen over the blocklist)

**Headline: a maintainable allowlist is achievable, and the "common case needs
zero entries" claim holds — but only when the gate is *layered* and type-matching
is used *asymmetrically*.** This validates and refines
`design-pii-rehydration-goldmine.md` §6.4's choice (a); it does not switch to the
blocklist. The blocklist design (§2.8) is documented as the lean-default interim,
explicitly labeled weaker.

The reframe, drawn from §2.7: **do not enumerate `(tool, path)` pairs as the
primary structure. Decide by `data-class × sink-egress-trust`, use the tool
schema to auto-handle the safe direction, and let an explicit list hold only the
residual — the exfil-capable exceptions.** Four layers, evaluated per placeholder
per argument, default-deny:

### Layer 0 — coarse taint is already present (keep it)
The placeholder token survives the LLM verbatim, so Airlock always knows *that* a
PII value is being placed into an argument. This is the free primitive every
layer below builds on. Do **not** claim per-value provenance (it does not survive
the model, §2.3).

### Layer 1 — type-compatibility as an *asymmetric* narrowing filter (zero entries)
Using the MCP tool's JSON Schema when available:
- **type-MISMATCH → auto-suppress** (keep the placeholder). Hydrating an
  `EMAIL_ADDRESS` into a `command`, `path`, or arbitrary free-text `body` field
  is denied with *no list entry and no PII leak* — the safe, correct default.
  This alone removes the large class of "model routed PII somewhere structurally
  wrong."
- **type-MATCH → *eligible*, not authorized.** A type-compatible slot is a
  *candidate* for hydration but is handed to Layer 2 — because "right type,
  wrong field" (§2.2) means type-match into an exfil sink is exactly the attack.

This is "allowlist by construction from the tool schema" used only in the
direction it is sound. It needs **zero list entries** for the common case and
degrades safely when the schema is absent/loose (unknown type ⇒ treated as
untyped ⇒ Layer 2 decides).

### Layer 2 — sink egress-trust (RAdAC-style; shrinks the list to exceptions)
Classify each tool (not each argument) into a small, *legible* egress-trust band
— the coarse risk axis of §2.5, deliberately not an opaque score:
- **Round-trip / internal sink** — the tool returns data to the same domain the
  PII came from (e.g. a corporate calendar/search over the user's own data). Not
  really egress. **Auto-allow** hydration into type-eligible args. **Zero
  entries.**
- **Exfil-capable sink** — the tool can send data to an arbitrary external
  destination (http fetch/post, send-email-to-arbitrary-recipient, shell,
  webhook). Hydration here is real egress. **Requires an explicit Layer-3 entry.**
- **Unknown** (newly seen / runtime-discovered tool, no classification) —
  **default to exfil-capable = deny**, and emit a suppressed-hydration event so
  the operator classifies it. Unknown is safe by construction; this is the
  answer to 0.6.1's runtime-discovered-tools problem.

Most real tool traffic is round-trip, so Layer 2 also clears the common case
with zero entries. The tool→band classification *is* a small list, but it is
`O(tools)` not `O(tools × args)`, it is a legible trust judgment (not a
per-argument micro-decision), and unknown defaults safe — so it does not decay
dangerously.

### Layer 3 — the residual allowlist (short, recertified, auto-populated)
The only explicit `(tool, path)` entries that ever exist are **exfil-capable
sink × the specific argument a legitimate workflow needs real PII in** (e.g.
"`send_invoice`, `/to_address`, EMAIL"). Everything routine was handled by
Layers 1–2. Keep this list maintainable using the §2.7 discipline:
- **Auto-populate from suppressed-hydration telemetry** (§2.4, §6.4 #2): every
  suppressed hydration emits a **value-free** audit event ("tool X, path Y,
  class EMAIL, band=exfil, not allowlisted"). Operators ratify entries from this
  feed — learn-then-enforce — not from user-reported breakage. This is what
  turns a fail-closed list from painful into self-documenting.
- **Every entry carries owner + justification + expiry**, with periodic
  **recertification** (Tufin/AlgoSec model). Entries default to expire, so the
  list cannot silently grow forever; decommissioning a workflow/tenant
  auto-revokes its entries.
- **Class-scoped, not value-scoped**: entries name the *data class* permitted at
  the sink (EMAIL vs SSN), echoing Vault/Skyflow role×class. An SSN does not
  ride an EMAIL entry.
- **Schema revalidation stays a secondary integrity check, never the gate**
  (§6.2 already states this).

### Why the "zero-entry common case" claim holds — and its honest boundary
It **holds**: for a well-typed, round-trip tool (the common case), Layers 1–2
authorize hydration with **no list entry**, and for a type-mismatched placement
they auto-suppress with **no list entry**. The list only ever accumulates
**exfil-capable exceptions** — which is precisely where a human *should* be
making an explicit least-privilege decision anyway. The residual is therefore
short *by construction* and auditable (each entry is an exfil-egress grant with
an owner and an expiry).

The claim's **boundary**, stated plainly:
1. It depends on the tool→egress-band classification (Layer 2). That is a small
   list that could decay — mitigated by *unknown ⇒ deny + telemetry*, so decay
   is fail-safe, not fail-open.
2. Type-compatibility (Layer 1) depends on tool schema quality; untyped tools
   fall through to Layer 2, where they are safe but coarser. `format` being
   annotation-only (§2.2) means Layer 1 is a *heuristic filter*, never a proof.
3. None of this fixes a *malicious/compromised approved tool* (supply chain) —
   the allowlist reduces, does not eliminate, egress risk (§6.4 #5). That trust
   seam is 0.6.1 MCP-governance's problem, not this note's.

### Minimum shippable subset (respecting NFR-12 lean default)
No external PDP, no OPA/Cedar required by default:
- v1: Layer 0 (already present) + Layer 2 built on the **existing
  `mcp_tool_guard` tool allowlist as the trust signal** (tools already permitted
  are the trust catalog; extend it with an egress band) + Layer 3 as a small
  config list, default-deny for exfil/unknown bands + suppressed-hydration
  telemetry. This is a modest extension of Airlock's existing MCP allowlist +
  argument-sanitization posture (same trust model, one more surface).
- v1.1: Layer 1 type-compatibility when MCP schemas are available (pure
  refinement; auto-suppresses obvious mismatches, further shrinks Layer 3).
- Optional-heavier tier (only if a deployment demands provable policy audit at
  multi-tenant scale): express the residual policy in **Cedar** (§2.1) to get
  decidable "does this change grant new egress?" checks. Never Rego (undecidable
  analysis). Never a required network hop.

---

## 4. Degradation & composition with 0.6.0 tenancy and 0.6.1 MCP-executor

- **Post-call budget / lean default (NFR-12).** All layers run **post-call**
  (off the <1ms admission path), and are pure local computation — schema lookup,
  a band map, a list check. **No always-on policy service is required**; the
  Cedar option is opt-in and still in-process. This satisfies "moderate compute
  OK, no external PDP by default."
- **0.6.0 multi-tenant.** Airlock already has authenticated tenant identity
  (`key:<last8>`). Make Layer 3 (and the Layer-2 band map) **per-tenant-ready
  but templated** — one rule keyed on tenant identity (Vault-templating model,
  §2.7) rather than N copies of the list, so tenancy multiplies *scope* without
  multiplying *entries*. Suppressed-hydration telemetry and recertification are
  per-tenant. PBAC/purpose labels (§2.2) can ride here as an audit annotation for
  a compliance persona, not as the gate.
- **0.6.1 Airlock-as-MCP-executor.** This is where the control *fully matters*:
  today the client executes tool calls (its own tool permissions are a
  backstop), but once Airlock executes, rehydration→execution is a single
  in-proxy egress step with no downstream gate. The layered design is built for
  this: **unknown/runtime-discovered tools default to the exfil band = deny +
  telemetry**, so a newly discovered tool cannot silently receive PII. CaMeL
  (§2.3) is the north-star architecture for that milestone (capability-at-sink);
  the Layer-2 egress-trust band is a pragmatic, legible approximation of CaMeL's
  capability check that Airlock can ship without a dual-LLM rebuild. The
  allowlist should land **with or before** Airlock-as-executor, not after
  (consistent with §6.4).
- **Degradation policy.** Inherit §6.3: if rehydration/classification is
  unavailable, **keep the placeholder** (functionality degrade, no PII egress —
  safe). The failure mode of *this* control is always fail-closed-on-egress by
  construction, which is the correct asymmetry for a PII gate.

---

## 5. Sources (primary where possible; access date 2026-08-08)

Policy-as-code / ABAC / ReBAC
- Cedar: https://dl.acm.org/doi/10.1145/3649835 · https://arxiv.org/abs/2403.04651 · Cedar Analysis (2025-06-16) https://aws.amazon.com/blogs/opensource/introducing-cedar-analysis-open-source-tools-for-verifying-authorization-policies/
- OPA hardening (CNCF, 2025-03-18): https://www.cncf.io/blog/2025/03/18/open-policy-agent-best-practices-for-a-secure-deployment/
- Zanzibar (USENIX ATC '19): https://research.google/pubs/zanzibar-googles-consistent-global-authorization-system/
- NIST SP 800-162 (2014, upd 2019-08-02): https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-162.pdf

Type / purpose / provenance
- JSON Schema Validation Draft 2020-12 §7 (format vocab): https://json-schema.org/draft/2020-12/json-schema-validation
- Liao, provenance sensitivity in agent action selection (arXiv:2607.20827, 2026-07-23, un-peer-reviewed): https://arxiv.org/html/2607.20827v1
- Google DLP de/re-identification (last reviewed 2024-06-07): https://docs.cloud.google.com/architecture/de-identification-re-identification-pii-using-cloud-dlp
- PBAC: Byun & Li VLDB Journal 2008 https://link.springer.com/article/10.1007/s00778-006-0023-0 · SACMAT 2005 https://dl.acm.org/doi/10.1145/1063979.1063998 · GDPR Art. 5 https://gdpr-text.com/read/article-5/
- Denning lattice (CACM 1976): https://dl.acm.org/doi/10.1145/360051.360056 · Myers & Liskov DLM/Jif: https://www.cs.cornell.edu/andru/papers/iflow-tosem.pdf
- FIDES (arXiv:2505.23643, 2025-05-29, quotes via summarizer — verify): https://arxiv.org/abs/2505.23643 · NeuroTaint (arXiv:2604.23374, 2026-04-25): https://arxiv.org/abs/2604.23374
- CaMeL (arXiv:2503.18813, 2025-03-24 rev 2025-06-24): https://arxiv.org/abs/2503.18813

Learn-then-enforce / risk-adaptive / JIT
- AWS IAM Access Analyzer policy generation (2021-04-07): https://aws.amazon.com/blogs/security/iam-access-analyzer-makes-it-easier-to-implement-least-privilege-permissions-by-generating-iam-policies-based-on-access-activity
- Policy mining (Sanders et al., ACSAC 2019): https://www.acsac.org/2019/program/final/1/92.pdf
- Cilium/Tetragon observe-mode: https://tetragon.io/docs/concepts/tracing-policy/mode/
- NIST RAdAC (glossary → SP 800-95 / SP 800-160 v2r1): https://csrc.nist.gov/glossary/term/risk_adaptive_adaptable_access_control · ARES 2011 formalization: https://profsandhu.com/cs6393_s19/ARES11-RAdAC-final.pdf
- Felt et al. SOUPS 2012 (permission attention/comprehension): https://dblp.org/rec/conf/soups/FeltHEHCW12.html · Wijesekera et al. USENIX Sec 2015 (arXiv:1504.03747): https://arxiv.org/abs/1504.03747
- Azure/Entra PIM (JIT): https://learn.microsoft.com/en-us/entra/id-governance/privileged-identity-management/pim-configure

Detokenization / DLP / egress + blocklist compensations
- Vault Transform: https://developer.hashicorp.com/vault/docs/secrets/transform · Vault policies: https://developer.hashicorp.com/vault/docs/concepts/policies · templating: https://developer.hashicorp.com/vault/tutorials/policies/policy-templating
- Skyflow governance/redaction: https://docs.skyflow.com/docs/governance/overview
- Microsoft Purview DLP (updated 2026-06-26): https://learn.microsoft.com/en-us/purview/dlp-learn-about-dlp
- Tufin rule recertification: https://www.tufin.com/blog/automating-rule-recertification-management · AlgoSec app-centric recert: https://www.algosec.com/webinar/firewall_rule_recertification
- OAuth scope creep: https://auth0.com/blog/oauth2-access-tokens-and-principle-of-least-privilege/
- Cloudflare rate-limiting vs exfil: https://developers.cloudflare.com/waf/rate-limiting-rules/best-practices/
- Canary/honeytokens: https://www.acalvio.com/resources/glossary/what-you-need-to-know-about-canary-tokens-acalvio/

Evidence-thinness flags: the 2026 arXiv provenance papers (2607.20827, 2604.23374)
and FIDES are recent/largely un-peer-reviewed — cited as emerging framing, not
established practice; FIDES quotes came via a fetch summarizer and should be
verified against the PDF before any verbatim use. CARTA and PCI-DSS specifics
rest on secondary/practitioner sources (the normative originals are
paywalled). PBAC "real implementation" claims lean on vendor docs
(Immuta/Velotix). Several vendor doc pages expose no publication date.
