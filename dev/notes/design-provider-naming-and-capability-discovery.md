# Design: Provider-Explicit Model Naming & Machine-Discoverable Capabilities

**Date:** 2026-06-26
**Status:** **As-built — shipped in 0.5.2.** (v2 design resolved the codex
design-review BLOCK — model_alias collision, shared provider classifier,
vertex-batch overclaim — see §9.) Plan: `dev/plans/0.5.2-plan.md`; board:
`dev/plans/runs/STATUS-0.5.2.md`; orchestrator:
`dev/plans/prompts/0.5.2-ORCHESTRATOR.md`. Branch `feat/0.5.2-naming` (cut from
`main` @ `c3eaed7`).
**Shipped:** `config.yaml` `provider/model` aliases (dual-listed) +
`airlock/capability.py` (single-source `endpoints`/served-by helper),
`airlock/models_seam.py` (additive `airlock` object on `GET /v1/models`),
`model_info` capability on `GET /model/info` (`airlock/proxy.py`), and the
catalog/naming regression lock (Pack 0.5.2-COMPAT-tests, merge `4610db0`). User
docs: `docs/guide/{routing,batch,vertex-batch,provider-observability}.md` +
`CHANGELOG.md` (0.5.2). Vertex remains **chat-only** at `vertex_location: global`
(batch is region-gated and not advertised).
**LiteLLM target:** the running pin (1.89.x); the `/v1/models` + `/model/info`
response shapes and the router exact-match-before-provider-parse behavior in §4
are verified against that version on a separate dir+port (never the live proxy).
**Scope:** additive entries in `config.yaml model_list` (Appendix A of the plan);
a targeted change to `airlock/fast/router.py` provider inference; a new
`airlock/capability.py` helper (single source of truth for `endpoints`); a new
additive ASGI seam that augments `GET /v1/models`; `model_info:` blocks on the
catalog. **No request-path behavior change.**
**Traces to:** `dev/user-needs.md` UN-21 (Discoverable Provider Selection),
UN-22 (Declared Capabilities).
**Related:** [design-mutation-and-provider-transparency.md](design-mutation-and-provider-transparency.md)
(`X-Airlock-Served-By`/`-Region` — the *verify* surface), [design-routing-fanout-guardrails.md](design-routing-fanout-guardrails.md)
(`X-Airlock-Model-Override`, auto-pin), [design-provider-quota-observability.md](design-provider-quota-observability.md)
(`infer_provider` consumers: breaker/accounting/quarantine).

---

## 1. Summary

Airlock's catalog uses **three inconsistent naming conventions** that conflate two
orthogonal axes — *provider/quota* (AI Studio vs Vertex) and *API surface* (sync vs
batch) — and encode neither discoverably:

- bare names with the provider implied (`claude-opus`, `gpt-5`, `gemini-3.5-flash`);
- a `-vertex` **provider** suffix vs an `-aistudio`/`-batch` **capability** suffix
  (`config.yaml:129,150,173,195`), where `-aistudio` is byte-identical to the bare
  alias on the sync path (`:150` `gemini/gemini-3.5-flash` == `:77`);
- separate `-batch` twins that duplicate a sync entry solely to carry an
  `airlock_batch` marker (`gemini-3.5-flash-aistudio :150`, `mistral-large-batch
  :173`).

A researcher pinned `gemini-3.5-flash-aistudio` for a sweep, watched it hang while
plain `gemini-3.5-flash` worked, and concluded it was "a separate broken
deployment." It is not — both resolve to `gemini/gemini-3.5-flash` on sync; the
suffix only changes the *batch* gateway path.

**The fix, as data not string-parsing:**

1. **Prefix = provider, everywhere.** Add a `provider/model` alias for every catalog
   entry (`anthropic/…`, `openai/…`, `aistudio/…`, `vertex/…`, `mistral/…`,
   `perplexity/…`, `tavily/…`, `vllm/…`) — Appendix A of the plan. The bare name
   stays as a documented, ops-repointable default for multi-provider models;
   legacy bare/`-aistudio`/`-vertex`/`-batch` names are **dual-listed and
   deprecated** (removed in 0.6.0 — §2.3).
2. **Capability as metadata.** Each entry publishes a `model_info` block —
   `provider`, `region?`, `endpoints:[chat|batch]`, `underlying`, `deprecated?` —
   on LiteLLM's `/model/info`, and an additive `airlock:{…}` object on
   `GET /v1/models`. `endpoints` is **computed from the real wiring** by one
   helper (`airlock/capability.py`), so it cannot drift from routing.

