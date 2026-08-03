# Production prompt-injection classifier design

**Status:** Approved — 2026-08-03
**Scope:** 0.5.9 internal milestone; no package-version change or public release

## Decision

Airlock will add a two-tier, input-side prompt-injection control:

1. a local, deterministic **input injection tripwire** for clear known attack
   forms; and
2. an opt-in, provider-managed **Google Model Armor prompt-injection
   classifier** for semantic direct-prompt detection.

The tripwire is a light classifier. Model Armor is the heavy classifier. In
`adaptive` selection, the light classifier runs first and a positive result
short-circuits the heavy call. Otherwise the heavy classifier always runs; it
is never skipped merely because an input is short. This preserves coverage for
short attacks while avoiding a remote call for attacks already decisively
blocked by the local control.

Model Armor is selected because it is a purpose-built service for prompt
injection, jailbreaks, sensitive-data exposure, and harmful-content policies,
and is independent of Airlock's routed model provider. It exposes distinct
user-prompt and document-oriented inspection semantics, which gives Airlock a
path to indirect-injection protection later. See the [Model Armor
overview](https://docs.cloud.google.com/model-armor/overview) and [REST
reference](https://docs.cloud.google.com/model-armor/reference/rest).

## Current state and gap

Airlock already provides meaningful defense in depth:

- PII redaction and keyword policy run before calls.
- The fast guardian detects abusive client behavior rather than content intent.
- MCP tool policy restricts tool names and arguments.
- The response scanner detects known injection, override, exfiltration, and
  tool-call patterns in model and MCP responses.

Those controls do **not** provide semantic input prompt-injection detection.
The response scanner is a post-response, regex-based control, and the semantic
guard's runtime registry currently has no classifiers. The new control fills
that input-side gap; it does not replace response scanning, tool policy, PII
redaction, or model/provider isolation.

Do not claim that Airlock has a semantic input injection classifier until the
adapter is registered in the runtime, covered by production-corpus evidence,
and deployed in the selected mode.

## Why this is the first classifier

Prompt injection is the immediate proxy-level risk that existing controls leave
open: paraphrased instruction overrides, role-play jailbreaks, encoded forms,
and malicious natural-language instructions in request content. A dedicated
classifier covers that class more directly than either alternative:

- **Not an embedding topic filter first.** Topic relevance is valuable for a
  later application-specific policy, but requires a reliable policy/context
  embedding and risks rejecting legitimate exploratory or cross-domain work.
- **Not a generic moderation endpoint first.** Moderation is a different
  policy—harmful-content classification—not evidence of prompt-injection
  coverage. For example, OpenAI's [Moderations API](https://platform.openai.com/docs/api-reference/moderations)
  should remain an optional, separately named content-policy integration.
- **Not a general LLM judge first.** A judge is useful for later escalation and
  for review, but has a larger, less stable policy surface than a dedicated
  injection service.

Azure Prompt Shields is a viable adapter for deployments already invested in
Azure AI Content Safety; it explicitly supports user-prompt and document
attacks. It is not the first implementation because Airlock already has Google
provider infrastructure and Model Armor provides the same targeted capability.
See [Azure Prompt Shields documentation](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/quickstart-jailbreak).

## Runtime contract

### Registration and configuration

Add an idempotent built-in-classifier bootstrap to the semantic-guard startup
path. It must register classifiers exactly once, make their names visible in
startup/health diagnostics, and never silently convert missing setup into a
clean classification result.

The Model Armor adapter is enabled only when all of the following are present:

- `AIRLOCK_MODEL_ARMOR_ENABLED=true`
- `AIRLOCK_MODEL_ARMOR_TEMPLATE` — a full Model Armor template resource name
- application-default credentials or workload identity authorized to use that
  template

Use direct Google authentication and the Model Armor REST API; never send a
classifier request back through Airlock, which would create recursive routing.
No credential belongs in YAML or in the corpus artifact. The adapter may rely
on the existing `vertex` optional dependency for `google-auth`, but must give a
clear disabled/unavailable diagnostic if that extra is not installed.

`AIRLOCK_MODEL_ARMOR_TIMEOUT_SECONDS` defaults to a small bounded value (two
seconds is the initial target). Timeout, authentication, malformed-response,
and service errors are recorded as classifier errors and fail open by default.
They must be observable separately from a clean verdict.

### Classifier result

The adapter returns a `ClassifierResult` with:

- `name`: `model_armor_prompt_injection`
- binary score: `1.0` for detected and `0.0` for not detected
- threshold: `0.5`
- label: `prompt_injection` or `clean`
- measured duration
- safe metadata only: service, template identifier or stable template label,
  API/version identifier when available, verdict kind, and request kind

Raw prompt, document, authentication, request IDs that expose user data, and
provider response bodies must not be written to classifier metadata or JSONL.

The light `input_injection_tripwire` uses the same result contract, has
`ClassifierMetadata(cost_class="light")`, and records only match categories,
never matched text. Model Armor has `cost_class="heavy"`. Both are tagged
`prompt_injection`; neither receives a `min_content_length` skip rule.

### Input boundary

Phase A classifies **current user-originated text** and explicit MCP arguments
as direct input. It must preserve message role/provenance rather than flattening
all message content: system/developer instructions are not attacker input and
are not supplied as the user prompt. Text is classified only after the existing
PII-redaction stage.

Phase B is intentionally separate: classify untrusted retrieved documents and
MCP tool results as documents *before* they re-enter an agent/model context.
That requires explicit provenance at the retrieval/tool boundary. The current
post-response scanner remains the control for those paths until that boundary
is implemented. Do not represent Phase A as full indirect-injection coverage.

### Enforcement contract

The current semantic guard raises whenever a classifier result is blocking.
That must become an explicit semantic enforcement mode:

| Mode | Behavior |
| --- | --- |
| `observe` (initial default) | record verdicts and errors; never raise |
| `shadow` | record `would_block`; never raise |
| `enforce` | raise only for a positive classifier verdict |

The mode belongs to the semantic guard, not to the global weighted enforcer:
the existing weighted signals do not include a semantic-classifier signal.
Metadata must preserve both the classifier verdict and the action actually
taken, so an observed violation cannot be mistaken for a blocked request.

## Evaluation and rollout

1. Implement the bootstrap, tripwire, Model Armor adapter, direct-input
   extraction, bounded timeout/error handling, and mode semantics with unit
   and integration tests using a fake HTTP transport.
2. Run the adapter in `observe` against a redacted, labeled corpus. Preserve no
   raw prompts in Git or logs.
3. Retain an all-versus-adaptive corpus result. It must name the classifier and
   template revision, corpus revision/hash, run time, mode, classifier/error
   counts, verdict totals, mismatches, latency distribution, skips, and
   short-circuit counts.
4. A mismatch means adaptive selection remains opt-in and is investigated; it
   is never authorization to loosen enforcement.
5. Review observed false positives/negatives and failures, promote to `shadow`,
   and only then make an explicit enforcement decision.

The corpus must include labeled direct attacks, benign requests that discuss
security or quote attacks, role-play and paraphrase variants, Unicode/encoded
variants, short attacks, multilingual samples, routine support/code prompts,
and MCP-argument examples. It must be broad enough to exercise both tiers;
the existing five-sample mechanism-only corpus is insufficient.

## Independent review and closeout evidence

The owner approved a bounded independent automated review on 2026-08-03. After
implementation, send a separate-provider review packet through the running
Airlock instance. The packet may contain only the tracked adapter, semantic
guard, direct-input extraction, focused tests, this design, and the redacted
corpus schema. It must exclude credentials, local configuration, operational
logs, and unredacted corpus samples.

Retain the provider/model identity, prompt scope, findings, fixes, and
re-review result in a release-review artifact. This is an independent automated
review, not a substitute for a human review if a human review is later required.

The 0.5.9 internal closeout marker remains prohibited until all of these are
true:

- a real classifier is registered in the shipped runtime;
- production corpus-equivalence evidence is retained;
- the approved independent review is retained and findings are resolved or
  explicitly accepted;
- the final commit is pushed and its GitHub CI run is green.

## Implementation touchpoints

- `airlock/guardrails/semantic.py` — bootstrap, selection, result/action
  metadata, and semantic mode
- `airlock/guardrails/prompt_injection.py` — local tripwire and Model Armor
  adapter
- `airlock/text_extract.py` — role-preserving direct-input extraction
- `tests/test_semantic_guard.py` and new adapter tests — contract, failure,
  privacy, adaptive, and mode coverage
- `dev/corpora/` and `dev/notes/` — redacted corpus manifest and retained
  equivalence/review evidence
- `docs/guide/guardrails.md` — correct public wording once the runtime feature
  exists
