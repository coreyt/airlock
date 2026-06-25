# Design: Mutation & Serving-Backend Transparency

**Date:** 2026-06-24
**Status:** Design complete & codex-reviewed — **0.5.0 transparency workstream**
(branch `feat/0.5.0-resilience-admin`, stacked on the 10 resilience+admin packs).
Plan: `dev/plans/0.5.0-transparency-plan.md`; board:
`dev/plans/runs/STATUS-0.5.0-transparency.md`. Builds on the metadata bus and the
`model_override_headers` flush hook shipped in the same release.
**LiteLLM target:** pinned to **1.89.0** (the `_hidden_params` field contract in §3
and the streaming hooks in §7.5 are verified against this version).
**Scope (proposed):** new `airlock/transparency.py`; touch points in
`airlock/reasoning_effort.py`, `airlock/fast/router.py`, `airlock/fast/guardian.py`,
`airlock/fast/monitor.py`, `airlock/guardrails/*` (pii_guard, enhanced_interceptor,
reasoning_stripper), `airlock/callbacks/model_override_headers.py`,
`airlock/callbacks/enterprise_logger.py`, `airlock/callbacks/metrics.py`, `config.yaml`.
**Traces to:** `dev/user-needs.md` UN-19 (Transparent Request Mutations),
UN-20 (Truthful Serving-Backend Attribution).
**Related:** [design-provider-quota-observability.md](design-provider-quota-observability.md)
(CC-9 `record_type`, `provider_ratelimit`), [design-routing-fanout-guardrails.md](design-routing-fanout-guardrails.md)
(`X-Airlock-Model-Override`), [design-rate-limit-client-errors.md](design-rate-limit-client-errors.md)
(response-header contract).

---

## 1. Summary

Airlock's pitch over plain LiteLLM (or any thin wrapper) is that it is a *control
plane*: it normalizes, routes, guards, and protects. But control without
transparency is a black box. Two concrete gaps undermine that pitch today.

**Gap A — mutations are mostly silent.** An audit of the request path found ~30
distinct mutation sites in 7 categories. Most write *some* ad-hoc `airlock_*`
metadata key, but there is no uniform ledger and no default surfacing contract, so
the client cannot tell from the response what changed. Examples that are fully
invisible to the caller today:

| Mutation | Site | Recorded as | Client sees |
| --- | --- | --- | --- |
| `reasoning_effort` → provider floor / dropped | `reasoning_effort.py:57-66` | (nothing durable) | nothing |
| `drop_params` provider-invalid param removal | LiteLLM layer | nothing | nothing |
| model alias resolution | `guardian.py:281-286` | `airlock_alias` | nothing (DEBUG log) |
| smart / cost-tier / budget routing | `router.py:520-621` | `airlock_routing` | only `X-Airlock-Model-Override`, only if unpinned |
| fallback suppression + `num_retries=0` | `guardian.py:174-178` | `airlock_pinned_request` | nothing |
| system-prompt injection (enhanced profiles) | `enhanced_interceptor.py:32`, `enhanced_passthrough.py:157` | implicit | nothing |
| PII scrub of messages / MCP args | `pii_guard.py:200,344` | `airlock_pii_map` | nothing |
| post-call reasoning-block strip | `reasoning_stripper.py:179,208` | log only | nothing |
| Gemini mode → `reasoning_effort` | `gemini_interface.py:71-73` | `airlock_gemini` | Gemini-only headers |

**Gap B — provider attribution is a guess, not an observation.** `airlock_provider`
is computed by `infer_provider(model_name)` *before* the call (`guardian.py:451`,
`monitor.py:197`, `enterprise_logger.py:437`). That is a prediction from the
requested alias. The same logical model can be served by different backends —
Anthropic native vs AWS Bedrock vs GCP Vertex; OpenAI native vs Azure; Google via
Vertex AI vs AI Studio — and the inference cannot distinguish them. The ground
truth already exists and is thrown away: LiteLLM stamps
`response._hidden_params` with `custom_llm_provider`, `api_base`, `region_name`,
`model_id`, `litellm_model_name`, `received_model_id`, and `response_cost`
(verified against the installed LiteLLM in `.venv`). Because spend and
rate-limit/quarantine accounting key off the *inferred* provider, a same-provider
failover or a router deployment swap is both invisible and mis-billed.

This note proposes one canonical **mutation ledger** and one **served-backend
attribution** pass, surfaced through the transparency plumbing that already
exists (metadata bus → `async_post_call_response_headers_hook` flush → JSONL
record → metrics/TUI), so transparency becomes a default-on benefit rather than a
reverse-engineering exercise.

## 2. Hard design decisions

