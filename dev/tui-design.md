# Airlock TUI — Design Proposal

## 1. Intent Analysis

Two personas drive the design:

| Persona | Goal | Frequency |
|---------|------|-----------|
| **Operator** | Launch proxy, monitor traffic, check guardrail status | Daily |
| **Engineer** | Tune threat thresholds, debug circuit breaker trips, review PII redactions, analyze trends | Weekly |

The TUI must serve both without forcing the Operator through Engineer-level
complexity. The solution: a **sidebar-driven cockpit** where the Operator lives
on Dashboard and Logs, while the Engineer drills into Models, Threats, and
Settings.

---

## 2. Information Architecture

### 2.1 Navigation Topology

**Shell layout** (Cockpit Standard):

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Header: "Airlock" + proxy status indicator + clock                    │
├────────────┬────────────────────────────────────────────────────────────┤
│            │                                                           │
│  Sidebar   │  Workspace (1fr)                                         │
│  (20 cols) │                                                           │
│            │  Content varies by selected screen.                       │
│  Dashboard │  Some screens use internal TabbedContent                  │
│  Models    │  for sub-contexts.                                        │
│  Threats   │                                                           │
│  Clients   │                                                           │
│  Logs      │                                                           │
│  Analysis  │                                                           │
│  Settings  │                                                           │
│  Flow      │                                                           │
│  MCP Srvrs │                                                           │
│  Chat      │                                                           │
├────────────┴────────────────────────────────────────────────────────────┤
│  Footer: Key hints — [Tab] Focus  [1-9,0] Screen  [q] Quit  [?] Help  │
└─────────────────────────────────────────────────────────────────────────┘
```

**Reasoning:**

- **Sidebar (Breadth):** 10 screens — a docked `ListView` keeps all
  destinations visible. Single-digit accelerators (`1`–`9`, `0`) let the user
  jump instantly.
- **Internal Tabs (Breadth):** Settings and Analysis use `TabbedContent` for
  sub-contexts that the user switches between without losing position.
- **Drill-Down (Depth):** Model detail and log entry detail use `push_screen()`.
  `Esc` always pops back.
- **Modals (Transient):** Confirmations ("Apply changes?"), quick actions
  ("Test API key?"), and error details.

### 2.2 Screen Inventory

| # | Screen | Key | Persona | Purpose | Internal Tabs |
|---|--------|-----|---------|---------|---------------|
| 1 | Dashboard | `1` | Operator | At-a-glance proxy health and traffic | — |
| 2 | Models | `2` | Both | Per-model circuit state, latency, failover | — |
| 3 | Threats | `3` | Engineer | Active backoffs, threat scores, recent blocks | — |
| 4 | Clients | `4` | Both | Per-client request rate and protection status | — |
| 5 | Logs | `5` | Both | Live log tail with filtering | — |
| 6 | Analysis | `6` | Engineer | Run offline analysis, view reports | Optimizations, Cache, Trends, Hypotheses |
| 7 | Settings | `7` | Both | Configuration management | Providers, Guardrails, Logging, Advanced, MCP |
| 8 | Flow | `8` | Engineer | Real-time guardrail pipeline monitor | Signals, Pipeline, Raw, Tool Result |
| 9 | MCP Servers | `9` | Both | MCP server health, lifecycle, tools | Info, Console, Tools |
| 10 | Basic Chat | `0` | Both | Test LLM connectivity and interaction | — |

---

## 3. Screen Designs

### 3.1 Dashboard

The Operator's home screen. Dense, read-only, auto-refreshing.

```
┌─ Proxy Status ──────────────────────┬─ Guardrails ──────────────────────┐
│                                     │                                    │
│  Status: ● Running at 0.0.0.0:4000 │  PII Guard      ● active  142     │
│  Uptime: 4h 23m                     │  Keyword Guard  ● active    3     │
│  Requests today: 1,247              │  Fast Guardian  ● active    0     │
│  Error rate: 2.1%                   │                                    │
│                                     │                                    │
├─ Request Traffic (last hour) ───────┴────────────────────────────────────┤
│                                                                          │
│  12:00  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇                                               │
│  12:10  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇                                           │
│  12:20  ▇▇▇▇▇▇▇▇▇▇▇▇▇                                                  │
│  12:30  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇                                              │
│                                                                          │
├─ Model Health ───────────────────────────────────────────────────────────┤
│                                                                          │
│  Model            Circuit    Reqs    Err%    p50      p95                │
│  claude-sonnet    CLOSED      423    1.2%   1,200ms  4,500ms            │
│  claude-haiku     CLOSED      312    0.8%     800ms  2,100ms            │
│  gpt-4o           HALF_OPEN   201    8.4%   2,300ms  12,000ms           │
│  gpt-4o-mini      CLOSED      311    0.6%     600ms  1,800ms            │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Widgets:**

