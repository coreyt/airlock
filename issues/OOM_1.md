# OOM-1 — G-9 overload exhausts the Airlock service cgroup

**Status:** root cause fixed in source and validated through the historical
failure landmark. Fathom EARP owns any subsequent full G-9 collection.

## Incident

During the G-9 R2 answer-arm run on 2026-08-09, Airlock became unresponsive
under sustained load and then hit its 4 GiB systemd cgroup limit. systemd
restarted it once.

The preceding health-route restart was a separate defect and was fixed before
this run. The restart at 14:02 CDT was a controlled deployment restart, not an
OOM.

## Evidence

- The kernel killed the **`litellm` child** at 16:24:16 CDT with
  `CONSTRAINT_MEMCG`.
- Cgroup usage was 4 GiB; the kernel reported 4.14 GiB anonymous memory,
  zero file-backed memory, and about 944 MiB swap use.
- The killed process had about 4.14 GiB anonymous RSS. The Airlock launcher
  and `uv` parent were small, so this was not a parent-process leak.
- `MemoryHigh=3G` fired thousands of pressure/reclaim events and delayed the
  kill, but did not prevent it. Liveness timed out while pressure was active.
- The archived file contains 3,417 successful Gemini request records and two
  failed Gemini request records, almost all for `gemini-3.1-flash-lite`.
  Crucially, the controlled restart at 14:02 CDT created the process that was
  later OOM-killed: that fresh process handled **1,661** successes between
  14:19 and 14:54, followed by the two failures. Average serialized messages
  were 25 KiB (maximum 278 KiB); average serialized response was under 1 KiB.
- The two failures began 25 seconds apart after the successful traffic had
  ended. They took 246 and 466 seconds, respectively. The OOM followed 71
  minutes after the latter failure. The process was therefore not killed while
  a large observed G-9 request backlog was active.
- The JSONL file was about 107 MiB, while cgroup file memory at OOM was zero:
  logging is not the retained 4 GiB allocation.
- Optional Fathom, S3, and SQL telemetry sinks were not enabled.

## Controls present but not yet a root-cause finding

The production configuration has no explicit LiteLLM deployment parallelism
limit, and Airlock's existing per-client admission gate is off by default. Both
are valid resilience controls, but the actual G-9 runner is sequential, and
the historical evidence shows only two overlapping failed calls. They do not
establish that missing ingress concurrency limits caused this OOM.

PII analysis also uses `asyncio.to_thread` for full prompts. It remains a
plausible allocation amplifier, but it is **not** an established cause: a
sequential workload cannot build an unbounded PII executor queue by itself.
The PII maps themselves are small (average serialized size about 664 bytes;
maximum 15 KiB).

## Ruled out or secondary concerns

- Enterprise JSONL logging is synchronous and has no in-memory queue.
- Fast-state sample deques are bounded. Its registry maps need eventual
  cardinality cleanup, but one G-9 client/model cannot explain this incident.
- Streaming response scanning is unbounded per stream by design, but the
  observed responses were small and it is not the leading explanation here.
- `airlock_pii_map` is presently included in enterprise records. That is a
  separate data-exposure concern because it can contain original PII; it should
  be excluded or redacted independently of this OOM remediation.

## Candidate remediation controls (not approved as the fix)

These controls can be considered after the heap diagnosis identifies a failure
mechanism. They must not be represented as the OOM fix without evidence.

| Control | What it protects | Limitation |
|---|---|---|
| Enable the existing Airlock admission gate | A known client, such as G-9; rejects excess requests immediately with 429 | Per-client only; a client can be split across identities |
| Add an Airlock **global** in-flight admission limit | The entire proxy and all client identities | Must be implemented; it does not exist today |
| Bound the Presidio executor/work queue | Concurrent NLP copies and CPU/memory spikes | Does not bound provider requests by itself |
| Set LiteLLM `default_max_parallel_requests` (and/or model-level limit) | Concurrent provider calls per deployment | Bounds outbound calls but can leave unbounded accepted HTTP requests waiting in memory |
| Bound request body/token size | Pathological single requests | Does not solve ordinary concurrent overload |
| `MemoryHigh` / `MemoryMax` | Host protection and alerting | A safety net, not capacity control |

## Next experiment: profile safely, do not repeat the unbounded full run

Do **not** rerun G-9 unchanged on production. It already demonstrated the
failure and makes normal Airlock clients unavailable before the OOM.

After the immediate cap is active, run a staged, separately identifiable
reproduction:

1. Use a staging/isolated Airlock process or a dedicated temporary systemd
   cgroup, not the primary production proxy.
2. Begin at a fixed low evaluator concurrency; ramp only after each memory
   plateau is observed. Preserve PII-on behaviour in the first run.
3. Capture a native allocation profile of the LiteLLM child (for example,
   Memray) and periodic RSS/cgroup snapshots. `tracemalloc` alone is
   insufficient because spaCy/NumPy and HTTP libraries can allocate outside
   Python's tracked heap.
4. Repeat only the smallest reproducing segment with PII disabled **in the
   isolated environment**. The delta distinguishes Presidio amplification from
   pending LiteLLM/provider request retention; it is not a proposed production
   security setting.
5. Define a stop condition below the hard cgroup limit (for example, abort at
   sustained `MemoryHigh` pressure or 80% of `MemoryMax`). Preserve the profile
   and logs, then stop the test cleanly.

The profile must preserve the actual sequential G-9 request shape before a
new ingress guard is introduced, otherwise it cannot distinguish a backlog
problem from retained state after provider failures.

### 2026-08-10 profiling attempt

Two native Memray traces were captured with a temporary `systemd --user`
override, then the normal unit and virtual environment were restored. The
traces are retained outside the repository at
`~/.local/share/airlock/oom-profiles/` (mode `0600`):

- `oom-profile-2026-08-10-r1-258-requests.bin`
- `oom-profile-2026-08-10-r2.bin`

The supplied `/tmp/airlock_oom_repro.py` is **sequential**, not concurrent.
Both attempts completed 258 successful requests at about 1.7 requests/second,
then the deliberate clean service stop produced the expected
`RemoteDisconnected` on request 259. The service cgroup initialized around
1.18 GiB and stayed in a narrow 1.18--1.19 GiB band, with no `MemoryHigh`
pressure. Consequently these traces establish only the sequential baseline;
they do **not** capture the 4 GiB failure state and cannot identify the OOM
allocator. Their retained Memray heap is roughly 159 MiB and dominated by
startup/import allocations.

The actual G-9 R2 answerer loop is sequential: it calls one `urllib`
completion per task and has no worker pool or task group. The supplied repro
has the same concurrency shape, but its default prompt is only about 5 KiB;
the completed G-9 traffic averaged about 25 KiB per serialized message. The
next profile must therefore use the observed 25 KiB payload distribution,
record cgroup memory and request completion/timeout state, and cleanly stop
below `MemoryMax`. Do not infer that PII or LiteLLM is exonerated from the
small-payload sequential plateau.

### Historical request timeline correction

The archived JSONL contains 3,417 successful Gemini request records. It shows
serial G-9 traffic through `14:54:50` CDT. At
`15:05:13`, one 18.7 KiB request began; a second 19.1 KiB request began at
`15:05:38` before the first had finished. They failed at `15:09:19`
(`APIConnectionError`, 246 s) and `15:13:24` (`Timeout`, 466 s), respectively.
The cgroup OOM kill was not until `16:24:16`, 71 minutes after the last failed
request completed. Thus the evidence does not support a large simultaneous
G-9 backlog. The priority diagnostic is retained asynchronous state after
failed Gemini/LiteLLM requests.

### 2026-08-10 matched failure-state experiment (in progress)

Airlock is running under a temporary Memray wrapper with a 3.25 GiB cgroup
watchdog. Two independent sequential clients each sent 258 successful,
approximately 25 KiB-prompt requests and then hit an upstream `TimeoutError`
on request 259. The requests overlap in the same way as the archived failures.
At the beginning of the post-timeout observation period the service cgroup was
about 1.23 GiB (1.34 GiB peak), with zero swap, zero restarts, and no cgroup
high/max/oom events. The service is being kept idle for the historical
71-minute post-failure interval. This experiment covers 516 successful
requests, not the 1,661 successful requests handled by the freshly restarted
OOM process, so a stable result alone cannot exonerate all full-run mechanisms.

The idle profiled service has 135 systemd tasks after the two clients exit.
Accordingly, the same historical task count is not evidence of 135 in-flight
G-9 requests; it primarily reflects the LiteLLM/runtime thread population.

At 18 minutes after the failures, the LiteLLM child had 1.04 GiB private
anonymous RSS (424 MiB `AnonHugePages`) and 133 threads, all but the event-loop
thread waiting on futexes. Its descriptor count was only 15. This is a large,
persistent native baseline, but not yet proof of a leak: Memray's wrapper
parent itself uses roughly 236 MiB and its native instrumentation can affect
allocator behavior. The post-observation comparison must include an
unprofiled process before attributing these allocations to Airlock code.