**What's additive vs. what needs real code (post-review):** served-by attribution
is post-call and pinning is model-string-agnostic, so those two need **no change**.
But the change is *not* purely additive: adding `aistudio/` and `vertex/` entries
that share a stripped provider-model surfaces a **latent alias-table collision**
(`model_alias.load_from_config`) that could silently repoint the bare default to the
wrong provider. So 0.5.2 carries **three targeted code changes**, all in the
NAME-aliases pack:

1. **`model_alias` collision-safe resolution** (§4.1) — two-pass loading (explicit
   keys immutable) **and** a provider-aware prefix-strip; without both, new entries
   or native `vertex_ai/…` inputs can silently repoint to the wrong provider.
2. **A shared `airlock_provider_for()` classifier** (§4.2) used by both
   `set_router_config` and the capability helper, so `enhanced/…` and the slash
   aliases are attributed to the real served-by provider in one place.
3. **`capability.endpoints_for()` derives `batch` from real wiring** (§4.5) —
   marker-or-*regional*-vertex, never a blanket `vertex_ai/` ⇒ batch (§2.5).

Plus **one assumption to prove with a test**: `aistudio/`/`vertex/` resolve to their
explicit entries and are never re-parsed as native provider routing (§4.1).

---

## 2. Hard design decisions

These resolve the plan's open questions (§"Open questions for the human") and the
register N1–N6. Decisions 2.2–2.4 were settled with the operator at kickoff
(2026-06-26, STATUS §7); the rest follow from the code map in §4.

### 2.1 Alias grammar (N1)

- **Grammar:** `<provider>/<model>`, a single `/`. `<provider>` ∈
  `{anthropic, openai, aistudio, vertex, mistral, perplexity, tavily, vllm}` — the
  **airlock-facing provider token**, deliberately *distinct* from LiteLLM's native
  routing tokens where they would collide: we use `aistudio/` (native is `gemini/`)
  and `vertex/` (native is `vertex_ai/`). The other six equal the native token but
  are still resolved by exact catalog match, never re-parsed (§4.1).
- **Bare names** remain as convenience aliases. For single-provider models the bare
  name is permanent. For multi-provider models (Gemini) the bare name is a
  **documented default** (decision 2.2), ops-repointable; the *prefixed* name is the
  stable client contract.
- **`smart`** stays as-is — a routing directive, never a provider model, never
  pinned (`guardian.py:84` short-circuits `original_model == "smart"`). It gets no
  prefix and no `model_info.endpoints`.

### 2.2 Bare-alias default provider (settled)

Bare `gemini-3.5-flash` (and the other bare Gemini aliases) **stays → AI Studio**
(`gemini/`, the incumbent). No request-path change. Documented as ops-repointable;
clients who need a guaranteed provider pin `aistudio/…` or `vertex/…`.

### 2.3 Deprecation window (settled)

Legacy bare-where-superseded / `-aistudio` / `-vertex` / `-batch` aliases are
**deprecated in 0.5.2, removed in 0.6.0** (one-minor window). 0.5.2 only marks them
(`model_info.deprecated: true`) and keeps them fully functional (dual-listed). A
changelog deprecation notice names 0.6.0 as the removal release. **Removal is not in
this release** — a diff that deletes a legacy alias is a review BLOCK.

### 2.4 N6 consolidation (settled): one entry per model, capability in metadata

**Consolidate.** The `-batch`/`-aistudio` twins fold into the single
`provider/model` entry that serves sync **and** advertises `endpoints:[chat,batch]`:

| Legacy twins | Consolidated entry (new) | Batch wiring | `endpoints` (0.5.2) |
|---|---|---|---|
| `gemini-3.5-flash-aistudio` (`:150`) | `aistudio/gemini-3.5-flash` | `airlock_batch backend: aistudio` | chat, batch |
| `gemini-3.1-pro-aistudio` (`:158`) | `aistudio/gemini-3.1-pro` | `airlock_batch` | chat, batch |
| `gemini-3.5-flash-vertex` (`:129`) | `vertex/gemini-3.5-flash` | vertex-native, **`vertex_location: global`** | **chat** (batch gated — see ⚠) |
| `gemini-3.1-pro-vertex` (`:136`) | `vertex/gemini-3.1-pro` | vertex-native, **`vertex_location: global`** | **chat** (batch gated — see ⚠) |
| `mistral-large-batch` (`:173`) | `mistral/mistral-large` | `airlock_batch backend: mistral` | chat, batch |
| `mistral-small-batch` (`:181`) | `mistral/mistral-small` | `airlock_batch` | chat, batch |
| `qwen36-27b-vllm-batch` (`:195`) | `vllm/qwen3.6-27b` | `airlock_batch backend: vllm` | chat, batch |

The legacy twin `model_name`s are **retained, dual-listed, deprecated** (decision
2.3) — same `litellm_params`, same `airlock_batch` marker — so existing batch
clients keep working through the window.

