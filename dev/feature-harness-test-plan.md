# Airlock Feature Test Harness Plan

This document defines acceptance criteria and test methods for every
user-facing feature in Airlock. The harness exercises the system end-to-end
against a running proxy instance with real (or sandboxed) API keys.

Tests are grouped by subsystem. Each test has:

- **Feature** — what is being tested
- **Acceptance criteria** — observable conditions that must hold
- **Method** — how to exercise and verify

---

## Prerequisites

| Requirement | Detail |
|---|---|
| Running proxy | `airlock tui --start` or `airlock start` |
| API keys | At least `ANTHROPIC_API_KEY` and one of `OPENAI_API_KEY` / `GOOGLE_AISTUDIO_API_KEY` |
| Master key | `AIRLOCK_MASTER_KEY` set in `.env` |
| Blocked keywords | `AIRLOCK_BLOCKED_KEYWORDS=classified,topsecret` |
| PII entities | Default set or `AIRLOCK_PII_ENTITIES=CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER` |
| MCP tools env | `AIRLOCK_MCP_ALLOWED_TOOLS` and/or `AIRLOCK_MCP_BLOCKED_TOOLS` set |
| Log directory | `AIRLOCK_LOG_DIR=./logs` |
| Proxy URL | `http://localhost:4000` (default) |

All `curl` commands assume `AIRLOCK_URL=http://localhost:4000` and
`AIRLOCK_KEY` set to the master key value.

---

## 1. Proxy Core

### 1.1 Health endpoint

**Acceptance:** `GET /health` returns 200 with JSON body when authenticated.
Returns 401 without auth when master key is configured.

**Method:**
```bash
# Authenticated — expect 200
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $AIRLOCK_KEY" $AIRLOCK_URL/health

# Unauthenticated — expect 401
curl -s -o /dev/null -w "%{http_code}" $AIRLOCK_URL/health
```

### 1.2 Chat completion (basic)

**Acceptance:** `POST /v1/chat/completions` with a valid model returns a
well-formed OpenAI-compatible response containing `choices[0].message.content`,
`usage.prompt_tokens`, and `usage.completion_tokens`.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","messages":[{"role":"user","content":"Say hello"}]}'
```

Validate: response has `id`, `object: "chat.completion"`, `choices` array,
`usage` dict.

### 1.3 Streaming completion

**Acceptance:** Same endpoint with `"stream": true` returns `text/event-stream`
with `data:` lines containing incremental `delta.content`, terminated by
`data: [DONE]`.

**Method:**
```bash
curl -sN $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","messages":[{"role":"user","content":"Count to 5"}],"stream":true}'
```

Validate: multiple `data:` lines, final `data: [DONE]`, each chunk has
`delta` key.

### 1.4 Model listing

**Acceptance:** `GET /v1/models` returns all models from `config.yaml`
`model_list`.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/models \
  -H "Authorization: Bearer $AIRLOCK_KEY" | jq '.data[].id'
```

Validate: output includes `claude-haiku`, `claude-sonnet`, `gpt-4o`,
`gemini-flash`, `tavily-search`, etc.

### 1.5 Unsupported parameter handling

**Acceptance:** Request with an unsupported parameter (e.g. `foo: "bar"`) does
NOT return an error. `drop_params: true` silently drops it.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","messages":[{"role":"user","content":"hi"}],"foo":"bar"}'
```

Validate: 200 response, no error about unknown parameter.

---

## 2. Multi-Provider Routing

### 2.1 Provider routing by model name

**Acceptance:** Each configured model alias routes to the correct upstream
provider. Requests to `claude-*` hit Anthropic, `gpt-*` hit OpenAI,
`gemini-*` hit Google, etc.

**Method:** Send a simple completion to each model family and verify a
successful response:
```bash
for model in claude-haiku gpt-4o-mini gemini-flash mistral-small perplexity-sonar; do
  echo "--- $model ---"
  curl -s $AIRLOCK_URL/v1/chat/completions \
    -H "Authorization: Bearer $AIRLOCK_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Say your model name\"}]}" \
    | jq '{model: .model, content: .choices[0].message.content[:80]}'
