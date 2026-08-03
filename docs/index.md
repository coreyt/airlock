# Airlock documentation

Airlock is an enterprise LLM proxy built on LiteLLM. It gives AI coding tools a
single OpenAI-compatible endpoint while adding guardrails, routing,
observability, and operational controls.

## Choose your path

- **New to Airlock?** Start with [Installation](getting-started/installation.md),
  then [Configuration](getting-started/configuration.md), then [Connecting AI
  Tools](getting-started/connecting-tools.md).
- **Operating a deployment?** See [Operations](operations.md),
  [Troubleshooting](troubleshooting.md), and the [Admin API](guide/admin-api.md).
- **Integrating a client?** Consult the live [API reference](reference/api.md),
  [response-header reference](reference/response-headers.md), and
  [routing guide](guide/routing.md).
- **Evaluating the design?** Read the [architecture overview](architecture/overview.md)
  and [system diagram](architecture/diagram.md).

## Core capabilities

| Capability | Start here |
| --- | --- |
| Provider routing and model aliases | [Routing](guide/routing.md) |
| PII, keyword, semantic, and response controls | [Guardrails](guide/guardrails.md) |
| Provider protection and admission | [Rate limiting](guide/rate-limiting.md) |
| Logs, metrics, tracing, and response transparency | [Observability](guide/observability.md) |
| Batch workloads | [Batch processing](guide/batch.md) |
| MCP tools | [MCP servers](guide/mcp-servers.md) |

## Continue reading

- [CLI reference](guide/cli.md)
- [TUI dashboard](guide/tui.md)
- [Changelog](changelog.md)
