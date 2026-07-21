# Design: GPT-5.6 (Sol / Terra / Luna) model family

**Status:** ✅ **IMPLEMENTED — shipped in 0.5.6** (tagged `v0.5.6`, PyPI 2026-07-21).
**Shipped in 0.5.6:** §2 catalog, §3 alias dispositions, §4.1 twins, §4.2
dropped-qualifier guard, §7 litellm floor, §8 tiers + fallbacks, §9 template fixes,
§13 warn-only detection.
**Still outstanding (0.5.7 / 0.5.8):** §4.2.2 helpful rejection response (→ 0.5.7 F-3),
§5.1 effort *enforcement* (→ 0.5.8, gated on the §13 window), §6
`X-Airlock-Model-Alias` (→ 0.5.8 — **live gap**, see below).

> ⚠️ **Known live gap.** 0.5.6 advanced `gemini-flash-lite`, `gemini-pro` and
> `gemini-flash` to newer generations *without* the §6 `served=` disclosure that was
> designed to make such advances visible. Callers are on different models at different
> prices and have not been told. Accepted knowingly to hit the 2026-07-23 deadline.
**Date:** 2026-07-20
**Supersedes:** nothing. Extends the 0.5.2 provider/model naming + capability
discovery contract.

---

## 1. Summary

OpenAI shipped GPT-5.6 on 2026-07-09 as a three-model family — **Sol**, **Terra**,
**Luna**. (There is no bare `gpt-5.6` model at the API; Airlock exposes one as a
convenience alias — §2.2.) This design adds them to Airlock, replaces silent model
substitution with helpful rejection, and fixes one Airlock-induced bug the family
exposes.

The central observation, and the thing that drives every decision below:

> GPT-5.6 is a **flat price-tier triple over one capability envelope**, not a
> capability ladder. All three variants share an identical 1,050,000-token context,
> 128,000-token max output, a 2026-02-16 cutoff, and an identical capability flag
> set. They differ *only* in price and quality.

This is structurally unlike `mini`/`nano`, which are distilled models with smaller
context and weaker capability. Mapping Sol/Terra/Luna onto Airlock's existing
size-suffix aliases would therefore misrepresent them — and, as §3 shows, would
silently raise costs.

## 2. Verified ground truth

Source: litellm `v1.93.0` `model_prices_and_context_window.json`, fetched live and
re-verified independently by two agents and by a codex review pass.

| model | $/1M in | $/1M cached in | $/1M out | ctx | max out |
|---|---|---|---|---|---|
| `gpt-5.6` (bare) | 5.00 | 0.50 | 30.00 | 1,050,000 | 128,000 |
| `gpt-5.6-sol` | 5.00 | 0.50 | 30.00 | 1,050,000 | 128,000 |
| `gpt-5.6-terra` | 2.50 | 0.25 | 15.00 | 1,050,000 | 128,000 |
| `gpt-5.6-luna` | 1.00 | 0.10 | 6.00 | 1,050,000 | 128,000 |

- 16 keys at the v1.93.0 tag (bare + sol/terra/luna × {`""`, `azure/`, `azure/us/`,
  `azure/eu/`}); litellm `main` adds `bedrock_mantle/openai.gpt-5.6-*` for 19.
- **No Pro variant exists.**
- `supports_reasoning`, `supports_function_calling`, `supports_prompt_caching`: all true.
- `supported_endpoints`: `/v1/chat/completions`, `/v1/responses`, `/v1/batch`.
- Reasoning-effort flags: `supports_none_reasoning_effort=true`,
  `supports_xhigh_reasoning_effort=true`,
  `supports_minimal_reasoning_effort=**false**`, and **no** `supports_max_*` key.
- **Surcharge: requests over 272K input tokens bill 2× input, 1.5× output** — for the
  **whole request**, not just the tokens above the threshold. Applies to all three
  variants. Confirmed against litellm's day-0 GPT-5.6 announcement, which publishes
  explicit short/long dual rates per variant (§12.4):

  | variant | input short/long | output short/long |
  |---|---|---|
  | sol | $5.00 / $10.00 | $30.00 / $45.00 |
  | terra | $2.50 / $5.00 | $15.00 / $22.50 |
  | luna | $1.00 / $2.00 | $6.00 / $9.00 |

### 2.1 Corrections to earlier analysis

Three claims made during investigation were wrong and are corrected here so they
are not re-derived later:

1. **litellm 1.89.0 (currently installed) already knows GPT-5.6.** Its *bundled*
   backup map does not, but `litellm.model_cost` carries all 19 keys because litellm
   fetches the remote map at import. Pricing works today, un-upgraded — *but only
   with network egress at startup*. See §7.
2. **`xhigh` and `max` are NOT dropped.** litellm 1.89.0 gates per-level off the
   model map (`_supports_reasoning_effort_level`); 5.6 sets `supports_xhigh=true`,
   and `max` hits no gate at all. Both reach OpenAI intact.
3. **Bare `gpt-5.6` exists in litellm's price map**, priced identically to Sol — but
   see §2.2: it is **not** a real OpenAI API id.

### 2.2 Live verification against the provider APIs (2026-07-20)

Queried `GET /v1/models` with a real key. This supersedes map-derived inference.

**OpenAI — `gpt-5.6*` ids actually offered:**

```
gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra
```

> ⚠️ **Bare `gpt-5.6` is NOT in OpenAI's model list.** litellm's map carries it, but
> the API does not offer it. This **validates** the §4.1 decision to pin the
> `gpt-5.6` alias to the explicit `openai/gpt-5.6-sol` body: had it been pointed at
> the floating bare id, every request through that alias would have 404'd.
> Airlock may still *expose* `gpt-5.6` as a convenience alias — it just must never
> forward that string upstream.

**`gpt-5.3-codex` IS offered** — resolves the medium-confidence flag in §3.1. Also
still listed (until their 2026-07-23 API retirement): `gpt-5-codex`, `gpt-5.1-codex`,
`gpt-5.1-codex-max`, `gpt-5.1-codex-mini`, `gpt-5.2-codex`.

**Google AI Studio — config targets checked:**

