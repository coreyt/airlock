# Airlock Project Plan

## Objective

Make Presidio "gateway-grade" for Airlock by reducing unnecessary latency while preserving protection against production data leakage.

## Current State (from investigation)

- Presidio runs in a pre-call guardrail with lazy initialization.
- PII entity scope is configurable via `AIRLOCK_PII_ENTITIES`.
- There is no explicit runtime mode switch for `regex_only` vs hybrid (regex + spaCy NER).
- The slow analyzer does not yet include PII-stage performance/effectiveness analysis.

## Workstreams

### 1) PII Runtime Modes

**Goal:** introduce explicit execution profiles without changing default behavior.

- Add optional env var `AIRLOCK_PII_MODE` with initial values:
  - `hybrid` (default; current behavior)
  - `regex_only` (structured entities only; no free-text NER entities)
- Add startup validation/logging so invalid mode values fail safe to `hybrid`.
- Document mode semantics and recommended usage by risk profile.

**Exit criteria**
- Existing deployments behave unchanged when `AIRLOCK_PII_MODE` is unset.
- Operators can switch to `regex_only` without code changes.

### 2) PII Telemetry for Slow-Loop Optimization

**Goal:** make Presidio cost measurable per request.

- Capture and log:
  - `pii_guard_enabled`
  - `pii_mode`
  - `pii_entities_requested`
  - `pii_input_chars`
  - `pii_redaction_count`
  - `pii_guard_duration_ms`
  - `pii_cold_start`
- Ensure telemetry is structured and available in JSONL logs.

**Exit criteria**
- A full day of production logs includes these fields for PII-scanned calls.
- p50/p95 PII timing can be computed from logs without extra instrumentation.

### 3) Slow Analyzer: PII Efficiency Dimension

**Goal:** convert telemetry into policy recommendations.

- Add PII analysis functions in `airlock/slow/analyzer.py`:
  - latency distribution by model/team/route
  - redaction yield (detections per scanned request)
  - size-to-latency curve by input length buckets
  - low-yield/high-cost outlier detection
- Emit hypotheses with confidence and concrete policy actions.

**Exit criteria**
- `airlock-analyze` includes at least one new PII-specific optimization section.
- Analyzer outputs route/team candidates for `regex_only` trials.

### 4) Stop-Word / Suppression Research and Rollout

**Goal:** reduce false positives in engineering traffic while preventing production-data leakage.

- Build a curated suppression taxonomy (reviewed by security + platform):
  1. **Engineering literals:** placeholders like `example.com`, `foo@bar.com`, `123-45-6789` test fixtures.
  2. **Code artifacts:** package names, class names, UUID-like synthetic IDs in tests.
  3. **Documentation tokens:** RFC examples, known fake card ranges, tutorial snippets.
- Implement two suppression paths:
  - **Regex pipeline:** pre-normalization + explicit safe-pattern allowlist before expensive recognizers.
  - **spaCy/NER pipeline:** post-entity filtering with token/context-based suppression rules.
- Add guardrails to prevent risky over-suppression:
  - never suppress high-risk entities in high-risk routes by default,
  - require explicit security approval for new suppressions,
  - keep per-suppression hit counters for auditability.

**Exit criteria**
- False-positive rate in engineering prompts decreases measurably.
- No statistically significant drop in true-positive detection on canary eval set.

### 5) Policy-Driven Rollout

**Goal:** safely deploy performance improvements.

- Define policy tiers:
  - Tier A (high risk): hybrid scanning with minimal suppression.
  - Tier B (normal): hybrid scanning + approved suppression.
  - Tier C (low risk/high throughput): regex-only + strict structured entities.
- Run 1–2 week A/B or phased rollout with success gates:
  - gateway p95 reduction target
  - redaction-yield non-regression target
  - incident count remains unchanged

**Exit criteria**
- Measured latency improvement with acceptable security metrics.
- Final policy profile defaults approved by security and platform owners.

## Milestones

1. **M1 — Observability first:** telemetry fields land, no policy change.
2. **M2 — Analyzer extension:** PII dimension added to `airlock-analyze`.
3. **M3 — Runtime mode switch:** `AIRLOCK_PII_MODE` shipped behind default `hybrid`.
4. **M4 — Suppression framework:** stop-word/safe-token controls with audit trail.
5. **M5 — Controlled rollout:** policy tiers deployed and validated.

## Risks and Mitigations

- **Risk:** over-aggressive suppression hides real PII.
  - **Mitigation:** route-based policy tiers, canary tests, security approval workflow.
- **Risk:** mode changes create inconsistent behavior across teams.
  - **Mitigation:** central policy config + explicit defaults + change logs.
- **Risk:** telemetry overhead increases request latency.
  - **Mitigation:** keep fields lightweight and derived in-process.