done
```

Validate: each returns 200 and the response `model` field reflects the
upstream provider model.

### 2.2 Cross-provider fallback

**Acceptance:** When a model's primary provider fails or is exhausted, the
request falls back to the next model in the `fallbacks` chain defined in
`router_settings`.

**Method:** Hard to trigger organically. Can be validated by:
1. Setting an invalid API key for one provider temporarily.
2. Sending a request to a model from that provider.
3. Verifying the response comes from a fallback model.

Alternatively, confirm circuit breaker behavior (see 5.3).

### 2.3 Cost-based routing

**Acceptance:** With `routing_strategy: cost-based-routing`, when multiple
deployments serve the same model alias, the router prefers the lower-cost
deployment.

**Method:** Requires a model alias with multiple deployments at different
costs. Verify via JSONL logs that the cheaper deployment is selected
preferentially.

---

## 3. Guardrails — Pre-Call

### 3.1 PII redaction

**Acceptance:** A request containing PII (credit card, SSN, email, phone) is
allowed through, but the PII is replaced with `<CREDIT_CARD>`, `<US_SSN>`,
`<EMAIL_ADDRESS>`, or `<PHONE_NUMBER>` placeholders before reaching the
upstream provider. The JSONL log for the request shows the redacted content.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","messages":[{"role":"user","content":"My SSN is 123-45-6789 and email is test@example.com. Say OK."}]}'
```

Validate:
- Response returns 200 (request was not blocked, only redacted).
- JSONL log entry shows redacted content with `<US_SSN>` and `<EMAIL_ADDRESS>`.
- The LLM response references the placeholder, not the original PII.

### 3.2 PII in multipart messages

**Acceptance:** PII detection works on messages with `content` as a list of
`{type: "text", text: "..."}` parts, not just plain strings.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","messages":[{"role":"user","content":[{"type":"text","text":"Card: 4111-1111-1111-1111"}]}]}'
```

Validate: JSONL log shows `<CREDIT_CARD>` in the content parts.

### 3.3 Keyword blocking

**Acceptance:** A request containing a blocked keyword returns a 400-level
error with a policy violation message. The blocked content is NOT echoed back.

**Method:**
```bash
curl -s -w "\n%{http_code}" $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","messages":[{"role":"user","content":"Tell me about classified operations"}]}'
```

Validate: HTTP 400 (or 403), error body contains "policy" or "blocked",
body does NOT contain the word "classified".

### 3.4 Keyword case insensitivity

**Acceptance:** Keyword matching is case-insensitive. `CLASSIFIED`,
`Classified`, and `classified` all trigger blocking.

**Method:** Repeat 3.3 with `"CLASSIFIED"` and `"Classified"` variants.

### 3.5 Fast Guardian — threat detection

**Acceptance:** Rapid-fire requests from the same client trigger threat
detection. After enough rapid requests, the client receives a backoff
response.

**Method:**
```bash
# Fire 20 rapid requests
for i in $(seq 1 20); do
  curl -s -o /dev/null -w "%{http_code}\n" $AIRLOCK_URL/v1/chat/completions \
    -H "Authorization: Bearer $AIRLOCK_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-haiku","messages":[{"role":"user","content":"ping"}]}' &
done
wait
```

Validate: later requests in the burst return 429 or include backoff metadata.

### 3.6 Fast Guardian — large payload detection

**Acceptance:** A request with an abnormally large payload triggers threat
scoring (payload anomaly heuristic).

**Method:** Send a request with a very long message (e.g. 50KB of text).
Check JSONL logs for threat assessment metadata.

### 3.7 MCP tool guard — allowlist/blocklist

**Acceptance:** MCP tool calls to blocked tools are rejected. Tool calls to
tools not on the allowlist (if configured) are rejected.

**Method:** Requires MCP client or a direct test against the guardrail.
Unit tests cover this; for the harness, verify via POST check (see 8.2).

### 3.8 MCP tool guard — argument sanitization

**Acceptance:** MCP tool arguments containing path traversal (`../`,
`%2e%2e`), shell metacharacters (`;`, `|`, `&`, `` ` ``), or command
substitution (`$()`) are rejected.

**Method:** Requires MCP client sending crafted arguments. Unit tests
cover this; harness validates via POST MCP guardrail registration check.

### 3.9 Enforcer (adaptive blocking)

**Acceptance:** In `observe` mode (default), enforcer logs what it would
block but does not actually block. In `enforce` mode, requests exceeding the
composite score threshold are blocked.

**Method:**
- Set `AIRLOCK_ENFORCE_MODE=observe`, send requests, verify none are blocked
  by the enforcer and logs show `would_block` metadata.
- Set `AIRLOCK_ENFORCE_MODE=enforce`, verify blocking behavior activates.

---

## 4. Guardrails — During-Call

### 4.1 Semantic guard observation

