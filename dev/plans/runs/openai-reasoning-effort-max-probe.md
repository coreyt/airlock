# OpenAI `reasoning_effort=max` provider probe

**Status:** READY — deterministic contract test is in CI; the provider probe is an
explicit, billable operator action.

## Purpose

LiteLLM's current model map does not supply a decisive
`supports_max_reasoning_effort` value for the GPT-5.6 family. Airlock therefore must
not guess whether to accept or reject `max` in P-2 enforcement. The test in
[`tests/test_openai_reasoning_effort_live.py`](../../../tests/test_openai_reasoning_effort_live.py)
has two deliberately separate evidence paths:

1. `test_max_probe_contract_is_direct_and_unmodified` is a deterministic mocked test
   and runs in normal CI. It proves the provider probe sends `reasoning_effort: "max"`
   unchanged, directly to OpenAI, using the concrete model and a bounded request.
2. `test_openai_accepts_max_reasoning_effort` is a real provider probe. It runs only
   when the operator supplies both a funded `OPENAI_API_KEY` and the explicit
   `AIRLOCK_LIVE_OPENAI_REASONING_EFFORT=1` opt-in. It is not CI and must never be
   enabled by repository configuration.

The probe is direct on purpose: sending it through LiteLLM/Airlock could cause a
missing capability bit to drop the parameter before OpenAI sees it, yielding false
success evidence.

## Normal CI acceptance

```bash
uv run pytest tests/test_openai_reasoning_effort_live.py -q
```

Expected result: the mocked contract test passes and the billable test is skipped.
This validates the probe's wire contract; it does **not** settle provider support.

## Provider evidence procedure

Use a funded key with permission for the target model. The default is the configured
concrete provider model `gpt-5.6-sol`; override only to test another concrete OpenAI
model deliberately.

```bash
AIRLOCK_LIVE_OPENAI_REASONING_EFFORT=1 \
uv run pytest tests/test_openai_reasoning_effort_live.py \
  -q -s -k openai_accepts_max_reasoning_effort
```

The test sends one non-streaming request with a 64-token output ceiling. It prints
only the model and OpenAI request id—never the key.

Interpret the outcome narrowly:

| result | P-2 treatment |
|---|---|
| HTTP 200 | Record the model and request id below; make `max` supported for that concrete model in the enforcing design and tests. |
| OpenAI parameter-validation 4xx | Record the model and request id below; keep `max` unsupported for that concrete model and test the resulting Airlock 400. |
| auth, quota, rate-limit, network, or timeout failure | **Inconclusive.** Do not change validation behavior; retry later with a funded, authorized key. |

Do not generalize one model's result to a different OpenAI model family. A later
enforcing implementation must encode support at the same model granularity that the
evidence covers.

## Results

| date | model | result | OpenAI request id | recorded by |
|---|---|---|---|---|
| | | | | |