| Region | Widget | Data Source |
|--------|--------|-------------|
| Proxy Status | `Static` labels | `/health` probe via `@work` (5s interval) |
| Guardrails | `Static` labels with status indicators | Guardrail config + counters from `fast.state` |
| Request Traffic | `Sparkline` or `Static` bar chart | `fast.state.store` request counts |
| Model Health | `DataTable` (read-only) | `fast.state.store` model states |

**Refresh strategy:** A `set_interval(5.0)` timer calls a `@work` method that
reads from `fast.state.store` and updates all widgets. The `/health` probe runs
on a separate `@work` to avoid coupling.

### 3.2 Models

Per-model detail with circuit breaker visualization.

```
┌─ Models ─────────────────────────────────────────────────────────────────┐
│                                                                          │
│  Model            Circuit    Failures  Recovery   Failover Chain         │
│  ─────────────    ───────    ────────  ────────   ──────────────         │
│ ▸claude-sonnet    CLOSED          0   —          haiku → gpt-4o         │
│  claude-haiku     CLOSED          0   —          sonnet → gpt-4o-mini   │
│  gpt-4o           OPEN            5   18s left   sonnet → gpt-4o-mini   │
│  gpt-4o-mini      CLOSED          0   —          haiku → gpt-4o         │
│                                                                          │
├─ Selected: claude-sonnet ────────────────────────────────────────────────┤
│                                                                          │
│  Recent Latency (ms)                                                     │
│  p50: 1,200   p95: 4,500   p99: 8,200                                   │
│                                                                          │
│  ▁▂▃▂▁▂▃▅▃▂▁▂▃▂▁▂▃▂▁▃▅▇▅▃▂▁▂▃▂▁▂▃▂▁▂▃▅▃▂                              │
│                                                                          │
│  Circuit Breaker Config                                                  │
│  Failure threshold: 5    Recovery timeout: 30s    Success threshold: 3   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Topology:** Master-detail within the same screen. The `DataTable` (top) is the
master; arrow keys move the cursor and the detail pane (bottom) updates
instantly. `Enter` on a row pushes a full-detail screen if needed.

**Widget selection:**

| Element | Widget | Reasoning |
|---------|--------|-----------|
| Model list | `DataTable` | Tabular data, 4-5 rows, keyboard-navigable |
| Circuit state | Color-coded label in table cell | GREEN=CLOSED, YELLOW=HALF_OPEN, RED=OPEN |
| Latency sparkline | `Sparkline` | Compact time-series in ~2 rows |
| Config values | `Static` labels | Read-only display |

### 3.3 Threats

Active threat monitoring for the Engineer.

```
┌─ Active Backoffs ────────────────────────────────────────────────────────┐
│                                                                          │
│  Client ID      Threat Score   Backoff Until    Reasons                  │
│  ──────────     ────────────   ─────────────    ───────                  │
│  alice@co       0.82           12:34:56 (14s)   volume_spike, rapid_fire │
│  bob@co         0.71           12:35:10 (28s)   error_probing            │
│                                                                          │
│  No active backoffs? "All clear — no clients in backoff"                 │
│                                                                          │
├─ Threat Detection Config ────────────────────────────────────────────────┤
│                                                                          │
│  Block threshold:     0.7      Base backoff:    2s                       │
│  Max backoff:         3600s    Decay factor:    0.95                     │
│  Volume spike ratio:  5×       Rapid-fire gap:  100ms                   │
│  Payload max chars:   100,000  Error probe pct: 80%                     │
│                                                                          │
├─ Recent Blocks (last 50) ────────────────────────────────────────────────┤
│                                                                          │
│  12:34:42  alice@co    volume_spike + rapid_fire     score=0.82          │
│  12:30:15  bob@co      error_probing                 score=0.71          │
│  12:28:03  alice@co    volume_spike                  score=0.45 (warn)   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Widgets:**

