# Slice 120 — release documentation contract status

**Status:** complete.

## Review and ratified change

The active engineering index is 0.5.14, while the prior contract test still
asserted the historical 0.5.10 plan path. That assertion no longer represented
the active release and would either fail every documentation run or encourage a
future maintainer to restore stale guidance.

The contract now requires `dev/README.md` to link to
`plans/0.5.14-todo.md`. The page already does so under “Next engineering
backlog”; the test makes this active-release linkage durable. Published 0.5.12
material remains correctly labelled as the latest published release.

## Verification

The documentation-contract test runs in the Slice 50 focused no-credit suite.
It verifies all MkDocs navigation and local links, entry-point links, the active
plan reference, the benchmark-safe profile, and the OpenRouter configuration
contract. Strict MkDocs build remains a release-closeout check.
