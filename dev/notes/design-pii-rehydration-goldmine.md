# Design — PII Rehydration Without a Goldmine

Status: **DRAFT, do not commit without owner review.**
**Folded into `design-pii-rehydration-primary.md` (2026-08-08) — that note is
authoritative. This remains the Part-A detail reference (verified threat model
with file:line, opaque-handle, graceful-degrade).**
Scope: `airlock/guardrails/pii_guard.py` rehydration mechanism + the telemetry
sink pipeline it feeds. Companion to `dev/design-note-pii-rehydration.md`.

> **§6 amends this note (2026-08-08).** A second independent review confirmed
> §1's critical finding and sharpened three areas; the owner then took
> decisions. §6 is authoritative where it conflicts with §2/§3: it **supersedes
> the option-F verdict** (opaque handle is now the durable target, not
> rejected), **supersedes option G** (rehydration becomes opt-in per approved
> tool+path, not merely "tool-args-only"), and **adds a graceful-degrade
> policy** and the **hydration-allowlist trade-off analysis**. Read §1 for the
> verified threat model, then §6 for the decided design.

**Definition.** A *goldmine* is any concentrated, long-lived, or loggable store
of cleartext PII — or a stable placeholder↔PII mapping — that turns one exploit
into a mass-PII breach.

**Headline finding (verified in code, not run live): the goldmine already
exists.** The cleartext reverse map `airlock_pii_map` is written to the
always-on enterprise JSONL log on every PII-bearing request, alongside the
redacted messages it reverses, and is retained for 30 days by default. The
chain is traced file:line in §1. The fix is small (a sink-boundary scrub) and
should ship before any other rehydration work.

---

## 1. Threat model — concrete exposure surfaces, ranked

### S1 — CRITICAL (confirmed by code reading): the map is persisted to the enterprise JSONL sink

The full chain, every link verified:

1. `airlock/guardrails/pii_guard.py:233` — pre-call stores the cleartext map:
   `data.setdefault("metadata", {})["airlock_pii_map"] = mapping`.
2. The map is **never removed** — no `pop`/`del` of `airlock_pii_map` exists
   anywhere in `airlock/` (grep-verified). It lives in `metadata` for the whole
   request lifecycle, including the logging callbacks.
3. `airlock/callbacks/request_event.py:120-125` — the event builder merges
   top-level and `litellm_params` metadata (deliberately, so guard mutations
   are never lost), then
   `airlock/callbacks/request_event.py:176` — snapshots **every** `airlock_*`
   key: `guardrail_meta = {k: v for k, v in metadata.items() if k.startswith("airlock_")}`.
   `airlock_pii_map` matches this prefix filter.
4. `airlock/callbacks/projections.py:46` — `project_enterprise` spreads
   `**event.guardrail_meta` into the record, next to the redacted
   `messages` (projections.py:35). One JSONL line therefore contains both the
   redacted text and the key that reverses it — self-contained reconstruction.
5. `airlock/callbacks/enterprise_logger.py:344-349` — the enterprise sink's
   `record_event` calls `project_enterprise` then `_write_log`, which appends
   to `logs/airlock-YYYY-MM-DD.jsonl` (`enterprise_logger.py:194-207`).
6. This path is **live**: `airlock/callbacks/recorder.py:32` registers the
   enterprise sink always-on and first; `recorder.py:81` (`_self_register()`)
   installs the recorder callback into all four LiteLLM callback lists at
   import.
7. The only scrub on this path, `_redact_record`
   (`enterprise_logger.py:176-191`), redacts fields listed in
   `AIRLOCK_LOG_REDACT_FIELDS` — **empty by default**
   (`.env.example:42-43`), and not set in `config.yaml`/`config.local.yaml`
   (grep-verified). So by default nothing removes the map.
8. Retention: `AIRLOCK_MAX_LOG_DAYS` default 30 (`enterprise_logger.py:118-119`).

**Exploit:** any single file-read primitive — path traversal, log-shipping
misconfiguration, backup exposure, a SIEM forwarder pointed at `logs/`, or a
compromised analysis tool reading the JSONL — harvests every redacted value
for up to 30 days of traffic in one pass. This is the goldmine definition
verbatim. Note the irony: the redaction feature *created* the concentrated
store; without rehydration the JSONL held placeholders only.

Caveat on claim strength: this is confirmed by static tracing of the live
wiring (recorder self-registration + always-on enterprise sink + projection
spread). I did not run the proxy (out of scope for this review). Verification
test T1/T4 in §4 makes the claim executable.

### S2 — HIGH (confirmed by code reading): pre-call block records carry the map too

`airlock/callbacks/enterprise_logger.py:241-262` —
`write_precall_block_record` independently snapshots and spreads
`**guardrail_meta` (same `airlock_` prefix filter, line 241-243) into a JSONL
record. It is called from `airlock/fast/guardian.py:127` when provider
protection blocks a request. The PII guard runs **first** in pre-call
(`config.yaml:870-874`, "must run first"), so a PII-bearing request that is
subsequently blocked writes its cleartext map to the same log file. Same
exploit as S1; lower volume (blocked requests only).

### S3 — MEDIUM-HIGH (timing-dependent; needs empirical check): hydrated cleartext in the logged `response` field