| id | status |
|---|---|
| `gemini-2.5-pro`, `-flash`, `-flash-lite` | ✅ real |
| `gemini-3.1-pro` | ❌ **absent** — only `gemini-3.1-pro-preview` |
| `gemini-3-flash` | ❌ **absent** — only `gemini-3-flash-preview` |
| `gemini-3.1-flash-lite`, `gemini-3.5-flash` | ✅ real |

This confirms the template was shipping two Gemini ids that do not exist (§9).

**Still unverified:** `reasoning_effort` level acceptance. The available OpenAI key
authenticates and lists models but has **no quota**, and the billing error fires
*before* parameter validation — so no information about `max`/`xhigh` was obtained.
See §11 Q2.

## 3. Decision: expose under real names; do not absorb into legacy aliases

**Decision:** expose Sol/Terra/Luna under their real names (bare + `openai/`-prefixed
twins), plus a bare `gpt-5.6` family alias pinned to the explicit Sol body. Legacy
semantic aliases are **not** repointed.

### Rationale

**Why not map onto `gpt-5` / `gpt-5-mini` / `gpt-5-nano`:**

1. **It is a silent cost increase.** Luna has no price-equivalent incumbent — it is
   *more expensive* than what the cheap aliases point at today:

   | alias | current target | price | Luna delta |
   |---|---|---|---|
   | `gpt-5-mini` | `gpt-5.4-mini` | $0.75 / $4.50 | **+33% in, +33% out** |
   | `gpt-5-nano` | `gpt-5.4-nano` | $0.20 / $1.25 | **5× in, 4.8× out** |

   Airlock's cost tiers, budget enforcement, and downgrade routing all assume nano is
   the cheap floor. Repointing would break that assumption for every client that never
   changed a line. This is arithmetic, not taste.

2. **The family is 3-wide; the alias set is 4-wide** (`gpt-5`, `-pro`, `-mini`,
   `-nano`). With no Pro, a 3→4 mapping strands `gpt-5-pro` and forces one variant to
   serve two roles.

3. **Sol/Terra/Luna are not a capability ladder** (see §1).

**Why not real names only:** it would strand the existing client base and the
fallback/cost-tier machinery, which is keyed on semantic aliases.

*(An earlier draft justified this partly with "OpenAI itself ships bare `gpt-5.6`".
That is **false** — §2.2 shows the API offers only the three variants. The bare alias
is an Airlock convenience, pinned to the explicit Sol body, and must never be
forwarded upstream verbatim.)*

### 3.1 Legacy alias dispositions (decided)

| alias | target | disposition |
|---|---|---|
| `gpt-5` | `openai/gpt-5.5` | **UNCHANGED.** 5.6 is reachable under its own names; see §6 for how clients are told. |
| `gpt-5-pro` | `openai/gpt-5.5-pro` | **UNCHANGED.** "Pro" is a *capability tier* (`mode: responses`, max reasoning depth, ~6× price), not a version coordinate. No 5.6 Pro exists; mapping pro to Sol would be a silent capability downgrade. Document the contract at `config.yaml:51`. |
| `gpt-5.4` | `openai/gpt-5.4` | **UNCHANGED — literal version pin.** Terra is price-identical ($2.50/$15) but `gpt-5.4` must keep meaning exactly `gpt-5.4`. Terra gets its own name. Retirement is a separate, later decision. |
| `gpt-5-mini` | `openai/gpt-5.4-mini` | **UNCHANGED.** See cost table above. |
| `gpt-5-nano` | `openai/gpt-5.4-nano` | **UNCHANGED.** See cost table above. |
| `gpt-5-codex` | `openai/gpt-5.3-codex` | **UNCHANGED in root.** 5.3-codex is the sole surviving codex model. The *template* is broken — see §9. |

**Verified:** every current root target is real, `openai`-provider, priced, and
carries no `deprecation_date` in litellm 1.93.0. **Root `config.yaml` needs no
mapping changes.**

> **✅ RESOLVED (§2.2):** `gpt-5.3-codex` **is** offered by OpenAI's `/v1/models`,
> verified live on 2026-07-20. The secondary source disputing API access was wrong.
> Caveat: listing proves the id is offered, not that a completion succeeds — the
> available key has no quota, so no call was made.

## 4. Alias-resolution traps (both verified against the code)

### 4.1 First-writer-wins prefix binding — `airlock/fast/model_alias.py:287`

`_provider_body_alias.setdefault((token, body), alias)` is **first-writer-wins**.
When several aliases share one `litellm_params.model`, the provider-prefixed form
binds to whichever appears **first in `model_list`**. Measured behaviour:

| config layout | `openai/gpt-5.6-sol` resolves to |
|---|---|
| legacy `gpt-5`→sol listed first | `gpt-5` ❌ |
| 5.6 entries listed first | `gpt-5.6` ⚠️ (still not `gpt-5.6-sol`) |
| **with explicit `openai/` twins** | `openai/gpt-5.6-sol` ✅ |

`resolve()` checks `_exact` before provider/body prefix stripping, so an explicit
`model_name: openai/gpt-5.6-sol` entry is authoritative and immutable.

**Consequence: the 0.5.2 dual-listing pattern is load-bearing here, not cosmetic.**
Beyond routing, a misroute would corrupt `X-Airlock-Served-By` attribution and the
logged `model`, making observability actively misleading.

**Decision:** ship the `openai/`-prefixed twins in **both** root config **and** the
CLI template. (The template currently ships no twins at all; omitting them would
leave every new install exposed to the same misroute — an inconsistency in the
original proposal, caught in review.)

**Ordering is load-bearing:** `-sol` must precede bare `gpt-5.6` so dated snapshots
(`gpt-5.6-sol-2026-07-09`) resolve to the explicit variant.

### 4.2 DECISION: strict resolution with a helpful rejection

**Airlock stops silently substituting models.** This applies the same principle as
§5.1 (`reasoning_effort`): give clients what they asked for, and when that is not
possible, **say so usefully** rather than quietly serving something else.

