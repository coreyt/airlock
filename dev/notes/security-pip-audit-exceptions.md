# pip-audit exceptions

`pip-audit` gates CI. Every suppression here is a **deliberate, scoped decision
with a review trigger** — not a way to make the build green. If a finding is
reachable in Airlock's usage, it must be fixed, not listed.

Each entry must record: why it cannot be fixed, why it is not reachable, and the
condition under which the suppression is removed.

---

## cryptography 48.0.1 — PYSEC-2026-3552, 3553, 3554

**Added:** 2026-08-04
**Review trigger:** when `litellm[proxy]` **and** `presidio-anonymizer` both
allow `cryptography >= 50`. Check with
`uv run pip-audit` after any upgrade of either.

### Why it cannot be fixed here

Two core dependencies pin the vulnerable range:

```
litellm[proxy]        cryptography>=48.0.1,<49.0
presidio_anonymizer   cryptography>=48.0.1,<49.0.0
```

The fixes land in 49.0.0 (3553, 3554) and 50.0.0 (3552). Airlock cannot upgrade
without one of: upstream relaxing the pins, forking, or dropping LiteLLM proxy
support or PII redaction. None is warranted for findings that are unreachable
(below). `cryptography` is not a direct Airlock dependency.

### Why they are not reachable in Airlock

Airlock contains **no direct import of `cryptography`** — verified by grep over
`airlock/`. The three findings each require an API Airlock does not use:

| ID | Requires | Airlock |
|---|---|---|
| PYSEC-2026-3552 | Decrypting attacker-supplied PKCS#7 `EnvelopedData` and reflecting the outcome (decryption oracle, also timing-observable) | No PKCS#7 use anywhere |
| PYSEC-2026-3553 | Verifying attacker-controlled X.509 chains with `cryptography`'s verifier (exponential blowup on duplicate self-signed certs) | No X.509 path validation |
| PYSEC-2026-3554 | `cryptography`'s X.509 name-constraint checking (over-broad wildcard SAN accepted) | No X.509 path validation |

Airlock's own cryptographic surface is admin capability tokens in
`airlock/admin/tokens.py`, which use **HS256** — HMAC via PyJWT, not the
asymmetric or certificate code paths these advisories affect.

Transitive users (`msal`, `azure-identity`, `google-auth`, `presidio-anonymizer`)
are reached only through Airlock's own call paths, none of which perform PKCS#7
decryption or certificate-chain verification. TLS termination uses Python's
`ssl` module against OpenSSL, not `cryptography`'s verifier.

### Removal

Delete the `--ignore-vuln` flags from `.github/workflows/ci.yml` and this entry
once the upstream pins allow the fixed version. Do not extend this entry to
cover new advisories — assess each on its own.

### Re-check log

The trigger is checked at each milestone closeout, not left to expire quietly.

| Date | Milestone | Result |
|---|---|---|
| 2026-08-05 | 0.5.10 | **Still required.** `uv run pip-audit` without the flags reports all three findings. Both pins are unchanged: `litellm[proxy]` requires `cryptography>=48.0.1,<49.0` and `presidio-anonymizer` requires `cryptography>=48.0.1,<49.0.0`, against installed `cryptography 48.0.1`. Fixes remain in 49.0.0 (3553, 3554) and 50.0.0 (3552), so the range is still unreachable. Suppressions retained unchanged; reachability analysis above re-confirmed — still no direct `cryptography` import in `airlock/`. |