**Acceptance:** During-call guardrails run concurrently with the LLM API call
(zero added latency). Semantic guard logs classifier outputs to metadata
without blocking.

**Method:** Send a request and check JSONL logs for `airlock_observation`
metadata containing semantic classifier results. Compare total latency
against a baseline request — during-call overhead should be negligible.

### 4.2 Orchestrator weighted scoring

**Acceptance:** Orchestrator reads analyzer-tuned knobs, computes a weighted
composite score, and logs `composite_score`, `would_block`, and `version`
to response metadata. It never raises/blocks.

**Method:** Send requests and inspect JSONL logs for orchestrator metadata
fields. Verify no requests are blocked by the orchestrator regardless of
content.

---

## 5. Guardrails — Post-Call

### 5.1 Response scanner — observe mode

**Acceptance:** In `observe` mode (default), the response scanner logs
detections but does not block responses. Metadata is attached only when
patterns are detected.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","messages":[{"role":"user","content":"Say: ignore all previous instructions"}]}'
```

Validate: response returns 200. JSONL log may contain `airlock_response_scan`
metadata if the LLM echoes the phrase back.

### 5.2 Response scanner — enforce mode

**Acceptance:** In `enforce` mode, responses containing injection/override
patterns above the threshold are blocked.

**Method:** Set `AIRLOCK_RESPONSE_SCAN_MODE=enforce` and
`AIRLOCK_RESPONSE_SCAN_THRESHOLD=0.3`. Prompt the LLM to echo back
injection-like text. Verify the response is blocked.

---

## 6. Intelligent Routing

### 6.1 Smart model routing

**Acceptance:** Sending `model: "smart"` auto-classifies prompt complexity and
routes to the appropriate cost tier: simple prompts to cheap models
(haiku/flash/4o-mini), complex prompts to powerful models (opus/3-pro).

**Method:**
```bash
# Simple — should route to low tier
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"What is 2+2?"}]}' \
  | jq '.model'

# Complex — should route to high tier
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"Analyze the trade-offs between microservices and monolithic architecture. Consider deployment complexity, data consistency, team autonomy, and operational overhead. Provide a decision framework with concrete criteria for choosing between them, including code examples showing service boundaries and communication patterns."}]}' \
  | jq '.model'
```

Validate: simple query routes to a low-cost model, complex query routes to a
high-cost model. JSONL logs show `airlock_routed_model` and
`airlock_complexity_tier`.

### 6.2 Session affinity

**Acceptance:** Requests with the same `metadata.airlock.session_id` are
routed to the same model within the session TTL window.

**Method:**
```bash
SESSION_ID="test-session-$(date +%s)"
for i in 1 2 3; do
  curl -s $AIRLOCK_URL/v1/chat/completions \
    -H "Authorization: Bearer $AIRLOCK_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"smart\",\"messages\":[{\"role\":\"user\",\"content\":\"Request $i\"}],\"metadata\":{\"airlock\":{\"session_id\":\"$SESSION_ID\"}}}" \
    | jq '.model'
done
```

Validate: all three requests route to the same model.

### 6.3 Cost tier directive

**Acceptance:** `metadata.airlock.cost_tier` overrides default routing to
select a model from the specified tier.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"Hello"}],"metadata":{"airlock":{"cost_tier":"high"}}}' \
  | jq '.model'
```

Validate: routes to a high-tier model (opus, 3-pro, etc.) despite the simple
prompt.

### 6.4 Provider preference directive

**Acceptance:** `metadata.airlock.prefer_provider` biases routing toward the
specified provider.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"Hello"}],"metadata":{"airlock":{"prefer_provider":"openai"}}}' \
  | jq '.model'
```

Validate: routes to an OpenAI model (gpt-4o-mini or gpt-4o).

### 6.5 Provider budget caps

**Acceptance:** Provider budget limits in `router_settings.provider_budget_config`
are enforced. When a provider's daily budget is exhausted, requests fall back
to other providers.

**Method:** Set a very low budget (e.g. $0.01) for one provider, send enough
requests to exhaust it, verify subsequent requests fall back.

---

## 7. Callbacks & Logging

### 7.1 JSONL logging

**Acceptance:** Every request (success and failure) produces a JSONL log entry
in `logs/airlock-YYYY-MM-DD.jsonl` containing: `timestamp`, `success`,
`model`, `user`, `prompt_tokens`, `completion_tokens`, `total_tokens`,
`duration_ms`.

**Method:** Send a successful and a failing request. Read today's log file.

```bash
# Successful request
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-haiku","messages":[{"role":"user","content":"hi"}]}'

