# Slice 20 — typed Fast Guardian threat-backoff HTTP 429 status

**Disposition:** implemented locally; no push required for this slice.

## Accepted contract

DFR-34/DAC-34 is ratified in `dev/requirements.md`. `UN-16` remains the
existing user-need authority; draft DUN-33 was merged into it. Both a newly
created threat backoff and an already-active backoff now raise the typed
`AirlockThreatBackoff` exception. Its client response is HTTP 429 with a
minimum-one, whole-second `Retry-After`, `type: airlock_threat_backoff`,
`code: threat_backoff`, and `airlock.source: threat_backoff`.

The exception retains no client identifier or heuristic reason. Its body is a
generic retry message and does not use provider/circuit-breaker headers. The
existing provider-breaker and admission-shed exception/handler contracts remain
separately registered and regression-tested.

## TDD and review evidence

* **RED:** focused tests initially failed collection because
  `AirlockThreatBackoff` did not exist.
* **GREEN:** Guardian, proxy-error, admission, and harness regressions pass:
  `156 passed, 2 skipped`.
* The test suite exposed LiteLLM's guardrail translation before FastAPI's
  subclass handler. The implementation therefore also follows LiteLLM's
  `ProxyException` protocol while remaining a `RateLimitError`; its integration
  test proves the actual translated response is 429 with `Retry-After: 3` and
  the exact OpenAI-shaped error body.
* **Code review:** reviewed the error boundary, integer ceiling/minimum, direct
  FastAPI handler, LiteLLM translation path, redaction surface, and provider /
  admission regression isolation. The only implementation adjustment was the
  explicit LiteLLM protocol noted above.
* `ruff check` and `ruff format --check` pass for all changed Python files;
  `make verify` passes; `mkdocs build --strict` passes.
* The full ordinary `make test` was attempted twice. This execution environment
  terminated each run after roughly one minute at 21–23% with no failure output;
  it did not yield a final result. It remains a release-closeout verification
  item. A standalone MyPy run also reports pre-existing missing stubs for
  LiteLLM/FathomDB imports outside this slice and is not a repository gate.

## Residual risk and rollback

The only nonstandard detail is the narrow dual exception protocol required by
the installed LiteLLM guardrail pipeline; the end-to-end translation test pins
that behavior. Reverting the Guardian/error-handler change restores the former
generic path. No dependency, configuration, provider, secret, TUI/Admin, or
GitHub change was made.
