# MCP Servers

Airlock can proxy MCP (Model Context Protocol) tool servers alongside LLM providers. All MCP tool calls flow through the same guardrail pipeline as LLM requests.

## Configuration

Add entries to `mcp_servers` in `config.yaml`. Each server names a `transport`
(usually `stdio`), a `command`, its `args`, and any `env` it needs:

```yaml
mcp_servers:
  newscatcher:
    transport: stdio
    command: python3
    args: ["-m", "airlock.mcp_servers.newscatcher_server"]
    env:
      NEWS_CATCHER_API_KEY: os.environ/NEWS_CATCHER_API_KEY
```

> **Two LiteLLM constraints govern every stdio server — read before adding one.**
> They are not Airlock rules; they come from the LiteLLM proxy that runs the
> servers, and they bite at tool-discovery time (often silently) rather than at
> startup. See [Command allowlist](#command-allowlist) and
> [No variable expansion](#no-variable-expansion-use-absolute-paths) below.

### Command allowlist

LiteLLM only launches stdio MCP servers whose command **basename** is on a fixed
allowlist:

```
deno  docker  node  npx  python  python3  uvx
```

The check is `os.path.basename(command)`, so `/opt/venvs/x/bin/python` passes
(basename `python`) but a custom launcher like `ado-mcp` or `my-tool` is rejected
with a `403 ... not in the allowlist` when a client first lists tools.

To allow extra commands, list their basenames (comma-separated) in
`LITELLM_MCP_STDIO_EXTRA_COMMANDS` in `.env`:

```bash
# .env — allow the `ado-mcp` and `my-tool` launchers
LITELLM_MCP_STDIO_EXTRA_COMMANDS=ado-mcp,my-tool
```

Prefer routing through an allow-listed launcher when you can — e.g. point
`command` at a venv's `python`/`python3` and run the server as a module or script
(below) instead of adding a bespoke binary to the allowlist.

### No variable expansion — use absolute paths

LiteLLM does **not** expand `${HOME}`, `~`, or any environment variable inside an
MCP `command` or `args` entry. `${HOME}/projects/x/server.py` is taken literally
and fails to resolve (the launcher tries to open a path with a literal `${HOME}`
segment). Always use **literal absolute paths**:

```yaml
# WRONG — ${HOME} is not expanded, the server fails to start
command: ${HOME}/projects/my-tool/.venv/bin/python

# RIGHT — literal absolute path
command: /home/alice/projects/my-tool/.venv/bin/python
```

Because absolute host paths are machine-specific, keep servers that use them in a
gitignored [`config.local.yaml`](#machine-specific-servers-configlocalyaml), not
in the tracked `config.yaml`.

## Command resolution patterns

**Module via `python3 -m`** — cwd-independent, requires the package installed in
the command's interpreter. `python3` is allow-listed:

```yaml
my_server:
  transport: stdio
  command: python3
  args: ["-m", "my_package.server"]
```

**Script with its own venv** — point `command` at the venv interpreter (basename
`python`, allow-listed) so the script's dependencies (e.g. the `mcp` SDK) resolve.
Use absolute paths for both the interpreter and the script:

```yaml
my_server:
  transport: stdio
  command: /home/alice/projects/my-server/.venv/bin/python
  args: ["/home/alice/projects/my-server/mcp-server/server.py"]
```

**Installed script via `uvx`** — `uvx` is allow-listed and resolves the tool in an
ephemeral environment (note: `uv` alone is **not** allow-listed — use `uvx`, or
`python3`/`python` from a venv):

```yaml
my_server:
  transport: stdio
  command: uvx
  args: ["my-mcp-tool"]
```

**Custom launcher binary** — allowed only after you add its basename to
`LITELLM_MCP_STDIO_EXTRA_COMMANDS` (see [Command allowlist](#command-allowlist)):

```yaml
ado_mcp:
  transport: stdio
  command: /home/alice/projects/ado-mcp/.venv/bin/ado-mcp   # basename `ado-mcp`
  args: ["--profile", "full"]
  env:
    ADO_ORG_URL: os.environ/ADO_ORG_URL
    ADO_PAT: os.environ/ADO_PAT
```

**Other runtimes** (`node`, `npx`, `deno`, `docker`) are allow-listed and supported.

## Environment variables

Use `os.environ/VAR_NAME` to pass environment variables from Airlock's `.env` to
the MCP server. Unlike `command`/`args`, these `env` **values** are resolved by
Airlock from the process environment. Airlock validates the references at startup
and **exits** with a clear error if any referenced variable is unset (MCP env
errors are fatal; see [Operations → Startup Validation](../operations.md)).

## Machine-specific servers: `config.local.yaml`

MCP servers with absolute host paths (a venv interpreter or a custom binary under
someone's home directory) do **not** belong in the tracked `config.yaml`. Keep
them in a gitignored `config.local.yaml` and pull it in with LiteLLM's `include:`
(start from `config.local.yaml.example`):

```yaml
# config.yaml — LOCAL-ONLY line; see the caveat below before committing
include: ["config.local.yaml"]
```

```yaml
# config.local.yaml (gitignored, machine-specific)
mcp_servers:
  ado_mcp:
    transport: stdio
    command: /home/alice/projects/ado-mcp/.venv/bin/ado-mcp
    args: ["--profile", "full"]
    env:
      ADO_ORG_URL: os.environ/ADO_ORG_URL
      ADO_PAT: os.environ/ADO_PAT
  newscatcher:            # copied from config.yaml — see the replace caveat
    transport: stdio
    command: python3
    args: ["-m", "airlock.mcp_servers.newscatcher_server"]
    env:
      NEWS_CATCHER_API_KEY: os.environ/NEWS_CATCHER_API_KEY
```

Three things to get right, or servers silently disappear on the next restart:

1. **The included file `mcp_servers` *replaces* the main one — it does not merge.**
   LiteLLM's `_process_includes` extends *list* values but **overwrites** *dict*
   values like `mcp_servers`. So `config.local.yaml` must list **every** MCP
   server you want at runtime, including ones already in `config.yaml` (e.g.
   `newscatcher`) — anything you leave out is dropped.
2. **`include:` must NOT be committed in `config.yaml`.** `config.local.yaml` is
   gitignored, and a missing include file aborts startup (`FileNotFoundError`) on
   a fresh checkout. Keep the `include:` line as a local, uncommitted edit. (It
   also re-appears as a working-tree change after every `git pull` that touches
   `config.yaml` — re-add it if a pull reverts it.)
3. **Use absolute paths and mind the allowlist** in `config.local.yaml` exactly as
   in `config.yaml` — the constraints above apply equally to included files.

## Guardrail coverage

All MCP tool calls flow through PII redaction, keyword blocking, and threat detection automatically. The MCP Tool Guard adds tool-specific allowlist/blocklist and argument sanitization (`AIRLOCK_MCP_ALLOWED_TOOLS` / `AIRLOCK_MCP_BLOCKED_TOOLS`). No extra configuration needed.

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
    transport: stdio
    command: python3
    args: ["-m", "airlock.mcp_servers.newscatcher_server"]
    env:
      NEWS_CATCHER_API_KEY: os.environ/NEWS_CATCHER_API_KEY
```

Install the extra with `pip install airlock-llm[search]` and set
`NEWS_CATCHER_API_KEY` in `.env`. For general web search exposed as a
chat model, see the Tavily provider in
[Configuration](../getting-started/configuration.md#search-providers).

## Management

Use the TUI Config screen (key `4`) to view MCP server status and start/stop/restart managed servers.

## Verifying after a change or restart

MCP servers are launched lazily and most failures surface only when a client
first lists tools — so a clean startup log does **not** prove a server works.
After adding/editing a server or restarting the proxy, confirm tool discovery
directly (the REST helper is mounted at `/mcp-rest`; the bare `/mcp` path is the
streaming transport and only speaks SSE):

```bash
# Lists the aggregated tools across all healthy MCP servers (auth required)
curl -s -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  http://localhost:4000/mcp-rest/tools/list | python3 -m json.tool
```

If a server is misconfigured you will see it in the proxy stderr log as one of:

- `403 ... not in the allowlist` → add its basename to `LITELLM_MCP_STDIO_EXTRA_COMMANDS`.
- `No such file or directory` with a literal `${HOME}`/`~` in the path → replace with an absolute path.
- `Connection closed` / `ModuleNotFoundError` at init → the interpreter lacks the
  server's dependencies; point `command` at a venv that has them.
