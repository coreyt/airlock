# Feature Design: Admin Advisor

An agentic loop that lets administrators ask natural-language questions
about Airlock's operational state and get answers grounded in real data,
using the LLMs Airlock is already proxying.

**Examples of questions it answers:**

- "Why does claude-sonnet have a 40% error rate today?"
- "Which client is causing rate limits on Anthropic?"
- "Should I adjust the PII detection threshold?"
- "What's the best failover model for gemini-pro right now?"
- "Why are requests slow this morning?"

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  TUI (Screen 6: Advisor)  OR  CLI: airlock advise   │
│  ┌───────────────────────────────────────────────┐  │
│  │  "Why does claude-sonnet have a 40% error     │  │
│  │   rate today?"                                │  │
│  └───────────────────┬───────────────────────────┘  │
└──────────────────────┼──────────────────────────────┘
                       │ (in-process call)
                       ▼
┌──────────────────────────────────────────────────────┐
│              Advisor Agent Loop                      │
│                                                      │
│  1. Gather context (tool calls)                      │
│     ├─ read StateStore snapshot (in-memory)           │
│     ├─ read JSONL logs (tail + filtered scan)         │
│     ├─ read config.yaml                               │
│     ├─ read airlock-knobs.json                        │
│     ├─ run analyzer.analyze() for report              │
│     └─ read /health/circuits                          │
│                                                      │
│  2. Build prompt with system context + user question  │
│                                                      │
│  3. Call LLM (prefer local; warn on remote)           │
│                                                      │
│  4. Parse response — if it contains an action         │
│     proposal (config change), present for approval    │
│                                                      │
│  5. If approved → apply config change                 │
│     (write config.yaml, signal proxy reload)          │
│                                                      │
│  6. Return answer + any actions taken                 │
└──────────────────────────────────────────────────────┘
```

---

## Design Decisions

### 1. Model Selection — Local-First with Explicit Warnings

The advisor reuses `_is_local_model()` logic from `tui/screens/test.py`
to distinguish local vs. cloud models in `config.yaml`.

Selection order:

1. `AIRLOCK_ADVISOR_MODEL` env var override (explicit operator choice).
2. Scan `model_list` for entries with a custom `api_base` (vLLM, Ollama,
   etc.) — pick the highest-capability local model.
3. If no local model exists: pick the cheapest remote model and display a
   warning before the first call:

```
WARNING: No local model configured. The advisor will send operational
data to [provider]. This may include client IDs, error messages, and
request patterns. Proceed? [y/N]
```

The warning fires once per session (TUI) or once per invocation (CLI).
`--local-only` flag on the CLI causes a hard error if no local model is
available.

```python
# advisor/model_select.py

def select_advisor_model(config: dict) -> tuple[str, bool]:
    """Pick the best model for the advisor, preferring local.

    Returns (model_name, is_local).
    """
```

### 2. Data Gathering — Tool-Based Context Assembly

Rather than dumping all operational data into the prompt, the advisor
uses a tool pattern where the LLM selectively requests data based on
the question.  This keeps token usage bounded and means smaller local
models can be effective (the tools do the heavy lifting).

| Tool Name             | Source                       | Returns                                                                 |
|-----------------------|------------------------------|-------------------------------------------------------------------------|
| `get_state_snapshot`  | `StateStore` singleton       | Client states, model circuits, provider states, spend — compact JSON    |
| `get_recent_errors`   | JSONL logs (today+yesterday) | Filtered to `success=false`, grouped by model/client/error_type         |
| `get_analysis_report` | `analyzer.analyze()`         | Full AnalysisReport (optimizations, trends, hypotheses, semantic)       |
| `get_circuit_health`  | `health.get_circuit_health()`| Current circuit breaker states for all models                           |
| `get_config`          | `config.yaml`                | Current config with API keys redacted                                   |
| `get_guard_signals`   | JSONL logs                   | Recent guardrail observations, filterable by guardrail name or client   |
| `get_client_profile`  | StateStore + logs            | Single-client deep dive: error rate, latency, threat score, requests    |
| `get_model_profile`   | StateStore + logs            | Single-model deep dive: circuit state, error patterns, latency dist     |
| `get_knobs`           | `airlock-knobs.json`         | Current guardrail tuning weights and thresholds                         |

Agent loop is bounded: 3–5 iterations max.

```
iteration 1: LLM sees question → requests tool calls
iteration 2: tool results injected → LLM reasons over data
iteration 3: (optional) LLM requests more specific data
iteration 4: LLM produces answer + optional action proposals
```

### 3. IPC — In-Process, No New Network Listeners

**TUI:** The advisor runs as a Textual `@work` worker thread, same
pattern as `TestPane`.  It reads `StateStore` directly (thread-safe —
already uses locks), reads JSONL logs via `_load_logs()`, and calls
the LLM via `urllib`.  Results post back via Textual message passing.

```
TUI process
├── Main thread (Textual event loop)
├── JSONL tailer thread (existing)
├── Alert engine (existing)
└── Advisor worker thread (new)
    ├── Reads StateStore directly
    ├── Reads JSONL logs via _load_logs()
    ├── Calls LLM via urllib (same as TestPane)
    └── Posts results via Textual message passing
