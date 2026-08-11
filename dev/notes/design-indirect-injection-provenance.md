# Design: indirect-injection and multi-turn provenance boundary

**Status:** 0.5.12 B-0 design complete; implementation requires the recorded
human enforcement decision and an independently reviewed semantic classifier.

## Purpose

Indirect prompt injection enters an agent conversation through material the
caller did not author: tool output, retrieved documents, browser/search results,
files, and prior assistant/tool turns.  Airlock needs one provenance seam for
that ingress and for PII rehydration egress, rather than an unrelated mechanism
for each direction.

The boundary is not a claim that text is malicious. It is a durable statement of
where the text came from and what trust it has at the point Airlock routes it to
a model or tool.

## Decision

Attach a value-free `airlock_provenance` envelope to the canonical request event
and request metadata. It is an ordered list of bounded segments:

```json
{
  "version": 1,
  "segments": [
    {"origin":"caller", "trust":"authenticated", "kind":"message", "index":0},
    {"origin":"tool", "trust":"untrusted", "tool":"web_search", "index":1},
    {"origin":"prior_turn", "trust":"untrusted", "role":"assistant", "index":2}
  ]
}
```

It carries origin, trust class, structural position, declared tool/server, and
opaque correlation IDs only. It never copies message text, prompt fragments,
PII placeholders, reverse-map handles, or classifier explanations containing
payload. The request event snapshots this envelope before fan-out, just as it
does other guardrail decisions.

## Trust classes and default behavior

| Origin | Default trust | Rationale |
|---|---|---|
| Authenticated caller input | `authenticated` | Caller identity is known, not content-safe. |
| Airlock-owned static system policy | `trusted_system` | Versioned local policy only. |
| Tool/MCP output, retrieval, browser/search/file content | `untrusted` | It can contain hostile instructions unrelated to the caller's goal. |
| Prior assistant turn | `untrusted` unless explicitly generated from trusted-system-only context | Models may repeat untrusted instructions. |
| Unknown or malformed provenance | `untrusted` | Fail-safe classification. |

No trust class bypasses PII redaction, egress authorization, tool authorization,
or provider circuits. Provenance is an input to injection policy, not a universal
authorization token.

## Enforcement seam

1. Normalize inbound OpenAI/MCP structures into provenance segments before any
   semantic inspection. Direct caller message text remains identifiable as such;
   tool/retrieval text is marked untrusted.
2. Deterministic checks may inspect all text but preserve the envelope.
3. A selected semantic injection classifier receives the text plus its segment
   trust label. It reports a value-free verdict keyed to segment indices.
4. The policy enforcement point applies the configured mode:
   - `observe`: emit verdict/provenance telemetry; do not alter the request.
   - `shadow`: construct the sanitized/block outcome and report it; release the
     original only where the policy explicitly permits shadow behavior.
   - `enforce`: reject or remove the implicated untrusted segment before provider
     egress, with a typed, non-payload error.
5. The canonical event carries counts, origins, classifier/version, verdict,
   and action. Raw content remains in the existing controlled request fields,
   not duplicated into provenance telemetry.

The PEP must run before provider routing and before any side effect. It must not
be implemented as an asynchronous callback that races the upstream request.

## Composition with PII egress provenance

PII rehydration answers: “may a value of this class cross into this tool argument
now?”  Injection provenance answers: “did this instruction originate outside the
trusted control boundary?”  Both consume the same identity, event, audit, and
mode model, but their enforcement actions remain independent:

- an untrusted segment can be blocked even when it contains no PII;
- a trusted caller value can remain a PII-egress denial for an exfil sink; and
- a PII placeholder/reverse-map handle is never provenance payload.

## Implementation preconditions

Implementation is deliberately gated on two facts that are not yet true:

1. Select and independently review a production semantic injection classifier
   (BLK-001). The current registry is empty; regex response scanning is not an
   input-classifier substitute.
2. Human DECIDE the enforcement contract after the observe measurement: what is
   removed vs. blocked, and which client-visible error/retry semantics apply.

When both are satisfied, implement in this order: normalized segment model and
golden parser tests; value-free event projection; classifier adapter; observe
telemetry; shadow tests; enforce tests; then an isolated real-traffic validation.

## Acceptance criteria for the future implementation

- A tool-output instruction is distinguishable from caller-authored text through
  the whole request lifecycle and event projection.
- Missing/malformed provenance is untrusted, never silently trusted.
- Observe mode is behavior preserving and cannot serialize payload through its
  new telemetry fields.
- Enforce mode stops a selected untrusted instruction before provider egress.
- PII map/handle values cannot appear in any provenance structure, event, or
  typed error.
