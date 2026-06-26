# Served-Backend Header Smoke Test

A small, reusable harness for verifying Airlock's **served-backend transparency
headers** end-to-end against a *running* proxy:

| Header | Meaning |
| --- | --- |
| `X-Airlock-Served-By` | The provider that **actually served** the call (read from the response, never guessed from the requested alias). |
| `X-Airlock-Served-Region` | The served region — emitted for **gateway** backends that report one (e.g. `vertex_ai`). |
| `X-Airlock-Mutations` | Compact, byte-bounded ledger of request mutations applied by Airlock. |

The goal: request a model **alias** and confirm `X-Airlock-Served-By` reports the
*real* backend provider behind it, not the alias name.

> Source of truth for the header logic: `airlock/transparency.py`
> (`served_headers`, `attribute_served_backend`) and the injection hook in
> `airlock/callbacks/model_override_headers.py`.

---

## SAFETY — read first

Production Airlock is **live on this host** (port **4000**, plus something on
**8090**). This harness is built so you never touch it:

- The client (`served_header_client.py`) **refuses** any `--base-url` containing
  `:4000` or `:8090`.
- The runbook (`run_isolated_instance.sh`) **refuses** ports 4000/8090 and any
  port already in use, and it only ever **copies** `config.yaml` / `.env`.
- Health probes hit **`/health/liveliness`** only — **never `/health`** (which
  fans out *live completions to every configured model*).

Standing up the isolated instance and making calls **costs real provider tokens**
and is a manual, operator-gated step.

---

## Files

- `served_header_client.py` — stdlib-only OpenAI-compatible client. Runnable
  (`python served_header_client.py ...`) and importable
  (`from served_header_client import chat_completion`).
- `run_isolated_instance.sh` — operator runbook to launch/stop an isolated
  second proxy on a spare port with isolated state.
- `README.md` — this file.

---

## Step 1 — stand up an isolated instance (operator)

```bash
# default port 4137; override with PORT=<n>
./dev/smoketest/run_isolated_instance.sh start
# ... runs the smoke tests ...
./dev/smoketest/run_isolated_instance.sh stop
```

`start` copies `config.yaml` + `.env` into `dev/smoketest/.runtime/`, then
**appends overrides** to the copied `.env` so the test instance never writes to
production state. It launches `uv run airlock start --port <PORT>` in the
background and records the PID; `stop` kills only that PID.

### State-isolation env vars the operator must set

These are written automatically by `run_isolated_instance.sh` into the copied
`.env`; listed here so you can verify (or set them by hand for a manual launch):

| Env var | Set to | Why |
| --- | --- | --- |
| `AIRLOCK_HOST` | `127.0.0.1` | Loopback only. |
| `AIRLOCK_PORT` | `4137` (or chosen) | Spare port, never 4000/8090. |
| `AIRLOCK_CONFIG` | `.runtime/config.yaml` | Use the copied config. |
| `AIRLOCK_LOG_DIR` | `.runtime/state/logs` | Logs + **fallback** state dir (`airlock/cli/main.py`, `airlock/datastore.py`). |
| `AIRLOCK_STATE_DIR` | `.runtime/state` | `airlock.db` + `cb_state.json` checkpoint (`airlock/datastore.py`, `airlock/proxy.py`). |
| `AIRLOCK_S3_BUCKET` | *(blank)* | `s3_logger` discards instead of uploading to the shared bucket. |
| `AIRLOCK_SQL_URL` | *(blank)* | `sql_logger` disables itself. |
| `AIRLOCK_ENABLE_FATHOMDB` | `0` | Keep FathomDB storage off for the test. |
| `AIRLOCK_ENFORCE_MODE` | `observe` | No blocking surprises during the smoke test. |

`AIRLOCK_MASTER_KEY` is **inherited from the copied `.env`** — it is the bearer
token you pass to the client as `--api-key`.

> Uncertainty / operator judgement: the two filesystem state paths above
> (`AIRLOCK_LOG_DIR`, `AIRLOCK_STATE_DIR`) are the only local-disk shared-state
> dirs in the codebase. The remote sinks (`AIRLOCK_S3_*`, `AIRLOCK_SQL_URL`,
> FathomDB) are neutralized defensively. If your production `.env` configures any
> *other* writable backend (e.g. a custom callback with its own path), neutralize
> it in the copied `.env` too before starting.

---

## Step 2 — run the smoke test (client)