> ⚠ **Vertex batch is NOT advertised at `vertex_location: global`** (codex BLOCK 3).
> Vertex `BatchPredictionJob` for the 3.x models needs a **regional** location;
> the config's own comment (`config.yaml:126-128`) warns `global` may not support
> batch. Advertising `endpoints:[chat,batch]` for a deployment that can't actually
> batch would violate UN-22 ("provably match routing"). So in 0.5.2 the `vertex/…`
> entries publish **`[chat]`**; `endpoints_for()` (§4.5) lights up `batch`
> automatically iff a *regional* `vertex_location` is configured. The N6
> consolidation (fold `-vertex` → `vertex/…`) still holds; only the advertised
> capability is made truthful. This **supersedes** the plan's Appendix-A optimistic
> `chat, batch` for the vertex rows (the plan is aspirational; the design is
> truthful).

**Consequence for the bare names:** bare `gemini-3.5-flash` (AI Studio) is
**sync-only** (`endpoints:[chat]`, no marker). Batch on AI Studio Gemini is reached
via `aistudio/gemini-3.5-flash`. This is intentional: the bare name is the
convenience default; the prefixed name is where batch capability is declared.

### 2.5 Capability schema (N3) and its single source of truth

`model_info` block per entry (LiteLLM passes config `model_info` through verbatim on
`/model/info`; we add the same fields to `/v1/models` via the §4.4 seam):

```yaml
model_info:
  airlock_provider: <served-by token>   # anthropic|openai|gemini|vertex_ai|mistral|perplexity|tavily|openai(vllm)
  region: <str|null>                    # vertex_location for vertex_ai; null otherwise
  endpoints: [chat]                     # or [chat, batch]
  underlying: <litellm model string>    # e.g. "gemini/gemini-3.5-flash" — ties bare+prefixed+legacy aliases together
  deprecated: <bool>                    # true on legacy/suffix aliases
```

- **`airlock_provider` == the served-by token**, i.e. exactly what
  `infer_provider(alias)` returns and what `X-Airlock-Served-By` reports post-call
  (§4.3). This is **load-bearing for the discover→pin→verify recipe**: a client
  reads `airlock_provider` from `/model/info`, pins the alias, and verifies the
  header equals it. They MUST be the same value. (Field is named `airlock_provider`,
  not `provider`, to avoid clashing with any LiteLLM-reserved `model_info` key.)
- **vLLM wrinkle (recorded):** `vllm/qwen3.6-27b` routes through `openai/`
  (openai-compatible), so its served-by token is `openai`, and
  `airlock_provider: openai`. The `vllm/` *prefix* is the human-facing family hint;
  `underlying: "openai/qwen3.6-27b"` (api_base reveals the local host) disambiguates.
  We keep `airlock_provider` = served-by truth so *verify* holds.
- **`endpoints` single source of truth (N3, the anti-duplication rule):** the value
  written in `model_info.endpoints` is **validated against** `capability.endpoints_for(entry)`
  (§4.5) by a config-consistency test, and the `/v1/models` seam (§4.4) computes its
  `endpoints` from the **same** helper — so config-validation and the served surface
  share one source. `batch ∈ endpoints` **iff** (a) the entry carries an
  `airlock_batch` marker, **or** (b) it is a `vertex_ai/` model **with a regional
  `vertex_location`** (not `global`/unset). Rule (b) is the codex-BLOCK-3 fix: a
  bare `vertex_ai/` prefix is **not** sufficient — Vertex batch needs a region, and
  the current entries use `global`, so they advertise `[chat]` until repointed. The
  published metadata cannot silently disagree with routing — the consistency test
  fails CI if it does.
- **`/model/info` passthrough must be proven, not assumed.** We publish `model_info`
  in config and rely on LiteLLM surfacing it on `/model/info`. CAP-modelinfo adds a
  **smoke test on the separate dir+port** asserting `/model/info` actually returns
  our `endpoints`/`airlock_provider`/`underlying` fields for the pinned LiteLLM
  version (rather than assuming the passthrough). If LiteLLM strips unknown
  `model_info` keys, the fallback is to surface the same fields through the airlock
  `/v1/models` seam (§4.4), which we own.

### 2.6 `/v1/models` augmentation seam (N4)

**ASGI response-transform middleware**, not a FastAPI route override — it mirrors the
proven batch-gateway install discipline (`batch/middleware.py:546-583`, dual
pre-start/post-start) so import-order cannot break it, and it is **purely additive**
(adds an `airlock` sub-object to each model; never removes/renames a standard
field). Rationale over a route override: LiteLLM owns `/v1/models`; wrapping the
ASGI response is non-invasive and survives LiteLLM upgrades that re-register the
route. (§4.4.)