1. **One ledger, not N more keys.** Introduce a single ordered list
   `metadata["airlock_mutations"]`. Every mutating site appends one record via a
   shared helper. The existing keys (`airlock_routing`, `airlock_alias`,
   `airlock_failover`, `airlock_pii_map`, …) stay for backward compatibility, but
   the ledger is the normalized, authoritative view. Rationale: the present
   sprawl is exactly why surfacing is inconsistent; a second sprawl would not fix
   it.

2. **Default-on, but safe and bounded — with a header-safe surfacing policy.**
   Transparency that is off by default is not a benefit. So `X-Airlock-Mutations`
   and `X-Airlock-Served-By` ship default-on. Three guards make that safe:
   (a) **value-free redaction by construction** — redaction records are created
   only via `record_redaction(field, count, category, …)`; the `Mutation`
   constructor *rejects* `before`/`after` when `op == "redact"` (a validated
   invariant, not a convention). (b) **A header-value allowlist** — the serializer
   surfaces an `after` value **only** for scalar/enum fields on the allowlist
   `HEADER_VALUE_FIELDS = {"model", "reasoning_effort", "fallbacks", "num_retries"}`;
   every other op/field (`inject`, `rewrite`, `redact`, and any non-allowlisted
   field) surfaces as `field=<op>` or `field=<op>(<count>)` only — **never** the
   content, so an injected system prompt or a rewritten message body can never
   appear in a header. (c) **Byte-bounded** — truncate to `…+N more` past the byte
   budget, with the full ledger in the JSONL record. The body envelope
   (`airlock.mutations`) is opt-in via `X-Airlock-Explain: 1` and is **non-streaming
   only** (see Decision 7).

3. **Served truth is read from the response; inference stays pre-call — with a
   streaming-correct source.** Keep `infer_provider()` for routing/policy (it must
   run before a response exists). `attribute_served_backend(response)` extracts the
   served identity tolerantly: it reads `response._hidden_params` for
   `api_base`/`region_name`/`model_id`/`response_cost`, and for the provider it
   reads `_hidden_params["custom_llm_provider"]` **falling back to the wrapper
   attribute `getattr(response, "custom_llm_provider", None)`** — because on the
   streaming path the wrapper carries `custom_llm_provider` as an instance attribute
   from construction (`streaming_handler.py:113`) while it is injected into
   `_hidden_params` only during chunk iteration (`:738/746`). The request record
   gains a `served` block alongside the existing inferred `airlock_provider`; the
   two are explicitly distinct. When the provider cannot be determined (pre-call
   block, transport error, provider unknown at header-flush time), `served` is
   `null`/partial and the record is marked `attribution: "inferred"`; the header is
   simply omitted rather than guessed.

4. **Accounting follows served on success; failure paths use the error's provider.**
   On a successful response, spend is taken from `served.response_cost` and keyed to
   `served.provider` (more accurate than re-derivation). On a **failure/quarantine
   path** there is often no response and no `_hidden_params`: rate-limit/quarantine
   state then keys off the provider parsed from the exception / provider-429
   metadata if present, **else** the inferred provider — it never blocks on a served
   read that does not exist. Accounting attributes to the **final served response**,
   not to intermediate fallback *attempts* (breaker counting of failed attempts
   keeps its existing per-attempted-provider behavior; only spend/served-attribution
   is final-response-only). Gated behind `transparency.attribute_accounting_to_served`
   (default **on**, documented as a bugfix) so operators who built dashboards on the
   old inferred keying can opt out.

5. **The logger attributes independently — no cross-callback ordering dependency.**
   `enterprise_logger._build_record` calls `attribute_served_backend(response)`
   itself and falls back to `kwargs["response_cost"]`, rather than depending on the
   response-headers hook having stashed `served` into metadata first. The header
   hook's metadata stash is a best-effort optimization for the *header only*; the
   authoritative `served`/`mutations`/`attribution` record is finalized in the
   success-logging hook regardless of callback order.

6. **No new request behavior.** Like UN-17, this is observe-only for the request
   path. The ledger and attribution change what is *reported*, never whether a
   request is mutated or where it goes. (Decision 4 changes which counter a number
   lands in, not whether the call happens.)

7. **Streaming is first-class — split surface (see §4.1).** Identity + pre/during-call
   mutations ride response headers (flushed before the SSE body); `response_cost`
   and post-call (reasoning-strip) mutations + the full record ride
   `async_log_success_event` on the assembled response. The `X-Airlock-Explain`
   body envelope is **non-streaming only** — an SSE stream's headers are already
   flushed and the OpenAI chunk schema cannot carry a trailing `airlock.mutations`
   object without breaking clients; for streams the same data is available in the
   headers (pre-call) and the JSONL log (full).

