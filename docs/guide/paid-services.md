# Paid Side Services

Three configured services consume paid credits on every call. This page records what protects them today and how to restrict them per client.

## Current posture

| Service | Reaches Airlock as | Authentication | Per-client authorization |
|---|---|---|---|
| Tavily (`tavily-search`, `tavily/web-search`) | Model entry | `AIRLOCK_MASTER_KEY` | Optional (below) |
| Perplexity (`perplexity-sonar*`) | Model entry | `AIRLOCK_MASTER_KEY` | Optional (below) |
| NewsCatcher | MCP server | `AIRLOCK_MASTER_KEY` + MCP tool guard | Optional (below) |

**All three are authenticated.** None is reachable without a valid key — Tavily and Perplexity are ordinary model entries and sit behind the same virtual-key check as every other model; NewsCatcher is reached through the MCP path, which additionally passes the [MCP tool guard](mcp-servers.md).

What was missing until 0.5.10 is **authorization**. Authentication answers "is this a valid caller"; it does not answer "may *this* caller spend credits on *this* service". Any authenticated client could reach any paid service.

## Restricting a service to specific clients

Set an allowlist of authenticated client IDs per service:

```bash
AIRLOCK_PAID_SERVICE_ALLOW_TAVILY=key:abcd1234,key:efgh5678
AIRLOCK_PAID_SERVICE_ALLOW_PERPLEXITY=key:abcd1234
AIRLOCK_PAID_SERVICE_ALLOW_NEWSCATCHER=key:abcd1234
```

Client IDs are the key-derived form `key:<last 8 characters of the API key>` — the same identity the guardrail chain already uses. You can read a client's ID from any request record's `airlock_client` field, or from the Overview screen's client table.

Behavior:

- **Unset (the default) means unrestricted.** Every authenticated client may reach the service, exactly as before 0.5.10. Enabling this cannot silently start refusing production traffic — it is opt-in per service.
- **Set but empty means nobody.** `AIRLOCK_PAID_SERVICE_ALLOW_TAVILY=` refuses every caller, which is a usable kill switch.
- **Configuring one service does not gate the others.** Each service is independent.
- **An unauthenticated caller is refused** when an allowlist exists, rather than waved through — otherwise the allowlist would be trivially bypassable.

A refused request fails with a `PermissionError` naming the service. The message deliberately does not list who *is* authorized: that would leak the tenant list to the one party that should not have it.

### Authorization is not subject to enforcement mode

`AIRLOCK_ENFORCE_MODE=observe` means "score requests but do not block on guardrail heuristics". It does **not** relax paid-service authorization: an authorization decision is a hard gate, not a weighted signal, so it applies in every mode. Since `observe` is this deployment's default, a policy that honored the mode would silently do nothing.

### Identity is key-derived, never the client header

Authorization uses the authenticated, key-derived identity — never the `X-Airlock-Client` request header. That header is client-controllable, so authorizing on it would let any caller spend another tenant's credits simply by claiming their name. A test pins this.

## Observability

Every request that touches a paid service is stamped with `airlock_paid_service` in its record:

```json
{"service": "tavily", "allowed": true, "reason": "allowlisted"}
```

`reason` is one of `unrestricted`, `allowlisted`, `not_allowlisted`, or `unauthenticated`, so you can tell a permitted call from an un-gated one. Refusals also log a `paid_service_denied` warning.

## Not covered: quotas and billing attribution

This is an access decision only — "may this client call it at all", not "how much has it spent". Per-service quotas and billing attribution are deliberately deferred to the 0.6.x tenant-keys work, where budgets are the theme. Building a second budget system here would duplicate infrastructure that line already needs.

For spend that *is* tracked today, see [Provider Quota Observability](provider-observability.md) — provider-level spend and daily caps are enforced separately and shown on the Overview screen.
