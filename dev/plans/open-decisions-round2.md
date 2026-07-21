# Open decisions — round 2 (post-implementation)

**Date:** 2026-07-20
**Supersedes nothing.** Round 1 (D-1…D-10) is resolved — see
[`0.5.8-open-decisions.md`](0.5.8-open-decisions.md). These are the items that remain
or that the round-1 work created.

Status: 🔴 blocks a release · 🟡 decide before that item ships · 🟢 confirm-and-move-on

---

## O-1 🔴 — The branch mixes two releases; it must be split before 0.5.6 ships

Branch `fix/model-config-hardening` currently contains **both**:

| change | belongs in |
|---|---|
| template/root config corrections + consistency tests | **0.5.6** (hotfix) |
| `airlock/fast/router.py` `_alias_body_map` + body-aware `_apply_cost_tier` + its tests | **0.5.8** (per the plan) |

The plan says the router change takes a normal review cycle rather than riding an
urgent patch, but the code doesn't reflect that yet.

| # | option | effect |
|---|---|---|
| **A** | **Split: cherry-pick the config+test commits to a 0.5.6 branch, leave the router fix for 0.5.8** | **Recommended.** |
| B | Ship both as 0.5.6 | Contradicts the plan's own reasoning; puts a routing behaviour change in an urgent patch. |
| C | Hold the hotfix until 0.5.8 | Misses the 2026-07-23 codex retirement. Not viable. |

**Recommendation: A.** The whole argument for a separate hotfix was "smallest reviewable
thing that stops the bleeding." Shipping a routing behaviour change inside it discards
that argument at the moment it matters. The split is mechanical — the two changes touch
disjoint files.

**Note:** the `_apply_cost_tier` fix is currently *unreferenced* by any shipped 0.5.6
behaviour, so pulling it out cannot break the hotfix.

---

## O-2 🔴 — `gemini-flash-lite` advances silently until the header ships

**I created this problem and want it on the record.** You asked for two things:
advance `gemini-flash-lite` to 3.1, **and** have the X- header tell clients it is
3.1-flash-lite rather than 2.5-flash-lite.

The alias advance is **done** (both configs). The header is **P-6 of 0.5.8** — not
built. So if 0.5.6 ships as-is, the alias silently changes generation with no in-band
disclosure. That is precisely the failure mode the design condemns in §4.2 and §6:
*the client is served something other than what it named and isn't told.*

| # | option | effect |
|---|---|---|
| **A** | **Move the `gemini-flash-lite` advance out of 0.5.6 into 0.5.8, shipping it together with the header** | **Recommended.** |
| B | Ship the advance in 0.5.6, header later | A silent generation change for an unknown window — the exact thing we are trying to stop doing. |
| C | Ship the advance in 0.5.6 with CHANGELOG-only disclosure | Better than B, but release notes are not in-band; callers who don't read them still get a silent swap. |
| D | Pull the header forward into 0.5.6 | Header needs the `model_successors` config plumbing and its tests; that is not an urgent-patch-sized change. |

**Recommendation: A.** 0.5.6's remit is "fix ids that are broken or retiring."
`gemini-2.5-flash-lite` is **neither** — it is real and working. The advance is a
product improvement, and coupling it to the header is what makes it honest. It also
keeps the hotfix minimal, reinforcing O-1.

**If you prefer the advance now**, say so and I'll take C and write the CHANGELOG entry
— but A is the one consistent with the principle you set.

---

## O-3 🔴 — `max` reasoning_effort: still needs a funded key

Unchanged from round 1 (D-2), still the **only blocker on P-2 implementation**.
OpenAI documents `none/low/medium/high/xhigh/max`; litellm sets no
`supports_max_reasoning_effort` flag for 5.6. Under strict validation this one bit
decides accept-vs-400, and both wrong answers are failures.

The live attempt failed: the key authenticates and lists models but has **no quota**,
and the billing error fires *before* parameter validation.

| # | option | |
|---|---|---|
| **A** | **Funded key → one minimal call → settle it** | **Recommended.** Cost is cents. |
| B | Trust litellm, reject `max` | Rejects a documented-valid level; breaks legitimate callers. |
| C | Allow-list `max` | May 400 upstream, surfacing as a confusing provider error. |

**Recommendation: A.** This is exactly the class of guess your strict-validation
principle forbids. Until it's settled, **do not implement the `max` branch either way** —
a placeholder here becomes a silent wrong default.

---

## O-4 🟡 — Root's generic Gemini aliases (`gemini-pro`, `gemini-flash`)

You advanced `gemini-flash-lite`. These two still track 2.5. Their 3.x counterparts
exist **only as `-preview`** (`gemini-3.1-pro-preview`, `gemini-3-flash-preview`) —
verified live.

| # | option | |
|---|---|---|
| **A** | **Leave on 2.5; keep 3.x reachable under its explicit `-preview` names** | **Recommended.** |
| B | Advance both to the `-preview` ids | Preview ids move and get withdrawn — root's own comment records `gemini-3-pro-preview` being shut down 2026-03-09. A generic alias is the worst place for that instability. |
| C | Advance `gemini-flash` only | No principled line; same preview risk. |

