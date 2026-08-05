# Release archive index

Completed delivery evidence remains version-controlled so a shipped behavior
can be traced to its plan, implementation record, and independent review. It
is historical context, not an active roadmap.

## Active delivery

- [0.5.10 catch-up and clean-up](../0.5.10-plan.md) — active; its status board is
  [`../runs/STATUS-0.5.10.md`](../runs/STATUS-0.5.10.md).

## Completed release trains

| Release | Plan | Status/evidence |
| --- | --- | --- |
| 0.4.0 | [`../0.4.0-plan.md`](../0.4.0-plan.md) | [`../runs/STATUS-0.4.0.md`](../runs/STATUS-0.4.0.md) |
| 0.5.0 | [`../0.5.0-plan.md`](../0.5.0-plan.md) | [`../runs/STATUS-0.5.0.md`](../runs/STATUS-0.5.0.md) |
| 0.5.1 | [`../0.5.1-plan.md`](../0.5.1-plan.md) | [`../runs/STATUS-0.5.1.md`](../runs/STATUS-0.5.1.md) |
| 0.5.2 | [`../0.5.2-plan.md`](../0.5.2-plan.md) | [`../runs/STATUS-0.5.2.md`](../runs/STATUS-0.5.2.md) |
| 0.5.3 | [`../0.5.3-plan.md`](../0.5.3-plan.md) | [`../runs/STATUS-0.5.3.md`](../runs/STATUS-0.5.3.md) |
| 0.5.4 | [`../0.5.4-plan.md`](../0.5.4-plan.md) | [`../runs/STATUS-0.5.4.md`](../runs/STATUS-0.5.4.md) |
| 0.5.5 | [`../0.5.5-plan.md`](../0.5.5-plan.md) | [`../runs/STATUS-0.5.5.md`](../runs/STATUS-0.5.5.md) |
| 0.5.7 | [`../0.5.7-plan.md`](../0.5.7-plan.md) | [`../runs/STATUS-0.5.7.md`](../runs/STATUS-0.5.7.md) |
| 0.5.8 | [`../0.5.8-plan.md`](../0.5.8-plan.md) | [`../runs/STATUS-0.5.8.md`](../runs/STATUS-0.5.8.md) |
| 0.5.9 | [`../0.5.9-plan.md`](../0.5.9-plan.md) | [`../runs/STATUS-0.5.9.md`](../runs/STATUS-0.5.9.md) |

0.5.9 was an **internal** milestone: it closed out at
`milestone/0.5.9-internal-closeout` (`b2752d7`) with no version bump, so
everything it delivered — including the breaking `GET /health` change — first
reaches the public with the next published release. Its steward handoff in
`../prompts/0.5.9-MASTER-HANDOFF.md` carries a superseded banner; the status
board is authoritative on any conflict.

`0.5.6` has a plan but no status board and no `v0.5.6` tag; it is deliberately
absent from the table above rather than linked to evidence that does not exist.

`prompts/` holds the implementer prompts used by these completed trains and
`runs/` holds their immutable outputs, reviews, and measurement evidence. Keep
those paths stable: they are referenced by design notes and audit records.

When a future train closes, add it to this table, retain its evidence in Git,
and change its status board from live to archived. Do not delete evidence just
to reduce directory size.
