# Airlock engineering documentation

`dev/` is the engineering record for Airlock. It explains why the product is
shaped as it is and how current work is verified. It is not public operator
documentation; start in [`../docs/`](../docs/index.md) for installation,
configuration, and operations.

## Start here

| Need | Canonical location |
| --- | --- |
| Product intent and acceptance criteria | [User needs](user-needs.md) |
| Testable product requirements | [Requirements](requirements.md) |
| Runtime boundaries and design rationale | [Architecture](architecture.md) |
| Approved semantic input-injection design | [Prompt-injection classifier design](notes/design-prompt-injection-classifier.md) |
| Accepted refactoring direction | [Refactoring analysis](notes/refactoring-analysis-2026-07-31.md) |
| TUI test-maintenance methods and measured 0.5.15 outcome | [TUI test methods](notes/tui-test-methods-0.5.15.md) |
| Latest published release | [0.5.12 plan](plans/0.5.12-plan.md), [PII egress canary](plans/runs/0.5.12-pii-egress-canary-2026-08-11.md), and [CHANGELOG](../CHANGELOG.md) |
| Next engineering backlog | [0.5.14 TODO](plans/0.5.14-todo.md) |
| OOM investigation instrumentation | [High-water runbook](debugging/instrumentation/oom-high-water.md) |
| How to operate the delivery harness | [Plans guide](plans/README.md) and [harness runbook](agent-harness-runbook.md) |
| Shipped behavior | [Public docs](../docs/index.md), [CHANGELOG](../CHANGELOG.md), and source/tests |

## Document lifecycle

Every engineering record is one of the following:

- **Active** — defines work that is currently being implemented or verified.
- **Accepted** — explains a shipped design decision; retain it as rationale.
- **Superseded** — retained for history, but replaced by a linked newer record.
- **Archived** — completed release plans, prompts, run evidence, and reviews.

Release work is indexed in [`plans/`](plans/README.md). The current release
board is the only live status source. Completed-release artifacts remain in
Git for auditability and are catalogued from the [release archive
index](plans/archive/README.md); do not treat their status language as current
commitment.

## Source-of-truth rules

- `airlock/` and `tests/` define implemented behavior and regression coverage.
- The shipped `config.yaml` template and `.env.example` define supported setup
  defaults; public docs explain how to use them.
- The running proxy's `/openapi.json` and `/airlock/docs` define the live HTTP
  surface. The static [API reference](../docs/reference/api.md) explains how
  to use that surface.
- `docs/` is canonical for users and operators. `dev/` may link to it but must
  not duplicate it.

## Maintaining the record

When a change affects users, operators, configuration, a response contract, or
deployment, update the matching public page and its test in the same pull
request. When it changes an accepted technical decision, link the design note
to the implementation and targeted tests. At release closeout, mark the plan
and its status board archived in the release archive index.