One material part of that baseline is now identified. Presidio lazily loads
spaCy's installed `en_core_web_lg` model at the first PII scan; its vocabulary
vectors alone occupy 411,501,600 bytes (the installed package is 425 MiB).
This costly fixed resident allocation materially reduces the 4 GiB cgroup
headroom, but cannot by itself explain the additional ~3 GiB at the OOM.

A read-only debugger snapshot identifies the mass idle worker pool as NumPy's
bundled OpenBLAS: representative workers block in `blas_thread_server` in
`libscipy_openblas`. Its 128 native workers contribute substantial virtual
address space and scheduler overhead. Limiting `OPENBLAS_NUM_THREADS` is a
candidate footprint reduction to test, but is not yet an OOM root-cause fix.

The isolated sequential PII replay also exposes short-lived native spikes that
coarse RSS sampling misses. Its 25-request sampler observed a 1.647 GiB RSS
maximum, while cgroup `memory.peak` recorded 2.229 GiB of anonymous memory.
The scope contained only the replay shell and one Python process, and it
completed all **3,417** archived message sets in 2,165.849 seconds with no
`high`, `max`, or OOM event. Its final RSS was 1.125 GiB; no rising RSS or
cgroup-peak curve appeared after initialization. This establishes that
`AnalyzerEngine.analyze()` has a sizeable transient allocation and expensive
fixed baseline, but rules out the PII scan *alone* as the deterministic
1,661-request retention mechanism. It does not test LiteLLM callbacks, router
state, or failed-request cleanup.

### 2026-08-10 matched observation result

The paired-timeout service was left idle through the full 71-minute historical
delay. At 11:28 CDT it was still about 1.32 GiB current (1.442 GiB cgroup peak),
with zero swap, restarts, `high`, `max`, `oom`, and `oom_kill` events. Thus two
overlapping timed-out requests are not sufficient to create the delayed OOM.
The finalized r4 Memray file is stored privately outside the repository; like the
short r1/r2 traces it shows roughly 2.315 GiB aggregate allocation but only a
166.8 MiB traced peak, dominated by Pydantic startup/allocation churn. It did
not capture the native 4 GiB state and cannot by itself identify the allocator.

The normal unprofiled service starts near 552 MiB before its first PII scan.
The actual full proxy path then initializes Presidio/its native workers and
settles near 1.38 GiB. A fast, single-in-flight reproduction reached 258
successful requests at roughly 1.75 requests/sec with a 1.389 GiB cgroup peak,
then received an upstream `TimeoutError` on request 259; Airlock stayed alive
and had zero pressure/OOM events. This repeats the provider burst interruption
from the earlier 258-request diagnostics, so the next replay is paced at the
historical approximately 46 requests/minute before drawing a high-count memory
conclusion.

That paced replay was then stopped by an upstream **HTTP 503 Service
Unavailable** on request 104 (not by Airlock) while the proxy remained healthy
at about 1.39 GiB with no cgroup pressure, restart, or OOM event. The Airlock
archive classifies it as the Gemini provider's `ServiceUnavailableError`, and
the proxy stderr identifies the Google `generateContent` endpoint. This is
neither evidence of a rate-limit response nor evidence of an Airlock memory
failure. It currently prevents a live high-count replay, so the next
diagnostic must exercise the full Airlock path against a deterministic local
OpenAI-compatible upstream, retaining PII and callbacks, before G-9 is retried
when the real provider is available. The diagnostic client now records any
HTTP status code as well as its exception type.

### 2026-08-10 isolated full-path replay (OpenAI-path baseline complete)

An isolated, `MemoryHigh=3 GiB` / `MemoryMax=3.25 GiB` Airlock instance ran
the same sequential 25 KiB-prompt shape at the historical 1.3-second cadence
against a loopback-only deterministic upstream. It retained Airlock's PII
guard, recorder, metrics, fast monitor, cost router, and configured guardrail
callbacks while avoiding the unavailable Google endpoint. It completed all
**1,661** requests in 2,159.433 seconds (35:59), with **zero** `high`, `max`,
`oom`, or `oom_kill` events and zero service restarts. The cgroup ended at
1.210 GiB current and 1.231 GiB peak. This rules out the generic successful
Airlock path, including the deliberately larger-than-G-9 25 KiB prompt shape,
as the direct source of the historical 4 GiB anonymous-memory kill. It is
still diagnostic evidence only, not the required live G-9 acceptance proof.

