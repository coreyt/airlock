# MCP stdio config constraints (LiteLLM) — agent reference

Authoritative notes on the LiteLLM-imposed rules that govern `mcp_servers` stdio
entries and the `config.local.yaml` include mechanism. These constraints surface
**at lazy tool-discovery time** (first client request), not at proxy startup — so a
clean startup log does not prove a server works. Discovered the hard way during a
2026-06-28 restart that silently dropped two working MCP servers.

## 1. stdio command allowlist

LiteLLM only launches stdio MCP servers whose command **basename** is allow-listed.

- Definition: `litellm/constants.py` →
  `MCP_STDIO_ALLOWED_COMMANDS = frozenset({deno,docker,node,npx,python,python3,uvx}) | (LITELLM_MCP_STDIO_EXTRA_COMMANDS split on ",")`.
- Enforcement: `litellm/proxy/_experimental/mcp_server/mcp_server_manager.py` (~L1910)
  and the Pydantic validator in `litellm/proxy/_types.py` (~L1360/L1433). The check is
  `os.path.basename(server.command).lower()` (with `.exe/.cmd/.bat/.com` stripped).
- Failure mode: `403: MCP stdio command '<cmd>' is not in the allowlist (...). Add it
  to LITELLM_MCP_STDIO_EXTRA_COMMANDS to allow this command.`

Implications:
- A venv interpreter passes because its basename is `python`/`python3`
  (`/home/u/proj/.venv/bin/python` → `python`). **Prefer this** for script servers.
- `uv` is **not** allow-listed; `uvx` is. Use `uvx`, or a venv `python`.
- A custom launcher binary (e.g. `ado-mcp`) must be added to
  `LITELLM_MCP_STDIO_EXTRA_COMMANDS=ado-mcp` in `.env` (basename, comma-separated).

## 2. No variable expansion in command/args

LiteLLM does **not** expand `${HOME}`, `~`, or `$VAR` in MCP `command`/`args`. They
are passed literally — e.g. `${HOME}/x/server.py` makes the launcher try to open a
path containing a literal `${HOME}` segment (resolved relative to cwd, the project
root), giving `FileNotFoundError`.

- Only the `env:` block **values** are resolved, via LiteLLM's `os.environ/VAR` syntax.
- Fix: use **literal absolute paths**. Because those are machine-specific, such
  servers belong in the gitignored `config.local.yaml`, not the tracked `config.yaml`.

## 3. `include:` / config.local.yaml merge semantics

`litellm/proxy/proxy_server.py::_process_includes` (~L3492):

```python
for key, value in included_config.items():
    if isinstance(value, list) and key in config:
        config[key].extend(value)   # lists EXTEND
    else:
        config[key] = value          # dicts/scalars REPLACE
```

- `mcp_servers` is a dict → the included file's value **replaces** the main config's.
  `config.local.yaml` must therefore list **every** runtime MCP server, including
  bundled ones (`newscatcher`) — anything omitted is dropped.
- `general_settings` is also a dict but is **not** defined in `config.local.yaml`, so
  `config.yaml`'s (master_key, etc.) survives. Only keys present in the included file
  replace.
- The included file **must exist** or startup fails (`FileNotFoundError`). Hence the
  `include: ["config.local.yaml"]` line is **local-only / never committed** — a fresh
  checkout has no `config.local.yaml` (gitignored) and would fail to start.

## 4. Airlock interaction (`proxy.py`)

- `airlock/proxy.py::_prepare_runtime_config` loads `config.yaml`, injects per-model
  `model_info` (capability records), applies env-driven overrides, and writes a temp
  `airlock-runtime-*.yaml` (same dir as config.yaml) that LiteLLM then loads. It
  **does not** process `include:` itself or merge `config.local.yaml` — it preserves
  the `include:` key, and LiteLLM resolves it relative to the temp file's dir (project
  root), so `config.local.yaml` at the root resolves correctly.
- The temp runtime file is normally deleted on shutdown; a hard kill can orphan one.
  `airlock-runtime-*.yaml` is gitignored.

## 5. Known fragility / proposed improvement

The tracked `config.yaml` cannot carry the `include:` line, so each machine needing
local MCP servers must re-add it locally, and it re-surfaces as a working-tree change
after pulls. A cleaner design: have `_prepare_runtime_config` auto-merge a
`config.local.yaml` sibling when present (replicating the list-extend / dict-replace
semantics, or an explicit deep-merge), removing the need to edit the tracked file.
Not yet implemented — would need parity tests against LiteLLM's `_process_includes`.

## Verify after any MCP change/restart

```bash
curl -s -H "Authorization: Bearer $AIRLOCK_MASTER_KEY" \
  http://localhost:4000/mcp-rest/tools/list | python3 -m json.tool   # NOT /mcp (SSE)
grep -iE "allowlist|no such file|connection closed|modulenotfound" service-stderr.log | tail
```

See also: user guide `docs/guide/mcp-servers.md`, design `dev/architecture.md` §4.
