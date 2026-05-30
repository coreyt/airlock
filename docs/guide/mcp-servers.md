# MCP Servers

Airlock can proxy MCP (Model Context Protocol) tool servers alongside LLM providers. All MCP tool calls flow through the same guardrail pipeline as LLM requests.

## Configuration

Add entries to `mcp_servers` in `config.yaml`:

```yaml
mcp_servers:
  ado_mcp:
    command: uv
    args: ["run", "python", "-m", "ado_mcp.mcp.server"]
    env:
      ADO_ORG_URL: os.environ/ADO_ORG_URL
      ADO_PAT: os.environ/ADO_PAT
```

## Command resolution patterns

**Module via `python -m`** -- cwd-independent, requires package installed in the proxy's venv:

```yaml
my_server:
  command: uv
  args: ["run", "python", "-m", "my_package.server"]
```

**Installed script via `uv run`** -- cwd-independent, resolves from PATH/venv:

```yaml
armada:
  command: uv
  args: ["run", "armada-mcp"]
```

**Script file** -- must use an absolute path:

```yaml
my_server:
  command: python3
  args: ["/home/user/projects/my-server/server.py"]
```

**Other runtimes** (Node.js, npx, Bun, Poetry) are also supported.

## Environment variables

Use `os.environ/VAR_NAME` to pass environment variables from Airlock's `.env` to the MCP server. Airlock validates these references at startup and gives clear error messages for missing values.

## Guardrail coverage

All MCP tool calls flow through PII redaction, keyword blocking, and threat detection automatically. The MCP Tool Guard adds tool-specific allowlist/blocklist and argument sanitization. No extra configuration needed.

## Bundled servers

### NewsCatcher

Airlock ships a NewsCatcher CatchAll MCP server
(`airlock.mcp_servers.newscatcher_server`, stdio transport) for news
search. It exposes two tools:

- `newscatcher_search` — submit a query, poll for results (up to ~3 min), return records
- `newscatcher_search_quick` — same, with a shorter 60-second timeout

```yaml
mcp_servers:
  newscatcher:
    command: uv
    args: ["run", "python", "-m", "airlock.mcp_servers.newscatcher_server"]
    env:
      NEWS_CATCHER_API_KEY: os.environ/NEWS_CATCHER_API_KEY
```

Install the extra with `pip install airlock-llm[search]` and set
`NEWS_CATCHER_API_KEY` in `.env`. For general web search exposed as a
chat model, see the Tavily provider in
[Configuration](../getting-started/configuration.md#search-providers).

## Management

Use the TUI Config screen (key `4`) to view MCP server status and start/stop/restart managed servers.