| Region | Widget | Reasoning |
|--------|--------|-----------|
| Active backoffs | `DataTable` | Tabular, sortable, keyboard-navigable |
| Detection config | `Static` labels (read-only) | Display only; editing happens in Settings |
| Recent blocks | `RichLog` (append-only) | Scrollable history, newest on top |

### 3.4 Logs

Live log viewer with filtering — the Master-Detail pattern.

```
┌─ Filter ─────────────────────────────────────────────────────────────────┐
│  Model: [All        ▾]  User: [__________]  Status: (•) All (○) OK (○) Err │
├─ Log Entries ────────────────────────────────────────────────────────────┤
│                                                                          │
│  Timestamp             Model           User       Tokens   Dur     OK    │
│  ──────────────────    ──────────────  ────────   ──────   ─────   ──    │
│  2025-02-15 12:34:42   claude-sonnet   alice       1,247   1.2s    ✓    │
│  2025-02-15 12:34:38   gpt-4o          bob           832   2.3s    ✓    │
│  2025-02-15 12:34:35   claude-haiku    carol         456   0.8s    ✓    │
│ ▸2025-02-15 12:34:30   gpt-4o          alice         —     0.1s    ✗    │
│  2025-02-15 12:34:28   claude-sonnet   dave        2,100   3.1s    ✓    │
│                                                                          │
├─ Entry Detail ───────────────────────────────────────────────────────────┤
│                                                                          │
│  Request ID: abc123    Error: RateLimitError                             │
│  Messages: [{"role": "user", "content": "Explain the auth flow..."}]    │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Topology:** Split-pane master-detail. The `DataTable` is the master (top);
arrowing through rows updates the detail pane (bottom) instantly. `Enter` on a
row pushes a full-detail screen with complete messages and response JSON.

**Widget selection:**

| Element | Widget | Reasoning |
|---------|--------|-----------|
| Model filter | `Select` | 5-6 options — dropdown saves space |
| User filter | `Input` | Free-text search |
| Status filter | `RadioSet` (horizontal) | 3 options — all visible, 1 keystroke |
| Log table | `DataTable` | Tabular, sortable columns |
| Detail pane | `RichLog` or `Static` | Read-only formatted text |

**Data loading:** Reads JSONL files via `@work`. Initial load fetches today's
file. Scrolling up triggers loading of previous day's file. Filter changes
re-query the data.

### 3.5 Analysis

Run offline analysis and browse the report.

```
┌─ Controls ───────────────────────────────────────────────────────────────┐
│  Days: [7___]   [Run Analysis]   Last run: 12:00:00 (1,247 requests)    │
├─ Report ── Optimizations │ Cache │ Trends │ Hypotheses ──────────────────┤
│                                                                          │
│  1. [HIGH] Model 'gpt-4o' has 8.4% error rate over 201 requests         │
│     Evidence: 17 failures in last 24h, up from 3 in prior period         │
│     Action: Check provider status or configure failover                  │
│                                                                          │
│  2. [MEDIUM] Model 'claude-sonnet' p95 latency is 4,500ms               │
│     Evidence: p95 increased 40% vs prior period                          │
│     Action: Consider haiku for non-critical requests                     │
│                                                                          │
│  3. [LOW] Top user 'alice' accounts for 34% of traffic                   │
│     Evidence: 423 / 1,247 requests                                       │
│     Action: Review if single-user concentration is expected              │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Topology:** Controls bar at top, `TabbedContent` below with four tabs
matching the four analysis dimensions. The "Run Analysis" button triggers a
`@work` method that calls `airlock.slow.analyzer.analyze()`.

**Widget selection:**

| Element | Widget | Reasoning |
|---------|--------|-----------|
| Days input | `Input` (numeric, validated) | Free-form number |
| Run button | `Button` (primary) | Single action trigger |
| Report tabs | `TabbedContent` | 4 parallel sub-contexts |
| Report content | `RichLog` or `Static` per tab | Formatted read-only text |

### 3.6 Settings

Configuration management — the most widget-dense screen.