**Recommendation: A.** `gemini-flash-lite` was a *different* case and that's why it
was safe: 3.1-flash-lite is **GA**. Generic aliases should point at GA models; preview
models should be opt-in under explicit names. If a 3.x pro/flash goes GA, revisit then.

---

## O-5 🟡 — Warn-only measurement window: length and owner

Design §13 / plan P-6c gate enforcement of **both** strict changes behind a warn-only
release. Two parameters are unset:

- **Window length.** Recommend **≥2 weeks or ≥1 full billing cycle, whichever is
  longer** — long enough to capture monthly batch jobs and low-frequency callers, which
  are exactly the ones most likely to be broken by a silent-behaviour change.
- **Owner for T-4** (the report: affected clients, volume, distinct rejected pairs).
  Without a named owner this stalls and enforcement either slips indefinitely or ships
  unmeasured — the two failure modes the window exists to prevent.

**Recommendation:** adopt the window above and name an owner now, while the reasoning
is fresh. Also confirm T-2 early: if the events turn out not to be queryable via
existing tooling, that blocks the measurement and is better discovered in week 1.

---

## O-6 🟢 — Confirm the widened `X-Airlock-Model-Alias` scope

Originally scoped to successor advice only (`newer=`). I widened it: it now emits
`served=` on **every aliased response**, which is what makes O-2's disclosure possible.

That is a larger surface than approved — every aliased request gains a header.

| # | option | |
|---|---|---|
| **A** | **Confirm the widened scope** | **Recommended.** |
| B | Revert to successor-only | Then alias advances stay undisclosed and O-2 has no fix. |

**Recommendation: A.** The `served=` disclosure is the mechanism that makes moving a
generic alias safe, and it generalises — every future alias advance is covered without
new work. Cost is a short header on aliased responses, gated by the existing
transparency config like `Served-By` and `Mutations`.

---

## O-7 🟢 — Ticket for the seven $0.00-cost models

`enhanced/gemini-coding`, `openai/qwen3.6-27b`, `openai/qwen3-32b`,
`openai/gemma4-31b`, `openai/kimi-dev-72b`, `openai/internal-docs`,
`tavily/web-search` are absent from litellm's map, so `response_cost` is 0/None and
they are invisible to budgets and spend reporting.

**Recommendation: raise a ticket, do not fix inline.** Self-hosted models legitimately
have no *upstream* price, so this needs a design decision — operator-configured rate? an
explicit "unpriced" state distinct from $0.00? exclusion from budget math? — not a
lookup patch. Flagging because "silently $0" and "genuinely free" are indistinguishable
today, and only one of them is correct.

---

## O-8 🟢 — Review gate before 0.5.8 implementation

0.5.8 now contains two behaviour changes (P-2, P-2b), a new config block
(`model_successors`), a new header, and the 5.6 entries.

**Recommendation:** one design+plan review pass before coding starts. Note the codex CLI
sandbox is currently broken in this environment (`bwrap: loopback: Failed RTM_NEWADDR`)
and could not read files on the last two attempts — it correctly refused to review
rather than invent findings. Either fix the sandbox or use a subagent reviewer, which
worked and produced the six findings already folded in.

---

## Summary

| # | item | status | recommendation |
|---|---|---|---|
| O-1 | Branch mixes 0.5.6 + 0.5.8 changes | 🔴 | Split; router fix waits for 0.5.8 |
| O-2 | `gemini-flash-lite` advances silently pre-header | 🔴 | Move the advance to 0.5.8, ship with the header |
| O-3 | `max` validity | 🔴 | Funded key, one call; don't implement until settled |
| O-4 | Root `gemini-pro` / `gemini-flash` | ✅ **DONE** | Advanced to 3.x preview per instruction (recommendation overridden). pro→`gemini-3.1-pro-preview`, flash→`gemini-3-flash-preview`. Cost increases: +60%/+20% and +67%/+20%. `gemini-3.5-flash` rejected for flash — $1.50/$9.00 breaks the `low` tier. |
| O-5 | Warn-only window length + owner | ✅ **DONE** | Runbook at [`runs/warn-only-measurement-window.md`](runs/warn-only-measurement-window.md). Window 2026-07-21 → 2026-08-21 (one billing cycle); owner `coreyt` by default — **reassign if wrong**; T-1..T-6 with executable commands. |
| O-6 | Widened `X-Airlock-Model-Alias` scope | 🟢 | Confirm |
| O-7 | ~~Seven~~ **six** $0.00-cost models | ✅ **TICKETED** | [`notes/ticket-unpriced-models.md`](../notes/ticket-unpriced-models.md). 4 are legitimately free (self-hosted vLLM). Real bug is **1**: `enhanced/gemini-coding` hides Gemini 3.1 Pro spend ($2/$12). `tavily/web-search` is P2. |
| O-8 | Review gate before 0.5.8 | 🟢 | One pass; codex sandbox is broken, use a subagent |

**O-1 and O-2 both block a clean 0.5.6, which has a hard 2026-07-23 deadline.**
O-3 blocks 0.5.8 implementation but nothing sooner.