**The problem this replaces.** `gpt-5.6-mini`, `-nano`, `-pro` do not exist, but they
are the most likely names a client guesses — precisely because sol/terra/luna break
convention. They score 0.57–0.59 against bare `gpt-5.6`, above
`_AUTO_ROUTE_THRESHOLD` (0.50), so today they silently resolve to **Sol at $5/$30**,
logged at DEBUG. Someone reaching for "the cheap 5.6" is billed 5× and never told.

**Rejected alternative:** earlier drafts proposed "guess-guard" aliases mapping the
non-existent names to Luna. That was dropped — it is still silent substitution, it
invents model names that leak into `/v1/models`, and it contradicts the principle
above. It solved the cost symptom by creating a naming-truth problem.

#### 4.2.1 The rule

Fuzzy matching stops being a *routing* mechanism and becomes a *diagnostic* one.

| case | behaviour |
|---|---|
| exact match | serve it |
| near-match **within the same cost tier** | serve it, log the resolution (typo tolerance and dated snapshots — `gpt-5.6-sol-2026-07-09` → `gpt-5.6-sol` — stay working) |
| near-match **crossing a cost tier** | **reject**, and use the fuzzy score to *propose* the fix |
| no match above `_WARN_THRESHOLD` | **reject**, list the available models |

The cost-tier gate is what makes this safe to adopt: it preserves the convenience
where it is free and withholds it where it costs money.

#### 4.2.2 The rejection must be genuinely helpful

A bare error is not acceptable. The response carries the diagnosis Airlock already
computed — it has the fuzzy scores, the catalog, and the tier data.

**Response body** (OpenAI-shaped, so existing SDK error handling works):

```json
{
  "error": {
    "message": "Unknown model 'gpt-5.6-mini'. Did you mean 'gpt-5.6-luna'? OpenAI's GPT-5.6 family is named sol/terra/luna, not mini/nano/pro. Closest by cost tier: gpt-5.6-luna (low, $1/$6). Also available: gpt-5.6-terra (medium, $2.50/$15), gpt-5.6-sol (high, $5/$30).",
    "type": "invalid_request_error",
    "param": "model",
    "code": "model_not_found",
    "airlock": {
      "requested": "gpt-5.6-mini",
      "suggestions": [
        {"model": "gpt-5.6-luna",  "score": 0.58, "tier": "low"},
        {"model": "gpt-5.6-terra", "score": 0.52, "tier": "medium"},
        {"model": "gpt-5.6-sol",   "score": 0.57, "tier": "high"}
      ],
      "reason": "fuzzy_match_crosses_cost_tier"
    }
  }
}
```

**Response headers** carry a compact form for clients that don't parse bodies, using
the established `;`-joined `key=value` grammar (§6):

```
X-Airlock-Model-Suggestion: requested=gpt-5.6-mini;suggested=gpt-5.6-luna;reason=crosses_cost_tier
```

**Design constraints:**
- The suggestion is **derived**, never hardcoded — it comes from the existing scorer
  and the tier tables, so a new model family needs no code change to be suggested well.
- Suggestions are **ranked by score**, and each is annotated with its tier and price so
  the client can see *why* the substitution wasn't made for them.
- The prose message must be self-sufficient. Most clients surface `error.message` and
  nothing else; the structured `airlock` block is a bonus, not the payload.
- **Never** suggest a model the caller is not entitled to (respect any per-client
  catalog filtering) — a helpful error must not become a capability-disclosure channel.
- HTTP **404** with `code: model_not_found` matches OpenAI's own behaviour for unknown
  models, so SDK retry/error paths behave predictably.

#### 4.2.3 Consequences for the rest of this design

- **No synthetic `model_list` entries.** The `airlock_synthetic` marker, the
  `/v1/models` filtering rules, and their tests are all **dropped** — nothing fake is
  ever registered.
- Cost-tier and fallback completeness (§8.1.1) still applies to the **real** callable
  names: sol/terra/luna, bare `gpt-5.6`, and the four `openai/` twins — **eight**, not
  eleven.
- Threshold constants (`_AUTO_ROUTE_THRESHOLD` 0.50, `_WARN_THRESHOLD` 0.35) keep their
  values but change meaning: 0.50 now gates *same-tier auto-routing*, and matches
  between 0.35 and 0.50 become *suggestions* rather than silent routes.

> **Blast radius — this is a behaviour change beyond GPT-5.6.** Any cross-tier fuzzy
> match that silently worked before now 404s. That is the intent, but the affected
> set is unknown for the same reason as §5.1: it was never logged loudly. Ship it
> behind the same warn-only measurement window (§13).

### 4.3 Not a regression: `_strip_version` and alpha suffixes

`_strip_version` strips trailing *pure-numeric* segments only, so `gpt-5.6-sol`
keeps its version in the core (`gpt-5.6-sol`) while `gpt-5.4` reduces to `gpt`. This
means `gpt-5.7-sol` will not version-match `gpt-5.6-sol`. **This is pre-existing** —
`gpt-5.4-mini` behaves identically — and is explicitly *not* in scope. Thresholds
(`_AUTO_ROUTE_THRESHOLD=0.50`, `_WARN_THRESHOLD=0.35`) need no change.

## 5. The `reasoning_effort` bug — Airlock-induced

Verified end-to-end through litellm 1.89.0's real `map_openai_params` for
`gpt-5.6-sol`:

| client sends | Airlock emits | reaches OpenAI | without Airlock |
|---|---|---|---|
| `none` | `minimal` | **`<DROPPED>`** ❌ | `none` ✅ |
| `off` | `minimal` | **`<DROPPED>`** ❌ | `off` (would 400) |
| `minimal` | `minimal` | `<DROPPED>` | `<DROPPED>` |
| `low`/`medium`/`high` | unchanged | ✅ | ✅ |
| `xhigh` | unchanged | ✅ | ✅ |
| `max` | unchanged | ✅ | ✅ |

`_OFF_INTENT` (`reasoning_effort.py:28`) catches `none`; line 59 rewrites it to
`minimal` — converting the one value 5.6 explicitly **supports** into the one it
explicitly **rejects**, which litellm then drops, yielding model-default (high)
reasoning.