`_hydrate_tool_calls` mutates the response object **in place**
(`pii_guard.py:332`: `fn.arguments = json.dumps(args)`). The `RequestEvent`
carries the **raw response object by reference**
(`request_event.py:268`, design §3.10), and the enterprise projection
serializes it at dispatch time (`projections.py:36`) inside a background
thread/task (`request_event.py:398-414`,
`asyncio.to_thread`). Whether serialization runs before or after the post-call
hydration hook is a race; whenever it runs after, the JSONL `response` field
contains cleartext PII in tool-call arguments — even with S1 fixed. I could
not determine the ordering statically and did not run the proxy;
this needs the T5 test in §4. Even if the ordering "usually" logs
pre-hydration, an in-place mutation shared with the telemetry path is a latent
leak, and it should be made deterministic (Stage 2, §3).

### S4 — MEDIUM: unbounded in-request lifetime + third-party observers of `metadata`

The map is never popped after hydration, so every post-call actor sees it:
other post-call guards (`config.yaml:919-939` — response scanner, reasoning
stripper both receive `data`), LiteLLM's own failure/exception handling, and —
important operationally — LiteLLM's verbose/debug modes, which dump request
metadata to stdout. Running the proxy with detailed debug logging while PII
redaction is active would print cleartext maps to whatever captures stdout.
Airlock cannot scrub inside LiteLLM's own loggers; the only robust fixes are
shortening the map's lifetime (pop after hydration) and an ops invariant
(no detailed-debug in production with PII enabled).

### S5 — LOW-MEDIUM: streaming deferral does not avoid the goldmine

Streaming hydration is deferred (`pii_guard.py:12-16`, design note §7). The
warning at `pii_guard.py:243-249` frames the consequence as a correctness gap
(client receives placeholders). Security-wise the map is still **built and
still attached to metadata** for streaming requests, so S1/S2 fire regardless.
Deferring streaming deferred the *benefit*, not the *risk*.

### S6 — LOW (accepted): cleartext in process memory

The mapping dict holds original values as Python `str` objects for the request
duration. Python strings are immutable and cannot be zeroized; copies are made
freely (slicing at `pii_guard.py:98` creates one per entity). Memory scraping
or a core dump of the proxy process can recover in-flight PII. For a
single-tenant self-hosted proxy this is an accepted residual risk mitigated by
scope + lifetime minimization (§3) and ops hygiene (`ulimit -c 0` /
`kernel.core_pattern` disabled on the prod host), not by attempting
zeroization — see §5 research notes.

### Properties that are already right (preserve these)

- **Per-request placeholder numbering.** Counters reset each request
  (`pii_guard.py:196-197`), so `<EMAIL_ADDRESS_1>` is not a stable
  cross-request pseudonym. No cross-request correlation goldmine exists.
  Dedup is within-request only (`pii_guard.py:100-104`). Tested at
  `tests/test_pii_guard.py:931-932` (two requests → independent maps).
- **Airlock's own log lines are value-free.** `pii_redacted` logs count +
  entity types only (`pii_guard.py:234-238`); `pii_hydrated` logs count only
  (`pii_guard.py:276`). Tested (test-plan F2/F3).
- **The mutation ledger is value-free by construction.**
  `record_redaction` routes through a ctor that rejects values
  (`airlock/transparency.py:119-140`, guard at transparency.py:41).
- **The map does NOT reach fathom, s3, or sql.** `project_fathom`
  (`projections.py:108-152`) copies named fields only and reads
  `guardrail_meta` solely for a skip flag (`fathom_logger.py:147`);
  `project_s3` / `project_sql` (`projections.py:59-105`) carry no
  guardrail_meta at all. The goldmine is the enterprise JSONL path only
  (S1/S2) — confirmed by reading all four projections.

---

## 2. Options