```
┌─ Settings ── Providers │ Guardrails │ Logging │ Advanced ────────────────┐
│                                                                          │
│  Guardrails                                                              │
│  ─────────                                                               │
│                                                                          │
│  PII Guard            [ON ]                                              │
│  Entity types:        ☑ CREDIT_CARD  ☑ US_SSN                           │
│                       ☑ EMAIL        ☑ PHONE                            │
│                                                                          │
│  Keyword Guard        [ON ]                                              │
│  Blocked keywords:    [Project X, SECRET____________]                    │
│                                                                          │
│  Fast Guardian        [ON ]                                              │
│  Threat threshold:    [0.7____]                                          │
│  Base backoff (s):    [2______]                                          │
│  Max backoff (s):     [3600___]                                          │
│                                                                          │
│                                    [Apply Changes]                       │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**Topology:** `TabbedContent` with four tabs:

| Tab | Contents |
|-----|----------|
| Providers | API key inputs (masked), model list display |
| Guardrails | PII guard toggle + entity checkboxes, keyword guard toggle + keyword input, fast guardian toggle + threshold inputs |
| Logging | Log directory input, S3 bucket/prefix/batch inputs, SQL URL input |
| Advanced | Host/port inputs, request timeout, failover map display |

**Widget decision matrix applied:**

| Setting | Type | Widget | Reasoning |
|---------|------|--------|-----------|
| PII Guard enabled | Boolean | `Switch` | 1 keystroke (Space) |
| Keyword Guard enabled | Boolean | `Switch` | 1 keystroke |
| Fast Guardian enabled | Boolean | `Switch` | 1 keystroke |
| PII entity types | Multi-select, 4-6 options | `Checkbox` group | Standard CUA multi-select |
| Blocked keywords | Free text | `Input` | Comma-separated entry |
| Threat threshold | Numeric | `Input` with validation | Continuous range 0.0–1.0 |
| API keys | Secret text | `Input` (password=True) | Masked entry |
| Log backend | Exclusive choice, 3 options | `RadioSet` | File / S3 / SQL — all visible |
| Host/port | Text/numeric | `Input` | Free-form with defaults |

**Apply strategy:** Changes are staged locally. The "Apply Changes" button
writes to `.env` and/or `config.yaml` and shows a modal: "Settings saved.
Restart the proxy for changes to take effect."

### 3.7 Advisor

LLM-powered operational diagnostics. The administrator types a
natural-language question and the advisor runs a tool-calling loop
against the proxy to gather data, then returns an answer grounded in
facts.

```
┌─ Advisor ────────────────────────────────────────────────────────────────┐
│                                                                          │
│  Model: [Auto (local preferred) ▼]                                       │
│                                                                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Advisor: claude-sonnet has a 38% error rate over 247 requests in the   │
│  last 24 hours. The errors are concentrated in the last 3 hours and     │
│  are all RateLimitError from Anthropic.                                  │
│                                                                          │
│  Recommendation: Add claude-haiku as a failover model.                   │
│                                                                          │
│  [dim]Tools used: get_model_profile, get_recent_errors[/dim]             │
│                                                                          │
├──────────────────────────────────────────────────────────────────────────┤
│  > ask a question...                                              [Ask]  │
└──────────────────────────────────────────────────────────────────────────┘
```

**Widgets:**

| Region | Widget | Data Source |
|--------|--------|-------------|
| Model selector | `Select` | `config.yaml` model_list, local models tagged |
| Output area | `Static` in `VerticalScroll` | `run_advisor()` result |
| Input | `Input` + `Button` | User question |

**Execution:** The advisor runs in a `@work(thread=True)` worker.
`run_advisor()` calls the proxy's `/v1/chat/completions` endpoint,
handles tool calls from `TOOL_REGISTRY`, and returns an `AdvisorResult`.
Results are posted back to the main thread via `call_from_thread`.

**Model selection:** Prefers local models (vLLM, Ollama) to avoid sending
operational data to remote providers. Displays a warning banner when a
remote model is used.

**Design doc:** `dev/feature-admin-advisor.md`

---

## 4. Keyboard Map

### Global Bindings (always active)

| Key | Action | Shown in Footer |
|-----|--------|-----------------|
| `1`–`9`, `0` | Jump to screen by number | Yes |
| `q` | Quit application | Yes |
| `?` | Help modal | Yes |
| `Ctrl+P` | Command palette | Yes |
| `Tab`/`Shift+Tab` | Move focus | Yes |
| `Esc` | Back / close modal / cancel | Yes |

### Screen-Specific Bindings

| Screen | Key | Action |
|--------|-----|--------|
| Dashboard | `r` | Force refresh |
| Models | `Enter` | Push model detail screen |
| Logs | `f` | Focus filter bar |
| Logs | `Enter` | Push log entry detail screen |
| Logs | `/` | Quick search in log entries |
| Analysis | `Enter` | Run analysis |
| Settings | `Ctrl+S` | Apply changes |
| Flow | `Space` | Pause/resume live stream |
| MCP Servers | `Enter` | Show server detail |
| Basic Chat | `Enter` | Send query (when focused on input) |

---

## 5. Color Semantics

Following the Cockpit Standard — 4 semantic colors max, no "Angry Fruit Salad":

| Token | Usage | Terminal Color |
|-------|-------|----------------|
| `$success` | Healthy, CLOSED, running, ✓ | Green |
| `$error` | Errors, OPEN, blocked, ✗ | Red |
| `$warning` | HALF_OPEN, warnings, elevated threat | Yellow |
| `$accent` | Focus borders, selected items, headers | Cyan |
| `$text` | Primary text | White/default |
| `$text-muted` | Secondary text, placeholders | Dim/grey |

---

## 6. Async Strategy

Every I/O operation uses `@work` to keep the UI thread alive:

| Operation | Method | Interval |
|-----------|--------|----------|
| Health probe | `@work(exclusive=True)` | 5s via `set_interval` |
| State store read | `@work` | 5s via `set_interval` |
| Log file read | `@work` | On screen enter + on filter change |
| Analysis run | `@work(exclusive=True, thread=True)` | On button press |
| Config write | `@work` | On "Apply" press |

---

## 7. File Structure

```
airlock/tui/
├── __init__.py
├── app.py                 # AirlockApp(App) — shell, sidebar, screen mounting
├── proxy_manager.py       # LiteLLM subprocess lifecycle
├── mcp_manager.py         # MCP server health and lifecycle
├── screens/
│   ├── __init__.py
│   ├── dashboard.py       # DashboardPane — proxy health, model overview
│   ├── models.py          # ModelsPane — circuit breaker, per-model metrics
│   ├── threats.py         # ThreatsPane — active backoffs, threat detection
│   ├── clients.py         # ClientsPane — per-client request rate, protection
│   ├── logs.py            # LogsPane — JSONL log browsing with filters
│   ├── analysis.py        # AnalysisPane — offline analysis and reports
│   ├── settings.py        # SettingsPane — config management (tabbed)
│   ├── flow.py            # FlowPane — real-time guardrail pipeline monitor
│   ├── mcp_servers.py     # McpServersPane — MCP server health, lifecycle, tools
│   └── chat.py            # ChatPane — interactive LLM connectivity testing
├── widgets/
│   ├── __init__.py
│   ├── status_indicator.py  # Colored dot + label (● Running)
│   ├── metric_card.py       # Bordered box with title + value
│   └── safe_data_table.py   # Thread-safe DataTable wrapper
└── styles/
    └── app.tcss             # All CSS in one file (~430 lines)