```bash
BASE=http://127.0.0.1:4137
KEY="$AIRLOCK_MASTER_KEY"   # from the runtime .env

# 0) liveness (no completion, no token spend)
python dev/smoketest/served_header_client.py --base-url $BASE --health

# 1) NATIVE alias — expect X-Airlock-Served-By: gemini, no region
python dev/smoketest/served_header_client.py \
  --base-url $BASE --api-key "$KEY" --model gemini-3.5-flash-aistudio

# 2) GATEWAY alias — expect X-Airlock-Served-By: vertex_ai, region: global
python dev/smoketest/served_header_client.py \
  --base-url $BASE --api-key "$KEY" --model gemini-3.5-flash-vertex

# 3) streaming variant (headers still emitted; mutations fire on streams too)
python dev/smoketest/served_header_client.py \
  --base-url $BASE --api-key "$KEY" --model gemini-3.5-flash-vertex --stream

# 4) explain envelope (non-streaming) — adds the additive `airlock` body block
python dev/smoketest/served_header_client.py \
  --base-url $BASE --api-key "$KEY" --model claude-opus --explain

# JSON output for scripted assertions
python dev/smoketest/served_header_client.py \
  --base-url $BASE --api-key "$KEY" --model gemini-3.5-flash-vertex --json
```

---

## Step 3 — what to verify

For each call, confirm the served-by header reports the **real backend**, not the
requested alias:

1. **Native alias** (`gemini-3.5-flash-aistudio`): `X-Airlock-Served-By: gemini`,
   **no** `X-Airlock-Served-Region`.
2. **Gateway alias** (`gemini-3.5-flash-vertex`): `X-Airlock-Served-By:
   vertex_ai` **and** `X-Airlock-Served-Region: global`.
3. The body `model` field shows the served model id; the requested alias is what
   you sent. Served-by must reflect the provider, regardless of alias name.
4. With `--explain`, a non-streaming response body carries an additive
   `airlock: { "mutations": [...] }` envelope **iff** mutations were recorded
   (absent envelope just means no mutations for that call — not a failure).

`X-Airlock-Served-By` is **omitted entirely** when the provider can't be
determined (the proxy never guesses) — so an absent header on an exotic/error
path is by design, not a bug.

---

## Scenario — restart durability (0.5.1 STORE-seam)

Proves that captured provider spend survives a proxy restart (Q3) and that the
fix lives in the right process (FIX-1: checkpoint/restore run in the litellm
**child**, where spend is mutated — not the launcher). The no-network subprocess
round-trip unit test
(`tests/test_fast_spend_store.py::TestSubprocessRoundTrip`) is the automated
durability proof; this is the live operator confirmation.

**The agent did NOT run this.** A human operator runs it on the isolated
dir+port at sign-off.

```bash
# 1) start the isolated instance (writes spend_state.json + cb_state.json into
#    $RUNTIME_DIR/state via AIRLOCK_STATE_DIR)
PORT=4137 ./dev/smoketest/run_isolated_instance.sh start

# 2) drive a few BILLED calls so a provider accrues rolling spend
for i in 1 2 3 4 5; do
  python dev/smoketest/served_header_client.py \
    --base-url http://127.0.0.1:4137 --model <real-billed-alias> \
    --api-key "$AIRLOCK_MASTER_KEY" --prompt "ping"
done

# 3) note the provider's rolling daily spend (advisor / admin / logs)

# 4) restart — stops launcher + reaps the orphaned child, then relaunches against
#    the SAME state dir (prepare never deletes it)
PORT=4137 ./dev/smoketest/run_isolated_instance.sh restart

# 5) re-check the provider's spend: it must be NON-ZERO and ~equal to step 3
#    (rehydrated from spend_state.json on child startup, age-bounded to the 24h
#    window). A reset-to-0 here means restart durability regressed.
```

Verify:
- Provider spend after restart is the in-window total from before (not 0, not
  double-counted — restore is idempotent).
- Spend accuracy holds even past 1000 calls/day (R5: bucketed µ$, no deque cap).
- Breaker state (`cb_state.json`) recovery still rides the same restart, gated to
  its 5-minute freshness window; spend restore is age-bounded, NOT 5-min gated.

---

## Scenario — budget warn-ratio (0.5.1 SET-warnratio)