> A client asking for *no* reasoning silently gets *maximum* reasoning, and pays for it.
> This is precisely the failure mode the module's own docstring exists to prevent.
> For 5.6, disabling the module entirely would be strictly better than its current behaviour.

### 5.1 DECISION: validate and reject — do not translate

**Airlock must not rewrite what the client asked for.** The current module's whole
premise — "translate off-intent to the provider's floor so intent survives
`drop_params`" — is the bug generator. It guesses at intent, and when the guess is
wrong (as with 5.6) the client silently gets the *opposite* of what they asked for and
pays for it.

**New contract:**

| client sends | Airlock does |
|---|---|
| a value the target model **accepts** | **pass through untouched** |
| a value the target model **rejects** | **reject the request** — HTTP 400, useful message |
| a value no model defines (`highest`, `max-plus`, `verbose`) | **reject** — never fuzzy-map to a real level |

**Explicitly: do not accept `highest` as a synonym for `max`.** No synonym table, no
off-intent set, no floor translation. A misspelled or unsupported level is a client
bug, and the client should be told so — immediately and precisely — rather than have
Airlock silently pick something plausible.

**Error response must be actionable.** It names the rejected value, the resolved
target model, and the levels that model actually accepts:

```
400 Bad Request
{
  "error": {
    "message": "reasoning_effort 'minimal' is not supported by gpt-5.6-sol.
                Supported values: none, low, medium, high, xhigh.",
    "type": "invalid_request_error",
    "param": "reasoning_effort",
    "code": "unsupported_reasoning_effort"
  }
}
```

This is strictly better than the status quo in every case, including the ones the
module was written for: a client sending `none` to a model that rejects it now learns
that, instead of silently getting maximum reasoning.

**Why validation must still be model-aware:** `none` is *valid* for 5.6 and *invalid*
for 5.4; `minimal` is the reverse. So the validator still consults the litellm model
map — it just **rejects** on mismatch instead of **rewriting**.

**Breaking-change note.** Clients sending `none`/`off` to older OpenAI models get a
400 where they previously got silently-translated `minimal`. That is the point — the
translation was never visible to them — but it is a behaviour change and belongs in
the changelog and the release notes. `AIRLOCK_NORMALIZE_REASONING_EFFORT` should be
retained as an escape hatch that disables **validation** (falling back to litellm's
`drop_params`), renamed to reflect that it no longer normalizes.

**Scope note:** this replaces the translation behaviour for **all** providers, not
just OpenAI. The Gemini (`→ disable`) and Anthropic (`→ drop the param`) branches are
the same class of guess and become validation too. Anthropic has no enum, so its rule
is "accept anything litellm maps, reject nothing" unless a concrete invalid set can be
established — do not invent one.

### 5.2 Fix shape

Replace the hardcoded per-provider floor with a **model-aware, map-driven** one:

```python
def _supported_efforts(model: str | None) -> frozenset[str] | None:
    """The reasoning_effort levels the target model actually accepts.

    Derived from the litellm model map's per-level capability flags, never
    hardcoded — a new model family must not require a code change to be
    validated correctly.

    Returns None when the model is unknown, which means "cannot validate":
    pass the value through untouched rather than rejecting on ignorance.
    """
```

Resolution order:

1. **Resolve the Airlock alias to its `litellm_params.model` body first.**
   *(Review finding — the original proposal missed this.)* `data["model"]` may be a
   semantic alias such as `gpt-5`, whose provider body lives in `model_list`.
   Prefix-stripping alone is insufficient.
2. `litellm.get_model_info(body)` → build the supported set from the per-level flags
   (`supports_none_reasoning_effort`, `supports_minimal_reasoning_effort`,
   `supports_xhigh_reasoning_effort`, …) plus the always-present `low/medium/high`.
3. **Unknown model → return `None` → pass the value through untouched.** Do not
   reject on ignorance, and do not guess a substitute. Rejecting an unknown model's
   effort value would break self-hosted and custom endpoints that aren't in the map
   (§12.6 lists seven such models already in config).

Additional constraints:

- Delete `_OFF_INTENT` and the `_OPENAI_VALID` / `_GEMINI_VALID` literals. They encode
  exactly the guesses §5.1 forbids.
- `max` is **not** currently flagged by litellm (`supports_max_reasoning_effort` is
  absent for 5.6, §2). Decide deliberately: either treat an absent flag as "not
  supported" and reject `max`, or allow-list it pending upstream confirmation
  (§11 Q2). **Do not let it pass by fall-through accident**, which is how `xhigh` and
  `max` survive today.
- Since Airlock no longer rewrites the value, there is **no mutation to record** on
  the success path. Remove those `record_mutation` calls rather than leaving them
  reporting a no-op — a ledger entry for an unchanged value is noise. Rejections are
  surfaced as errors, not mutations.

## 6. New response header: alias guidance

**Requirement:** because `gpt-5` deliberately stays on 5.5 (§3.1), clients must be
told, in-band, that a newer family exists and how to reach it.

**Proposed:** `X-Airlock-Model-Alias` — a k/v note disclosing **which concrete model
actually backed the alias the client asked for**, plus an optional newer-generation
pointer. Emitted on every aliased response, not only OpenAI ones.

```
X-Airlock-Model-Alias: requested=gpt-5;served=openai/gpt-5.5;newer=openai/gpt-5.6-sol
X-Airlock-Model-Alias: requested=gemini-flash-lite;served=gemini/gemini-3.1-flash-lite
```

**Two distinct jobs, one header:**

1. **`served=` — always emitted when the request came in on an alias.** A generic alias
   is a moving target by design; the client cannot otherwise know which generation it
   got. This is what makes advancing an alias safe: when `gemini-flash-lite` moved from
   `gemini-2.5-flash-lite` to `gemini-3.1-flash-lite` (2026-07-20), every caller sees
   `served=gemini/gemini-3.1-flash-lite` and knows it is **not** on 2.5 any more.
   Silently advancing an alias without this disclosure is the same class of problem as
   silently substituting a model (§4.2) — the client is served something other than the
   literal thing it named, and is not told.
2. **`newer=` — emitted only when a successor is configured** (§6.1). This is the
   `gpt-5` → 5.6 discovery case.

