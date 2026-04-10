# Connecting AI Tools

Point any OpenAI-compatible client at `http://localhost:4000` (or your deployed Airlock URL).

## Claude Code

```bash
# Install client-side hooks and route traffic through the proxy
airlock hooks install
eval $(airlock dogfood)
claude
```

Every request now flows through PII redaction, keyword blocking, and JSONL logging. Open `airlock tui` in another terminal to watch traffic in real time.

## Cursor / Windsurf

In settings, set:

- **OpenAI Base URL**: `http://localhost:4000/v1`
- **API Key**: your Airlock master key (from `.env`)

## GitHub Copilot

In VS Code `settings.json`:

```json
{
  "github.copilot.advanced": {
    "debug.overrideProxyUrl": "http://localhost:4000/v1"
  }
}
```

## Any OpenAI-compatible client

Set the base URL to `http://localhost:4000/v1` and use your Airlock master key as the API key. Airlock translates between providers transparently.