8. **`drop_params` transparency is derived, not hooked.** LiteLLM's
   `drop_params` removal happens inside LiteLLM, so there is no Airlock call-site to
   instrument. Instead, OBS-ledger computes it: pre-call, after provider resolution,
   compare the client-supplied params against
   `litellm.get_supported_openai_params(model=…, custom_llm_provider=…)` (present in
   1.89.0 at `litellm_core_utils/get_supported_openai_params.py`) and record each
   client param absent from the supported set as an `op:"drop"` mutation with reason
   `"provider-unsupported (drop_params)"`. This satisfies UN-19's explicit
   `drop_params` inclusion without depending on LiteLLM internals.

## 3. Data shapes

```python
# airlock/transparency.py

MutationOp = Literal["set", "drop", "clamp", "rewrite", "inject", "redact", "suppress"]
MutationStage = Literal["pre_call", "during_call", "post_call"]

@dataclass(slots=True)
class Mutation:
    field: str                 # "reasoning_effort", "model", "messages", "fallbacks"
    op: MutationOp
    before: Any | None         # omitted/None for inject
    after: Any | None
    stage: MutationStage
    source: str                # "reasoning_effort.normalize", "router.cost_tier", ...
    reason: str | None         # human-readable: "openai has no 'off'; floored to minimal"
    # redact-only, value-free:
    count: int | None = None
    category: str | None = None

    def __post_init__(self):
        # CC-T2 invariant: redaction records can NEVER carry the matched value.
        if self.op == "redact" and (self.before is not None or self.after is not None):
            raise ValueError("redact mutations must be value-free; use record_redaction()")

# Header serializer surfaces an `after` VALUE only for these scalar/enum fields.
# Everything else (inject/rewrite/redact, message/system-prompt content) shows
# `field=<op>` or `field=<op>(<count>)` — never content.
HEADER_VALUE_FIELDS = {"model", "reasoning_effort", "fallbacks", "num_retries"}

@dataclass(slots=True)
class ServedBackend:
    provider: str | None       # custom_llm_provider (or wrapper attr on streams); None ⇒ inferred-only
    api_base_host: str | None  # host only, e.g. generativelanguage.googleapis.com
    region: str | None         # region_name (bedrock/vertex)
    model_id: str | None       # litellm_model_name / received_model_id
    response_cost: float | None # None at header-flush time on streams; final in the log hook
    backend_kind: Literal["native", "gateway", "unknown"]  # derived: bedrock/azure/vertex => gateway
```

Helpers:

```python
def record_mutation(metadata: dict, *, field, op, before=None, after=None,
                    stage, source, reason=None) -> None: ...   # append to metadata["airlock_mutations"]
def record_redaction(metadata: dict, *, field, count, category,
                     stage, source) -> None: ...               # value-free redact record (CC-T2)
def attribute_served_backend(response, *, cost_fallback=None) -> ServedBackend | None:
    ...  # reads _hidden_params; provider falls back to getattr(response,"custom_llm_provider")
def detect_dropped_params(data: dict, model: str, provider: str) -> list[str]:
    ...  # client params absent from litellm.get_supported_openai_params(...) → op:drop (Decision 8)
def mutations_header(ledger: list[Mutation], budget_bytes: int = 256) -> str:
    ...  # allowlist-aware, byte-bounded, `…+N more`
def served_headers(s: ServedBackend) -> dict[str, str]: ...   # {} when provider is None
```

Config (`config.yaml`, all backward compatible):

```yaml
transparency:
  mutation_headers: compact          # off | compact | full   (default: compact)
  served_headers: true               # X-Airlock-Served-By / -Region (default: true)
  explain_body_optin_header: X-Airlock-Explain   # request header that adds body envelope
  attribute_accounting_to_served: true           # fix spend/quarantine keying (default: true)
  mutation_header_budget_bytes: 256
```

Response headers (additive to the §2 contract of the rate-limit design note):

- `X-Airlock-Mutations: reasoning_effort=minimal;model=claude-sonnet;fallbacks=suppressed;pii=redacted(3)` (bounded; `…+N` when over budget)
- `X-Airlock-Served-By: vertex_ai`
- `X-Airlock-Served-Region: us-east5` (only when present)

JSONL request record (extends `enterprise_logger._build_record`):

