# TUI Dashboard

The terminal dashboard provides real-time views of traffic, guardrail decisions, model status, and operational diagnostics.

```bash
airlock tui --start    # start proxy + dashboard
airlock tui            # dashboard only (connect to running proxy)
```

## Screens

| Key | Screen | Purpose |
|-----|--------|---------|
| `1` | Overview | Proxy health, guardrail status, model/client/provider overview |
| `2` | Guards | PII redaction stats, keyword blocking, guardrail signal details |
| `3` | Logs | JSONL log browsing with model/user/status filters |
| `4` | Config | Configuration viewer, MCP server management |
| `5` | Test | Interactive LLM connectivity testing (Basic Chat) |
| `6` | Advisor | LLM-powered operational diagnostics and config recommendations |

Press the number key to switch screens, or `q` to quit.

## Overview (Screen 1)

The operator's home screen. Shows proxy status, provider/model health, active clients, and alerts at a glance. Auto-refreshes every 5 seconds. Per-provider rate-limit headroom and spend-vs-cap are shown here too (see [Provider Quota Observability](provider-observability.md)).

### Clear a quarantine (`c`)

Highlight a provider row and press **`c`** to clear its quarantine. The TUI calls
the loopback [Admin API](admin-api.md) (`clear-quarantine`, half-open probe mode),
which logs an `admin_action` record; the tailer ingests it and the countdown you
were watching clears. No credential is needed — being on the host is the
authorization (Path A). This requires `admin.enabled: true` in `config.yaml`.

## Guards (Screen 2)

Displays PII redaction statistics, keyword blocking counts, and guardrail signal details. Useful for monitoring guardrail activity and tuning thresholds.

Detail tabs: **Signals**, **Pipeline**, **Mutations**, **Raw**, **Tool Result**, and **Semantic**.

### Semantic tab

Aggregates semantic prompt-injection classifier verdicts over the last 7 days, refreshing every 30 seconds. It is the operator surface for the same data as [`airlock semantic-report`](cli.md) and reuses that aggregation rather than recomputing it.

Three things it shows deliberately:

- **Status and action, on separate rows.** `status` is the classifier's verdict; `action` is what Airlock actually did. Outside `enforce` mode they differ, so a detection is *not* a blocked request. When they diverge, the panel says so in words rather than leaving you to compare two numbers.
- **Per-classifier detected / clean / unavailable counts.** Unavailable is its own column and is never folded into `clean` — a classifier that could not answer has not cleared anything.
- **Unavailable reasons, with `rate_limit` called out in red.** Rate limiting is attacker-inducible and fails open, so a classifier being rate-limited into silence looks identical to quiet, clean traffic unless it is flagged.

An empty window prints an explicit note that no verdicts is not the same as no threats: if the classifier registry is empty, or every classifier is unavailable, there is nothing to report and the panel would otherwise look reassuring.

The tab reads JSONL through the bounded reader (`airlock/log_query.py`) on a background thread. Like the rest of the TUI it runs in a separate process and never subscribes to the in-process request event bus, so it cannot affect request latency. If the window is truncated by the reader's bound, the panel says `partial window` rather than presenting a partial scan as complete.

## Logs (Screen 3)

Live JSONL log viewer with filtering by model, client, and status. Shows the most recent requests with error highlighting.

## Config (Screen 4)

Displays the current `config.yaml` contents and MCP server status. Provides controls to start/stop/restart managed MCP servers.

## Test (Screen 5)

Interactive Basic Chat for testing any configured model. Select a provider and model from the dropdowns, compose a prompt, and send. The screen displays:

- **Q2** (top-left): User query text
- **Q1** (top-right): Extracted response content with token usage
- **Q3** (bottom-left): Full outgoing request (URL, headers, JSON body)
- **Q4** (bottom-right): Full incoming response (HTTP status, headers, JSON body)

Use the Parameter Builder button to configure `temperature`, `max_tokens`, `top_p`, `top_k`, `stop` sequences, and `system` prompt.

## Advisor (Screen 6)

Ask natural-language questions about Airlock's operational state. The advisor uses an LLM to query data and provide answers. See the [Advisor guide](advisor.md) for details.
