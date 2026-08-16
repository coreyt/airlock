# Slice 20 — typed Fast Guardian threat-backoff HTTP 429

**Status:** implementation in progress (revised 2026-08-15).

## Prior-slice closure and revision

Slice 10's repository controls are implemented and externally configured on
`main`; its delivery PR still awaits the required independent CODEOWNERS review
and its ordinary CI result. Slice 20 has no runtime, source, or release-order
dependency on that pending merge, so it may proceed locally without a push.

The Slice 3 draft, Slice 4 architecture review, Slice 5 verification matrix,
and Slice 6 owner decision remain applicable. The current code confirms the
identified seam: `AirlockFastGuardian` still raises plain `ValueError` for both
an existing client backoff and a new threat block. Existing
`AirlockProviderBlocked` and `AirlockAdmissionShed` handlers already establish
the correct typed, explicitly registered FastAPI boundary. No dependency or
runtime-version change is needed.

## Ratified contract

The draft DUN-33 is incorporated into existing `UN-16`; it does not create a
new user need. DFR-34/DAC-34 is ratified as follows:

> Fast Guardian threat backoff SHALL raise `AirlockThreatBackoff`, an
> Airlock-owned `RateLimitError`, for both the request that creates a backoff
> and requests rejected during it. The proxy SHALL render it as an
> OpenAI-shaped HTTP 429 with a whole-second, minimum-one `Retry-After`, stable
> `type`/`code` `airlock_threat_backoff`/`threat_backoff`, and
> `error.airlock.source: threat_backoff`. It SHALL expose no client identity,
> threat score, heuristic reason, request content, provider identity, or
> provider-circuit-breaker header. Provider and admission 429 contracts remain
> distinct and unchanged.

## Design and review

`AirlockThreatBackoff(RateLimitError)` will carry only the raw remaining
duration. `retry_after_seconds()` will convert it at the HTTP boundary with
ceiling and a minimum of one, avoiding early retry from fractional duration or
a race at expiry. The guardian will use that exception for both threat paths;
it will not embed duration or client data in the message.

LiteLLM may translate a guardrail exception before FastAPI resolves a subclass
handler. Therefore this type also uses LiteLLM's `ProxyException` protocol and
its direct `to_dict()` shape while remaining a `RateLimitError`; an integration
test covers that translation. This is a compatibility adapter, not a
registration of a catch-all provider-rate-limit handler.

`threat_backoff_response_payload()` and a dedicated FastAPI handler will be
registered specifically by `install_airlock_error_handlers_on_proxy_app`.
Specific registration preserves LiteLLM's treatment of provider 429s and
Airlock admission shedding. The body will use only a generic retry message and
the stable source; its only header will be `Retry-After`.

This is an additive, pre-existing inference-path error translation: it adds no
network call, storage, configuration, credential, dependency, or TUI/Admin
surface. Failure rollback is the isolated revert of the guardian/error-handler
change. Security review finds the principal risk to be client or heuristic
disclosure; the exception intentionally retains neither and tests assert the
response omits them. Architecture review finds no authority or provider
semantic expansion.

## TDD and verification

1. Add focused guardian and proxy-boundary RED tests for typed exceptions,
   ceiling/minimum retry, OpenAI shape, installer registration, and absence of
   sensitive fields; retain provider/admission regression tests.
2. Make the minimal exception, payload/handler, registration, guardian, and
   client-documentation changes.
3. Run focused tests, then relevant rate-limit/error regressions and the
   ordinary repository verification applicable to the changed Python/docs
   surface. Record RED/GREEN evidence and code review in the Slice 20 status.

No push is required for this local implementation slice. A later release
closeout decides whether to publish it with other approved work.
