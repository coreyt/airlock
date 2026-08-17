# Slice 30 — configured credential without enabled alias warning status

**Disposition:** implemented locally; no push required for this slice.

## Accepted contract

DFR-36/DAC-36 is ratified in `dev/requirements.md`; draft DUN-35 is addressed
by the existing operator configuration needs rather than a new canonical user
need. At startup, after `.env` loading and before optional provider discovery,
Airlock now emits one advisory, local warning for each recognised provider with
a nonblank recognised credential and no explicit effective alias.

The fixed event is
`airlock.startup.provider_credential_without_alias`. Its output contains only
the canonical provider, `credential_configured=true`,
`configured_alias_count=0`, and `source=startup_validation`. It omits values,
environment-variable names, paths, request content, provider errors, and all
other free-form configuration. The warning changes neither exit status,
routing, discovery, runtime configuration, Admin/TUI state, nor the inference
path.

## Design, B3 resolution, and review

`airlock/startup_validation.py` owns a finite registry of documented provider
credentials and derives effective aliases with the installed LiteLLM's direct,
non-recursive include semantics: direct include order, top-level list extension,
and top-level replacement otherwise. It deliberately does not add a generic
configuration loader. `airlock_provider_for()` remains the standard canonical
provider classifier; local vLLM uses its explicit `backend: vllm` plus
`VLLM_API_KEY` reference because it is OpenAI-compatible on transport.

Code review confirmed the startup call occurs before discovery, emits only the
fixed schema, catches a local validation read failure without leaking its
details, and has no provider/network or configuration mutation path. The
principal residual risk is warning noise from deliberately configured but
unused credentials; remediation is documented (add a reviewed alias or remove
the deployment secret) and warning behavior is advisory.

## RED/GREEN and verification evidence

* **RED:** `tests/test_startup_validation.py` initially failed collection because
  `airlock.startup_validation` did not exist.
* **GREEN:** pure registry, direct include/nested-ignore, every recognised
  provider, vLLM, redaction, and startup fallback/wiring tests pass.
* Focused startup/proxy/config/documentation suite: `94 passed, 1 xpassed`.
* Ruff lint/format, `make verify`, strict MkDocs build, and `git diff --check`
  pass.

No dependency, provider configuration, Admin/TUI, GitHub, or push action was
added. Rollback is removal of the pure module and one proxy startup invocation.
