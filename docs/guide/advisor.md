# Advisor

The advisor is an LLM-powered assistant that helps administrators diagnose issues, understand trends, and get configuration recommendations by querying Airlock's operational data.

## Usage

### CLI

```bash
# One-shot question
airlock advise "why does claude-sonnet have a high error rate?"

# Interactive session
airlock advise --interactive

# Force local model only (no data sent externally)
airlock advise --local-only "what should I tune?"

# Target a specific proxy
airlock advise --host myproxy --port 8080 "check system health"

# Use a specific model
airlock advise --model local-llama "summarize recent errors"
```

### TUI

Press `6` in the TUI to open the Advisor screen. Select a model from the dropdown (local models are tagged), type a question, and press Enter or click Ask.

## How it works

The advisor runs a bounded agent loop (max 5 iterations):

1. **Model selection** -- picks a local model if available, falls back to remote with a warning
2. **System prompt** -- briefs the LLM on Airlock concepts (circuit breaker, guardrails, smart router, etc.)
3. **Tool calling** -- the LLM requests data from 9 tools that query operational state
4. **Answer** -- the LLM synthesizes the data into a concise answer
5. **Actions** -- if the LLM identifies a fix, it proposes a config change with a diff preview

## Data tools

The advisor has access to these tools:

| Tool | Data source | Returns |
|------|-------------|---------|
| `get_state_snapshot` | StateStore (in-memory) | Client states, model circuits, provider health, spend |
| `get_recent_errors` | JSONL logs | Errors grouped by model, client, and error type |
| `get_analysis_report` | Slow analyzer | Full analysis: optimizations, trends, hypotheses |
| `get_circuit_health` | StateStore | Circuit breaker state for all models |
| `get_config` | config.yaml | Current config with API keys redacted |
| `get_guard_signals` | JSONL logs | Guardrail observations, filterable by guardrail or client |
| `get_client_profile` | StateStore + logs | Deep dive on one client |
| `get_model_profile` | StateStore + logs | Deep dive on one model |
| `get_knobs` | airlock-knobs.json | Current guardrail tuning weights and thresholds |

## Config proposals

When the advisor identifies an actionable fix, it proposes a config change:

- Changes are classified by risk: **low** (adding models), **medium** (threshold changes), **high** (removing models, disabling guards)
- A unified diff preview is shown before applying
- High-risk changes require typing `CONFIRM`
- A `.bak` backup is created before writing
- YAML is validated before applying

## Privacy

The advisor prefers local models (any model with a custom `api_base` in config) to avoid sending operational data to remote providers.

When a remote model is used:

- **CLI**: a warning is printed to stderr
- **TUI**: a warning banner appears above the response

Use `--local-only` on the CLI to hard-fail if no local model is available.

Set `AIRLOCK_ADVISOR_MODEL` to override model selection globally.

## Audit trail

All advisor actions are logged to `logs/advisor-audit.jsonl`:

```json
{
  "timestamp": "2026-04-10T12:34:56+00:00",
  "action_type": "query",
  "description": "why does claude-sonnet have a high error rate?",
  "outcome": "success",
  "model_used": "local-llama",
  "details": {
    "iterations": 3,
    "tool_calls": ["get_model_profile", "get_recent_errors"],
    "actions_proposed": 1,
    "is_local": true
  }
}
```