| # | Option | Security gain | Latency / complexity cost | Residual risk | Verdict |
|---|--------|---------------|---------------------------|---------------|---------|
| A | **Sink-boundary denylist scrub** — strip `airlock_pii_map` in `build_request_event` (before the guardrail_meta snapshot) and in `write_precall_block_record` | Kills S1 + S2 outright; enforced in code, not env config | One `set` membership check; ~zero latency; ~10 lines | In-process exposure (S3–S6) remains | **Do now** |
| B | **Bounded map lifetime** — `metadata.pop("airlock_pii_map", None)` at the end of the post-call hook | Shrinks S4 window; defense-in-depth for S1 (cannot rely on it alone — callback timing vs. hook is racy, and failure paths skip the success hook) | One line | Map still present pre-hydration; failure paths keep it until GC | **Do now** (with A, not instead of A) |
| C | **Hydrate a copy, not the shared object** — deepcopy the response (or the tool-call subtree) in the post-call hook, hydrate the copy, return it; the telemetry-referenced object stays redacted | Kills S3 deterministically, no race analysis needed | One deepcopy of a ModelResponse per PII-bearing tool-call response (~tens of µs, off the admission hot path — post-call, and only when a map exists) | None for S3 | **Do soon** (Stage 2) |
| D | **Encrypt the map in metadata** (AES-GCM, per-request ephemeral key held in a guard-owned store) | Sinks would leak ciphertext, not cleartext | Key store is new in-process state that must itself be bounded and cleaned; key + ciphertext live in the same process, so vs. a memory-level attacker it buys nothing; vs. a log-level attacker A already wins for ~0 cost | Key-store cleanup bugs become a new goldmine (accumulating keys); real complexity | **Not worth it** for a single-process proxy. Reconsider only if the map ever crosses a process/persistence boundary (multi-worker, streaming resume) |
| E | **Vaultless tokenization / FPE (NIST FF1)** — derive the placeholder from the PII with a keyed cipher; no map exists anywhere; detokenize by decrypting | No map to leak, ever | Deterministic keyed tokens are **stable cross-request pseudonyms** → the logs accumulate a correlation dataset, and one key compromise retroactively reverses *every log line ever written* — a worse goldmine than S1, because it is unbounded in time. Also: FPE outputs look like real values, so logs stop being visibly redacted; LLM round-trip fidelity of FPE tokens is worse than `<EMAIL_ADDRESS_1>` (models "correct" plausible-looking values but echo obvious placeholders verbatim); FF3-1 carries small-domain caveats (§5) | Key = master goldmine | **Reject** for this architecture. The current per-request random-numbered design is the privacy-superior one |
| F | **Out-of-band map store** (guard-owned dict keyed by request id, never in `metadata`) | LiteLLM's own debug logging could never see the map (fully fixes the S4 debug-dump case) | Plain dicts are not weak-referenceable in Python, so this needs id-keying + explicit cleanup + a TTL sweep for failure paths that never reach post-call. Imperfect cleanup ⇒ an *accumulating global* map — the textbook long-lived goldmine | Cleanup bugs strictly worse than status quo | **Reject.** The metadata-attached map is actually the right lifetime primitive: it is GC-bound to the request with zero bookkeeping. Fix the sink boundary, don't relocate the map |
| G | **Restrict hydration surface** (already: tool-call argument values only, never assistant prose, never keys/names — `pii_guard.py:305-333`, design §8) | Caps what prompt-injection can exfiltrate: the model cannot get cleartext into free text; it can only place placeholders into tool args, which the *client* then executes | Zero — this is current behavior; the cost is declining to build `text_and_tools` mode | Injection can still route hydrated values into an attacker-chosen *tool argument* (e.g. a URL for a fetch tool). Today Airlock returns tool calls to the client for execution, so the client's own tool permissions are the last gate. This gets sharper if 0.6.0's MCP gateway makes Airlock the executor | **Keep as a named invariant**; revisit at MCP-gateway design time |

Honest scoping for a self-hosted, single-writer proxy: A + B + C + G capture
essentially all of the achievable win. D, E, F add state, keys, or cleanup
obligations that each *create* goldmine-shaped failure modes bigger than the
residual they remove. Cleartext-in-process for the request duration (S6) is
the irreducible floor for a proxy that must re-emit original bytes; the
correct posture is minimizing scope (only this request's entities — already
true) and lifetime (B), and saying so plainly in docs rather than claiming
"PII never in memory."

---

## 3. Recommendation — staged, buildable

### Stage 0 (ship immediately; blocks on nothing): enforce the sink boundary

**Invariant: `airlock_pii_map` MUST NOT reach any sink or serialized record.**

1. In `build_request_event` (`request_event.py:176`), change the
   snapshot to exclude a module-level denylist:

   ```python
   SECRET_METADATA_KEYS = frozenset({"airlock_pii_map"})
   guardrail_meta = {
       k: v for k, v in metadata.items()
       if k.startswith("airlock_") and k not in SECRET_METADATA_KEYS
   }
   ```

   Doing it at the event build (not per-projection) means no current or
   *future* sink can see it — the event is the single source of truth
   (request_event.py docstring), so the scrub inherits that property.
2. Apply the same denylist in `write_precall_block_record`
   (`enterprise_logger.py:241-243`) — it builds its guardrail_meta
   independently and bypasses the event builder.
3. Tests T1, T2, T4 (§4).

Cost: ~10 lines + tests, zero measurable latency (a frozenset check per
metadata key, in the logging callback — not on the admission path at all, so
NFR-14 is untouched). Buys: eliminates the entire at-rest goldmine (S1, S2).

Also Stage 0: **one-time cleanup note for the owner** — existing
`logs/airlock-*.jsonl` files written since the rehydration feature went live
already contain maps for any PII-bearing traffic. They should be purged or
scrubbed (`jq 'del(.airlock_pii_map)'` per file) as an ops action. A design
note cannot fix data already on disk.

### Stage 1 (same PR or next): bound the map's lifetime

**Invariant: the map's lifetime ends when hydration ends.**

At the end of `async_post_call_success_hook` (`pii_guard.py:259-278`), after
`_hydrate_tool_calls`, remove the map:

```python
metadata.pop("airlock_pii_map", None)
```

This is defense-in-depth, not the primary control (the recorder callback may
run before or after the hook; failure paths never reach the hook — that is
why Stage 0 scrubs at the sink boundary instead of relying on ordering).
It shrinks the window in which post-call guards, error handlers, or debug
dumps can observe the map. Note the hydrator is registered as a *second*
guard instance (`config.yaml:936-939`) running last in post-call, so popping
here does not starve any other consumer.