**Grammar (verified against `transparency.py`):** `;`-joined `key=value` tokens, **no
space after the separator**, each value via `_header_safe`, byte-budgeted with the same
`…+N more` truncation as `mutations_header`. Matches `_mutation_token` exactly. Reuse
the existing serializer — do not invent a second format.

**`served=` carries the `litellm_params.model` body** (`gemini/gemini-3.1-flash-lite`),
matching `capability_record.underlying`, so one vocabulary describes the concrete model
everywhere. It must be read from what actually served the request, never guessed from
the alias name — same rule as `X-Airlock-Served-By`.

### 6.1 Successor map — concrete specification

"Data-driven" is not implementable as a slogan. The map is specified as:

**Location:** a new optional `model_successors:` block in `config.yaml`, loaded and
validated alongside `cost_tiers:` (same pattern: env override → config → empty
default). It is **not** a code constant — the whole point is that operators can update
it when OpenAI ships a new family, without a release.

**Schema:** `dict[str, str]` — **requested public alias → advertised successor
alias**. Both sides are Airlock alias names, not litellm bodies.

```yaml
model_successors:
  gpt-5: openai/gpt-5.6-sol
  gpt-5-mini: openai/gpt-5.6-luna     # only if operator wants to advertise it
```

**Keying decision:** the map keys on the **requested** alias (what the client sent),
not the underlying body — because the header's job is to answer "you asked for X;
there is a newer Y", and two aliases sharing a body may warrant different advice.

> **Implementation constraint (review finding):** read the requested alias from the
> preserved original — `metadata["airlock_request"]["requested_model"]`, which the
> guardian already stores — **not** from `data["model"]`. By the time the post-call
> hook runs, `data["model"]` may have been rewritten by routing, cost-tier swaps, or
> failover, so keying off it would look up the wrong alias and could advertise a
> "successor" to a model the client never asked for.

**Validation:** every key and every value must exist in `model_list`. Enforced by
`test_config_consistency.py`, the same way fallback sources/targets are — an
unresolvable successor is a config error, not a silently-dropped header.

**Default:** empty. With no map configured, the header is never emitted. Absence of
configuration must not produce guesses.

## 7. litellm version floor

**Decision: raise the floor to `>=1.93.0`.** The change is about **determinism, not
capability** (see §2.1 correction 1).

Installed 1.89.0 works *because* litellm fetches the remote cost map at import. Its
bundled map has no 5.6 keys. So under any of `LITELLM_LOCAL_MODEL_COST_MAP=True`, an
air-gapped/egress-filtered deploy, or a GitHub fetch failure at startup, 5.6 silently
degrades to **unknown model**: cost tracking reads **$0.00**, and budgets, provider
spend, near-limit routing, and quarantine accounting all under-count.

> Silent zero-cost accounting on a $5/$30 model is the risk that justifies the bump.

```toml
"litellm[proxy]>=1.93.0,!=1.82.7,!=1.82.8,<2",
```

The two `!=` exclusions fall below the new floor and become redundant; dropping them
is tidier but optional.

## 8. Cost tiers and fallbacks

### 8.1 Tiers

Add to `cost_tiers:` in `config.yaml` **and** to `_DEFAULT_COST_TIERS` in **both**
`airlock/fast/router.py:44-66` and `airlock/fast/settings.py:64-88` — these are
byte-identical duplicates and **must not drift**.

- `gpt-5.6-sol` → **high** ($5/$30; with `claude-opus` $5/$25)
- `gpt-5.6-terra` → **medium** ($2.50/$15; just under `claude-sonnet` $3/$15)
- `gpt-5.6-luna` → **low** ($1/$6; with `claude-haiku` $1/$5)

Luna is the **ceiling** of `low`, not the floor. `gpt-5-nano` ($0.20/$1.25) remains
the floor and must stay listed first. Place each entry **after** the incumbent leaders
so the default swap target is unchanged and the change stays additive.

#### 8.1.1 Every callable alias must be tiered — forced-swap hazard

**This is the most serious implementation trap in this design.** `_apply_cost_tier`
(`airlock/fast/router.py`) is a plain membership test:

```python
if model in tier_models:
    return model, None
new_model = tier_models[0]          # <-- forced swap
return new_model, f"cost_tier({tier}→{new_model})"
```

A model that is *not literally present* in the tier list is **not** left alone — it is
**silently swapped to the first model in that tier**. So if a client sends
`openai/gpt-5.6-sol` with `tier=high` and only the bare `gpt-5.6-sol` appears in the
`high` list, the request is rerouted to a **different model entirely**.

§4.1 creates **eight** callable names. Tiering only the three bare variants would leave
five of them subject to forced swaps.

**Requirement: every callable alias must appear in exactly one tier**, including all
four `openai/`-prefixed twins, and bare `gpt-5.6` — **eight** names:

| tier | must include |
|---|---|
| high | `gpt-5.6-sol`, `openai/gpt-5.6-sol`, `gpt-5.6`, `openai/gpt-5.6` |
| medium | `gpt-5.6-terra`, `openai/gpt-5.6-terra` |
| low | `gpt-5.6-luna`, `openai/gpt-5.6-luna` |

Each grouped so the tier's **first** entry — the swap target — is unchanged from today.

The same completeness requirement applies to `fallbacks:`: chains keyed only on bare
names leave the prefixed twins and the bare family alias without a chain.

> This hazard is **pre-existing and not 5.6-specific** — it applies to any alias
> absent from `cost_tiers`. Auditing the existing aliases for the same gap is worth
> doing, but is out of scope here.

`_TIER_MAP` (`router.py:113`) is model-agnostic and needs no change.

> **Noted pre-existing inconsistency:** `gpt-5.4` is $2.50/$15 — identical to Terra —
> but sits in `high`, annotated "cheaper previous flagship". Terra goes to `medium`
> regardless: `cost_tiers` drives *cost-based* routing, and $2.50/$15 next to
> `gpt-5-mini` at $0.75/$4.50 is defensible where $2.50 next to `claude-opus` at $5
> is not. Not fixed here.

### 8.2 Fallbacks

