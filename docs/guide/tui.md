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

The operator's home screen. Shows proxy status, provider/model health, active clients, and alerts at a glance. Auto-refreshes every 5 seconds.

## Guards (Screen 2)

Displays PII redaction statistics, keyword blocking counts, and guardrail signal details. Useful for monitoring guardrail activity and tuning thresholds.

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
