# Slice 70 — TUI lifecycle design review

**Draft review outcome:** approve DFR-30/DAC-30. Ordinary TUI tests may opt into
an explicit constructor-only `test_harness` mode. It composes the production
widget tree but suppresses mount-time overview, config, logs, guards, MCP, JSONL
tailer, and alert-loop work. It is not environment-derived and production keeps
the existing lifecycle by default.

## TDD and allocation

RED tests prove harness composition has every production pane and starts no
mount worker, while the default app still starts MCP/tailer lifecycle. GREEN
threads the explicit flag through the app and each mount seam. Existing worker,
cancellation, shutdown, and stale-callback tests deliberately continue using
the default app mode.
