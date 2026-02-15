# Presidio + Think-Slow Investigation

## Scope

This note reviews:

1. How Airlock currently uses Microsoft Presidio.
2. What configuration exists today for spaCy NER vs regex-driven detection.
3. How the "think slow" subsystem can be used to improve spaCy performance without hurting gateway latency.

---

## 1) Current Presidio Integration in Airlock

### Runtime path

Airlock runs Presidio only inside the `AirlockPIIGuard` pre-call guardrail:

- Messages are scrubbed before the upstream LLM call.
- String message content is scrubbed directly.
- Multi-part content only scrubs `{"type": "text"}` blocks and passes non-text blocks through.

Implementation details:

- Presidio is lazy-loaded on first request via `_get_presidio()`.
- `AnalyzerEngine()` and `AnonymizerEngine()` are created once and cached in module globals.
- Entity allowlist is controlled by `AIRLOCK_PII_ENTITIES`.
- Analysis is invoked with `language="en"`.

### Deployment/runtime assumptions

Airlock currently assumes spaCy model availability:

- Docker image installs spaCy and downloads `en_core_web_lg`.
- Local setup docs also instruct model download.

This means first Presidio use incurs cold-start model/engine initialization, then steady-state reuse.

---

## 2) spaCy NER vs Regex Configuration: What Exists Today

### What is configurable now

**Configurable today:** entity types via `AIRLOCK_PII_ENTITIES` only.

There is no first-class Airlock config switch such as:

- `AIRLOCK_PII_MODE=regex_only|hybrid|ner`
- explicit recognizer registry selection
- explicit NLP engine disable/enable for selected routes/tenants

### Effective behavior with current defaults

Default entities are structured PII (`CREDIT_CARD`, `US_SSN`, `EMAIL_ADDRESS`, `PHONE_NUMBER`; plus some examples include bank fields).

For these entity classes, detection is typically pattern/context recognizer heavy (regex + checksums/context) and generally cheaper than free-text NER entities (e.g., `PERSON`, `LOCATION`).

However, Airlock still constructs default `AnalyzerEngine()` and therefore still initializes NLP resources (spaCy stack) on first use. In practice:

- warm-request cost is usually acceptable for structured entities,
- cold-start penalty can still be noticeable,
- adding more NER-heavy entity types later can significantly increase per-request cost.

### Gap summary

Airlock has **entity-level control** but not **recognizer-mode control**. So today you can narrow *what* entities are searched, but not directly force a regex-only execution mode from Airlock config.

---

## 3) Think-Slow Design Relevance

Airlock's slow subsystem (`airlock/slow/analyzer.py`) is currently strong on:

- reliability patterns,
- p95 latency by model,
- cache opportunities,
- trend/hypothesis generation.

But it does **not** currently analyze PII-guard internals (Presidio latency, entity mix, cold starts, or payload risk segmentation).

That creates a blind spot: gateway p95 analysis can tell you *that* latency is high, but not whether Presidio/spaCy work is a contributor.

---

## 4) How Think-Slow Can Improve spaCy Performance

A practical closed-loop optimization plan:

### A. Add PII-stage observability in fast path logs

Add lightweight structured fields for each request after PII guard runs:

- `pii_guard_enabled` (bool)
- `pii_entities_requested` (list)
- `pii_redaction_count` (int)
- `pii_guard_duration_ms` (float)
- `pii_input_chars` (int)
- `pii_mode` (`hybrid` now; future `regex_only`)
- `pii_cold_start` (bool for first execution per worker)

This gives slow analysis direct visibility into Presidio cost vs prompt shape.

### B. Extend slow analyzer with PII dimensions

Add slow analyses to compute:

- P50/P95 `pii_guard_duration_ms` by model/team/route.
- Redaction yield (`pii_redaction_count > 0`) by policy scope.
- Cost-effectiveness: high latency but near-zero redaction routes.
- Input-size relationship: duration vs `pii_input_chars` buckets.

### C. Generate actionable hypotheses

Examples:

- "Route X has p95 PII time 140 ms with <0.2% redaction yield → switch to regex-only for this route."
- "Team Y frequently redacts structured entities only → keep regex recognizers, disable free-text NER entities."
- "Cold starts dominate first request in worker lifecycle → add startup warmup hook."

### D. Turn hypotheses into policy

Use slow findings to drive explicit policy profiles:

- **Low-risk/high-throughput paths:** regex-only profile.
- **High-risk/free-text paths:** hybrid with selective NER entities.
- **Egress-only scanning:** skip ingress/internal hops unless required.

This is the biggest latency lever for LLM gateways: selective scanning scope + recognizer minimization.

---

## 5) Recommended Next Iteration (Low Risk)

1. Introduce optional `AIRLOCK_PII_MODE` with default preserving current behavior.
2. Implement `regex_only` profile for structured entities.
3. Add PII timing/redaction metadata to logs.
4. Add new slow analyzer dimension for PII efficiency.
5. Run a 1–2 week A/B policy trial and compare gateway p95 + redaction recall proxy metrics.

This keeps compliance posture while moving from assumptions to measured tuning.

---

## Bottom Line

- Airlock currently uses Presidio in a lazy-loaded, always-on pre-call guardrail with entity allowlisting.
- Configuration today controls entity types, not explicit regex-vs-NER execution mode.
- The think-slow subsystem is well-suited to become the policy engine for Presidio tuning once PII-stage telemetry is logged.
- The most reliable path to spaCy performance gains is policy-driven selective scanning + recognizer minimization informed by slow-loop evidence.


---

## 6) Stop-Word Identification Research (Regex + spaCy NER)

For an engineering-heavy gateway, a major source of friction is false positives on code/test literals that look like PII.  A practical design is **suppression with safeguards**, not blanket ignore rules.

### A. Candidate stop-word/safe-token classes

1. **Known synthetic examples**
   - `example.com`, `foo@bar.com`, docs/tutorial placeholders.
2. **Test fixture identifiers**
   - common fake values used in integration tests and docs.
3. **Code-context artifacts**
   - package/class names, stack trace fragments, machine-generated tokens that are not user data.

These should be curated as a reviewed list, versioned, and auditable.

### B. Regex path

- Add a **safe-pattern allowlist** stage before expensive entity analysis for structured entities.
- Use strict anchors and context boundaries to avoid over-matching.
- Track counters for "suppressed_by_safe_pattern" to detect abuse or misconfiguration.

### C. spaCy/NER path

- Keep structured entities on pattern recognizers where possible.
- For NER entities (e.g., PERSON/LOCATION), apply post-detection suppression using:
  - token/context checks (code block context, file path context),
  - known safe-token dictionaries,
  - route-level policy (high-risk routes skip suppression by default).

### D. Security guardrails for suppression

- Never apply broad suppression globally across all routes.
- Require security review for new stop-word entries.
- Keep periodic replay tests with red-team prompts to ensure no production data slippage.

### E. Success criteria

- Lower false-positive rate for developer prompts.
- No meaningful regression in true-positive PII detection.
- Lower p95 guardrail latency where suppression enables cheaper processing paths.