```

**CLI (`airlock advise`):** Standalone process that reads logs/config
from disk and calls the LLM directly.  No IPC to the proxy needed
since all operational data lives in JSONL files.

**Why not a proxy API endpoint?** The proxy runs inside LiteLLM's
process.  Adding an advisor endpoint there means the proxy calls itself
recursively (advisor → proxy → provider).  Keeping the advisor outside
the proxy avoids circular dependencies and blast radius.

### 4. Config Modification — Guarded Write-Back

When the advisor recommends a config change, it outputs a structured
action block that gets parsed into a proposal:

```python
@dataclass
class ConfigProposal:
    description: str          # "Add claude-haiku as failover for claude-sonnet"
    config_path: str          # "config.yaml"
    diff_preview: str         # unified diff of proposed change
    risk_level: str           # "low" | "medium" | "high"
    requires_restart: bool    # True if proxy must restart to pick up change
```

Flow:

1. LLM outputs an `ACTION` block in its response.
2. Advisor parses it into a `ConfigProposal`.
3. TUI displays the diff preview with risk level.
4. Admin explicitly approves or rejects.
5. If approved: write `config.yaml`, optionally signal proxy restart via
   `ProxyManager`.

**No MCP filesystem server needed.** The advisor runs in the TUI
process which has filesystem access.  A YAML file write with a
confirmation gate is simpler and safer than standing up an MCP server.

Safety rails:

- Config writes create a `.bak` backup first.
- `airlock post` (POST validation) runs automatically after write to
  catch bad configs before restart.
- High-risk changes (removing models, disabling guards) require typing
  `CONFIRM`, not just `y`.
- All advisor actions logged to `logs/advisor-audit.jsonl`.

### 5. TUI Integration — Screen 6 ("Advisor")

Add a sixth screen, consistent with the existing 5-screen architecture.
Key binding: `6`.

Layout:

```
┌─────────────────────────────────────────────────┐
│  Model: [local/vllm-qwen ▼]  [local-only ☑]    │
├─────────────────────────────────────────────────┤
│                                                 │
│  Advisor: claude-sonnet has a 38% error rate    │
│  over 247 requests in the last 24 hours. The    │
│  errors are concentrated in the last 3 hours    │
│  and are all RateLimitError from Anthropic...   │
│                                                 │
│  Proposed action:                               │
│  ┌─────────────────────────────────────────┐    │
│  │ + Add claude-haiku as failover          │    │
│  │   Risk: low | Requires restart: yes     │    │
│  │   [Approve]  [Reject]  [Show Diff]      │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
├─────────────────────────────────────────────────┤
│ > ask a question...                        [Go] │
└─────────────────────────────────────────────────┘
```

Components:

- Model selector (pre-filtered to local models, with "all" option)
- Scrollable response area
- Proposed-actions panel (shown only when advisor suggests changes)
- Input area at bottom

Alternative considered: command-palette overlay (press `a` from any
screen).  Deferred to v2 — it's more complex and the screen pattern is
well-established.

### 6. CLI Surface

```bash
# One-shot question
airlock advise "why is claude-sonnet failing?"

# Interactive REPL
airlock advise --interactive