**Every callable alias needs a chain**, for the same reason every callable alias needs
a tier (§8.1.1): a name with no chain has no failover at all. All eight:

```yaml
    # Sol-bodied (high tier)
    - gpt-5.6-sol: [gpt-5.6-terra, gpt-5.6-luna, claude-opus]
    - openai/gpt-5.6-sol: [gpt-5.6-terra, gpt-5.6-luna, claude-opus]
    - gpt-5.6: [gpt-5.6-terra, gpt-5.6-luna, claude-opus]
    - openai/gpt-5.6: [gpt-5.6-terra, gpt-5.6-luna, claude-opus]
    # Terra-bodied (medium tier)
    - gpt-5.6-terra: [gpt-5.6-luna, gpt-5.6-sol, claude-sonnet]
    - openai/gpt-5.6-terra: [gpt-5.6-luna, gpt-5.6-sol, claude-sonnet]
    # Luna-bodied (low tier)
    - gpt-5.6-luna: [gpt-5.6-terra, gemini-flash, claude-sonnet]
    - openai/gpt-5.6-luna: [gpt-5.6-terra, gemini-flash, claude-sonnet]
```

Intra-family first (identical capability envelope + context = a genuinely transparent
swap), then cross-provider. Chains are grouped by **body**, so aliases sharing a body
fail over identically — a client calling `openai/gpt-5.6-sol` gets the same failover
behaviour as one calling `gpt-5.6-sol`.

**Context-compatibility hazard — why Luna does *not* fall back to `claude-haiku`.**
The 5.6 family accepts 1,050,000 input tokens; `claude-haiku` caps at **200,000**. A
long-context request failing over to haiku does not degrade, it **hard-fails**,
converting a retryable upstream blip into a client error. Chosen targets:
`gemini-flash` 1,048,576 ✅, `claude-sonnet` 1,000,000 ✅, `claude-opus` 1,000,000 ✅
— all within ~5% of 1.05M. A request in that last ~2% window still fails, but
exposure drops from ~850K tokens to ~50K.

*(The existing `gpt-5-nano: [claude-haiku, ...]` chain is safe only because 5.4-nano
requests rarely run long. Not changed here, but worth knowing.)*

### 8.3 Batch: deliberately not claimed

All three advertise `/v1/batch`, but `endpoints_for` (`capability.py:72`) reports
batch only on an explicit `airlock_batch` marker. **Omit the marker.** `chat`-only is
the honest answer under the anti-overclaim rule; add batch in a separate change once
an OpenAI batch round-trip is actually exercised. Overclaiming batch is worse than
underclaiming it.

## 9. Pre-patch: template drift (urgent, ships first)

Independent of GPT-5.6. `airlock/cli/templates/config.yaml` has drifted from root and
ships stale targets to **every new user**:

| alias | template target | status | fix |
|---|---|---|---|
| `gpt-5-codex` | `openai/gpt-5.1-codex` | **retires ~2026-07-23** | → `openai/gpt-5.3-codex` |
| `gpt-5-pro` | `openai/gpt-5.4-pro` | real, but a generation behind root | → `openai/gpt-5.5-pro` |
| `gpt-5` | `openai/gpt-5.4` | real, but root says flagship is 5.5 | → `openai/gpt-5.5` |
| `gpt-5-mini` / `-nano` | 5.4-mini / 5.4-nano | correct | keep |

Also missing from the template: **all** `openai/`-prefixed twins, and the `gpt-5.4`
alias that root's fallback chain depends on.

> **Date confidence:** OpenAI's model page marks GPT-5.1-Codex deprecated. The exact
> 2026-07-23 API-shutdown date comes from secondary sources; codex review could not
> extract it cleanly from the official deprecations page. The *fix* is correct
> regardless of the precise date — 5.1-codex is deprecated and 5.3-codex is not.

**Root cause of the drift:** `tests/test_config_consistency.py:47` validates
fallbacks against the `template_config` fixture, **not** root. A
`root_config`/`root_model_names` fixture exists but the fallback tests don't use it,
so drift in either direction goes uncaught. Fixing the test is part of the pre-patch.

## 10. Risks

| # | risk | severity | mitigation |
|---|---|---|---|
| 1 | Guessed `gpt-5.6-mini/nano/pro` → Sol at $5/$30, silently, at DEBUG | **high** | §4.2 strict resolution + helpful 404 |
| 2 | First-writer-wins prefix binding misroutes `openai/gpt-5.6-sol` | **high** | §4.1 explicit twins in root **and** template; comment at the `setdefault` |
| 3 | Silent $0.00 cost accounting if the remote map is unreachable | **high** | §7 litellm floor `>=1.93.0` |
| 4 | `none` → `minimal` → dropped → max reasoning, billed | **high** | §5 model-aware floor |
| 5 | ~~>272K surcharge invisible to budget logic~~ | — | **RESOLVED, §12 — no under-count; accounting is correct end to end.** |
| 14 | ~~litellm over-counts 5.6 long-context~~ | — | **WITHDRAWN — the claim was wrong.** The surcharge is real and litellm is correct (§12.4). No override, no upstream bug. |
| 15 | 7 configured models absent from litellm's map record **$0.00** cost, invisible to budgets | **medium** | §12.6 — pre-existing, unrelated to 5.6, needs its own ticket |
| 6 | 1.05M ctx vs fallback ceilings; ~50K residual exposure | **low** | §8.2; unavoidable without a context-aware fallback selector |
| 7 | `azure/gpt-5.6-sol` resolves to the OpenAI-direct entry, unwarned | **low** | Correct given no Azure entries configured; document |
| 8 | Tier dict drift between `router.py` and `settings.py` | **medium** | §8.1 update both; a shared constant is the real fix (out of scope) |
| 9 | Batch overclaim via speculative `airlock_batch` | **low** | §8.3 omit the marker |
| 10 | Untiered alias silently **force-swapped** to `tier_models[0]` | **high** | §8.1.1 tier all 8 callable names |
| 11 | ~~Guess-guards advertised in `/v1/models`~~ | — | **ELIMINATED** by §4.2 — no synthetic entries are ever created |
| 12 | TUI param schema drift — `airlock/tui/param_schemas.py` has a parallel per-provider schema with no `reasoning_effort` field and an empty `MODEL_OVERRIDES` (`:162`) | **medium** | §11 Q7 — inspect; the §5 fix does **not** propagate there automatically |
| 13 | MCP-exposed model/config surfaces not inspected | **low** | §11 Q8 — explicit no-change-after-inspection item |
| 16 | Strict resolution 404s cross-tier fuzzy matches that silently worked before; affected client set is **unknown** | **high** | §13 warn-only measurement window before enforcement |
| 17 | Airlock forwards bare `gpt-5.6` upstream and 404s | **medium** | §2.2 — alias pinned to the explicit `-sol` body; assert in tests |

