# Warn-only measurement window — RUNBOOK

**Status:** ACTIVE. Started with the 0.5.6 deploy on **2026-07-20**.
**Owner:** `coreyt` — *assigned by default because this is a single-maintainer repo.*
**Reassign if that is wrong; an unowned window is the failure mode this exists to avoid.**

| milestone | date |
|---|---|
| Window opens (first full day post-deploy) | **2026-07-21** |
| Window closes | **2026-08-21** |
| T-4 report due | **2026-08-21** |
| Enforce / extend decision | **2026-08-25** |

## Why a month, not two weeks

Long enough to capture **at least one full billing cycle**. The population most likely
to be broken by a silent-behaviour change is the one that calls infrequently — monthly
batch jobs, month-end reporting, scheduled reconciliation. A two-week window
systematically misses exactly those callers, and they are the worst ones to break
without warning. Two weeks is the floor; a billing cycle is the binding constraint.

## What is being measured

0.5.6 ships **warn-only** detection. Routing is byte-identical to before — no client
sees a behaviour change yet. What it emits is the answer to: *who would break if we
enforced?*

| event | fires when |
|---|---|
| `effort_would_reject` | a client sent a `reasoning_effort` the target model does not accept |
| `model_alias_dropped_qualifier` | a model name was refused because matching it would have meant ignoring a qualifier the caller supplied (already **enforcing** in 0.5.6 — 5.6-family names only, zero pre-existing population) |

**The effort check evaluates the value the CLIENT SENT**, not the value Airlock emits
after translation. This matters: `none` on `gpt-5.4` is translated to a supported
`minimal` today, so measuring the emitted value would report that cohort as fine and
hide the single largest group enforcement will break.

## T-1 — Confirm events are flowing

```bash
cd ~/projects/airlock
grep -h "effort_would_reject" logs/airlock-*.log | tail -5
```

Expect lines shaped:

```
2026-07-21 09:14:02 WARNING  airlock.reasoning_effort  event=effort_would_reject \
  requested=none translated_to=minimal model=gpt-5.4 \
  supported=high,low,medium,minimal client_id=<id>
```

**Zero hits is a real result, not a failure** — but distinguish "no affected traffic"
from "not wired up". Confirm the code path is live before concluding the former:

```bash
grep -c "airlock.reasoning_effort" logs/airlock-*.log
```

## T-2 — Confirm the events are queryable, not just greppable

Design §13.2 requires these to reach the observability path, not the log file only.
Each would-reject also records a ledger entry, so it should appear in
`X-Airlock-Mutations` and the unified `RequestEvent` stream.

```bash
# Ledger field name to look for
grep -h "reasoning_effort_would_reject" logs/airlock-*.log | head -3
```

**If they are NOT queryable via the normal tooling, that blocks the measurement** —
it is not a documentation gap, and it should be fixed in week 1 rather than discovered
at T-4.

## T-3 — Observe

No action. Let the window run. Do not enforce early on a small sample; a low count in
week 1 says little about a monthly caller.

## T-4 — The report (due 2026-08-21)

Produce these four numbers:

```bash
cd ~/projects/airlock

# 1. Total would-reject events
grep -h "effort_would_reject" logs/airlock-*.log | wc -l

# 2. Distinct affected clients
grep -ho "client_id=[^ ]*" logs/airlock-*.log | sort -u | wc -l

# 3. Distinct (requested -> model) pairs — what actually needs changing
grep -h "effort_would_reject" logs/airlock-*.log \
  | grep -o "requested=[^ ]* .*model=[^ ]*" \
  | sed -E 's/.*requested=([^ ]*).*model=([^ ]*).*/\1 -> \2/' \
  | sort | uniq -c | sort -rn

# 4. Per-client breakdown — who to notify
grep -h "effort_would_reject" logs/airlock-*.log \
  | grep -o "client_id=[^ ]*" | sort | uniq -c | sort -rn
```

Record the results in this file under **Results** below. A report that isn't written
down cannot inform the decision.

## T-5 — Decide per affected caller

For each client in (4): **notify**, **grace-extend**, or **enforce**.

Rough guidance:
- No affected clients → enforce immediately in 0.5.8.
- A few identifiable internal callers → notify, give them a sprint, then enforce.
- Many unknown/external callers → extend one more cycle and notify more loudly.
  Do **not** extend twice; that is how the bug becomes permanent.

## T-6 — Enforce, and delete the warn-only branch

0.5.8 turns the detection into real 400s. **Remove the warn-only code path — do not
leave it behind a config toggle.** A toggle that preserves the old behaviour
indefinitely is precisely how this class of bug survives; the whole point was to stop
silently substituting values.

Also resolve at this point:
- **`max` reasoning effort** — currently treated as unsupported because litellm sets no
  `supports_max_reasoning_effort` flag and no funded OpenAI key was available to test
  it. **This is a guess and is marked in-code for revisit.** Settle it with one live
  call before enforcing, or the enforcing release rejects a level OpenAI documents as
  valid.

## Results

*(fill in at T-4 — 2026-08-21)*

| metric | value |
|---|---|
| Window | 2026-07-21 → 2026-08-21 |
| Total `effort_would_reject` | |
| Distinct clients affected | |
| Distinct (requested → model) pairs | |
| Decision | |
| Decided by / date | |
