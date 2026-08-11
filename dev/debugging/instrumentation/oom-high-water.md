# OOM / native-memory high-water instrumentation

Use this runbook when an isolated Airlock/LiteLLM worker shows rising anonymous
RSS, cgroup memory pressure, liveness stalls, or an OOM. It documents the
opt-in recorder in `airlock.callbacks.oom_diagnostics`; it is not a normal
production telemetry sink.

## Safety and data handling

The recorder is inert unless `AIRLOCK_OOM_DIAGNOSTICS=1`. Its bounded JSONL
contains aggregate process/cgroup/allocator counters only. It never writes
request or response bodies, headers, model names, exception text, or client
metadata. Keep artifacts outside source control and use an isolated service
for a real-provider replay.

Never use `GET /health` as a liveness probe. Use `GET /health/liveliness` for
the safe liveness check.

## Enable the recorder

Start a fresh, isolated Airlock process with an artifact directory and a fixed
record bound. For a long, faithful replay, turn off Python allocation tracing
and use native profiling only at high water:

```bash
AIRLOCK_OOM_DIAGNOSTICS=1 \
AIRLOCK_OOM_DIAGNOSTICS_TRACEMALLOC=0 \
AIRLOCK_OOM_DIAGNOSTICS_DIR="$PWD/logs/oom-run" \
AIRLOCK_OOM_DIAGNOSTICS_EVERY=25 \
AIRLOCK_OOM_DIAGNOSTICS_MAX_RECORDS=20000 \
airlock start --host 127.0.0.1 --port 4012
```

For a short Python-allocation investigation, omit
`AIRLOCK_OOM_DIAGNOSTICS_TRACEMALLOC=0`; the default is enabled. The recorder
creates `litellm-<pid>.jsonl` mode `0600` in the chosen directory.

`config.yaml` registers the diagnostic guardrail. No config change is needed;
the environment flag makes it a no-op outside a diagnostic run.

## What each JSONL record contains

Each record has a monotonic timestamp and a phase:

- `diagnostics_started`
- `request_entry` — assigns the process-local sequence number.
- `provider_response` — provider response has returned.
- `callback_complete` — Airlock/LiteLLM success or failure telemetry callback
  completed.
- `periodic` — every `AIRLOCK_OOM_DIAGNOSTICS_EVERY` requests.
- `signal_usr1_*` or `signal_usr2_*` — an operator snapshot.

The payload includes cgroup current/peak/high/max/event counters, process RSS,
`smaps_rollup` anonymous/huge-page values, PSI, glibc `mallinfo2`, thread/FD
counts, optional tracemalloc totals, aggregate GC type counts at checkpoints,
and LiteLLM/httpx client-pool counts.

Important: `in_flight` is decremented at `callback_complete`, not at
`provider_response`. A growing value can therefore mean a stuck callback or
logging worker; it is not by itself proof of matching provider concurrency.

## High-water capture

At a chosen cgroup threshold, first request a low-perturbation snapshot:

```bash
kill -USR1 <litellm-child-pid>
```

`SIGUSR1` snapshots, runs `gc.collect()`, then snapshots again. It does not
trim allocator arenas. `SIGUSR2` also calls `malloc_trim(0)`, but only when no
requests are in flight; use it only when deliberately testing idle reclamation,
never to make a faithful pressure replay look healthy.

Once at high water, attach a short native profile instead of profiling from
startup:

```bash
memray attach --native --trace-python-allocators --duration 90 \
  --output oom-profile-highwater.bin <litellm-child-pid>
memray stats oom-profile-highwater.bin
```

Record the cgroup event counters before and after attachment. Native profiling
changes allocation behavior, so treat it as a bounded evidence capture, not a
normal load-test mode.

## Interpreting the results

The 2026-08 G-9 investigation established this useful pattern:

- High private-anonymous RSS plus large `mallinfo2.arena`/`fordblks`, but low
  `uordblks`, means native allocator retention rather than a live Python-object
  leak.
- Flat GC type counts and stable LiteLLM client/httpx connection counts further
  rule out a Python collection or connection-pool leak.
- If Memray attributes cumulative allocation churn to native ML functions,
  inspect whether an expensive model is being run unnecessarily per request.
- Liveness can stall under `MemoryHigh` reclaim before `memory.max` or
  `oom_kill` changes. Preserve the state; do not call it a kernel OOM unless
  the cgroup/kernel event proves one.

In that incident, Presidio's full spaCy/Thinc NER pipeline was running for
self-contained PII recognizers. It caused large temporary native arrays that
glibc retained in thread arenas. The source fix selects Presidio's
`NoOpNlpEngine` for the six shipped pattern/validation recognizers while
retaining full spaCy for NLP-dependent entities such as `PERSON`.

## Closeout checklist

Before stopping a diagnostic run, retain:

1. The bounded JSONL and any high-water Memray file.
2. `memory.events`, `memory.current`, and `memory.peak` from the service
   cgroup.
3. Aggregate per-call success/error counts from the real runner; its top-level
   `complete` verdict alone may hide error rows.
4. A safe `/health/liveliness` result and unit result/restart state.

Do not stop a driver merely because it crossed an historical request-count
landmark. Stop only at an explicit operator-approved safety boundary or after
the requested run is complete.