A follow-on isolated run will use LiteLLM's native Gemini adapter against an
emulated `v1alpha ... :generateContent` endpoint. That is needed before any
code remediation is chosen because the completed baseline used the OpenAI
adapter and did not execute Gemini-specific request/response transformation.

### 2026-08-10 isolated native-Gemini replay (complete)

The follow-on run did exercise LiteLLM's real
`gemini/gemini-3.1-flash-lite` adapter against a loopback response in the
native Google AI Studio `v1alpha ... :generateContent` shape. It completed all
**1,661** sequential 25 KiB-prompt requests in 2,159.430 seconds (35:59) with
zero `high`, `max`, `oom`, or `oom_kill` events and zero restarts. Its cgroup
finished at 1.262 GiB current / 1.287 GiB peak. Anonymous memory plateaued at
1.132 GiB after roughly request 1,000; the later measured growth was
file-backed cache from the deliberately retained JSONL request records
(~25 KiB per synthetic request). That is materially different from the
incident's 4 GiB all-anonymous OOM event.

This rules out both the generic successful proxy path and the native Gemini
request/response transformation as sufficient causes of the OOM. The remaining
historical discriminator is the pair of overlapping long upstream failures
that followed the 1,661 successes. The next isolated phase applies those two
timeouts to the same warmed proxy and records in-flight request count and
cgroup memory through the historical post-failure interval.

### 2026-08-10 overlapping-timeout phase (71-minute monitor running)

Without restarting the warmed native-Gemini proxy, a loopback-only fixture
accepted two independent requests 25 seconds apart and held both for 600
seconds. Aggregate-only fixture state recorded `peak=2`; both clients timed
out after 120 seconds. At client completion, Airlock was 1.262 GiB current /
1.287 GiB peak, 1.132 GiB anonymous memory, zero restarts, and zero
`high`/`max`/OOM cgroup events. The mock handlers later completed, and the
proxy remained unchanged.

An independent five-minute cgroup monitor is preserving the full historical
71-minute delayed-OOM interval. Its 5-, 10-, and 15-minute samples are stable
at about 1.263 GiB current / 1.287 GiB peak with the same 1.13 GiB anonymous
memory, zero restarts, and no cgroup event. The monitor must finish before
this diagnostic phase is called clean; only then may the real 4,597-query G-9
acceptance run be retried.

### 2026-08-10 process-lifetime correction

The controlled 14:02 CDT restart matters for attribution: only 1,661 successful
requests reached the process killed at 16:24, not the entire 3,417-record
archive. This closely matches the earlier observed failure around request 1,660.
It materially raises the priority of an actual full Airlock-path sequential replay
through that threshold, with request-count/concurrency and cgroup-memory samples.
The ongoing PII-only replay remains useful to isolate Presidio, but cannot prove
or disprove a leak in LiteLLM callbacks, router state, or failed-request cleanup.

The supplied `/tmp/airlock_oom_repro.py` independently records the same
deterministic symptom: its sequential, stdlib-only client has reproduced the
kill after roughly 1,600--2,000 requests twice. It uses no FathomDB/EARP
worker pool, so the next diagnostic run must use it (or the exact G-9 runner)
through 1,661 requests while recording cgroup memory after each fixed request
interval and the true in-flight count. That is the shortest safe reproduction
that can distinguish per-request retention from a long-lived idle allocation.

One compatible but unproven mechanism is allocator retention under the proxy's
large native-thread population. The short Memray trace allocated 2.318 GiB
over 258 requests while retaining only a roughly 167 MiB traced heap peak;
Pydantic accounted for 1.729 GiB of allocation churn. Combined with the
LiteLLM child's 128 OpenBLAS workers, glibc arena/thread-cache retention is a
testable hypothesis, **not** a diagnosis. The high-count replay must compare
the normal process with an early `MALLOC_ARENA_MAX`/OpenBLAS-thread cap before
any such setting can be accepted as remediation.

## Acceptance criteria for the remediation

- The complete G-9 R2 answer arm completes all **4,597** queries with no
  Airlock request error; a partial or cost-only result is not evidence.
- During that full run, cgroup `high`, `max`, `oom`, and `oom_kill` remain at
  zero and memory remains below the selected operational threshold rather than
  growing toward `MemoryHigh`.
- The canonical legacy liveness probe, `/health/liveliness`, remains responsive
  throughout. Do not use `GET /health`: it can initiate model completions.