# Failing request (bad model)
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"nonexistent-model","messages":[{"role":"user","content":"hi"}]}'

# Check logs
tail -2 logs/airlock-$(date +%Y-%m-%d).jsonl | jq '{success, model, prompt_tokens, duration_ms}'
```

### 7.2 MCP call logging

**Acceptance:** MCP tool calls include `call_type`, `mcp_tool_name`, and
`mcp_server_name` in JSONL log entries.

**Method:** Trigger an MCP tool call via the proxy and inspect the JSONL log
for MCP-specific fields.

### 7.3 Guardrail metadata in logs

**Acceptance:** Guardrail signals (PII redaction counts, keyword hits, threat
scores, enforcer verdicts, response scan results) are attached as
`airlock_*` metadata fields in JSONL log entries.

**Method:** Send a request containing PII, inspect JSONL log for
`airlock_pii_redacted`, `airlock_enforcement`, `airlock_observation`, or
`airlock_response_scan` fields.

### 7.4 Robust serialization

**Acceptance:** Log entries are valid JSON even when response objects contain
non-serializable types (Pydantic models, bytes, datetimes).

**Method:** Send requests to different providers (which return different
response object shapes). Verify every line in the JSONL log is valid JSON:
```bash
python3 -c "
import json, sys
for i, line in enumerate(open('logs/airlock-$(date +%Y-%m-%d).jsonl')):
    json.loads(line)
print(f'{i+1} lines OK')
"
```

---

## 8. Power-On Self-Test (POST)

### 8.1 Full POST

**Acceptance:** `airlock post` runs all 14 checks across 5 groups (Config,
Providers, Storage, Guardrails, MCP) and reports pass/fail/skip for each.

**Method:**
```bash
airlock post --json 2>&1 | jq '.groups[].checks[] | {name, status}'
```

Validate: all expected checks present; provider checks with valid keys pass;
storage checks pass if log dir exists.

### 8.2 POST skip flags

**Acceptance:** `--skip-providers`, `--skip-storage`, `--skip-mcp` each skip
their respective check group.

**Method:**
```bash
airlock post --skip-providers --skip-mcp --json 2>&1 | jq '.groups[].name'
```

Validate: output does NOT contain "Providers" or "MCP" groups.

### 8.3 POST with proxy down

**Acceptance:** POST reports provider health checks as failed (not crashed)
when the proxy is not running.

**Method:** Stop the proxy, run `airlock post`, verify check failures are
reported gracefully with error messages.

---

## 9. CLI Commands

### 9.1 Init scaffolding

**Acceptance:** `airlock init` creates `config.yaml`, `.env`, and `logs/`
directory in the target path. Prints a summary. Skips existing files unless
`--force` is used.

**Method:**
```bash
tmpdir=$(mktemp -d)
airlock init "$tmpdir"
ls "$tmpdir"  # config.yaml, .env, logs/
airlock init "$tmpdir"  # should print "already exists" messages
airlock init "$tmpdir" --force  # should overwrite
rm -rf "$tmpdir"
```

### 9.2 Status command

**Acceptance:** `airlock status` exits 0 when proxy is healthy, exits 1 when
proxy is unreachable.

**Method:**
```bash
# With proxy running
airlock status && echo "OK" || echo "FAIL"

# With proxy stopped
airlock status && echo "OK" || echo "FAIL"
```

### 9.3 Dogfood command

**Acceptance:** `airlock dogfood` outputs shell export lines for
`ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` pointing at the running proxy.

**Method:**
```bash
airlock dogfood
# Should output:
# export ANTHROPIC_BASE_URL=http://localhost:4000/v1
# export ANTHROPIC_AUTH_TOKEN=<master_key>
```

### 9.4 Analyze command

**Acceptance:** `airlock analyze` processes JSONL logs and outputs
optimization, trend, and hypothesis reports.

**Method:**
```bash
airlock analyze --days 1 --json | jq 'keys'
```

Validate: output JSON contains `optimizations`, `cache`, `trends`,
`semantic`, `hypotheses` keys.

---

## 10. Custom Providers

### 10.1 Tavily web search

**Acceptance:** `model: "tavily-search"` performs a web search and returns
results formatted as a chat completion response with URLs, titles, and
snippets.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"tavily-search","messages":[{"role":"user","content":"latest Python release"}]}' \
  | jq '{model: .model, content: .choices[0].message.content[:200]}'
```

