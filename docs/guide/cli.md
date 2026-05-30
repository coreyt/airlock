# CLI Reference

Airlock provides a unified `airlock` command with subcommands.

## Commands

### `airlock config`

Export or import Airlock configurations (`config.yaml`, `.env`, `logs/airlock-knobs.json`) as a `.zip` archive.

```bash
airlock config export          # creates a zip archive in the current directory
airlock config export --dir ~  # creates a zip archive in the home directory

airlock config import backup.zip          # extracts into current directory
airlock config import backup.zip --dir ~  # extracts into home directory
```

Existing files will be safely backed up before being overwritten during an import.

### `airlock init`

Generate `config.yaml`, `.env`, and `logs/` in the current directory.

```bash
airlock init              # generate in current directory
airlock init --dir /opt   # generate in specified directory
airlock init --force      # overwrite existing files
```

### `airlock start`

Launch the proxy (headless, no TUI).

```bash
airlock start
airlock start --host 0.0.0.0 --port 8080
airlock start --config /etc/airlock/config.yaml
```

### `airlock status`

Check if the proxy is running.

```bash
airlock status                         # probe localhost:4000
airlock status --host myproxy --port 8080
```

Exit code 0 if healthy, 1 if unreachable.

### `airlock tui`

Launch the terminal dashboard.

```bash
airlock tui --start    # start proxy + dashboard
airlock tui            # dashboard only (connect to running proxy)
```

See [TUI Dashboard](tui.md) for screen details.

### `airlock analyze`

Run offline log analysis.

```bash
airlock analyze                  # last 7 days, text output
airlock analyze --days 30        # last 30 days
airlock analyze --json           # machine-readable JSON
airlock analyze -o report.txt    # write to file
```

### `airlock advise`

Ask the LLM-powered advisor about operational data.

```bash
airlock advise "why is model X failing?"
airlock advise --interactive
airlock advise --local-only "what should I tune?"
airlock advise --model local-llama "check health"
airlock advise --host myproxy --port 8080 "summarize errors"
```

See [Advisor](advisor.md) for details.

### `airlock post`

Run Power-On Self-Test to validate configuration.

```bash
airlock post                          # full check
airlock post --skip-llm               # skip provider connectivity
airlock post --skip-llm --skip-mcp    # config + guardrails only
airlock post --json                   # machine-readable output
```

### `airlock hooks`

Manage Claude Code client-side hooks.

```bash
airlock hooks install    # install pre-submit and session hooks
airlock hooks status     # check hook installation state
```

### `airlock dogfood`

Print shell commands to configure Claude Code to route through Airlock.

```bash
eval $(airlock dogfood)
```

### `airlock install-service`

Install Airlock as a **systemd user service** so the proxy starts
automatically and is managed by `systemctl --user`. Copies
`deploy/airlock.service` to `~/.config/systemd/user/`, then reloads,
enables, and starts the unit. Requires `systemctl`, and a `.env` file in
the project root (run `airlock init` first) for API keys.

```bash
airlock install-service            # install, enable, and start
airlock install-service --dry-run  # print the commands without executing
```

Manage the running service with:

```bash
systemctl --user status airlock
systemctl --user restart airlock
journalctl --user -u airlock -f
```

If systemd "linger" is disabled the service only runs while you are
logged in. To start it at boot: `loginctl enable-linger $USER`.
