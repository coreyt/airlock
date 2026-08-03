# Refactoring analysis — 2026-07-31

**Status:** Accepted historical assessment. Revisit only when a scoped feature
touches one of the identified areas; this is not an active rewrite plan.

## Verdict

Airlock does not need a rewrite. The earlier structural work has resolved the
highest-risk debt: LiteLLM internals are isolated in `litellm_adapter.py`, the
`fast` to `guardrails` import boundary is tested, request telemetry is built
once and projected to sinks, and the former `state.py` monolith is split into
core, spend, MCP, and persistence modules.

Future refactoring should be narrow, behavior-preserving, and scheduled around
feature work rather than as a standalone rewrite.

## Recommended targets

| Priority | Target | Evidence | Recommended shape | Timing |
|---|---|---|---|---|
| High | `fast._state_core.StateStore` | The coordinator remains about 650 lines and owns client/model state, provider protection, admin mutations, MCP state, and JSONL projection. | Keep the existing facade; extract provider-protection and admin/projection collaborators behind it. | After the active 0.5.9 closeout; do not combine with unrelated delivery work. |
| High | `AirlockFastGuardian.async_pre_call_hook` | The hook is about 308 lines and combines admission, threat assessment, aliasing, routing, fallback policy, provider protection, and metadata stamping. | Extract ordered, small policy-step helpers with explicit inputs/outputs; retain its externally observed order. | After the active 0.5.9 closeout, when the request path changes for a feature. |
| Medium | TUI panes | `OverviewPane` and `ConfigPane` are roughly 685 and 636 lines and mix UI mutations, polling, and transformation. | Extract view-model/query helpers when changing a screen; preserve Textual thread-safety boundaries. | Opportunistic. |
| Low | `cli.main` | Parser construction and command dispatch occupy about 458 lines. | Move parser construction into per-command registration helpers. | Opportunistic; no runtime urgency. |

## Explicit non-targets

- Do not reopen the LiteLLM anti-corruption layer, RequestEvent/recorder, or
  `fast`/`guardrails` boundary without concrete evidence of a new problem.
- Do not replace `StateStore` wholesale: its existing injection facade and
  tests are useful compatibility seams.
- Do not mix a structural refactor with unrelated admission or model-resolution
  feature work.

## Guardrails for any refactor

1. Preserve request-pipeline ordering and OpenAI-compatible behavior.
2. Add characterization tests before moving logic, especially for 429s,
   response headers, model overrides, and provider quarantine.
3. Keep `airlock.fast` free of imports from `airlock.guardrails`.
4. Run the focused test family first, then the normal non-live suite.