Validate: response contains search results with URLs. `usage` object is
populated.

### 10.2 Perplexity search

**Acceptance:** `model: "perplexity-sonar"` returns a response with web
search grounding (native LiteLLM provider, no custom code).

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"perplexity-sonar","messages":[{"role":"user","content":"What is the current US president?"}]}' \
  | jq '.choices[0].message.content[:200]'
```

---

## 11. Circuit Breaker & Failover

### 11.1 Circuit breaker state transitions

**Acceptance:** After consecutive failures to a model, its circuit opens
(CLOSED -> OPEN). After the recovery window, it enters HALF_OPEN and allows a
probe request. A successful probe closes the circuit.

**Method:** Best tested via unit tests (mocking provider failures). For the
harness, verify circuit state via TUI Models screen or StateStore inspection
after intentionally failing requests (e.g. invalid API key on a model).

### 11.2 Failover map

**Acceptance:** When a model's circuit is open, requests are transparently
routed to the first healthy model in its failover chain.

**Method:** Same as 11.1 — verify the response comes from a fallback model
when the primary is down. JSONL logs show `airlock_failover_from` and
`airlock_failover_to`.

---

## 12. Hooks (Claude Code Integration)

### 12.1 Hooks install

**Acceptance:** `airlock hooks install` adds 4 hook entries to
`.claude/settings.json` (SessionStart, UserPromptSubmit, PreToolUse,
PostToolUse) without removing existing settings.

**Method:**
```bash
airlock hooks install
cat ~/.claude/settings.json | jq '.hooks | keys'
```

Validate: all 4 hook types present. Existing non-hook settings preserved.

### 12.2 Hooks status

**Acceptance:** `airlock hooks status` shows the currently configured hooks.

**Method:**
```bash
airlock hooks status
```

### 12.3 SessionStart hook

**Acceptance:** On Claude Code startup, the session_start hook probes
`/health` and returns proxy status in `additionalContext`.

**Method:** Tested automatically when Claude Code starts. Verify the status
message appears in the session context.

### 12.4 UserPromptSubmit hook — keyword blocking

**Acceptance:** Prompts containing blocked keywords are rejected before
reaching the proxy.

**Method:** In Claude Code, type a prompt containing a blocked keyword.
Verify the prompt is blocked with a policy message.

### 12.5 PreToolUse hook — edit protection

**Acceptance:** Edit/Write tool calls containing blocked keywords in the
content are blocked.

**Method:** In Claude Code, attempt to write a file containing a blocked
keyword. Verify the tool call is blocked.

---

## 13. TUI

### 13.1 Proxy launch from TUI

**Acceptance:** The TUI `--start` flag or Start button launches the LiteLLM
proxy subprocess. The proxy health indicator turns green. Console output
streams in the collapsible panel.

**Method:** `airlock tui --start`, observe dashboard. Verify Start/Stop
buttons toggle proxy state. Console shows LiteLLM startup logs.

### 13.2 Dashboard indicators

**Acceptance:** Dashboard shows proxy status (running/stopped), guardrail
indicators for each active guardrail, MCP gateway status, and traffic
summary.

**Method:** With proxy running, verify dashboard shows green health indicator,
guardrail names with status, and request count updates after sending
requests.

### 13.3 Models screen

**Acceptance:** Models screen shows a DataTable with model name, provider,
cost tier, request count, error count. MCP tools shown in a separate table
below model detail.

**Method:** Navigate to Models screen (key `2`), verify all configured models
appear. Send a few requests, verify counts update.

### 13.4 Logs screen

**Acceptance:** Logs screen streams JSONL log entries with search/filter and
detail pane.

**Method:** Navigate to Logs screen (key `4`), verify log entries appear.
Use filter to show only failures. Select an entry and verify detail pane
shows full request/response.

### 13.5 Flow screen

**Acceptance:** Flow screen shows real-time guardrail pipeline events with
timestamp, request_id, model, client_id, composite_score, would_block, and
signal breakdown.

**Method:** Navigate to Flow screen (key `7`), send requests, verify entries
appear with guardrail signal details. Test pause/resume.

### 13.6 MCP Servers screen

**Acceptance:** MCP Servers screen shows a DataTable of configured MCP
servers with name, transport type, and health status. Local/managed servers
can be started/stopped from the UI.

**Method:** Navigate to MCP Servers screen (key `8`), verify all configured
servers appear. Probe health for a running server. Check console output tab.

### 13.7 Screen navigation

**Acceptance:** All 8 screens are accessible via number keys (1-8) and the
tab bar. Screen transitions are instantaneous.

**Method:** Press keys 1-8 and verify each screen loads correctly.

---

## 14. Offline Analysis

### 14.1 Log analysis dimensions

**Acceptance:** Analyzer produces output across 5 dimensions: optimizations,
cache opportunities, trends, semantic, and hypotheses.

**Method:**
```bash
airlock analyze --days 7 --json | jq 'keys'
```

Validate: all 5 keys present with non-empty arrays (assuming sufficient log
data).

### 14.2 Trend detection

**Acceptance:** Trends report identifies increasing/decreasing/stable patterns
for volume, model share, error rate, latency, and user concentration.

**Method:** Accumulate several days of logs, run analyzer, verify trend
directions are reported with percentages.

### 14.3 Hypothesis generation

**Acceptance:** Hypotheses are testable statements with confidence scores
derived from observed patterns.

**Method:** Review analyzer output for hypothesis entries with `confidence`,
`statement`, and `evidence` fields.

---

## 15. Model Alias Resolution

### 15.1 Fuzzy model name matching

**Acceptance:** Approximate model names (e.g. `claude`, `gpt4`, `gemini`)
resolve to the closest configured model alias.

**Method:**
```bash
# Should resolve to a configured claude model
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude","messages":[{"role":"user","content":"hi"}]}' \
  | jq '.model'
