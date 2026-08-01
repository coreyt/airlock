# Ticket: models recording $0.00 spend

**Raised:** 2026-07-20 (from the 0.5.6 GPT-5.6 work)
**Status:** **SCHEDULED — 0.5.7 F-4** (`dev/plans/0.5.7-plan.md`). See that item for
the investigation-first approach; the framing below is partly superseded — see the
correction under P1.
**Severity:** medium — one real accounting hole, one minor

## Correction to the original framing

An earlier note claimed "seven models silently cost $0.00". That was wrong: it
counted aliases rather than distinct bodies and included a commented-out template
example. The real number is **6 bodies, of which only 2 are actually a problem.**

## Legitimately $0 — no action needed (4)

Self-hosted vLLM on `http://192.168.1.45:8000/v1`. There is no upstream invoice, so
$0 is the correct recorded cost:

| body | aliases |
|---|---|
| `openai/gemma4-31b` | `gemma-4`, `vllm/gemma-4` |
| `openai/kimi-dev-72b` | `kimi-dev`, `vllm/kimi-dev` |
| `openai/qwen3-32b` | `qwen3-32b`, `vllm/qwen3-32b` |
| `openai/qwen3.6-27b` | `qwen36-27b-vllm-batch`, `qwen3.6-27b`, `vllm/qwen3.6-27b` |

The only nuance is that "free" and "unknown" are indistinguishable in the data today.
Worth an explicit `airlock_unpriced: true` marker eventually so reports can say
"self-hosted, no cost" rather than implying a $0 API bill — but nothing is being
mis-billed.

## P1 — `enhanced/gemini-coding` hides real Gemini 3.1 Pro spend

**Investigation result (0.5.7 F-4): this is not an Airlock accounting bug.**

```yaml
model: enhanced/gemini-coding
enhanced_profile:
  target_model: gemini/gemini-3.1-pro-preview-customtools
```

The request is really served by `gemini-3.1-pro-preview-customtools`, which LiteLLM
prices at **$2.00 in / $12.00 out per 1M** and carries 200K threshold pricing. Although
`enhanced/gemini-coding` itself is not in the cost map, the provider returns the priced
inner response unchanged. Its `_hidden_params["response_cost"]` reaches Airlock's
adapter and fast monitor, which records the cost against Gemini provider spend.

`tests/test_enhanced_cost.py` pins both a normal target cost and a higher
long-context-derived value through the provider and accounting sink. No alias-level
price or response-cost restamping is necessary; adding either would duplicate and risk
drifting from LiteLLM's target-model pricing. Aliases covered: `gemini-coding` and
`aistudio/gemini-coding`.

**Note:** this is *not* GPT-5.6-related and predates that work. It surfaced only
because the 5.6 audit enumerated unpriced bodies.

## P2 — `tavily/web-search` has a real per-search cost

Tavily bills per search credit, not per token, so it will never appear in litellm's
token-based cost map. It currently records $0.00.

**Fix direction:** a flat per-request cost. litellm supports
`input_cost_per_request`; whether Airlock's cost path honours it for a custom provider
is unverified. Lower priority than P1 — the per-search cost is small — but it is a
genuine spend that is currently invisible.

## Why this wasn't fixed inline

It needs a design decision, not a lookup patch:

1. Should a self-hosted model record `$0.00` or an explicit "unpriced" state? They are
   not the same thing, and only one of them is honest.
2. Should custom providers inherit their target's pricing automatically (fixing P1
   generically) or declare it per-entry?
3. Do per-request costs flow through `response_cost` on the custom-provider path at
   all, or only token-based ones?

Answering (2) is no longer needed for enhanced models; the existing inner-response
path already provides it. P2 remains a separate custom-provider investigation.
