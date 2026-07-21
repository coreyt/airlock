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

**This is the actual bug.**

```yaml
model: enhanced/gemini-coding
enhanced_profile:
  target_model: gemini/gemini-3.1-pro-preview-customtools
```

The request is really served by `gemini-3.1-pro-preview-customtools`, which litellm
prices at **$2.00 in / $12.00 out per 1M** — and it carries 200K threshold pricing, so
long-context calls cost more still. But `enhanced/gemini-coding` is not in the cost
map, so `response_cost` is 0/None and **every one of those requests records $0.00**.

Consequences: provider budgets under-count, near-limit downgrade routing never
triggers for this path, and MTD/YTD spend reporting is wrong by the full amount.
Aliases affected: `gemini-coding`, `aistudio/gemini-coding`.

**Fix direction — with a correction.** This ticket assumed the fix is "attribution
plumbing". That may be wrong, and 0.5.7 F-4 checks it first:
`EnhancedPassthroughHandler.acompletion` ends with
`return await litellm.acompletion(model=target_model, ...)`, returning the **inner**
response directly. That inner call is against a priced model, so its
`_hidden_params["response_cost"]` may already be correct and may already reach
Airlock's reader. If so there is nothing to plumb — only a regression test to add.
Absence of `enhanced/gemini-coding` from the cost map explains why the *outer* model
has no price; it does not tell us whether the inner cost propagates. Determine that
before writing a fix. Worth checking whether other
custom providers (`tavily`) share the same defect by construction.

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

Answering (2) generically probably fixes P1 and P2 together.