Plus an ops invariant in `dev/dogfooding.md` / ops docs: **never run the
proxy with LiteLLM detailed-debug logging while `AIRLOCK_PII_ENABLED` is set**
(LiteLLM dumps request metadata wholesale; Airlock cannot scrub inside
LiteLLM's loggers), and disable core dumps on the prod host (S6).

Cost: one line + a doc paragraph. Buys: shrinks S4; documents the two
exposure channels code cannot close.

### Stage 2: make the logged response deterministically redacted

**Invariant: telemetry sees the pre-hydration response; only the client sees
the hydrated one.**

In the post-call hook, stop mutating the shared response in place. Deepcopy
the response (or, cheaper, only `choices[].message.tool_calls` — the only
subtree hydration touches), hydrate the copy, and return it. The object
LiteLLM's logging holds a reference to stays placeholder-only, killing the S3
race without needing to prove callback ordering. Only runs when a map exists
and tool calls are present, post-call — no admission-path cost. Add test T5.

This also settles the policy question honestly: Airlock's logs record what
*left the building* (redacted) — not what the client received — and the
`pii_hydrated count=N` log line plus the value-free mutation ledger record
that hydration happened. That matches the project's transparency posture
(value-free mutation ledger, CC-T2) without putting PII at rest.

### Stage 3 (when streaming hydration is built): same invariants, stated up front

Whatever buffering/reassembly design Phase 5 lands on must inherit: map never
in any sink (Stage 0 covers it automatically via the event builder), map
popped when the stream closes (including on client disconnect — the failure
path is the dangerous one), hydration into tool-call argument deltas only,
and the response scanner still runs before hydration
(`config.yaml:919-921` before `:936-939`) so injection detection always sees
placeholders, never cleartext.

### Explicitly not recommended

- No encryption of the map (option D) and no FPE/deterministic tokens
  (option E) — see §2 verdicts. Record this so the idea isn't relitigated
  cheaply: **deterministic tokens are the goldmine, time-shifted.**
- No `text_and_tools` hydration mode (design note §8's "optional future
  expansion") without a dedicated injection-focused review. Free-text
  hydration converts "model echoes a placeholder" from a nuisance into a
  disclosure, and prompt-injection making the model echo placeholders is
  trivial.

---

## 4. Verification — tests that prove the goldmine cannot form

The existing suite covers value-free *logger lines* (test-plan F2/F3) but has
**no test on the sink pipeline** — nothing in `tests/test_enterprise_logger.py`
or `tests/test_projections_equiv.py` mentions `airlock_pii_map`
(grep-verified). These close that gap:

- **T1 — no projection carries the map.** Build a `RequestEvent` from kwargs
  whose metadata contains `{"airlock_pii_map": {"<EMAIL_ADDRESS_1>": "goldmine-canary@example.com"}}`;
  for each of `project_enterprise` / `project_s3` / `project_sql` /
  `project_fathom` (fathom with all `AIRLOCK_FATHOM_STORE_*` flags forced on),
  assert `"airlock_pii_map"` and the canary string are absent from
  `json.dumps(record, default=_serialize)`. String-level assertion on the
  serialized form, not key lookup — catches nesting and future re-plumbing.
- **T2 — block records are clean.** `write_precall_block_record` with the same
  metadata → returned record and the written JSONL line (temp
  `AIRLOCK_LOG_DIR`) contain neither the key nor the canary.
- **T3 — map lifetime ends at hydration.** After
  `async_post_call_success_hook` runs with a non-empty map, assert
  `"airlock_pii_map" not in data["metadata"]` (Stage 1).
- **T4 — end-to-end canary sweep.** Pre-call with canary PII → build event
  from the resulting `data` (as the recorder would) → dispatch through a
  `RequestRecorder` to an in-memory sink and to the real enterprise sink with
  a temp log dir → read every byte written → assert placeholders present,
  canary absent. This is the executable form of the S1 claim and the
  regression gate for any future sink.
- **T5 — telemetry object stays redacted (Stage 2).** After the post-call
  hook, the *original* response object's tool-call arguments still contain
  placeholders; the *returned* response contains the canary.
- **T6 — no stable cross-request tokens** (guards the reject-FPE decision):
  already covered at `tests/test_pii_guard.py:931-932`; keep, and add an
  assertion that the same value in two requests yields independent maps whose
  placeholder→value bindings are not required to match.

CI placement: T1/T2/T4 belong next to the projection-equivalence tests so any
new projection or sink must pass the canary sweep to merge.

---

## 5. Research notes and sources

The single most useful external finding: **the whole ecosystem under-treats the
map as a secret**, which is exactly the gap Airlock's Stage 0 closes. Products
split into (a) map/vault designs — Presidio's pseudonymization sample,
LangChain, Protect AI LLM Guard, LiteLLM — whose docs nowhere call the mapping
a secret (LangChain even ships `save_deanonymizer_mapping()` to write cleartext
placeholder→PII to a JSON/YAML file); and (b) vaultless key-based designs
(Google DLP, FPE) where the goldmine collapses into a KMS-held key but
determinism buys correlation risk. Airlock's per-request, in-memory-only,
**randomized** (non-deterministic) map is already the privacy-superior half of
this split — the one remaining defect is that it currently reaches the log
(S1), not the design of the map itself.

### 5.1 Presidio / LangChain reversible anonymization

- Presidio's `DeanonymizeEngine` is deliberately narrow: it reverses only
  *reversible* operators, and the only built-in one is `decrypt` (same key as
  `encrypt`). Hash/mask/redact/replace are one-way by design. The docs carry
  **no warning** that a placeholder↔PII mapping is itself sensitive.
  https://presidio.dataprivacystack.org/anonymizer/
- The numbered-placeholder pattern Airlock uses (`<PERSON_1>` +
  client-held `entity_mapping`) is a Presidio *sample*
  (`InstanceCounterAnonymizer`), not a hardened feature; its only stated caveat
  is thread-safety of the shared counter/mapping — no security note. This
  matches Airlock's per-request accumulation (`pii_guard.py:196-197`), which
  sidesteps the thread-safety caveat by never sharing the map across requests.
  https://presidio.dataprivacystack.org/samples/python/pseudonymization/
- LangChain's `PresidioReversibleAnonymizer` keeps the fake→original map in
  memory and *encourages persisting it to disk* via
  `save_deanonymizer_mapping()` — a documented API that manufactures a durable
  cleartext goldmine, with no security note. Airlock must not adopt this
  pattern.
  https://python.langchain.com/v0.2/api_reference/experimental/data_anonymizer/langchain_experimental.data_anonymizer.presidio.PresidioReversibleAnonymizer.html

### 5.2 Vault vs vaultless tokenization; FPE / NIST SP 800-38G

- Vaulted tokenization concentrates a token↔cleartext store (the classic
  goldmine); vaultless shifts the goldmine to the key. For a proxy that only
  needs *this request's* reversal, neither is warranted — a request-scoped map
  is strictly smaller than either.
  https://www.encryptionconsulting.com/education-center/types-of-tokenization-vault-and-vaultless/
- **FPE / NIST history matters for the reject-FPE decision (option E):**
  Durak–Vaudenay broke FF3 over small domains (NIST 2017 advisory,
  https://csrc.nist.gov/News/2017/Recent-Cryptanalysis-of-FF3); SP 800-38G
  Rev.1 IPD (2019) introduced FF3-1 and made a **≥1,000,000 minimum domain size
  a requirement** (https://csrc.nist.gov/pubs/sp/800/38/g/r1/ipd); the **2nd
  public draft (Feb 2025) drops FF3 entirely** — Beyne's tweak-schedule attack
  hit both FF3 and FF3-1 — leaving **FF1 as the only surviving FPE mode**
  (https://csrc.nist.gov/pubs/sp/800/38/g/r1/2pd). Emails/SSNs/phones are
  small, structured domains where FPE is most fragile. If FPE were ever adopted
  (it should not be, per §2-E), it must be FF1, not FF3-1.
- **Deterministic tokens enable re-identification.** Same input → same token
  preserves referential integrity but lets an attacker correlate across
  datasets and run frequency/dictionary attacks; PPRL studies show
  re-identification rising with population and auxiliary data.
  https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0314486 .
  Even Google's DLP docs concede FPE "provides fewer security guarantees than
  other deterministic encryption methods."
  https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization .
  This is the evidence behind §2-E's "deterministic tokens are the goldmine,
  time-shifted": a deterministic scheme would make every log line historically
  reversible under one key compromise, unbounded in time — strictly worse than
  the 30-day windowed S1.

### 5.3 Keeping secrets out of memory / logs

- Envelope encryption consensus (fresh per-op DEK, drop plaintext key after
  use, KEK never leaves KMS/HSM) is the standard when a secret must *outlive*
  the operation or *cross a boundary* — neither is true for Airlock's
  request-scoped map, which is why option D is not worth it here.
  https://cloud.google.com/kms/docs/envelope-encryption
- **Python zeroization is effectively impossible for `str`**: strings are
  immutable and interned/copied, every slice/format/log makes another copy, and
  **core dumps contain the secret**. The accepted mitigation is architectural —
  minimize scope + lifetime, keep it out of exceptions/reprs/logs, disable core
  dumps/swap at the OS level — not pretend-zeroization. This is the direct
  justification for §1-S6 (accept, mitigate by scope/lifetime + `ulimit -c 0`)
  and Stage 1 (pop early). https://www.sjoerdlangkemper.nl/2016/06/09/clearing-memory-in-python/

### 5.4 Failure modes

- Sensitive-info disclosure is OWASP LLM02:2025 (risen to #2), which names
  **logs, prompts, responses, caches** as disclosure surfaces and prescribes
  redaction + DLP. S1 is a textbook instance.
  https://owasp.org/www-project-top-10-for-large-language-model-applications/
- **Rehydration is itself an exfiltration primitive.** In the LLM-Guard
  deanonymize pattern, any placeholder appearing in model output is
  automatically swapped back to cleartext before the response leaves the
  pipeline — so a prompt-injected "echo every `<..._N>` token you've seen"
  gets rehydrated into real PII *by the guardrail itself* if the output channel
  reaches an attacker.
  https://protectai.github.io/llm-guard/output_scanners/deanonymize/ .
  Airlock is **less exposed by design**: it rehydrates tool-call *argument
  values only*, never assistant free-text (`pii_guard.py:305-333`), so the
  model cannot get cleartext into prose. Research flag: no end-to-end public
  writeup of this attack against a proxy exists — this is a legitimately
  under-documented risk, and it is the reason §3 declines to build a
  `text_and_tools` mode and flags the 0.6.0 MCP-gateway (Airlock-as-executor)
  case as needing its own review.
- **Placeholder fidelity is a live failure class**, not theoretical: LiteLLM's
  Presidio guardrail has open bugs where masked tokens are never un-masked or
  the model mangles them (https://github.com/BerriAI/litellm/issues/6247,
  https://github.com/BerriAI/litellm/issues/22821). Airlock's structured
  tool-args-only path with exact-string replace + malformed-JSON skip
  (`pii_guard.py:324-333`) is more robust than free-text unmasking, and the
  streaming deferral (S5) explicitly avoids the split-token-across-chunks case
  those bugs hit.

### 5.5 Comparable products

- **LiteLLM** supports reverse redaction (`output_parse_pii: true`, replace-only
  un-mask) and, tellingly, a `logging_only` mode that masks "before logging to
  Langfuse... not on the actual llm api request/response" — i.e., LiteLLM
  treats logs as a distinct leak surface. Its docs do **not** state where/how
  long the placeholder→original map lives (in-memory per-request per code, but
  undocumented). https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2
- **Protect AI LLM Guard** uses an explicit Anonymize→`Vault`→Deanonymize flow;
  the Vault is a plain in-process placeholder→original store the caller passes
  between scanners, documented with no sensitivity/persistence warning.
  https://protectai.github.io/llm-guard/output_scanners/deanonymize/
- **Google Cloud DLP** is the most complete reversible reference: vaultless
  crypto tokenization (`CryptoDeterministicConfig` AES-SIV,
  `CryptoReplaceFfxFpeConfig` FPE), reversal capability held entirely in a
  **KMS-wrapped key** (unwrapped keys "not recommended"), determinism explicit
  and scoped by per-column "context" tweaks to narrow correlation. Good
  evidence that if you go vaultless you *must* KMS-wrap the key and accept
  correlation risk — costs Airlock has no reason to take on.
  https://docs.cloud.google.com/sensitive-data-protection/docs/pseudonymization
- **Cloudflare** (AI Gateway Guardrails / WAF PII detection) and **AWS Bedrock
  Guardrails** do **block/mask only — no documented reversible-redaction /
  un-mask path** (Bedrock masking is one-way; Comprehend redaction one-way).
  https://developers.cloudflare.com/waf/detections/ai-security-for-apps/pii-detection/ ,
  https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html .
  Lakera: no reversible-redaction feature found (detection/blocking only) —
  unconfirmed either way.

### Code references (all verified in this review)

- `airlock/guardrails/pii_guard.py:233` — map creation in metadata
- `airlock/guardrails/pii_guard.py:259-278` — post-call hydration hook (no cleanup)
- `airlock/guardrails/pii_guard.py:305-333` — tool-args-only hydration surface
- `airlock/callbacks/request_event.py:120-125, 176` — metadata merge + `airlock_*` snapshot
- `airlock/callbacks/projections.py:35-46` — enterprise projection spreads guardrail_meta beside redacted messages
- `airlock/callbacks/enterprise_logger.py:176-207, 241-262, 344-349` — opt-in-only redaction, block-record spread, live sink write
- `airlock/callbacks/recorder.py:32, 81` — always-on enterprise sink, live self-registration
- `config.yaml:870-874, 919-921, 936-939` — guard ordering (PII first pre-call; scanner before hydrator post-call)
- `airlock/transparency.py:41, 119-140` — value-free mutation ledger (the pattern Stage 0 extends to the map)

---

## 6. Amendments — second review + owner decisions (2026-08-08)

A second review (main-checkout, independent) reached §1's S1 finding by the
same file:line chain — three independent traces now agree the map reaches the
enterprise JSONL. It added three sharpenings, and the owner decided on all
three plus a policy resolution. This section is authoritative.

### 6.0 Owner decisions

| # | Decision | Status vs. prior note |
|---|----------|-----------------------|
| D-1 | **Opaque-handle is the durable target.** The reverse map lives in a bounded, request-scoped context store; only an opaque handle travels through `metadata`. | **Supersedes §2 option-F "reject."** F was rejected on cleanup-cost grounds; the owner accepts that cost for the S4-closing benefit. Cleanup discipline is now a hard requirement (6.1). |
| D-2 | **Rehydration is opt-in per approved tool + argument path.** Off by default; a request's placeholders are hydrated only into allowlisted `(tool, JSON-Pointer)` targets. | **Supersedes §2 option-G "keep tool-args-only invariant."** Tool-args-only is necessary but not sufficient — see 6.2. |
| D-3 | **Graceful-degrade to providers**, not fail-closed, on component unavailability — but typed by failure class and made loud/auditable. | **Resolves the §4 / test-plan policy contradiction** (plan said degrade-gracefully; test codified fail-closed `ImportError`). See 6.3. |
| D-4 | Stage 0 (denylist scrub, §3) still ships first as emergency containment, and the existing Aug-04 map-bearing log is purged as an ops action. | Unchanged; D-1 is the target the denylist is a bridge to. |

### 6.1 Opaque-handle — the durable design (supersedes option F)

**Invariant: cleartext PII (the map) never touches the `metadata` bus; only a
handle does.**

- Pre-call redaction builds the map and stores it in a **process-local,
  request-scoped context store** keyed by a server-generated opaque handle
  (a random id — never derived from PII, never stable across requests). Only
  `data["metadata"]["airlock_pii_handle"] = <handle>` travels the bus.
- Post-call hydration looks the map up by handle, hydrates the allowlisted
  targets (6.2), and **deletes the entry in a `finally`** — on success,
  exception, timeout, and client disconnect alike.
- A **bounded store with a short TTL sweep** reaps handles abandoned by
  requests that never reach post-call (the dangerous path). Bounded size +
  TTL is what stops the store itself becoming the accumulating goldmine the
  original option-F verdict warned about — that warning is now a **design
  constraint, not a veto**: the store MUST cap entries and MUST expire, and a
  test asserts an abandoned handle is gone after the TTL.

Why this beats the Stage-0 denylist as the end state: the denylist closes the
**sinks** (S1/S2) but cannot close **S4** — LiteLLM's own verbose/debug logging
dumps `metadata` wholesale, and Airlock cannot scrub inside LiteLLM's loggers.
With the handle design there is nothing sensitive in `metadata` to dump. Ship
the denylist now (emergency, ~10 lines); land the handle as the durable target.
The handle is also forward-compatible with the shard contract
(`design-shard-contract.md`): the context store is request-scoped and
process-local, never shared across shards/workers — a cross-process map cache
would re-create both the goldmine and a correlation surface.

Residual (unchanged from §1-S6): cleartext still exists as Python `str` in the
store for the request duration. Irreducible; mitigated by scope + lifetime +
`ulimit -c 0`, not zeroization.

### 6.2 Opt-in hydration allowlist (supersedes option G)

**Invariant: a placeholder is hydrated only into an approved `(tool, argument
path)`; everything else keeps the placeholder.**

The second review's decisive point: "tool-args-only" still lets the model route
a placeholder into **any** tool argument. Since Airlock hands tool calls to an
auto-executing client (and *becomes* the executor under 0.6.1's MCP gateway),
an injected or confused model can steer `<EMAIL_ADDRESS_1>` into an argument of
an HTTP/email/shell tool, and the guardrail itself reconstructs the real value
into the exfil path. Hydration is therefore an **egress capability** and must be
authorized like one.

Mechanism:
- Config declares approved targets, e.g. `pii.hydrate_allow: [{tool:
  "gmail_search", path: "/from_address"}, ...]`. Default **empty = deny all**
  (opt-in, D-2).
- At post-call, for each tool call, hydrate only placeholders sitting at an
  allowlisted `(tool, JSON-Pointer)`. Non-allowlisted positions keep the
  placeholder.
- **Schema revalidation is a secondary integrity check, not the gate.** An
  attacker's address is schema-valid in a `from_address` too; the allowlist is
  what authorizes PII release, schema only catches malformed hydration.
- **Per-tenant-capable** (aligns with 0.6.0 identity): tenants approve
  different tools. Optional in v1; the config shape should not preclude it.

The full trade-off analysis for this allowlist — the specifically requested
review — is 6.4.

### 6.3 Graceful-degrade policy (resolves the §4 contradiction)

**Decision (D-3): degrade to the provider, typed by failure class, never
silently.** The two classes are not symmetric:

| Failure | Graceful-degrade means | Safe? | Policy |
|---------|------------------------|-------|--------|
| **Rehydration** unavailable/fails (hydration error, malformed tool-JSON, streaming, handle missing) | Client receives the **placeholder**; request still served | **Yes** — no PII egress; only a functionality degrade | Degrade silently-but-logged; keep the existing malformed-JSON skip (`pii_guard.py:324-333`). This is the default. |
| **Redaction** unavailable (Presidio import fails, `analyze()` throws) | **Unredacted** prompt goes to the provider | **No** — this is fail-*open* on a data-egress control | Degrade is allowed per D-3 **but must be loud and audited**, and **configurable**. |

For the redaction case, the honest engineering requirement — so the decision
serves the compliance persona too — is:
- A config toggle `AIRLOCK_PII_FAIL_MODE = open | closed` (default `open` per
  D-3; regulated deployments set `closed` to block instead of leak).
- In `open` mode, every degraded request carries a **telemetry marker**
  `airlock_pii_unavailable: true` (a signal, not PII — it is *not* on the
  secret denylist and MUST reach the audit trail) plus a loud startup warning
  if `AIRLOCK_PII_ENABLED` is set while Presidio is unimportable.
- The marker makes "we passed PII through unredacted" an auditable event rather
  than a silent hole — consistent with the project's "unavailable is never
  clean" stance from 0.5.x. The current test codifies fail-closed `ImportError`;
  it must be updated to assert the marker + degrade under `open`, and the
  block behavior under `closed`.

### 6.4 Hydration allowlist — trade-offs (the requested review)

> **Refined by `design-rehydration-authorization-maintainability.md` (2026-08-08).**
> The owner's critique — a flat static `(tool, path)` list decays (too narrow →
> bloat or disablement) — is correct. Research concluded the list must be the
> **residual of a layered default-deny gate**, not the primary structure:
> coarse taint (free — the placeholder survives the LLM) → type-match narrowing
> → **sink egress-trust band** (round-trip tools auto-allow, exfil/unknown deny;
> a small `O(tools)` list, not `O(tools×args)`) → a short, telemetry-populated,
> recertified exception list. The "common case needs zero entries" claim holds
> **with a boundary** (depends on the egress-band classification, kept fail-safe
> by *unknown ⇒ deny*). A maintainable allowlist is achievable — the blocklist
> fallback is documented there but labeled strictly weaker. The trade-off list
> below stands as the analysis of the *residual* list; the layered gate is what
> keeps that residual short. See that note for the full design.

**What it buys:** least-privilege PII egress; the exfil primitive (6.2) is
capped to operator-approved destinations; every release is to a known target,
so it is auditable; and it matches Airlock's existing MCP allowlist +
argument-sanitization posture (same trust model, one more surface).

**What it costs / the trade-offs:**

1. **Maintenance burden vs. safety.** Every tool+arg that legitimately needs a
   real value must be enumerated. In an MCP/agent world with many or
   *runtime-discovered* tools, a static allowlist cannot pre-name them → unknown
   tools are default-denied → friction.
2. **Discovery / silent breakage (the sharp edge).** Default-deny means an
   un-allowlisted tool silently receives placeholders — re-creating the exact
   "unusable tool argument" defect rehydration was built to fix, but only for
   not-yet-approved tools. Operators would otherwise learn what to allowlist
   only when something breaks. **Mitigation (the move that makes this viable):**
   emit a **value-free audit event on every *suppressed* hydration** — "tool X,
   path Y had a placeholder but is not allowlisted." Operators build the
   allowlist from the suppressed-events feed, not from user-reported breakage.
   Preventive allowlist + detective telemetry is what turns a fail-closed
   allowlist from operationally painful into self-documenting.
3. **Granularity trade.** *Tool-name-only* allowlisting is low-maintenance but
   coarse — every argument of an approved tool becomes a PII sink, so a later-
   added `webhook_url` arg on an approved tool is a silent exfil path.
   *Tool+path* (JSON-Pointer) is precise but brittle: schemas evolve, nested/
   array arguments are awkward to point at, and the model may place the value
   in an unexpected field. Recommend **tool+path as the unit**, with tool-name
   wildcards allowed only for tools whose every argument is known-safe.
4. **Schema ≠ authorization.** Covered in 6.2: revalidation is integrity, not
   the gate. Listing it as the trade-off it is prevents a false sense of safety.
5. **Trust delegation.** The allowlist trusts that an approved `(tool, path)`
   is a safe PII destination — but an approved tool can itself be malicious or
   compromised (supply chain). The allowlist *reduces*, does not *eliminate*,
   egress risk; it moves trust to the allowlist curator. This is the seam that
   0.6.1 MCP governance (OAuth'd, provenance) tightens — note the dependency,
   do not try to solve tool-trust here.
6. **Per-tenant surface.** Correct long-term shape (tenants approve different
   tools) but multiplies config. Keep the schema per-tenant-ready; ship a global
   list first.

**Alternatives weighed:**

| Approach | Posture | Verdict |
|----------|---------|---------|
| **(a) Tool+path allowlist** (6.2) | Preventive, default-deny, least-privilege | **Chosen.** Only option that caps the egress primitive before it fires. |
| (b) Denylist of dangerous tools | Fail-open-ish | Reject — a newly-added dangerous tool leaks until someone lists it; wrong default for an egress control. |
| (c) No auto-hydration; client calls an explicit resolve endpoint | Safest for the proxy | Reject for v1 — it relocates the map to the client (goldmine moves, not gone) and breaks the transparent-UX value. Keep as a possible high-assurance mode. |
| (d) Provenance/taint tracking — hydrate only where the value originated | Elegant, precise | Reject for v1 — the model transforms text, so provenance is routinely lost; high complexity for partial coverage. |
| (e) Hydrate-anywhere + audit every event | Detective only | Reject as the primary control — catches abuse after PII already left. Useful only as the telemetry half of (a), which is exactly 6.4-#2. |

**Recommendation:** ship **(a) tool+path, default-deny, per-tenant-ready**,
paired with **suppressed-hydration audit telemetry** (#2) to make it
maintainable, schema revalidation as a secondary check, and an explicit note
that tool-trust itself is 0.6.1's problem. This fully matters only when Airlock
is the executor (0.6.1 MCP gateway); today the client executes tool calls, so
the client's own tool permissions are a backstop — but the allowlist should
land **with or before** Airlock-as-executor, not after.

### 6.5 Additional verification (extends §4)

- **T7 — handle, not map, on the bus.** After pre-call, assert
  `data["metadata"]` contains `airlock_pii_handle` and does **not** contain
  `airlock_pii_map`, and that the handle is not derivable from any PII value.
- **T8 — handle store cleanup.** The context entry is gone after post-call on
  success, on a raised exception, and on a simulated disconnect; an abandoned
  handle is gone after the TTL sweep; the store is bounded under load.
- **T9 — allowlist gates hydration.** A placeholder at a non-allowlisted
  `(tool, path)` stays a placeholder; at an allowlisted one it becomes the
  value; a suppressed-hydration audit event is emitted for the former and is
  **value-free**.
- **T10 — graceful-degrade markers.** With Presidio unimportable:
  `AIRLOCK_PII_FAIL_MODE=open` serves the request and stamps
  `airlock_pii_unavailable` (which survives to the audit sink — it is not on
  the secret denylist); `closed` blocks. Replaces the old fail-closed
  `ImportError` test.
