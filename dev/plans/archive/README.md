# Release archive index

Completed delivery evidence remains version-controlled so a shipped behavior
can be traced to its plan, implementation record, and independent review. It
is historical context, not an active roadmap.

## Active delivery

- [0.5.9 internal milestone](../0.5.9-plan.md) — active; its status board is
  [`../runs/STATUS-0.5.9.md`](../runs/STATUS-0.5.9.md).

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

`prompts/` holds the implementer prompts used by these completed trains and
`runs/` holds their immutable outputs, reviews, and measurement evidence. Keep
those paths stable: they are referenced by design notes and audit records.

When a future train closes, add it to this table, retain its evidence in Git,
and change its status board from live to archived. Do not delete evidence just
to reduce directory size.