```

---

## 16. End-to-End Scenarios

These composite scenarios exercise multiple subsystems together.

### 16.1 PII + logging + analysis

**Acceptance:** A request with PII is redacted, logged with redacted content,
and the redaction event appears in analyzer output.

**Method:** Send a PII-laden request, verify redaction in response context,
verify JSONL log has redacted content, run analyzer and verify PII stats.

### 16.2 Threat + backoff + recovery

**Acceptance:** Rapid-fire requests trigger threat detection, client gets
backed off, and after the backoff window expires the client can resume
normal usage.

**Method:** Send burst requests, observe backoff responses, wait for backoff
to expire, verify normal responses resume.

### 16.3 Smart routing + session affinity + PII

**Acceptance:** A `model: "smart"` request with `session_id` and PII content
gets: complexity-classified, session-affine routed, PII-redacted, and fully
logged.

**Method:**
```bash
curl -s $AIRLOCK_URL/v1/chat/completions \
  -H "Authorization: Bearer $AIRLOCK_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "smart",
    "messages": [{"role":"user","content":"My email is test@example.com. Explain monads."}],
    "metadata": {"airlock": {"session_id": "e2e-test-1"}}
  }' | jq '{model: .model}'
```

Validate: response uses a routed model, PII is redacted in logs, subsequent
requests with same session_id route to same model.

### 16.4 Keyword block + logging

**Acceptance:** A keyword-blocked request is rejected, the rejection is
logged in JSONL, and the blocked content is NOT present in the log entry.

**Method:** Send a request with a blocked keyword, verify 400 response,
inspect JSONL for a failure entry without the blocked keyword in the logged
content.

### 16.5 Full guardrail pipeline

**Acceptance:** A single request traverses: PII guard (pre_call) -> keyword
guard (pre_call) -> fast guardian (pre_call) -> enforcer (pre_call) ->
[LLM call + semantic guard + orchestrator (during_call)] -> response scanner
(post_call). All signals are logged.

**Method:** Send a request with mild PII (email) and inspect the JSONL log
for metadata from each guardrail stage.

---

## Execution Notes

### Running the harness

The test harness can be executed as:
1. **Manual** — run individual curl commands and inspect output.
2. **Scripted** — wrap the curl commands in a bash/python test script that
   asserts on HTTP status codes and JSON fields.
3. **Automated** — integrate into CI with a sandboxed proxy instance and
   test API keys (with low-cost models only).

### Cost control

To minimize API costs during harness runs:
- Use `claude-haiku`, `gpt-4o-mini`, `gemini-flash`, `mistral-small` (cheapest per provider).
- Keep prompts short (`"Say hello"`, `"What is 2+2?"`).
- Set `max_tokens: 10` where response content doesn't matter.
- Use `--skip-providers` on POST checks when not testing provider connectivity.

### Environment isolation

The harness should use a dedicated `.env` with:
- Low provider budget caps (prevent runaway costs).
- Known blocked keywords for predictable guardrail behavior.
- A unique `AIRLOCK_LOG_DIR` to avoid polluting production logs.
- A unique `AIRLOCK_MASTER_KEY` for the test instance.