**No collision** on `capability.py:102` `deprecated`: none of `gpt-5.6`, `-sol`,
`-terra`, `-luna` ends with `-aistudio`/`-vertex`/`-batch` (the check is `endswith`,
so `-terra` is not a `-vertex` substring hazard). A future `gpt-5.6-*-batch` alias
*would* be auto-flagged — relevant only if §8.3 is revisited.

**No admission-gate impact:** it is per-client RPM/concurrency and not model-cost aware.

## 11. Open questions / not verified

1. ~~**Does Airlock's spend accounting read litellm's `*_above_272k_tokens` fields?**~~
   **RESOLVED — see §12. Answer: no under-count. The premise was wrong twice over.**
2. 🔴 **Is `max` a valid effort level at the OpenAI API? — STILL OPEN, BLOCKING.**
   OpenAI's docs list it; litellm sets no `supports_max_reasoning_effort` key for 5.6.
   Under strict validation (§5.1) this one bit decides accept-vs-400, and getting it
   wrong fails in both directions. **A live call was attempted on 2026-07-20 and did
   not settle it:** the available `OPENAI_API_KEY` authenticates and lists models but
   has **no quota**, and the quota error fires *before* parameter validation. Needs a
   funded key. Until then, do not implement the `max` branch either way.
3. ✅ **RESOLVED — it is offered** by `/v1/models` (§2.2). Residual: listing is not
   proof a completion succeeds; the quota-less key blocked that check.
4. ✅ **RESOLVED — no.** `/v1/models` offers only sol/terra/luna; bare `gpt-5.6` is
   **not** a real id (§2.2). The §4.1 pin to the explicit `-sol` body is what makes
   the convenience alias safe, and is now load-bearing rather than merely prudent.
5. **Relative quality of Terra vs Sol.** Pricing and flags are documented; capability
   is not. Tier assignments are cost-driven, which matches what `cost_tiers` is for,
   but "Terra is a fine default" is unvalidated.
6. **Does `mode: responses` on the pro/codex entries cause issues on Airlock's
   chat-completions path?** Not traced. Pre-existing.
7. **TUI parameter schema.** `airlock/tui/param_schemas.py:73-85` carries a parallel
   per-provider `ProviderSchema` for `"openai"` that has **no `reasoning_effort` field
   at all**, and `MODEL_OVERRIDES` at `:162` is empty. The §5 model-aware fix operates
   on the proxy path and will **not** propagate to the TUI automatically. Determine
   whether the TUI needs the 5.6 names, the `none`/`xhigh`/`max` effort levels, or a
   populated `MODEL_OVERRIDES` — and if not, record why.
8. **MCP surface.** Airlock exposes MCP servers (`airlock/mcp_servers/`). Whether any
   of them enumerate models or model config is **not inspected**. Resolve as an
   explicit "no change required after inspection" item rather than leaving it silent.
9. **Release numbering.** Current version is 0.5.5; 0.5.6 is planned-but-unstarted
   (admission gate). The §9 pre-patch needs a version that does not collide.
   **`0.5.5.1` is withdrawn — not valid semver.** See plan P-0 for the live options;
   the recommendation is to fold the hotfix in as **item F-0 of the existing 0.5.6**
   and ship that item first, which needs no renumbering. **Your call.**

**No live inference was performed against OpenAI.** All reasoning-effort results come
from litellm's real transformation code executed locally against the live model map —
accurate for what litellm *emits*, not proof of what OpenAI *accepts*.

---

## 12. RESOLVED: long-context surcharge accounting (was P-1)

**Answer: Airlock does NOT under-count. No fix required.** The blocker's premise was
wrong on two counts, and the real risk points the opposite way.

### 12.1 Airlock never computes cost

Single source of truth is litellm's `response_cost`, read at
`airlock/litellm_adapter.py:60`. Every consumer traces back to that one number:

| consumer | path |
|---|---|
| provider spend | `airlock/fast/monitor.py:164,185-191` → `record_spend(now, cost)` |
| request events | `airlock/callbacks/request_event.py:232` |
| projections | `airlock/callbacks/projections.py:117` |
| MTD/YTD queries | `airlock/api/queries.py:26` |

A repo-wide search for manual rate arithmetic found exactly one hit —
`_state_spend.py:129`, an integer µ$→USD conversion, not pricing. **No independent
cost derivation exists anywhere in Airlock.**

### 12.2 litellm honours the threshold fields

`litellm/litellm_core_utils/llm_cost_calc/utils.py:216-333` parses the threshold out
of the field name (`_above_272k_tokens` → 272000) and, when exceeded, **replaces the
base rate for the entire request** — input, output, cache-creation and cache-read all
swap to tiered values. This matches OpenAI's stated semantics (the long-context rate
applies to the session, not only to tokens above the threshold).

The threshold is measured on **input tokens only** (`utils.py:248`:
`if usage.prompt_tokens > threshold`, strictly greater). Completion tokens don't count
toward tripping it, but are billed at the tiered output rate once it trips.

Empirically verified locally (no network):

```
gpt-5.6-sol  in=271999 out=1000  $1.389995
gpt-5.6-sol  in=272001 out=1000  $2.765010   <-- tier trips
# 272001×$10/M + 1000×$45/M = $2.765 — exactly 2x input / 1.5x output
```

### 12.3 No pre-flight cost estimate exists