# Pipe analysis report for commentary
airlock analyze --format json | airlock advise --stdin "summarize the top issues"

# Force local-only (error if no local model)
airlock advise --local-only "what should I tune?"

# Use a specific model
airlock advise --model local/vllm-qwen "check client threat scores"
```

---

## System Prompt (sketch)

```
You are the Airlock Advisor — an operational assistant for the Airlock
LLM proxy. You help administrators understand and resolve issues with
their proxy deployment.

You have access to tools that query Airlock's operational data. Use them
to ground your answers in facts. Never guess when you can look up data.

When you identify an actionable fix, output it as an ACTION block:
  ACTION: {"type": "config_change", "path": "...", "change": {...}}
Only propose actions you are confident about. Explain your reasoning.

Key Airlock concepts:
- Circuit breaker: CLOSED (healthy) → OPEN (5 failures) → HALF_OPEN
  (30s probe) → CLOSED (3 successes).  Failover routes to backup model.
- Threat detector: scores clients 0→1 on volume spike, rapid-fire,
  payload anomaly, error probing.  Blocks at >= 0.7.
- Guardrail chain: PII → keyword → fast guardian → enforcer → semantic
  → orchestrator → MCP tool guard → response scanner → PII hydrator.
- Smart router: classifies prompt complexity → routes to cost tier
  (simple/moderate/complex).
- Provider protection: per-client quarantine on rate limits, per-provider
  quarantine on sustained errors.
- Enforcement modes: "observe" (log only) vs "enforce" (block).
- Knobs: guardrail weights and thresholds auto-tuned by the slow
  analyzer, stored in airlock-knobs.json (30s cache TTL).
```

---

## Module Structure

```
airlock/advisor/
├── __init__.py
├── agent.py          # Core agent loop (gather → prompt → call → parse)
├── tools.py          # Data-gathering tools (get_state_snapshot, etc.)
├── model_select.py   # Local-first model selection + warning logic
├── prompts.py        # System prompt templates
├── proposals.py      # ConfigProposal parsing, diff generation, apply
└── audit.py          # Advisor action audit logging

airlock/tui/screens/
└── advisor.py        # TUI Screen 6

airlock/cli/
└── advise_cmd.py     # CLI entry point
```

---

## Privacy and Security

| Concern                          | Mitigation                                                        |
|----------------------------------|-------------------------------------------------------------------|
| Operational data sent to remote  | Local-first model selection; explicit warning + consent for remote |
| API keys in config               | Redacted before injection into prompt context                     |
| Client IDs in logs               | Included (necessary for diagnosis); covered by remote warning     |
| Advisor modifies config          | Explicit approval, `.bak` backup, POST validation, audit log      |
| Advisor privilege level          | Same as TUI operator — no escalation                              |

---

## What This Does NOT Need

- **No new database.** JSONL logs + StateStore + config.yaml are the
  data sources.  Adding SQLite/Postgres would be premature.
- **No MCP server for config.** Direct file I/O with confirmation gate
  is simpler and safer.
- **No new network listener.** Advisor runs in-process (TUI) or as a
  CLI tool.  No new ports to secure.
- **No streaming.** v1 waits for the full response.  Streaming adds
  complexity for marginal UX benefit in an admin tool.

---

## Implementation Status

**v1 implemented** — all modules, CLI, TUI screen, and tests are
merged.  73+ unit tests covering model selection, tools, agent loop,
prompts, proposals, CLI, and TUI integration.

Resolved open questions:
- Conversation history: ephemeral for v1 (no persistence).
- `--apply` flag: not implemented; human-in-the-loop required.
- `get_analysis_report` calls `analyze()` on demand; `write_knobs`
  side effect is suppressed so the advisor is read-only.

## Open Questions (v2)

- Should the advisor conversation history persist across TUI sessions?
- Should `airlock advise --apply` support non-interactive config changes?
- Should log reads be cached within a single agent turn to avoid
  redundant JSONL parsing when multiple tools query the same logs?
- Should `_load_logs`, `_load_models_from_config`, and `is_local_model`
  be extracted to shared utility modules to reduce duplication with
  `analyzer.py` and `tui/screens/test.py`?