```jsonc
{
  "record_type": "request",
  "airlock_provider": "anthropic",          // existing: inferred, kept
  "served": {                                // new (UN-20)
    "provider": "bedrock", "api_base_host": "bedrock-runtime.us-east-1.amazonaws.com",
    "region": "us-east-1", "model_id": "anthropic.claude-...", "response_cost": 0.0123,
    "backend_kind": "gateway"
  },
  "attribution": "served",                   // "served" | "inferred"
  "mutations": [                             // new (UN-19), the full ledger
    {"field": "reasoning_effort", "op": "set", "before": "off", "after": "minimal",
     "stage": "pre_call", "source": "reasoning_effort.normalize",
     "reason": "openai has no 'off' enum; floored to minimal"},
    {"field": "messages", "op": "redact", "after": null, "count": 3, "category": "pii",
     "stage": "pre_call", "source": "pii_guard"}
  ]
}
```

## 4. Wiring

- **Append at the source.** Each mutation site records at the point it already
  mutates `data`: `reasoning_effort.py:57` → `record_mutation(op="set")`;
  `pii_guard.py:200` → `record_redaction(count=…, category="pii")` (value-free,
  CC-T2); `guardian.py:174` → `record_mutation(op="suppress", field="fallbacks")`.
  The existing ad-hoc keys remain untouched (CC-T1).
- **Derived `drop_params` (Decision 8).** OBS-ledger calls
  `detect_dropped_params(data, model, provider)` pre-call (after provider
  resolution) and records each unsupported client param as `op:"drop"` — the one
  mutation with no Airlock call-site of its own.
- **`X-Airlock-Explain` body envelope is non-streaming only.** OBS-headers attaches
  `airlock.mutations` to the response body **only** for non-streaming responses with
  the opt-in header; for streaming it is skipped (the data is in the headers + the
  JSONL log — Decision 7).
- **Attribute + emit headers in the response-headers hook.**
  `model_override_headers.py`'s `async_post_call_response_headers_hook` receives
  both `data` (→ metadata → ledger) **and** `response` (→ `_hidden_params`). It is
  the natural point to: (a) call `attribute_served_backend(response)`, store the
  identity part of `served` back into metadata, and emit `X-Airlock-Served-By/-Region`;
  and (b) serialize the *pre/during-call* ledger into `X-Airlock-Mutations`. This
  fires for streaming and non-streaming alike (see §4.1).
- **Log + finalize in the success hook — independently.** `enterprise_logger._build_record`
  adds `mutations`, `served`, `attribution`. It **calls `attribute_served_backend(response,
  cost_fallback=kwargs.get("response_cost"))` itself** rather than depending on the
  header hook having stashed `served` first (Decision 5 — no cross-callback ordering
  assumption). It runs on `async_log_success_event`, which on streaming receives the
  *assembled* response — where `response_cost` and post-call (reasoning-strip)
  mutations are final (see §4.1).
- **Accounting.** On success, `monitor.py` keys spend off `served.provider`/
  `served.response_cost`; on failure/quarantine it uses the error's provider, else
  inferred (Decision 4). It never blocks on a missing served read.
- **Metrics/TUI.** Add `airlock_mutations_total{field,op}` counter and a `served`
  provider label on existing per-provider gauges; TUI Overview gains a "served via"
  column so native-vs-gateway split is visible.

### 4.1 Streaming seam (CC-T6) — resolved against LiteLLM 1.89.0

Streaming forces a split, because HTTP headers are flushed *before* the SSE body.
Verified against the installed LiteLLM 1.89.0 source:

- **Headers work for streams — but read the provider from the wrapper attribute.**
  The proxy calls `async_post_call_response_headers_hook` for streaming success
  (`litellm/proxy/common_request_processing.py:1276`) and passes the returned
  headers into `StreamingResponse(headers=…)` (`:1341`) *before* the generator
  yields — so a header set here reaches the client. **Caveat (verified):** at
  header-flush time the stream wrapper's `_hidden_params` (built at
  `streaming_handler.py:156`) carries only `model_id`/`api_base`/`additional_headers`
  — `custom_llm_provider` is injected into `_hidden_params` *during* chunk iteration
  (`:738/746`), so it is **not** in `_hidden_params` yet. It IS available as the
  wrapper **instance attribute** `response.custom_llm_provider` (set at
  `streaming_handler.py:113`). Therefore `attribute_served_backend` reads the
  provider as `_hidden_params.get("custom_llm_provider") or getattr(response,
  "custom_llm_provider", None)`. With that, `X-Airlock-Served-By` (+ `-Region` when
  present in `_base_hidden_params`) and the pre/during-call `X-Airlock-Mutations` are
  emitted for streaming too; if the provider is still unknown at this point the
  header is **omitted** (not guessed) and the log hook records it.