The hypothesised under-count site isn't there. The only budget-gating path,
`_apply_budget_awareness` (`airlock/fast/router.py`), reads
`store.get_provider_spend(provider).recent_spend()` — **recorded actual** spend, never
a forward estimate. The 0.5.6 admission gate is RPM/concurrency only, with no cost
dimension.

### 12.4 ✅ RESOLVED: the surcharge is REAL and litellm is correct

**An earlier revision of this document claimed GPT-5.6 dropped the 272K surcharge and
that litellm would over-count. That was WRONG and has been reverted.**

litellm's day-0 GPT-5.6 announcement publishes explicit short/long dual rates for every
variant — "Prices are per 1M tokens (USD), shown as short context (≤272K tokens) / long
context (>272K tokens)" — with sol at $5/$10 in and $30/$45 out, terra at $2.50/$5 and
$15/$22.50, luna at $1/$2 and $6/$9. That is exactly 2× input / 1.5× output, matching
the fields in the model map.

**Consequences:**
- litellm's `input_cost_per_token_above_272k_tokens` entries for 5.6 are **correct**.
- **No local price override is needed.** No upstream bug to file.
- Airlock inherits correct long-context accounting (§12.1–12.3) with no work required.
- Config comments and docs **should** state the surcharge — it is real, and it is
  material on a model whose headline feature is a 1.05M context.

**Provenance of the error:** a research pass concluded from OpenAI's flat-rate summary
table that 5.6 had no tier, without reconciling against the dual-rate table litellm
publishes. Recorded here because the same mistake is easy to repeat: a single flat
price per model is the *short-context* rate, not the whole pricing story.

Source: [litellm GPT-5.6 day-0 post](https://docs.litellm.ai/blog/gpt_5_6).

### 12.5 This is a general issue class, not 5.6-specific

Seven models **already in `config.yaml`** carry threshold pricing today, all handled
correctly:

| model | threshold | input | output |
|---|---|---|---|
| `openai/gpt-5.5`, `gpt-5.5-pro`, `gpt-5.4` | 272k | 2.0× | 1.5× |
| `gemini/gemini-2.5-pro` | 200k | 2.0× | 1.5× |
| `gemini/gemini-3.1-pro-preview` (+`-customtools`) | 200k | 2.0× | 1.5× |
| `vertex_ai/gemini-3.1-pro-preview` | 200k | 2.0× | 1.5× |

Gemini Pro at 200K is the one most likely to be hit in practice. All three Anthropic
models are flat (they carry only a cache-*duration* tier, not a context tier).

### 12.6 Separate gap found — worth its own ticket

Seven configured models are **absent from litellm's map entirely**, so they record
**no cost at all** (`response_cost` is 0/None): `enhanced/gemini-coding`,
`openai/qwen3.6-27b`, `openai/qwen3-32b`, `openai/gemma4-31b`, `openai/kimi-dev-72b`,
`openai/internal-docs`, `tavily/web-search`.

Self-hosted models legitimately have no upstream price, but silently recording $0
means they are invisible to budgets and spend reporting. Unrelated to GPT-5.6;
**not fixed here.**

### 12.7 Not fully verified

The exact gpt-5.5 multiplier was corroborated by search snippets and the litellm map,
but one primary-source read of OpenAI's pricing table came back garbled. It does not
affect the conclusion. If the gpt-5.5 figure matters for billing reconciliation, read
that table by hand.

---

## 13. Warn-only measurement window (required before enforcement)

Two changes in this design turn silent behaviour into rejections:

- **§4.2** — cross-tier fuzzy model matches become 404s.
- **§5.1** — provider-invalid `reasoning_effort` values become 400s.

Both replace behaviour that was **never surfaced to clients**, so the affected
population cannot be enumerated from the code — only measured. Enforcing blind would
break an unknown number of callers with no warning.

**Therefore both ship warn-only first, and enforce only in the following release.**

### 13.1 Warn-only semantics

In warn-only mode Airlock behaves **exactly as it does today** — same substitution,
same translation, same served model — and additionally:

1. Logs at **WARNING** with a structured, greppable payload (never DEBUG).
2. Emits the advisory response header the enforcing version would send
   (`X-Airlock-Model-Suggestion` / the `reasoning_effort` advisory), so clients can
   discover the problem before it becomes fatal.
3. Records a mutation-ledger entry, so it lands in `X-Airlock-Mutations` and the
   observability event stream like any other request mutation.

### 13.2 The log records must be machine-countable

This is the deliverable of the window, not a side effect. Each event carries a stable
`event` discriminator and enough fields to answer "who is affected, how often, and
what would break":

```
WARNING airlock.model_alias  event=fuzzy_match_would_reject
        requested=gpt-5.6-mini served=gpt-5.6 suggested=gpt-5.6-luna
        score=0.58 from_tier=high to_tier=low client_id=<id>
WARNING airlock.reasoning_effort  event=effort_would_reject
        requested=none translated_to=minimal model=gpt-5.4
        supported=minimal,low,medium,high client_id=<id>
```

Requirements:
- Both events must be reachable from the existing observability path (0.5.4 unified
  `RequestEvent` + recorder) — **not** log-file-only, so they can be counted with the
  same query tooling as everything else.
- `client_id` must be present wherever client identity is known, so the impact can be
  attributed to specific callers rather than a bare total.
- Counts must be retrievable without shell-grepping production logs.

### 13.3 TODO — measure, then enforce

Explicit follow-up items; enforcement **must not** ship until these are closed:

- [ ] **T-1** Warn-only release deployed, both events emitting to the event store.
- [ ] **T-2** Verify the events are queryable via existing tooling; if they are not,
      that is a blocker on the measurement, not a documentation gap.
- [ ] **T-3** Measurement window agreed (recommend ≥2 weeks / ≥1 full billing cycle,
      whichever is longer) and observed.
- [ ] **T-4** Report: affected client count, request volume, and the distinct
      (requested → would-be-rejected) pairs for both events.
- [ ] **T-5** For each affected caller, decide notify / grace-extend / enforce.
- [ ] **T-6** Enforce. Remove the warn-only branch — do **not** leave it as a
      permanent config toggle; a toggle that preserves the old behaviour indefinitely
      is how this bug survives.
