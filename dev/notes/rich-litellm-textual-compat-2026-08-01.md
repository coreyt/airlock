# Rich, LiteLLM, and Textual compatibility boundary — 2026-08-01

## Finding

Airlock cannot upgrade Textual beyond **6.2.1** while it uses the current
LiteLLM proxy distribution.  This is a resolver conflict, not an Airlock TUI
API finding:

| Package | Relevant requirement | Consequence |
| --- | --- | --- |
| `litellm[proxy]` 1.94.1 | `rich>=13.9.4,<14.0` | Rich 14 is excluded. |
| `textual` 6.2.1 | `rich>=13.3.3` | Resolves with Rich 13.9.4. |
| `textual` 6.3.0 through 6.12.0 | `rich>=14.2.0` | Cannot resolve with LiteLLM 1.94.1's proxy extra. |

The project declares `litellm[proxy]>=1.94.1,<2` and
`textual>=6.2.1,<7`; the lock correctly chooses LiteLLM 1.94.1, Textual
6.2.1, and Rich 13.9.4.  The practical Textual ceiling is therefore
`<6.3`, not merely `<7` (and not `<6.11`, as an earlier Dependabot response
suggested).

## Evidence

- The installed/locked LiteLLM 1.94.1 metadata lists
  `rich>=13.9.4,<14.0` for its `proxy` extra.
- The [Textual 6.2.1 PyPI metadata](https://pypi.org/pypi/textual/6.2.1/json)
  lists `rich>=13.3.3`.
- The [Textual 6.3.0 PyPI metadata](https://pypi.org/pypi/textual/6.3.0/json)
  and every released 6.x version through
  [6.12.0](https://pypi.org/pypi/textual/6.12.0/json) list `rich>=14.2.0`.
- The [LiteLLM 1.94.1 PyPI metadata](https://pypi.org/pypi/litellm/1.94.1/json)
  confirms the proxy-extra cap.  As checked on 2026-08-01, it is also the
  current LiteLLM release on PyPI, so there is no later 1.x package metadata
  to validate as a Rich-14-compatible upgrade.

## Feasible paths

1. **Keep the validated proxy baseline (recommended now).** Retain LiteLLM
   1.94.1 and pin Textual to `>=6.2.1,<6.3` in both the base dependency and
   the `tui` extra.  Update the Dependabot ignore rule to match.  This is
   dependency hygiene only; it intentionally does not undertake a Textual
   API migration.
2. **Re-evaluate after a LiteLLM release changes the proxy Rich cap.** Before
   broadening Textual, create a clean lock with that LiteLLM candidate and
   run the full proxy plus TUI suite.  A compatible resolver is necessary but
   not sufficient: Textual's own API migration still needs its dedicated
   plan/tests.
3. **Architectural split (not recommended as a dependency workaround).** A
   separate, standalone TUI client environment could use Rich 14, but the
   in-process Airlock TUI imports the same installed distribution as the
   LiteLLM proxy.  Splitting it would be product/packaging work with an IPC or
   client API boundary, not a routine package update.

Do not remove LiteLLM's `proxy` extra or force/patch Rich 14: the former
removes Airlock's proxy runtime requirements and the latter leaves the
declared LiteLLM requirement unsatisfied.

## Recommendation

Correct the dependency contract to `textual>=6.2.1,<6.3` and make the same
bound visible to Dependabot.  Defer the newer-Textual migration until a
LiteLLM proxy release supports Rich 14 and a dedicated TUI compatibility plan
can validate the Airlock subclasses and interactive flows.