- **Cost + post-call detail go to the log.** `response_cost` and the post-call
  reasoning-strip mutations are only final at stream end. The orchestrating logger
  hook `async_log_success_event` is invoked with the *assembled* streaming response
  (`litellm/litellm_core_utils/litellm_logging.py:2824-2847`), where
  `_hidden_params` is complete. **The full `served` record (incl. `response_cost`)
  and the complete ledger are finalized into the JSONL record here**, not in the
  header hook.
- **Net contract (CC-T6).** Identity-class transparency (which backend, which
  pre-call mutations) is header-surfaced for *all* responses; value/cost-class and
  post-call transparency is log/metric-surfaced. For non-streaming, both land in
  the same request lifecycle and the header hook already sees a fully populated
  `_hidden_params` (`common_request_processing.py:1459-1488`). `attribute_served_backend`
  must therefore tolerate a partial (`response_cost is None`) read in the header
  hook and a complete read in the success hook — the `ServedBackend.response_cost`
  field is `Optional` for exactly this reason.

## 5. Tests (TDD, RED first)

- `test_transparency_ledger.py` — each mutation site appends exactly one
  well-formed record; `Mutation(op="redact", before=…)` **raises** (CC-T2
  invariant); the matched secret string is absent from the ledger and header.
- `test_served_attribution.py` — crafted `_hidden_params` for each backend
  (anthropic/bedrock/vertex_ai/azure/openai/gemini) → correct `ServedBackend`;
  Google AI-Studio-vs-Vertex disambiguation via `api_base_host`; **streaming case**:
  `_hidden_params` lacks `custom_llm_provider` but the wrapper attribute
  `response.custom_llm_provider` is set → provider still resolved; provider truly
  unknown → `None` and `served_headers()` returns `{}`.
- `test_header_safety.py` — the serializer surfaces `after` values only for
  `HEADER_VALUE_FIELDS`; an `inject`/`rewrite` mutation carrying system-prompt or
  message text renders as `field=<op>` with **no content** in the header; byte-bound
  truncates to `…+N more` with the full ledger still in the record.
- `test_drop_params_detection.py` — `detect_dropped_params` records `op:drop` for a
  client param absent from `get_supported_openai_params` for the resolved provider
  (Decision 8).
- `test_accounting_follows_served.py` — success failover where inferred≠served
  debits the **served** provider's spend; a provider **429 with no response** keys
  quarantine off the error's provider (not a served read); flag off restores old
  behavior.
- `test_logger_independent_attribution.py` — `_build_record` produces `served`/
  `attribution` even when the response-headers hook did **not** run (Decision 5),
  using `kwargs["response_cost"]` as the cost fallback.
- `test_streaming_transparency.py` — served-by header present on a streamed
  response; `airlock.mutations` body envelope is **absent** for streaming even with
  `X-Airlock-Explain` (Decision 7); full ledger + cost land in the JSONL record.
- Regression: response **body** unchanged by default; default-on headers are the
  only wire change (CC-T7); config-free deploy still passes the existing suite.

## 6. Documentation updates

- `docs/guide/observability.md` (new or extend) — the mutation ledger, the
  header contract, the `X-Airlock-Explain` opt-in, and the served-vs-inferred
  distinction; a "why Airlock over plain LiteLLM" framing.
- `docs/guide/routing.md` — cross-link `X-Airlock-Served-By` to the existing
  `X-Airlock-Model-Override` story.
- `docs/reference/response-headers.md` — add the two new headers to the catalog.

## 7. Out of scope / follow-ups

- **True multi-deployment routing** (same `model_name` mapped to several
  backends with LiteLLM router selection). Airlock's config uses distinct aliases
  today (`gemini-3.5-flash` vs `gemini-3.5-flash-vertex`). This note does **not**
  add multi-deployment routing — but the served-attribution layer is explicitly
  designed so that *if* it is added later, "which deployment answered" is already
  captured from `model_id`/`api_base`/`region` with zero further work.
- **Request-body diffing** for messages (beyond redaction counts and system-prompt
  injection markers) — a full structural diff of message content is deferred.
- **Adaptive cooldown from served `response_cost`/headers** — overlaps with the
  rate-limit design note's follow-up; not pursued here.

## 8. Related

- [design-provider-quota-observability.md](design-provider-quota-observability.md) — the `record_type` discriminator and `provider_ratelimit` block this note's `served` field sits beside.
- [design-routing-fanout-guardrails.md](design-routing-fanout-guardrails.md) — `X-Airlock-Model-Override` and fallback suppression, two of the mutations this note makes uniformly observable.
- [design-rate-limit-client-errors.md](design-rate-limit-client-errors.md) — the response-header / body-envelope contract this note extends.
