# Slice 20 — FathomDB chat readiness status

**Status:** complete. Funded smoke remains a release-closeout action after all
no-credit feature tests are green.

## Ratified scope

- DFR-24/DAC-24: add explicit `gpt-4o-mini` and
  `openai/gpt-4o-mini` aliases backed by `openai/gpt-4o-mini` and
  `OPENAI_API_KEY`; preserve ordinary Airlock chat behavior without fallback.

## Design and code review

The aliases follow Airlock's existing bare/provider-prefixed model-list pattern.
They do not modify the router's fallback map, cost tiers, authentication,
guardrails, transport, or provider code. The capability helper derives
`openai` and `chat` from their normal model-list body, so no new special case
is necessary.

## TDD and verification evidence

| Phase | Command / result |
| --- | --- |
| RED | `uv run --extra test python -m pytest tests/test_config_consistency.py -q -k chat_benchmark_aliases_are_explicit` failed four times because neither config carried the aliases. |
| GREEN | The same command passed: `4 passed, 53 deselected`; it checks root/template parity, model body, environment key reference, `openai` attribution, chat-only capability, and absence from fallback chains. |
| Compatibility | `uv run --extra test python -m pytest tests/test_0_5_2_capability_compat.py -q -k 'capability or alias'` passed: `54 passed`. |
| Quality | Ruff check/format passed for the changed test and strict MkDocs build passed. |

## Release-closeout evidence

After the non-credit release matrix passes, run a manually authorized,
non-sensitive ordinary and streaming request through each documented benchmark
alias. Record only alias, timestamp, HTTP/stream outcome, and safe Airlock
headers—never keys, prompts, completions, or raw provider headers.