- The remediation has direct evidence for the diagnosed retention mechanism.
  If it adds a load-shedding limit, the test also proves deterministic 429
  behavior instead of an in-memory queue.

## G-9 artifact integrity correction

The archived `g9-r2-answer-arm-retry2` result is **not** an acceptance pass,
despite its `verdict: complete` field.  Its per-query JSONL has 9,194 rows:
4,597 retrieval rows plus 4,597 answer-arm rows.  Of the answer-arm rows,
1,660 are scored and 2,937 have `outcome: error` after Airlock became
unavailable.  The answer-arm runner catches each per-call exception, records
an error row, and continues; it then emits `complete` unless its separate
budget guard halted the run.  The artifact's cost and top-level verdict
therefore cannot establish completion or request reliability.

The full rerun must use an error-aware completion gate: exactly 4,597
answer-arm calls, zero `outcome: error` rows, and a live Airlock cgroup
monitor.  This is an evaluator-integrity prerequisite for the OOM acceptance
criterion, not evidence that the Airlock OOM was fixed.

## 2026-08-10 approved production rerun — safety stop, not acceptance

After the isolated native-Gemini reproduction finished cleanly, two real G-9
answer-arm processes ran against the same production Airlock instance.  Each
process is sequential, but together they provided up to two simultaneous
provider calls.  Neither is an acceptance pass:

- `retry2` recorded 4,597 answer rows with **1,661 successful/scored** and
  **2,936 error** rows.
- `retry3` recorded 4,597 answer rows with **537 successful/scored** and
  **4,060 error** rows.

The configured watchdog observed cgroup growth from a 1.270 GiB baseline to
3,499,393,024 bytes, with `MemoryHigh=3 GiB` crossing **46,085** times.  It
requested a deliberate stop at 3.25 GiB (before the 4 GiB hard cgroup limit).
`/health/liveliness` remained HTTP 200 on every monitor sample and there were
zero `memory.max`, `oom`, and `oom_kill` events.  Airlock did not restart.
The graceful stop itself timed out after 30 seconds and systemd sent SIGKILL;
that SIGKILL is an operator safety action, not a kernel OOM kill.  systemd
reported a 3.2 GiB service memory peak and 951.6 MiB swap peak.

This reproduces the historical risk under real provider traffic and confirms
the safety envelope works, but it is **not** a successful remediation.  The
exact recurrence of 1,661 scored requests in `retry2` strengthens the case
for retained state in the real provider-success path; the local native-Gemini
fixture did not reproduce it.  Concurrency is an amplifier in this run, not
yet a proven sole cause.  The allocator/object owner remains to be identified
before choosing a source-level fix.

## 2026-08-10 root-cause fix and validation

The retaining mechanism is now identified and fixed. Airlock constructed one
module-singleton Presidio `AnalyzerEngine`, but its default configuration loaded
spaCy `en_core_web_lg` and ran the spaCy/Thinc NER pipeline on every request,
even though the six shipped PII recognizers are self-contained pattern and
validation recognizers: `CREDIT_CARD`, `US_SSN`, `EMAIL_ADDRESS`,
`PHONE_NUMBER`, `US_BANK_NUMBER`, and `IBAN_CODE`.

The high-water native Memray capture attributed 22.5 GiB of 90-second
allocation churn to that path, led by Thinc `maxout`, `expand_window`, and
`layernorm`. The allocations were freed logically, but glibc retained the
resulting per-thread arenas as private anonymous RSS. The unpatched exact G-9
replay reached roughly 3.7 GiB, generated more than 180,000 `MemoryHigh`
events, and caused a multi-minute liveness stall before any cgroup OOM.

`airlock.guardrails.pii_guard` now constructs Presidio's supported
`NoOpNlpEngine` for exactly those six entities. This does **not** remove PII
scanning or change their entity spans; it only avoids irrelevant spaCy NER
inference. Any configuration containing an NLP-dependent entity such as
`PERSON` or `LOCATION` retains the full spaCy engine.

The patched, isolated exact G-9 run recorded 2,640 consecutive successful
answer calls (past the historical 1,661 failure landmark) at about 0.65--0.71
GiB cgroup use, with zero `MemoryHigh`, `MemoryMax`, OOM, restart, or answer
error events. It was intentionally closed at the operator's request so Fathom
EARP can run its own G-9 collection; the Airlock services and artifacts remain
available. This establishes the source fix for the OOM mechanism, while a
future independent 4,597-call EARP run remains the full acceptance artifact.