```

---

## 8. CLI Integration

Add `airlock tui` subcommand to `airlock/cli/main.py`:

```
airlock tui [--host localhost] [--port 4000]
```

The `--host` and `--port` flags configure which proxy instance to monitor (for
the health probe and status display). The TUI reads state from the in-process
`fast.state.store` singleton when running in the same process, or falls back to
HTTP probes for remote monitoring.

---

## 9. Dependencies

| Package | Purpose | Already in project? |
|---------|---------|---------------------|
| `textual` | TUI framework | No — new dependency |
| `pyyaml` | Config read/write | Yes |
| `python-dotenv` | .env read/write | Yes |

Single new dependency. Install via `pip install airlock-llm[tui]` optional extra.

---

## 10. Implementation Priority

| Phase | Screens | Status | Rationale |
|-------|---------|--------|-----------|
| 1 | Dashboard + Settings | Done | Operator can monitor and configure |
| 2 | Logs + Models + Threats | Done | Operator can investigate issues |
| 3 | Analysis + Clients | Done | Engineer deep-dive tools |
| 4 | Flow + MCP Servers | Done | Real-time pipeline visibility and MCP management |
| 5 | Basic Chat | Done | Interactive LLM connectivity testing |
| 6 | Advisor | Done | LLM-powered operational diagnostics |
