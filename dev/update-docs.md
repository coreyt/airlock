<!--
  DOCS EPOCH MARKER — update this line at the END of every successful run.
  Docs current as of: f9d01ed  (2026-06-26)  [main — reconciled the full 0.5.0 train + 0.5.1 (8a2c332→HEAD): 0.5.1 settings-coherence + storage-seam docs (config airlock_settings keys, provider_budget_config 3-behaviors, routing warn-ratio 0.9→0.8, operations spend-durability, .env.example, dev/architecture §3.8); 0.5.0 verify pass; fixed doc-infra drift (mkdocs nav: +rate-limiting/+provider-observability/+admin-api, response-headers broken anchor) so `mkdocs build --strict` is clean; observability.md full Prometheus metric table; CORRECTED provider-observability budget-gauge contradiction (gauges don't exist); cli.md missing flags; configuration.md files_settings+batch_profile; tracing.py OTel version 0.3.1→0.5.1. Flagged: no budget-spend Prometheus gauge (possible future).]
  (SHA of the commit whose tree the documentation was last verified against.)
-->

# Prompt: Update Repository Documentation Since a Docs Epoch

You are updating the documentation of **Airlock** so it once again matches the
code. You work from a **documentation epoch** — a git commit that marks "the docs
were accurate as of this tree" — diff the completed work since that epoch, and
bring every affected document up to date. This file is the operational prompt;
read it fully before acting.

> **When to use this prompt.** Per-pack work already maintains docs in its closing
> `docs(...)` commit (the harness's one-docs-commit-per-transition rule). Use *this*
> prompt for the cases that rule does not catch: **out-of-band landings** (a hotfix
> or owner-managed change pushed outside a pack), **drift recovery** after a stretch
> of fast iteration, **release-eve sweeps** before a version gate, or any time you
> suspect `dev/`↔`docs/`↔code have diverged. It is a whole-tree reconciliation, not
> a substitute for the per-pack discipline.

> **Scope guardrail.** This is a documentation task. **Do not modify any source
> code under `airlock/`.** **Test files under `tests/` are read-only here.** If the
> diff reveals a code bug or a code/doc contradiction, record it in the owning
> `dev/design-*.md`/`dev/feature-*.md` doc's "Known limitations" (or "Open issues")
> section and on the live status board (`dev/plans/runs/STATUS-<release>.md`); **do
> not fix it**. Surface it — a wrong doc is worse than its absence, but silently
> patching code from a docs pass is worse than both.

> **The requirements-contract guardrail (Airlock-specific).** `dev/requirements.md`
> IDs (`FR-*`, `NFR-*`) and `dev/user-needs.md` IDs (`UN-*`) are a traceability
> contract. **Never renumber or withdraw an existing REQ/UN ID from this prompt**,
> and do not silently mint new ones. You may adjust *prose* describing an
> already-shipped, already-IDed behaviour, and re-point a requirement's "Traces to"
> line to UN IDs that already exist. If completed work implies a genuinely new
> requirement or user need, **record the gap** on the status board and in the owning
> design doc, flag it in your summary, and leave the ID to a deliberate
> requirements pass — do not invent one mid-doc-sync.

---

## 0. Inputs

| Input | How to obtain | Default |
|-------|---------------|---------|
| `$EPOCH` | The "docs current as of" commit. Read the **DOCS EPOCH MARKER** at the top of this file. If a caller supplied a SHA/tag/`git describe`, use that instead. | the SHA in the marker |
| `$HEAD` | The commit to update docs *to*. | `HEAD` |
| `$SCOPE` | Optional path/subsystem filter (e.g. "only the batch gateway", "only the 0.4.0 guardrail track"). | whole repo |

If the marker SHA is unreachable (history rewritten), fall back to the most recent
commit whose subject starts with `docs(`/`docs:`, and note the substitution in your
summary.

---

## 1. Documentation structure (the target layout)

Keep documents in these locations and roles. **`dev/` engineering docs are the
build-time source of truth; `docs/` user-facing pages (mkdocs) are derived from
them.** Airlock has no single DOC-INDEX file — the cold-start maps are
`dev/plans/README.md` (the orchestrated-work state spine) and the `nav:` block in
the repo-root `mkdocs.yml` (the user-doc map). Read both first; you must leave both
accurate.

```
dev/   (engineering docs — source of truth; NOT shipped)
├── update-docs.md          THIS prompt + the docs-epoch marker
├── user-needs.md           UN-* product/consumer needs
├── requirements.md         FR-*/NFR-* — traces to UN-*; never renumber
├── architecture.md         system context, request lifecycle, module map, deps
├── architecture-overview.txt
├── design-*.md             design notes (fathom-storage-model, unified-batch-gateway,
│                           aistudio-gemini-batch, enhanced-provider, pii-rehydration, …)
├── feature-*.md            feature specs (advisor, dynamic-processing, guardrails, harness, …)
├── impl-plan-*.md          implementation plans (e.g. plan-fathom-storage-implementation.md)
├── *-test-plan*.md         per-feature test strategy (batch-tdd-test-plan, test-plan-pii-rehydration, …)
├── tui-design.md, tui-flow-screen.md          TUI specs
├── agent-harness-*.md      orchestration harness (reference / runbook / orchestrator-prompt)
├── *-findings.md, *-investigation.md, dogfooding.md, accessing-experiments.md   research notes
├── notes/                  datastore design notes (fathomdb-as-datastore, user-needs-datastore)
└── plans/                  orchestrated-work state spine (compaction-safe)
    ├── README.md                       THE plans-tree map — keep accurate
    ├── <release>-plan.md               pack ladder + per-pack/per-AC scoreboard
    ├── prompts/                        per-pack prompts + SLICE/MASTER templates
    └── runs/
        ├── STATUS-<release>.md         LIVE state board (record gaps/bugs here)
        ├── <pack-id>-output.json       implementer closure witness
        └── <pack-id>-review-<ts>.md    promoted reviewer verdict

docs/   (user-facing; mkdocs — nav in repo-root mkdocs.yml; builds to site/; plain language)
├── index.md                            docs home + map
├── getting-started/{installation,configuration,connecting-tools}.md
├── guide/                              tui, cli, advisor, fathom-storage, guardrails,
│                                       routing, batch, vertex-batch, mcp-servers
├── architecture/{overview,diagram}.md
├── operations.md                       deployment
├── troubleshooting.md
└── changelog.md                        user-facing changelog (mirrors root CHANGELOG.md)

repo root:
├── README.md               slim overview + doc map (push detail into docs/)
├── CHANGELOG.md            release changelog
├── PROGRESS.md             narrative ledger of what landed (NOT live pack state)
├── config.yaml             the central runtime contract (model_list, guardrails, …)
├── .env.example            documented env-var surface
├── mkdocs.yml              docs nav + build config
└── AGENTS.md               wake bootstrap; canonical agent state lives in .wake/
```

### Per-design-memo shape (`dev/design-*.md`, `dev/feature-*.md`)
Objective · approach + exact code/config step (with `config.yaml` keys and any
`airlock/datastore.py` schema/`CREATE TABLE` deltas where relevant) · the gap(s) it
closes · the surface it touches (HTTP API / CLI / TUI / config) · test plan · open
issues / known limitations. Re-verify every `file:line`, symbol, config key, and
default against the live tree at `$HEAD`.

### Public-surface contract (Airlock has no `dev/interfaces/` dir)
Airlock's public surface is three things, and each has its documented home:
- **The OpenAI-compatible HTTP API** (`airlock/api/`, `airlock/proxy.py`) — endpoint
  paths, request/response shapes, and health routes. Documented in
  `dev/architecture.md` and `docs/architecture/overview.md`.
  ⚠️ Liveness is `GET /health/liveliness`, **never** `GET /health` (the latter fires
  live completions to every model). Keep this distinction correct everywhere.
- **The CLI** (`airlock`, `airlock-analyze`; `airlock/cli/main.py`,
  `airlock/slow/cli.py`) — commands and flags. Documented in `docs/guide/cli.md`;
  verify against `uv run airlock --help`.
- **`config.yaml`** keys/defaults and the `.env.example` env surface. Documented in
  `docs/getting-started/configuration.md`.

A change to any of these surfaces needs a matching doc update **in the same PR**. If
the diff changed surface without one, that is a gap to flag.

---

## 2. Procedure (follow in order — later steps depend on earlier ones)

### Step 1 — Establish the epoch and read the diff
```
git log --oneline $EPOCH..$HEAD
git diff --stat $EPOCH..$HEAD -- airlock/ tests/ config.yaml .env.example pyproject.toml requirements.txt
git diff --name-status $EPOCH..$HEAD -- airlock/
```
Note new/removed modules, datastore schema changes (`grep -n 'CREATE TABLE' airlock/datastore.py`),
new/changed CLI commands or flags, new/changed `config.yaml` keys or defaults, new
HTTP routes, and any new dependency (`pyproject.toml` / `requirements.txt`).

### Step 2 — Classify the completed work
For each change decide which docs it touches:

| Change observed in `$EPOCH..$HEAD` | Docs to update |
|------------------------------------|----------------|
| **New `airlock/` module / subsystem** | `dev/architecture.md`; the owning `dev/design-*.md`/`feature-*.md` memo |
| **New / changed HTTP route or response shape** | `dev/architecture.md`; `docs/architecture/overview.md`; the owning design memo |
| **New / renamed / removed CLI command or flag** | `docs/guide/cli.md`; the owning feature doc; confirm against `uv run airlock --help` |
| **New / changed `config.yaml` key, default, or env var** | `config.yaml` inline comments; `docs/getting-started/configuration.md`; `.env.example`; every doc quoting that value (grep it) |
| **Changed guardrail / routing / advisor / batch behaviour** | the owning `dev/feature-*.md`/`design-*.md`; `docs/guide/{guardrails,routing,advisor,batch,vertex-batch}.md` |
| **Datastore schema / `CREATE TABLE` change** | `dev/design-fathom-storage-model.md`; `dev/plan-fathom-storage-implementation.md`; `docs/guide/fathom-storage.md` |
| **New dependency** (`pyproject.toml`, `requirements.txt`) | `dev/architecture.md`; if it touches the spaCy/Presidio PII model, the `Makefile` note + `docs/getting-started/installation.md` |
| **TUI change** | `dev/tui-design.md` / `dev/tui-flow-screen.md`; `docs/guide/tui.md` |
| **New / changed tests** | the owning `*-test-plan*.md`; `dev/requirements.md` "Traces to" prose if coverage shifted |
| **Behaviour-compat event** (documented behaviour change) | `CHANGELOG.md` + `docs/changelog.md`; narrative line in `PROGRESS.md` |
| **Completed work implying a new requirement / user need** | **DO NOT mint an ID** — log the gap on the status board + owning design doc; flag in summary |
| **Superseded design note / research** | mark it `SUPERSEDED` with a banner pointing to the successor (Airlock has no `dev/archive/`; banner in place or relocate to `dev/notes/`) |
| **Orchestration-harness change** (`.claude/agents/*`, pack flow) | `dev/agent-harness-reference.md` / `runbook.md`; `dev/plans/README.md` |

If `$SCOPE` is set, restrict to docs matching that subsystem.

### Step 3 — Update developer docs FIRST (dependency root)
Order within `dev/` matters because user docs derive from it:
1. **`user-needs.md` / `requirements.md`** — read-only for IDs. Adjust only *prose*
   describing already-shipped, already-IDed behaviour; re-point "Traces to" lines to
   existing UN IDs. New need? → log it, don't ID it.
2. **`architecture.md`** — module map, request lifecycle, HTTP surface, dependency
   list, datastore schema. Re-verify against `$HEAD`.
3. **`design-*.md` / `feature-*.md`** — the owning per-feature memos. Re-verify every
   `file:line`, symbol, config key, default, and `CREATE TABLE` step.
4. **`plans/`** — record gaps/bugs on `runs/STATUS-<release>.md`; advance the pack
   scoreboard in `<release>-plan.md` only for behaviour that actually shipped (state
   is derived from on-disk witnesses, not from prose — see `dev/plans/README.md`).
5. **`*-test-plan*.md`, `agent-harness-*.md`** — only if strategy/tiers changed or you
   logged a gap.

### Step 4 — Update user docs (derived from `dev/`)
After `dev/` is correct:
- `docs/guide/cli.md` ⟵ confirm against `uv run airlock --help` (and `airlock-analyze --help`).
- `docs/getting-started/configuration.md` ⟵ `config.yaml` keys/defaults + `.env.example`.
- `docs/guide/{guardrails,routing,advisor,batch,vertex-batch,mcp-servers,fathom-storage,tui}.md`
  ⟵ the owning feature/design memos (keep examples runnable).
- `docs/architecture/{overview,diagram}.md` ⟵ `dev/architecture.md`.
- `docs/getting-started/{installation,connecting-tools}.md`, `docs/operations.md`,
  `docs/troubleshooting.md` as needed.
- `docs/changelog.md` + `CHANGELOG.md` for behaviour-compat events.
- Root `README.md` stays a slim overview + doc map; push detail into `docs/`.

### Step 5 — Update cross-cutting indexes & nav (depends on Steps 3–4)
- **`mkdocs.yml` `nav:`** — every new `docs/` page is in nav; no orphaned pages.
  `mkdocs build --strict` fails on orphans/broken links, so this is enforceable.
- **`dev/plans/README.md`** — list only plan/prompt/run files that exist; keep the
  layout table true.
- **`docs/index.md`, `README.md`** — their doc maps reference only files that exist.
- **`scripts/check-version-consistency.py`** — if any version string moved, the
  version is consistent across `pyproject.toml`, `config.yaml`, docs, etc.

### Step 6 — Verify (gate before declaring done)
- **Factual:** every `file:line`, symbol, flag, default, config key, schema/`CREATE
  TABLE`, and citation matches `$HEAD` source; dependency claims match
  `pyproject.toml`/`requirements.txt`.
- **CLI parity:** `uv run airlock --help` commands/flags == `docs/guide/cli.md`.
- **Config parity:** `config.yaml` keys/defaults == `docs/getting-started/configuration.md` == `.env.example`.
- **Health-route correctness:** no doc recommends `GET /health` for liveness/probes —
  it must be `GET /health/liveliness`.
- **Links & nav:** `mkdocs build --strict --clean` passes (no orphans, no broken links).
- **No code touched:** `git status --porcelain airlock/ tests/` is empty (aside from
  pre-existing changes); no test file modified.
- **Build/lint green:** `mkdocs build --strict --clean`; `uv run ruff format --check`
  on any touched code blocks is N/A (docs only), but run
  `uv run python scripts/check-version-consistency.py`. A full
  `./scripts/preflight.sh --fast` is a good final sanity check that nothing leaked
  into source.

### Step 7 — Stamp the new epoch
Update the **DOCS EPOCH MARKER** at the top of this file to `$HEAD`'s short SHA and
today's date, with a one-line note on what the run reconciled. Commit the docs as a
single `docs(...)` commit (per the repo's conventional-commit + one-docs-commit-per-
transition style). Summarize what changed and list every gap/bug/surface-without-doc
you flagged (with where you logged it).

---

## 3. Conventions
- **Cite code as `` `airlock/relative/path:line` ``** (clickable). Names, flags,
  config keys, and defaults must be exact — re-read the source, do not recall.
- **Stale > missing:** if you cannot make a doc correct, delete it or banner it rather
  than leave it wrong.
- **Prefer editing** an existing doc over creating a parallel one; fold superseded
  material into a `SUPERSEDED` banner (or `dev/notes/`) instead of leaving stale
  duplicates — Airlock has no `dev/archive/`.
- **Design notes are the decision record** (Airlock has no `dev/adr/`). Do not
  contradict an accepted `design-*.md`/`feature-*.md`; supersede it with a banner
  pointing at its successor.
- **Public surface is contract:** the HTTP API, CLI flags, and `config.yaml` keys
  change only with a matching doc update in the same PR — if the diff broke this,
  flag it, don't paper over it.
- **No invented requirement/UN IDs** — log gaps, never mint.
- **`AGENTS.md` is the wake bootstrap, not the rulebook:** durable agent state lives
  in `.wake/` (`projection.md`, `constraints.md`, `decisions.md`); live pack state in
  `dev/plans/runs/STATUS-<release>.md`; the narrative ledger of what landed is
  `PROGRESS.md`. Do not duplicate live pack state into `PROGRESS.md`.

## 4. Execution model (optional, for large diffs)
A small diff is a single pass. For a large diff, fan out: one context-bounded subagent
per affected doc (or per subsystem) — give each only its target source module(s), the
relevant `dev/design-*.md`/`feature-*.md` memo, the matching surface (CLI `--help`,
`config.yaml`, or HTTP route), and the doc it owns — then run a **verify** agent
(facts / `file:line` / CLI+config parity / links) and a **review** agent (completeness
vs the Step-2 classification table) and dispatch targeted fix agents for any blocking
findings, before Steps 5–7. Keep `dev/` ahead of `docs/` in the sequence regardless of
parallelism, and let exactly one writer own `mkdocs.yml` and `dev/plans/README.md` to
avoid races.
