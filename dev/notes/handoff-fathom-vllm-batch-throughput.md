# Hand-off: Fathom × vLLM batch throughput tuning

**Date:** 2026-06-15
**Status:** ACTIVE collaboration in progress. Operator is changing vLLM settings
right now; Airlock side is staged and waiting.
**Audience:** the next Airlock session. Read this top-to-bottom, then re-probe
vLLM (step 1 under "Resume here") before doing anything.

## TL;DR — what we're doing

Fathom (the operator's LLM-driven extraction/synthesis harness) wants to run an
extraction over the LME paired-haystack set: **n=160 → ~7,150 sessions**. Naive
estimate at the default concurrency (8) is **~19 h**. We are finding the best
throughput before committing to the full run. Airlock owns the proxy-side knobs +
restarts; Fathom runs the real-workload benchmark batches; the operator owns the
vLLM-host settings.

The batch path itself is **built, merged, and live-verified** — this work is
purely *tuning*, not feature work.

## The system (so commands make sense)

- **Airlock** = `airlock.service` (systemd **--user** unit). Runs
  `uv run airlock start` from `~/projects/airlock`; reads `.env` (unit
  `EnvironmentFile`) + `config.yaml` **once at startup**. Restart:
  `systemctl --user restart airlock` (healthy ~6 s later). **Liveness:
  `GET /health/liveliness` — NEVER `GET /health`** (the latter fires live
  completions to every model). Port 4000. Master key: `AIRLOCK_MASTER_KEY` in
  `.env`.
- **vLLM** = separate Docker at **`http://192.168.1.45:8000/v1`**, serving
  `qwen3.6-27b` (27B, quantized to fit a 24 GB 3090), vLLM **0.21.0**. It has
  **no async Batch server API** (`/v1/files` + `/v1/batches` are 404) — only
  synchronous `/v1/chat/completions`. The operator controls this host; Airlock
  cannot change vLLM flags.

## What's built (merged to `main`, do NOT rebuild)

The **Airlock Batch Gateway, vLLM gateway-as-executor backend**. Because vLLM has
no batch API, Airlock *executes* the batch itself: after the content scan it
streams the scanned rows at vLLM's live `/v1/chat/completions` with bounded
concurrency, stages results, and owns the lifecycle/status.

- Code: `airlock/batch/vllm.py` (`VLLMBackend`, executor, reconciler),
  wired in `runtime.py`/`middleware.py`/`store.py`.
- Design + contract: `dev/plans/prompts/vllm-batch-executor.md`,
  `dev/notes/note-add-vllm-batch-backend.md`.
- Client interface (hand this to Fathom): `docs/guide/batch.md` →
  "Interface reference (for batch clients, e.g. Fathom)".
- Merges: `87ee456` (executor), `a00b66a` (live e2e), `54a4f78` (param-less
  content fetch). Live e2e: `tests/test_vllm_batch_e2e.py`
  (`AIRLOCK_LIVE_VLLM_E2E=1`). Full suite green (~1813).
- Config alias: `qwen36-27b-vllm-batch` (`config.yaml:195`, `backend: vllm`,
  `provider_model: qwen3.6-27b`). Loads on restart.
- Client contract reminder: `?custom_llm_provider=vllm` is required on
  `/v1/files` upload + `/v1/batches` create/poll; the **content GET no longer
  needs it** (gateway recognizes its own output file ids). The OpenAI SDK works
  via `default_query={"custom_llm_provider":"vllm"}`.

## Airlock-owned knobs (all = edit `.env`/`config.yaml` + restart; read once)

| Knob | Where | Default | Notes |
|---|---|---|---|
| `AIRLOCK_VLLM_BATCH_CONCURRENCY` | `.env` (line 64) | 8 | **currently set to 8.** Process-global `asyncio.Semaphore` bounding concurrent vLLM requests (`vllm.py:_get_semaphore`). The sweep variable. |
| `AIRLOCK_VLLM_BATCH_TIMEOUT` | `.env` | 120 (s) | per-row timeout. **Raise it if a trial shows timeout error-lines under contention.** |
| `AIRLOCK_VLLM_BATCH_RETRIES` | `.env` | 1 | per-row retry → error line on exhaustion. |
| `batch_profile.default.pii_redact` | `config.yaml:444` | `true` | **Operator AUTHORIZED setting `false` for the real 7,150-row run** (LME is synthetic/public; 0 redactions fired). Keep `scan_at_upload`+`keyword_block` on (≈free). Re-enable `true` after. Also fix the stale `NO-OP stub (to-do #2)` comment at `config.yaml:436-439,442` while editing — Item 2 made scanning real. |

**Flip concurrency cleanly:**
```bash
sed -i 's/^AIRLOCK_VLLM_BATCH_CONCURRENCY=.*/AIRLOCK_VLLM_BATCH_CONCURRENCY=16/' .env
systemctl --user restart airlock
# wait for liveness, then confirm it's live in the proxy process:
PID=$(systemctl --user show airlock -p MainPID --value)
tr '\0' '\n' < /proc/$PID/environ | grep '^AIRLOCK_VLLM_BATCH_CONCURRENCY='
```
`.env` is gitignored — never commit it.

## Division of labor (agreed with Fathom)

- **Airlock (you):** flip `AIRLOCK_VLLM_BATCH_CONCURRENCY` + restart between
  trials; raise `AIRLOCK_VLLM_BATCH_TIMEOUT` if timeouts appear; flip
  `pii_redact` for the real run.
- **Fathom:** runs the timed benchmark batches with the **real** workload (real
  LME session bodies, exact prompt, `max_tokens`, `enable_thinking:false`) —
  throughput is decode-bound and workload-specific, so a generic benchmark
  wouldn't transfer. Reads out sessions/min, parseable/50, error-lines.
- **Operator:** owns vLLM-host flags (doing changes now).

## Sweep protocol (handshake)

1. You set `conc=N` in `.env`, restart, verify, reply **"conc=N ready"**.
2. Fathom runs trial: ~50 real LME sessions, identical content each trial.
3. Fathom posts sessions/min · parseable/50 · error-lines.
4. Sweep `conc ∈ {8, 16, 32}` (add 64 only if 32 still climbing with 0
   error-lines). **Stop at the knee** (next step <~10% gain, or any error-lines /
   throughput drop → back off one).
5. Then a **`max_tokens` 1536→1024 A/B** at the winning concurrency — Fathom
   verifies parse-rate stays 100% (densest validate session ≈ 1k tokens; 1024
   risks truncation → keep 1536/1280 if it truncates).
6. Lock concurrency + max_tokens, then pick scope (1.8k vs 7.1k) from real
   wall-clock. **At the lock-in restart: also flip `pii_redact:false`** (one
   restart).

**CURRENT STATE (updated 2026-06-15, later session):** `conc=8` is still set and
live in the proxy env. The operator's vLLM changes **have now landed and are
verified** (see updated findings below): `kv_cache_dtype=fp8`,
`enable_prefix_caching=True`, `max_num_seqs=16`. Both Airlock→vLLM paths were
re-verified against this new config this session:
- **Live** `/v1/chat/completions` via alias `qwen3.6-27b` → HTTP 200, clean
  `enable_thinking:false` completion.
- **Batch** full flow via `qwen36-27b-vllm-batch` → upload → create → poll →
  param-less content GET, 2/2 rows `200`, `enable_thinking:false` honored
  (passthrough confirmed in code: `vllm.py:openai_line_to_vllm` forwards `body`
  verbatim, only rewriting `body.model`).

Still awaiting Fathom's trial-1 numbers. **The knee should now extend past the
old ≤8** — `max_num_seqs` is 16, so re-sweep concurrency against the new ceiling
(consider jumping straight to a 8 vs 16 comparison).

## vLLM findings

**UPDATE 2026-06-15 (operator's changes APPLIED + verified).** Re-probed
`/metrics` after the operator's vLLM restart. The new config is now live:
`cache_dtype=fp8`, `enable_prefix_caching=True`, `max_num_seqs=16`
(`num_gpu_blocks=438`, `gpu_memory_utilization=0.95`, `max_model_len=65536`).
This implements two of the three "highest-value levers" listed below. Operator's
read: KV is **not** the bind (effective pool ≈598k tokens); the meaningful change
is **`max_num_seqs` 2→16**, which is now the real concurrency lever — so the
sweep knee should move well past the old ≤8. Watch parse-rate for the fp8 +
experimental-Mamba-prefix-caching caveats. The baseline analysis below is kept
for history; it describes the config **before** this restart.

### Baseline (historical) — THE REAL CEILING WAS KV CACHE, NOT AIRLOCK CONCURRENCY

Probed `192.168.1.45:8000` `/v1/models` + `/metrics` on 2026-06-15 (baseline,
**before** the operator's changes):

- `max_model_len=65536`; `gpu_memory_utilization=0.95` (near max);
  `cache_dtype=auto` (fp16); `enable_prefix_caching=False`.
- **`num_gpu_blocks=458 × block_size=16 = ~7,328 tokens` total KV pool**, shared
  across ALL concurrent sessions.
- → Only ~`7328 / (prompt+output tokens per session)` sessions fit at once. If
  the haystack prompt is multi-thousand tokens, that's **~1–4 concurrent** —
  meaning Airlock concurrency past the knee just fills vLLM's queue. Expect the
  sweep knee **low (≤8)** at the baseline config.

**Highest-value levers are vLLM-side (operator's restart), not Airlock:**
1. **`enable_prefix_caching=True`** — if the paired-haystack sessions share a
   common context/prompt prefix (they usually do), the haystack is stored in KV
   **once** instead of per session → multiplies concurrency *and* cuts prefill.
   Probably the single biggest win. Was OFF.
2. **`kv-cache-dtype=fp8`** — ~2× KV pool → ~2× concurrency headroom.
   Ampere-compatible (storage quant). Verify parse-rate.
3. Per-session token cuts (`enable_thinking:false`, `max_tokens`) — less KV per
   session → more fit. Fathom already does `enable_thinking:false`.

`max_model_len` and `gpu_memory_utilization` are basically tapped out.

**Watch during every trial** (on the vLLM host's `/metrics`):
`vllm:num_requests_waiting_by_reason{reason="capacity"}` and
`vllm:kv_cache_usage_perc`. capacity-waiting > 0 ⇒ KV-bound ⇒ more Airlock
concurrency won't help; the win is vLLM-side.

## Resume here (next session, in order)

1. **Re-probe vLLM** (the operator just changed it):
   ```bash
   curl -s http://192.168.1.45:8000/v1/models | python3 -m json.tool | grep -i max_model_len
   curl -s http://192.168.1.45:8000/metrics | grep -iE "cache_config_info|num_gpu_blocks|kv_cache_usage|requests_waiting_by_reason" | grep -v '^#'
   ```
   Capture the **new** `num_gpu_blocks` (KV pool), `enable_prefix_caching`,
   `cache_dtype`, `gpu_memory_utilization`. The sweep numbers are only meaningful
   relative to the current vLLM config — note what changed vs the baseline above.
2. **Confirm Airlock health + alias:** `curl /health/liveliness` (NOT /health);
   `GET /v1/models` (with master key) shows `qwen36-27b-vllm-batch`.
3. **Resume the sweep handshake** at the agreed concurrency; reply "conc=N ready".
4. Keep `pii_redact=true` for the 50-row benchmarks; flip to `false` (+ fix the
   stale comment) at the real-run lock-in restart; **re-enable after the run**.
5. If the operator reports new vLLM flags (prefix caching / fp8), expect the knee
   to move up — re-sweep concurrency against the new ceiling.

## Guardrails / gotchas

- Use `GET /health/liveliness`, never `GET /health`.
- `.env` is gitignored — edit, never commit. Don't print the master key.
- Concurrency/timeout/profile changes need a **restart** (read once at startup).
- Disabling `pii_redact` is operator-authorized for the real run only; re-enable
  after. Don't disable other guards.
- Restarting Airlock briefly interrupts the proxy (not vLLM). Restarting vLLM is
  the operator's call (interrupts serving on the 3090s).
