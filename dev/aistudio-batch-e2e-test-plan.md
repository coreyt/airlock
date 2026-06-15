# AI Studio (Gemini) Batch — Live E2E Test Plan / Operator Gate

**Status:** active operator gate for the `#1 AI Studio batch gateway` acceptance row
(`dev/plans/runs/STATUS-0.4.0.md`). The 0.4.0 unit/integration suite proves the
*translation + gateway wiring* against mocks; **this gate proves the real round-trip
against Google's live Gemini batch endpoint.** Until it has passed at least once on a
given tree, "Airlock supports Gemini batch via AI Studio" is **unverified**, not done.

## Why a separate gate
Unit tests mock the `google-genai` SDK, so they validate our intent, not Google's
acceptance of it. The things that only break on first contact with the live API are
exactly the unmocked ones:

- whether the uploaded JSONL (`{"key", "request":{contents,…}}`, `mime_type:
  application/jsonl`) is accepted by `files.upload`;
- whether `batches.create(model=…, src=<file ref>)` accepts a file-ref `src` and the
  configured `provider_model` string;
- whether `JOB_STATE_*` polling reaches `JOB_STATE_SUCCEEDED`;
- whether `job.dest.file_name` + `files.download` returns the result JSONL we expect;
- whether `candidates → choices` projection survives a real response.

## Preconditions
- Install the extra: `uv sync --extra aistudio` (pulls `google-genai`).
- `GOOGLE_AISTUDIO_API_KEY` exported (present in `.env`).
- A `provider_model` the live key can run in batch mode. Default `gemini-3.5-flash`
  (matches `config.yaml`); override with `AIRLOCK_AISTUDIO_E2E_MODEL` if the live API
  rejects it — a rejection here is itself a real finding.

## How to run
```bash
set -a && . ./.env && set +a
AIRLOCK_LIVE_AISTUDIO_E2E=1 \
  uv run --extra aistudio pytest tests/test_aistudio_batch_e2e.py -v -s
```
Opt-in knobs (env):

| Var | Default | Meaning |
|-----|---------|---------|
| `AIRLOCK_LIVE_AISTUDIO_E2E` | unset | must be `1` to run (else skipped) |
| `AIRLOCK_AISTUDIO_E2E_MODEL` | `gemini-3.5-flash` | provider model passed to `batches.create` |
| `AIRLOCK_AISTUDIO_E2E_TIMEOUT` | `1200` | max seconds to wait for a terminal job state |
| `AIRLOCK_AISTUDIO_E2E_POLL` | `20` | poll interval seconds |

The test is **opt-in and billable**: with the opt-in unset it skips, so a normal
`pytest` run never submits a job.

## What it exercises (production path, not a bespoke flow)
It drives the real `airlock.batch.gateway` functions against a real
`AIStudioBackend` + a temp `BatchStore`, so the asserted path is the one the live
proxy runs:

1. `create_batch(...)` → stream-translate OpenAI lines → `backend.upload` →
   `backend.create` → store `CREATING→CREATED`. Asserts status `validating|in_progress`.
2. Poll `get_batch(...)` every `POLL`s until a terminal state or `TIMEOUT`. This calls
   the live `backend.poll` (`JOB_STATE_*` → OpenAI status) each tick.
3. On `completed`, `stage_results` runs `backend.fetch` (download + parse) and
   `from_provider_result` (Gemini → OpenAI), staging rows into the store.
4. Assert via `store.staged_bodies(batch_id)`:
   - both `custom_id`s present, no per-row `error`;
   - the `PONG` prompt's `choices[0].message.content` contains `PONG`;
   - the `2+2` prompt's content contains `4`.

## Pass / fail
- **Pass:** job reaches `completed` and both staged rows assert clean. Record the
  pass (tree SHA + date + provider model used) on the status board and flip the
  acceptance row from `✅ (unit; live e2e = operator gate)` to a verified state.
- **Fail/timeout:** capture the last batch object (`status`, `errors`) and the failing
  stage. A model-name rejection, upload-format rejection, or result-shape mismatch is a
  real defect in the AI Studio adapter — log it in the owning design note
  (`dev/design-aistudio-gemini-batch.md`, "Open issues") and on the status board; do
  not paper over it.

## Cleanup
Gemini batch jobs and their result files auto-expire; the test does not delete them.
To remove eagerly, `client.batches.cancel(name=…)` / `client.files.delete(name=…)`.