---

## 3. The naming/capability register — resolution (N1–N6)

| # | Concern | Resolution |
|---|---|---|
| N1 | Provider in the name | `provider/model` prefix for the whole catalog (Appendix A); bare = default (2.2); one rule everywhere. |
| N2 | Alias ↔ provider inference | `infer_provider` returns the **served-by** token for every new alias via the **catalog map** (`router.py:287-308`, built from each entry's `litellm_params.model`), *not* via the bare-prefix fallback. Do **not** add `aistudio`/`vertex` to `_PROVIDER_PREFIXES` — that would mis-attribute (§4.2). |
| N3 | Batch capability is a guess | `endpoints` published per entry, **derived/validated** by `capability.endpoints_for()` from the `airlock_batch` marker / vertex-native batch (2.5, §4.5). |
| N4 | `/v1/models` is bare | Additive ASGI seam folds `airlock:{airlock_provider,endpoints,underlying,region,deprecated}` into each model (2.6, §4.4). |
| N5 | Back-compat | Legacy names dual-listed (same `litellm_params` + marker), `deprecated:true`; `fallbacks`/`cost_tiers`/smart targets migrated to the **bare** internal names which are unchanged (§4.6). |
| N6 | Capability-in-name redundancy | Consolidated (2.4): one `provider/model` entry serves sync + advertises batch; legacy twins dual-listed for the window. |

---

## 4. Wiring (integration points — verified file:line)

All anchors verified on `feat/0.5.2-naming` (== `main` HEAD `c3eaed7`). The
implementer re-verifies against branch HEAD before editing (catalog drifts).

### 4.1 Resolution, the alias-table collision (codex BLOCK 1), and the collision proof

**The rewrite happens in Airlock first, before LiteLLM.** `guardian.py:295` calls
`alias_table.resolve(model_name)` and `:302` sets `data["model"] = resolved` — so the
*airlock* alias table, not LiteLLM's router, is the authoritative resolution point.
Any "LiteLLM exact-matches first" reasoning is therefore insufficient; correctness
must hold in `model_alias`.

`resolve()` (`fast/model_alias.py:288-351`): exact lookup against `_exact`
(`:304-305`), then a **provider-prefix-strip fast path** (`:310-316`) that, on a
miss, strips the leading `provider/` and retries the bare lookup. The danger is in
**`load_from_config` (`:235-264`)**, which for *every* entry inserts a `bare` key =
the stripped provider-model (`:253,255`): `gemini/gemini-3.5-flash`,
`vertex_ai/gemini-3.5-flash`, and the new `aistudio/…`+`vertex/…` aliases all strip
to the **same** bare key `gemini-3.5-flash` and overwrite each other **last-write-
wins**. Today this is benign-ish (bare and `-aistudio` share sync params); once we
add `vertex/gemini-3.5-flash` (→ `vertex_ai/`), a load-order accident could make
`resolve("gemini-3.5-flash")` return the **Vertex** entry — silently repointing the
bare default and **violating decision 2.2**. This is the codex BLOCK.

**Fix (NAME-aliases) — two-pass, collision-safe `load_from_config`:**
1. **Pass 1 — explicit `model_name` keys are authoritative and immutable.** Insert
   `_exact[alias.lower()] = alias` for every entry first; never overwrite one later.
   ⇒ the explicit bare entry `model_name: gemini-3.5-flash` owns
   `_exact["gemini-3.5-flash"]` → bare stays AI Studio (decision 2.2). The explicit
   `aistudio/…`/`vertex/…` keys own their own slugs → they resolve to themselves.
2. **Pass 2 — variant keys (bare-stripped, version-stripped) added only when (a) not
   already an explicit alias key and (b) unambiguous** (claimed by exactly one
   entry). A bare/variant key claimed by ≥2 entries is **dropped** (left to the
   exact/fuzzy paths), never silently bound to the last writer. Ambiguity is logged
   at startup via the existing `_log_table` (`:267-286`).

This preserves every existing single-provider convenience variant (still
unambiguous) while making the multi-provider Gemini bare/stripped keys
collision-safe.

**The resolve-time prefix-strip is also lossy (codex re-review BLOCK).** Pass-2
fixes load-time, but `resolve()`'s prefix-strip fast path (`:310-316`) strips *any*
`provider/` and retries the **bare** body — so a native input
`vertex_ai/gemini-3.5-flash` strips to `gemini-3.5-flash` and resolves to the
explicit **bare AI Studio** entry: a silent cross-provider repoint (Vertex → AI
Studio). Fix: make the strip **provider-aware**. Build at load time a
`(provider_token, bare_body) → alias` index plus the set of providers each bare body
appears under; in `resolve()`:

1. exact full-string match wins (explicit aliases incl. `vertex/…`, `aistudio/…`,
   and any native string we register) — unchanged `:304-305`.
2. on a slash miss, normalize the prefix to a provider token (`aistudio`→`gemini`,
   `vertex`→`vertex_ai`, native tokens pass through) and look up
   `(provider_token, bare_body)`: a unique hit resolves (so
   `vertex_ai/gemini-3.5-flash` → the **vertex** entry, `gemini/gemini-3.5-flash` →
   the **AI Studio** entry — both correct). If the bare body is owned by a **single**
   provider, resolve regardless of prefix (preserves `openai/claude-haiku` →
   `claude-haiku` SDK normalization). If the body is multi-provider and the prefix
   does **not** disambiguate, **return `None` directly — do NOT fall into fuzzy
   scoring and do NOT cache** (a silent fuzzy pick would reintroduce the repoint).
3. non-slash unknowns → fuzzy as today.

This both closes the repoint hole **and** makes native `vertex_ai/`/`gemini/` strings
route to the right deployment.

**Tests (RED first):** `resolve("gemini-3.5-flash")` → `gemini-3.5-flash` (AI
Studio); `resolve("vertex/gemini-3.5-flash")` and `resolve("vertex_ai/gemini-3.5-flash")`
→ the **vertex** entry (never bare AI Studio); `resolve("aistudio/gemini-3.5-flash")`
and `resolve("gemini/gemini-3.5-flash")` → the AI Studio deployment;
`resolve("openai/claude-haiku")` → `claude-haiku` (single-provider, prefix ignored);
legacy `-aistudio`/`-vertex` still resolve to themselves.

**The router collision proof (still required).** With the explicit entries in place,
both Airlock (`_exact`) and LiteLLM's router resolve `aistudio/…`/`vertex/…` by
exact `model_name` before any provider parsing; `aistudio`/`vertex` are *not* native
LiteLLM provider tokens (those are `gemini`/`vertex_ai`). NAME-aliases proves on the
separate dir+port that a pinned request to `aistudio/gemini-3.5-flash` and
`vertex/gemini-3.5-flash` resolves to the right backend and is **not** re-parsed as
an unknown provider (404/mis-route).

### 4.2 Provider inference (N2) — the shared classifier

`infer_provider()` (`fast/router.py:371-388`): **catalog-first** (`:382-384`, the
`_alias_provider_map`), then a `_PROVIDER_PREFIXES.startswith` fallback (`:134-145,
:385-387`). The catalog map is built by `set_router_config()` (`:287-308`): for each
`model_list` entry it sets `_alias_provider_map[model_name] = litellm_params.model.split("/",1)[0]`
when the litellm string contains `/` (`:305-306`).

⇒ A new entry `model_name: aistudio/gemini-3.5-flash` with `litellm_params.model:
gemini/gemini-3.5-flash` makes the catalog map return **`gemini`** for that alias —
the correct served-by token — **with no code change**. Likewise `vertex/…` →
`vertex_ai`, `anthropic/…` → `anthropic`, etc.

**But the raw `split("/")[0]` misclassifies `enhanced/`** (codex BLOCK 2):
`gemini-coding` → `litellm_params.model: enhanced/gemini-coding` → the catalog map
would store provider `enhanced`, which is wrong for provider protection,
accounting, and Gemini request semantics (the wrapped call is physically `gemini/…`;
`guardian.py:473` feeds `infer_provider` into `apply_gemini_request_semantics`).

**The change — one shared classifier, `airlock_provider_for()`:** introduce a single
function (in the new `airlock/capability.py`, imported by `router.set_router_config`
**and** the capability surfaces) that maps an entry/model string to its **served-by
provider token**:

- normal case: `litellm_params.model.split("/",1)[0]`, normalized
  (`vertex_ai_beta`→`vertex_ai`);
- **`enhanced/<profile>`** → resolve through `enhanced_profile.target_model`
  (`config.yaml:110`, `gemini/…`) → `gemini`;
- never returns a bare-prefix display token for `aistudio/`/`vertex/` (those come
  from the underlying `gemini/`/`vertex_ai/` string, not the alias prefix).

`set_router_config` builds `_alias_provider_map[model_name] = airlock_provider_for(entry)`
so the catalog is authoritative for *every* alias, and the capability helper uses the
same function — one classifier, two consumers (DRY, no second place to drift).

- **Do NOT add `aistudio`/`vertex` to `_PROVIDER_PREFIXES`.** That fallback returns
  the *prefix itself*; `aistudio`'s served-by is `gemini`, not `aistudio`. Adding it
  would mis-attribute any alias that misses the catalog. (The fallback stays as the
  last resort for truly uncataloged strings; on a `provider/model` miss it strips the
  known-airlock prefix and re-checks the bare model, mirroring `model_alias.py:310-316`.)
- Covered by tests: `infer_provider("aistudio/gemini-3.5-flash") == "gemini"`,
  `infer_provider("vertex/gemini-3.5-flash") == "vertex_ai"`,
  `infer_provider("aistudio/gemini-coding") == "gemini"` (enhanced/ path), and all of
  Appendix A.

**Consumers that depend on this** (must keep attributing correctly): breaker
(`circuit_breaker.py:69,107`), guardian quarantine (`guardian.py:321,405,419,473,508`),
monitor (`monitor.py:37`), enterprise logger (`enterprise_logger.py:34`),
state/TUI (`state.py:1417`, `overview.py:665`).

### 4.3 Pinning & served-by are automatically correct

- **Auto-pin** (`guardian.py:_is_client_pinned :83-91`, `_lock_pinned_request
  :204-224`): pinned = concrete, non-`smart`, no `cost_tier`/`prefer_provider`
  directive. **No provider parsing.** A slash-prefixed concrete alias is pinned
  exactly like a bare concrete name (fallbacks/retries off, 429-not-swap). No change.
- **Served-by** (`transparency.py`): `attribute_served_backend()` (`:178-225`) reads
  `custom_llm_provider` from the **post-call** `_hidden_params` (`:194`), normalized
  by `_normalize_served_provider()` (`:161-175`, `gemini`+AI-Studio-host → `gemini`,
  `vertex_ai_beta` → `vertex_ai`); `served_headers()` (`:297-304`) emits
  `X-Airlock-Served-By`/`-Region`. **It never inspects the request model string** —
  so because every new alias points at the same `litellm_params`, served-by is
  automatically right. No change. (This is why `airlock_provider` in `model_info`
  must equal the served-by token, 2.5.)

### 4.4 `/model/info` and the `/v1/models` seam (N4)

- **`/model/info` is LiteLLM-native** and surfaces each entry's `model_info` block
  verbatim (`litellm/proxy/proxy_server.py:12355-12395` `_get_proxy_model_info`
  reads `model.get("model_info", {})`; `litellm/router.py:8245` pops/stores it per
  deployment). **Refinement (v4, post-investigation — supersedes "hand-write config
  blocks"):** with **73** catalog entries, hand-authoring `model_info:` in YAML is a
  drift magnet and a huge diff. Instead, **compute and inject** it at startup:
  CAP-modelinfo iterates `model_list` in `airlock/proxy.py:_prepare_runtime_config()`
  (`:158-218` — the established seam that already mutates the config dict for MCP
  toggles / health checks / Fathom before writing the temp config the litellm child
  reads) and does `entry.setdefault("model_info", {}).update(capability_record(entry))`.
  LiteLLM then serves the computed capability natively on `/model/info` with **no
  hand-written blocks and no possible drift** (single source = `capability.py`). This
  is lower-risk than an ASGI response-transform for `/model/info` (no litellm
  response-format coupling, no per-request cost, an existing airlock pattern). The
  **single-source consistency** the original design enforced via a "config==helper"
  test is now structural (the value IS the helper output); the test becomes "injection
  produces the expected record per entry" + the live `/model/info` smoke.
- **`/v1/models` augmentation** = new ASGI middleware (e.g. `airlock/models_seam.py`
  + an `install_models_seam_on_proxy_app()` hook) that, for `GET /v1/models`
  responses, joins each `data[].id` (the `model_name`) to the config entry and adds
  an additive `airlock` object built from `capability.py`. Reuse the **dual
  pre-start/post-start install** of `batch/middleware.py:546-583` (wrap
  `app.middleware_stack` if already built, else `add_middleware`) and the same
  idempotency flag pattern (`app.state.airlock_*_installed`). Additive only: an
  unchanged OpenAI client that ignores `airlock` sees a standard response.

### 4.5 `airlock/capability.py` — single source of truth (new module)

Small, pure, no I/O. Given a `model_list` entry (dict) it returns the capability
record used by **both** the CAP-modelinfo consistency test and the `/v1/models`
seam:

```python
_REGIONAL = lambda loc: bool(loc) and loc.lower() != "global"   # Vertex batch needs a region

def endpoints_for(entry: dict) -> list[str]:
    eps = ["chat"]
    params = entry.get("litellm_params") or {}
    model = params.get("model", "")
    has_batch = (
        bool(entry.get("airlock_batch"))                        # gateway providers: aistudio/mistral/vllm
        or (model.startswith("vertex_ai/")                      # vertex-native batch...
            and _REGIONAL(params.get("vertex_location")))       # ...only when REGIONAL (codex BLOCK 3)
    )
    if has_batch:
        eps.append("batch")
    return eps

def airlock_provider_for(entry: dict) -> str | None:
    """The served-by token. Shared by router.set_router_config and the capability
    surfaces (§4.2). Handles enhanced/<profile> -> wrapped gemini, normalizes
    vertex_ai_beta -> vertex_ai."""
    ...

def capability_record(entry: dict) -> dict:            # {airlock_provider, region, endpoints, underlying, deprecated}
    ...
```

The CAP-modelinfo pack adds a test asserting, for every `model_list` entry, that the
hand-written `model_info.endpoints` equals `endpoints_for(entry)` — so the published
capability can never drift from the wiring (N3), and in particular the `vertex/…`
entries at `vertex_location: global` assert `[chat]`, not `[chat, batch]`. The
`/v1/models` seam (§4.4) and `set_router_config` (§4.2) call the **same** functions,
so all three surfaces (config validation, `/v1/models`, provider inference) are
computed from one place.

### 4.6 Internal-reference migration (N5)

`router_settings.fallbacks` (`config.yaml:383-410`) and `cost_tiers` (`:419-439`)
reference **bare** internal aliases (`claude-opus`, `gemini-3.1-pro`, `gemini-flash`,
…) — none of which are being renamed or removed. So **no fallback/cost_tier target
changes** are required for the bare set, and a reference-integrity test (every
`fallbacks`/`cost_tiers` target is a live `model_name`) guards the stale-target
class of bug (the 0.5.1 R2 regression). The smart-router cost-tier directives
likewise target bare names and are untouched. We deliberately do **not** point
internal references at the new prefixed aliases — keeping internal wiring on the
stable bare names minimizes churn and blast radius.

### 4.7 Custom providers (`enhanced/`, `tavily/`)

- `tavily/` is in both prefix maps (`router.py:144`, `model_alias.py:57`), so
  `tavily/web-search` already infers `tavily`. New alias `tavily/web-search` (==
  `litellm_params.model`) is a clean exact entry; served-by reports `tavily`.
- `enhanced/` (gemini-coding) is **not** in the prefix maps; `infer_provider("enhanced/…")`
  returns `None` today, and a naive `split("/")[0]` over `litellm_params.model =
  enhanced/gemini-coding` yields `enhanced` — wrong. The fix is **not** a one-off:
  the shared `airlock_provider_for()` (§4.2) resolves `enhanced/<profile>` through
  `enhanced_profile.target_model` → `gemini`, and **both** `set_router_config` and
  the capability helper use it, so router-side inference and the published
  `airlock_provider` agree. Served-by remains post-call from the inner physical
  `gemini/…` call. NAME-aliases adds a regression test that
  `infer_provider("aistudio/gemini-coding")`, `infer_provider("gemini-coding")`, and
  the capability provider all report `gemini`; the HITL smoke confirms
  `X-Airlock-Served-By: gemini` for an `enhanced/` call on the test port.

---

## 5. Tests (TDD, RED first — all no-network)

Authored per pack; listed here so the design names the surface.

- **NAME-aliases:** (a) **collision-safety** (§4.1 — two-pass loader + provider-aware
  prefix-strip): `resolve("gemini-3.5-flash")` → bare AI-Studio entry;
  `resolve("vertex/gemini-3.5-flash")` **and** `resolve("vertex_ai/gemini-3.5-flash")`
  → vertex entry (never bare AI Studio); `resolve("aistudio/gemini-3.5-flash")` and
  `resolve("gemini/gemini-3.5-flash")` → AI-Studio deployment;
  `resolve("openai/claude-haiku")` → `claude-haiku` (single-provider, prefix ignored); (b) **router collision proof** — `aistudio/`/`vertex/` resolve to the
  right backend and are NOT re-parsed as native provider routing (separate dir+port);
  (c) `infer_provider` / `airlock_provider_for` return the served-by token for every
  Appendix-A alias incl. `enhanced/` → `gemini` (§4.2, 4.7); (d) a prefixed concrete
  alias is auto-pinned identically to bare (§4.3); (e) legacy `-aistudio`/`-vertex`/
  `-batch` aliases still resolve+pin; (f) reference-integrity: every `fallbacks`/
  `cost_tiers` target is a live `model_name` (§4.6).
- **CAP-modelinfo:** capability-↔-wiring consistency — for every entry,
  `model_info.endpoints == capability.endpoints_for(entry)` and `batch` ∈ endpoints
  iff `airlock_batch` marker **or** regional-vertex (§4.5); the `vertex/…`-at-`global`
  entries assert `[chat]`. Plus a **`/model/info` smoke** (separate dir+port)
  proving LiteLLM actually surfaces our `model_info` fields for the pinned version
  (§2.5).
- **CAP-v1models:** the seam adds `airlock:{…}` to each model and is **additive**
  (every standard OpenAI field intact); a client ignoring `airlock` sees an
  unchanged response (§4.4).
- **COMPAT-tests:** cross-cutting — old+new alias resolve/pin/attribute; collision
  safety; batch create via `?custom_llm_provider=aistudio` with new + old alias both
  hit `backend_for_alias` → same backend (`batch/gateway.py:85-96`,
  `batch/runtime.py:99-127`; both keys present because legacy twins are dual-listed).

## 6. Documentation updates (DOCS pack)

`dev/user-needs.md` UN-21/UN-22; finalize this note as as-built; `docs/guide/`
`routing.md` (naming convention + discover→pin→verify recipe), `batch.md` +
`vertex-batch.md` (which alias batches; old→new map), `provider-observability.md`
(`X-Airlock-Served-By`/`-Region` as the verify surface); `docs/changelog.md`
(behavior-change register + deprecation notice naming 0.6.0). Reconcile per
`dev/update-docs.md` (`mkdocs build --strict`, config/CLI parity gate).

## 7. Out of scope / follow-ups

- **Removal** of legacy aliases — 0.6.0 (decision 2.3); a separate pack/release.
- **Client-SDK helper** for capability discovery — deferred.
- **Repointing the bare Gemini default to Vertex** — not now (decision 2.2); the
  schema supports it (ops-repointable) if the operator later chooses.
- **vLLM served-by honesty** (`openai` vs a true `vllm` token) — recorded wrinkle
  (2.5); not changed here to avoid touching `transparency.py` normalization.

## 8. Related

- Plan: `dev/plans/0.5.2-plan.md` (register N1–N6, Appendix A, behavior-change
  register, test surface).
- `X-Airlock-Served-By`/`-Region`: [design-mutation-and-provider-transparency.md](design-mutation-and-provider-transparency.md).
- `infer_provider` consumers & quarantine: [design-provider-quota-observability.md](design-provider-quota-observability.md).
- Auto-pin / `X-Airlock-Model-Override`: [design-routing-fanout-guardrails.md](design-routing-fanout-guardrails.md).
- Production-safety invariant (separate dir+port for all smoke): MEMORY
  [[airlock-production-safety]].

## 9. Design-review history

- **v1 → codex BLOCK (2026-06-27).** Three findings, all valid and incorporated:
  1. **model_alias collision** — `load_from_config` inserts a shared `bare` key for
     every entry sharing a stripped provider-model; guardian rewrites `data["model"]`
     via `resolve()` *before* LiteLLM, so the "LiteLLM exact-matches first" assumption
     was insufficient. → §4.1 two-pass collision-safe loader (explicit keys immutable;
     ambiguous variants dropped). *NAME-aliases scope.*
  2. **provider classifier** — `split("/")[0]` misclassifies `enhanced/gemini-coding`
     as `enhanced`. → §4.2 shared `airlock_provider_for()` used by both
     `set_router_config` and the capability helper. *NAME-aliases scope.*
  3. **vertex-batch overclaim** — `batch iff vertex_ai/` is too broad; Vertex 3.x
     batch needs a regional location and the entries use `vertex_location: global`. →
     §2.4/§2.5/§4.5 region-gated `endpoints_for()`; `vertex/…` publishes `[chat]` until
     repointed. *CAP-modelinfo scope.*
  (Note: the codex run's local sandbox failed (`bwrap` user-namespaces) and it
  reviewed code via the GitHub connector on `main` + the prompt summaries, not the v1
  note text; the re-review inlines the full v2 note so it reviews the actual design.)
- **v2 → codex BLOCK (2026-06-27, residual).** B (shared classifier) and C
  (region-gated vertex batch) confirmed **sound**. One residual on A: the *load-time*
  two-pass loader was correct, but the **resolve-time** prefix-strip (`model_alias.py:310-316`)
  still strips any `provider/` and could route native `vertex_ai/gemini-3.5-flash`
  to the bare AI-Studio entry. → §4.1 v3 adds a **provider-aware** prefix-strip
  (`(provider, bare) → alias` index; contradictory multi-provider prefixes fall
  through, never silently repoint). *NAME-aliases scope.*
- **v3 → PASS:** see `dev/plans/runs/0.5.2-NAMING-design-review-20260627T040523Z.md`.
- **v4 refinement (post-NAME-aliases, CAP-modelinfo authoring):** `/model/info`
  capability is **computed and injected** at startup via
  `proxy.py:_prepare_runtime_config()` rather than hand-written into 73 config
  `model_info:` blocks (§4.4). Strictly more single-source / less drift; gated by the
  CAP-modelinfo per-pack codex review rather than a separate design round.
