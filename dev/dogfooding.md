# Dogfooding Airlock with Claude Code

Route your own Claude Code sessions through Airlock so every AI interaction
passes through the same guardrails, logging, and visibility you provide to your
team.

## Prerequisites

- Airlock installed in your development virtualenv (`pip install -e .`)
- API keys configured in `.env` (at minimum `ANTHROPIC_API_KEY`)
- spaCy model downloaded (`python -m spacy download en_core_web_lg`) if using
  PII redaction

## Setup

```bash
# 1. Start the proxy (background it or use a separate terminal)
airlock start &

# 2. Install Claude Code hooks into the current project
airlock hooks install

# 3. Route Claude Code through the proxy
eval $(airlock dogfood)
```

After step 3, `ANTHROPIC_BASE_URL` points at your local proxy. Every Claude
Code request now flows through Airlock.

## What flows through the proxy

Once dogfooding is active, all Claude Code traffic passes through Airlock's
full pipeline:

| Layer | What it does |
|-------|-------------|
| **PII redaction** | Presidio scrubs credit cards, SSNs, emails, etc. before they reach Anthropic |
| **Keyword blocking** | Restricted terms (set via `AIRLOCK_BLOCKED_KEYWORDS`) are rejected |
| **JSONL logging** | Every request/response is logged to `$AIRLOCK_LOG_DIR` |
| **TUI visibility** | `airlock tui` shows live requests, state, and logs |
| **Client-side hooks** | SessionStart, prompt submit, tool use, and audit hooks run locally |

## Auth passthrough

Airlock uses LiteLLM's `os.environ/` syntax in `config.yaml` to read API keys
from the proxy's environment at runtime:

```yaml
model_list:
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY
```

This means your existing `ANTHROPIC_API_KEY` is used by the proxy to
authenticate with Anthropic. Claude Code itself authenticates to the proxy
using `ANTHROPIC_AUTH_TOKEN` (set by `airlock dogfood` when a master key is
configured).

No key duplication or separate credentials are needed — the same `.env` file
powers both the proxy and the upstream calls.

## Crash resilience

If the Airlock proxy goes down while `ANTHROPIC_BASE_URL` is set, Claude Code
API calls will **fail** — they won't silently fall back to direct provider
access.

### Automatic detection

The **SessionStart** hook probes the proxy on every Claude Code launch. If the
proxy is unreachable, it injects a warning with recovery steps:

> Airlock proxy is NOT reachable at localhost:4000. API calls routed through
> the proxy will FAIL. To recover: run `airlock start` to restart the proxy,
> or run `unset ANTHROPIC_BASE_URL` to bypass it.

### Manual recovery

If you're mid-session and the proxy crashes:

```bash
# Option A: restart the proxy
airlock start

# Option B: bypass the proxy for this session
unset ANTHROPIC_BASE_URL
```

## Troubleshooting

**Port already in use**
```
Error: address already in use (0.0.0.0:4000)
```
Another process is using port 4000. Either stop it or set a different port:
```bash
AIRLOCK_PORT=4001 airlock start
```

**Missing API key**
```
AuthenticationError: No API key provided
```
Ensure `ANTHROPIC_API_KEY` is set in `.env` (or exported) before starting the
proxy.

**Hooks not firing**
Run `airlock hooks status` to verify hooks are installed. If the output shows
no hooks, re-run `airlock hooks install`. Hooks must be installed in the
project directory where you launch Claude Code.

**`airlock dogfood` has no effect**
The command prints export statements — you must `eval` them:
```bash
eval $(airlock dogfood)
```
Without `eval`, the environment variables are not set in your shell.

**Proxy started but requests fail**
Check that `config.yaml` exists and contains valid model entries. Run
`airlock status` to verify the proxy is healthy.