Proves that the **single, configurable** `budget_warn_ratio` (env
`AIRLOCK_BUDGET_WARN_RATIO`, `airlock_settings.budget_warn_ratio`, default `0.8`)
drives the client-facing `X-Airlock-Budget-State: near_limit` header at the
configured fraction of a provider's daily budget — and that the router's proactive
swap (`X-Airlock-Model-Override`) now fires at the **same** point (behavior-change
#2: moved from the old hardcoded `0.9` to the configured ratio).

The automated proof is the unit coverage in
`tests/test_fast_monitor.py::TestBudgetWarn` (monitor warn) and
`tests/test_fast_router.py::TestBudgetAwareness` (router swap), which assert both
read `get_settings().budget_warn_ratio`. This is the live operator confirmation that
the header surfaces end-to-end.

**The agent did NOT run this.** A human operator runs it on the isolated dir+port at
sign-off (it costs real provider tokens).

```bash
# 1) start the isolated instance with a LOW budget + an easy-to-cross warn ratio so a
#    few cheap calls cross the threshold. Set these in the copied .runtime/.env BEFORE
#    start (or export before run_isolated_instance.sh start):
#      AIRLOCK_BUDGET_WARN_RATIO=0.5
#      AIRLOCK_PROVIDER_BUDGETS='{"<provider-of-your-alias>": 0.01}'
PORT=4137 ./dev/smoketest/run_isolated_instance.sh start

# 2) drive BILLED calls until the provider's rolling spend crosses ratio*budget
#    (0.5 * 0.01 = $0.005 here). Capture headers each time:
for i in 1 2 3 4 5; do
  python dev/smoketest/served_header_client.py \
    --base-url http://127.0.0.1:4137 --model <real-billed-alias> \
    --api-key "$AIRLOCK_MASTER_KEY" --prompt "ping" --json | \
    python -c 'import sys,json; h=json.load(sys.stdin)["headers"]; \
print("budget-state:", {k:v for k,v in h.items() if k.lower()=="x-airlock-budget-state"}); \
print("model-override:", {k:v for k,v in h.items() if k.lower()=="x-airlock-model-override"})'
done

PORT=4137 ./dev/smoketest/run_isolated_instance.sh stop
```

Verify:
- Once rolling spend ≥ `budget_warn_ratio * budget`, the response carries
  `X-Airlock-Budget-State: near_limit` (the monitor warn path).
- At the **same** threshold, an unpinned request that has a healthy alternative shows
  `X-Airlock-Model-Override` (the router proactive swap) — proving both honor the one
  ratio. Lowering `AIRLOCK_BUDGET_WARN_RATIO` makes both fire sooner; a `0`/absent
  budget yields neither header regardless of ratio (AC-0).

---

## Alias analysis (from `config.yaml`, 39 models)

Served-by values come from the provider prefix of each alias's `litellm_params.model`,
classified in `airlock/transparency.py`:

- `_NATIVE_PROVIDERS = {anthropic, openai, gemini}`
- `_GATEWAY_PROVIDERS = {bedrock, azure, vertex_ai}`

### ⭐ Ideal native-vs-gateway pair (same underlying Gemini model)

The config defines the *same* Gemini models behind both an AI-Studio (native) and
a Vertex (gateway) alias — the perfect A/B for served-by verification:

| Alias | Backend (`litellm_params.model`) | Kind | Expected `X-Airlock-Served-By` | Expected Region |
| --- | --- | --- | --- | --- |
| `gemini-3.5-flash-aistudio` | `gemini/gemini-3.5-flash` | **native** | `gemini` | *(none)* |
| `gemini-3.5-flash-vertex` | `vertex_ai/gemini-3.5-flash` | **gateway** | `vertex_ai` | `global` |
| `gemini-3.1-pro-aistudio` | `gemini/gemini-3.1-pro-preview` | **native** | `gemini` | *(none)* |
| `gemini-3.1-pro-vertex` | `vertex_ai/gemini-3.1-pro-preview` | **gateway** | `vertex_ai` | `global` |

> Recommended smoke pair: **`gemini-3.5-flash-aistudio`** (native) vs
> **`gemini-3.5-flash-vertex`** (gateway). Same model, different served-by — so
> any divergence is purely the routing/attribution path.

### Other concrete native aliases

| Alias | Backend | Expected `X-Airlock-Served-By` |
| --- | --- | --- |
| `claude-opus` | `anthropic/claude-opus-4-8` | `anthropic` |
| `claude-sonnet` | `anthropic/claude-sonnet-4-6` | `anthropic` |
| `gpt-5` | `openai/gpt-5.5` | `openai` |
| `gemini-flash` | `gemini/gemini-2.5-flash` | `gemini` |

### Gateway aliases present

| Alias | Backend | Expected `X-Airlock-Served-By` | Region |
| --- | --- | --- | --- |
| `gemini-3.5-flash-vertex` | `vertex_ai/gemini-3.5-flash` | `vertex_ai` | `global` |
| `gemini-3.1-pro-vertex` | `vertex_ai/gemini-3.1-pro-preview` | `vertex_ai` | `global` |

> No **Bedrock**-routed alias is configured in this `config.yaml` (no `bedrock/`
> model), so Vertex is the only gateway backend available for the gateway side of
> the test. If a `bedrock/...` alias is added later, it should report
> `X-Airlock-Served-By: bedrock` with its `aws_region_name` as the region.

### Caveats / non-standard providers

- `gemini-coding` → `enhanced/gemini-coding` uses a custom `enhanced` handler
  (`airlock/providers/enhanced_passthrough.py`); its served-by is not a standard
  native/gateway provider and may be omitted/`unknown`. **Avoid it** for this
  smoke test.
- vLLM / local aliases (`qwen*-vllm-batch`, `gemma-4`, `kimi-dev`, `qwen3-32b`,
  `qwen3.6-27b`) route via `openai/...` to a local server; served-by would read
  `openai`. Not useful for the native-vs-gateway distinction.
- `mistral/*`, `perplexity/*`, `tavily/*` are not in either provider set, so they
  classify as `unknown` and `X-Airlock-Served-By` is omitted. Don't use them here.
