# Screen 7: Flow — Real-Time Guardrail Pipeline Monitor

> **Status: Not implemented / roadmap.** The standalone Flow screen
> described here was never built. Live guardrail decisions are surfaced
> in the shipped **Guards** screen instead — see
> [docs/guide/tui.md](../docs/guide/tui.md).

## Purpose

A "tail -f" for the guardrail pipeline. Operators watch requests stream
through in real time, pause to inspect, and drill into the weighted
scoring breakdown for any individual request. When more classifiers are
added, this screen scales naturally — each new signal appears as another
row in the vote table.

---

## Information Architecture Decision

| Question | Answer | Rationale |
|----------|--------|-----------|
| Navigation depth? | **Breadth** — same level as other screens | It's a parallel context, not a drill-down from Logs |
| Stream vs. detail? | **Master-detail** within a single pane | Pause → select → detail. No push_screen needed. |
| Detail complexity? | **Tabs** inside the detail pane | Signals tab + Raw tab. Keeps detail scannable. |
| Transient actions? | **Inline** (pause/resume) | No modals — spacebar toggles, zero keystroke cost |

**Topology:** Cockpit Standard sidebar slot `7`. One screen, two zones
(stream + detail), detail has tabbed sub-views.

---

## Layout

```
┌─ Header ───────────────────────────────────────────────────────────┐
├──────────┬─────────────────────────────────────────────────────────┤
│ Sidebar  │  ┌─ Status Bar ──────────────────────────────────────┐  │
│          │  │ ● LIVE  │  142 requests  │  3 would_block  │ 1s  │  │
│ 1 Dash   │  └─────────────────────────────────────────────────────┘  │
│ 2 Models │  ┌─ Stream Table (2fr) ────────────────────────────────┐  │
│ 3 Threat │  │ Time       Model         Client     Score  Verdict  │  │
│ 4 Logs   │  │ 10:31:42   claude-sonnet key:..def  0.00   ✓ pass  │  │
│ 5 Analys │  │ 10:31:41   gpt-4o        key:..abc  0.82   ⊘ block │  │
│ 6 Settin │  │ 10:31:40   claude-haiku  key:..def  0.15   ✓ pass  │  │
│ 7 Flow   │  │ 10:31:39   claude-sonnet key:..xyz  0.00   ✓ pass  │  │
│          │  │ ▼ (auto-scroll — newest on top)                     │  │
│          │  └─────────────────────────────────────────────────────┘  │
│          │  ┌─ Detail Pane (1fr) ─────────────────────────────────┐  │
│          │  │ Signals │ Pipeline │ Raw                             │  │
│          │  │ ┌───────────────────────────────────────────────┐   │  │
│          │  │ │ GUARDRAIL      VOTE  SCORE  WEIGHT  CONTRIB   │   │  │
│          │  │ │ pii_scan        ✓     0.00   0.40    0.000   │   │  │
│          │  │ │ keyword_scan    ⚑     1.00   0.40    0.400   │   │  │
│          │  │ │ threat_read     ✓     0.20   0.20    0.040   │   │  │
│          │  │ │─────────────────────────────────────────────── │   │  │
│          │  │ │ COMPOSITE              0.44   threshold: 0.50 │   │  │
│          │  │ │ VERDICT                ✓ pass (below threshold)│   │  │
│          │  │ │ ENFORCEMENT            shadow — would_block    │   │  │
│          │  │ │ KNOBS VERSION          2024-01-15T10:00:00Z    │   │  │
│          │  │ └───────────────────────────────────────────────┘   │  │
│          │  └─────────────────────────────────────────────────────┘  │
├──────────┴─────────────────────────────────────────────────────────┤
│ Footer:  space Pause  ↑↓ Select  7 Flow  q Quit                   │
└────────────────────────────────────────────────────────────────────┘
```

---

## Widget Inventory

| Zone | Widget | ID | Purpose |
|------|--------|----|---------|
| Status bar | `Static` | `#flow-status` | Live/Paused indicator + counters |
| Stream | `DataTable` | `#flow-table` | Tail of recent requests (row cursor) |
| Detail | `TabbedContent` | `#flow-detail-tabs` | Signals / Pipeline / Raw |
| Detail > Signals | `Static` | `#flow-signals` | Vote table with weighted breakdown |
| Detail > Pipeline | `Static` | `#flow-pipeline` | Pipeline stage breadcrumb |
| Detail > Raw | `Static` | `#flow-raw` | Full JSON observation dump |

**Widget selection rationale (per Decision Matrix):**

- Stream → `DataTable` with `cursor_type="row"`: keyboard-navigable,
  supports highlighting, proven pattern in Logs/Models screens.
- Status bar → `Static`: read-only, updated every poll cycle. No input
  needed. A Label would also work but Static supports rich text for the
  colored dot.
- Detail tabs → `TabbedContent`: parallel sub-contexts (signals vs raw
  vs pipeline). IBM CUA: tabs for parallel views within a context.
- Vote table → `Static` with formatted text, not a nested DataTable.
  Rationale: the signal list is small (3-10 items), doesn't need
  scrolling or cursor. Rich text formatting (colors, alignment) is
  simpler in Static. If we ever exceed ~15 signals, promote to DataTable.

---

## Zones

### Zone 1: Status Bar

A single-line `Static` at the top that shows stream state at a glance.

**States:**

| State | Display | Color |
|-------|---------|-------|
| Live | `● LIVE` | `$success` (green) |
| Paused | `⏸ PAUSED` | `$warning` (yellow) |

**Counters** (updated each poll):
- Total requests seen this session
- Count with `would_block == True`
- Poll interval (e.g., "1s")

**Format:**
```
● LIVE  │  142 requests  │  3 would_block  │  1s poll
```

When paused:
```
⏸ PAUSED  │  142 requests  │  3 would_block  │  press space to resume
```

---

### Zone 2: Stream Table

`DataTable` with `cursor_type="row"`, newest entries at the top.

**Columns:**

| Column | Width | Source | Format |
|--------|-------|--------|--------|
| Time | 8 | `timestamp` | `HH:MM:SS` |
| Model | 16 | `model` | truncated |
| Client | 12 | `airlock_observation.client_id` | last 8 chars |
| Score | 6 | `composite_score` | `0.44` or `-` |
| Verdict | 10 | derived | `✓ pass` / `⊘ block` / `~ shadow` / `- n/a` |
| Enforce | 8 | `airlock_enforcement.mode` | `observe` / `shadow` / `enforce` / `-` |

**Verdict derivation:**
- If `airlock_enforcement` exists and `mode == "enforce"` and `should_block`: `⊘ block`
- If `airlock_enforcement` exists and `mode == "shadow"` and `should_block`: `~ shadow`
- If `airlock_observation` exists and `would_block`: `⊘ would`
- Otherwise: `✓ pass`

**Behavior:**
- **Live mode:** Auto-scrolls. New rows inserted at the top. Table cursor
  stays at top. Max 200 rows displayed (oldest pruned).
- **Paused mode:** No new rows. Cursor freely navigable. Row highlight
  triggers detail pane update.
- **Color coding:** Rows with `would_block == True` get `$error` styling
  on the Verdict cell. Shadow blocks get `$warning`.

---

### Zone 3: Detail Pane (Tabbed)

Three tabs inside a `TabbedContent`:

#### Tab 1: Signals (default)

The vote breakdown — the core of the screen. Each guardrail signal gets
a row showing its vote, score, weight, and contribution to the composite.

```
┌─ Signals ────────────────────────────────────────────────────┐
│                                                              │
│  GUARDRAIL       VOTE   SCORE   WEIGHT   CONTRIBUTION        │
│  ─────────────────────────────────────────────────────────    │
│  pii_scan         ✓     0.00    ×0.40    = 0.000             │
│  keyword_scan     ⚑     1.00    ×0.40    = 0.400             │
│  threat_read      ✓     0.20    ×0.20    = 0.040             │
│  ─────────────────────────────────────────────────────────    │
│  COMPOSITE               0.44            (sum / Σweights)    │
│                                                              │
│  Threshold:  0.50                                            │
│  Verdict:    ✓ pass  (0.44 < 0.50)                           │
│  Enforce:    shadow — logged, not blocked                    │
│  Knobs:      2024-01-15T10:00:00Z                            │
│                                                              │
│  ── Signal Details ──────────────────────────────────────     │
│  keyword_scan: matched ["forbidden"] (1 match)               │
│  pii_scan: no entities detected                              │
│  threat_read: client key:..def, score 0.20, no backoff       │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Vote symbols:**
- `✓` — not detected (score < 0.5, green)
- `⚑` — detected (score >= 0.5, red)

**Signal Details** section: Expands the `details` dict from each signal
into a human-readable one-liner. This is where guardrail-specific info
surfaces (which keywords matched, which PII entities found, threat score
breakdown). Scales naturally — each new guardrail type adds one detail
line.

#### Tab 2: Pipeline

A horizontal breadcrumb showing which pipeline stages the request passed
through, with timing for each.

```
┌─ Pipeline ───────────────────────────────────────────────────┐
│                                                              │
│  PRE_CALL                           DURING_CALL              │
│  ───────────────────────────────    ──────────────────────    │
│  [✓] PII Guard          0.8ms      [✓] Orchestrator  1.2ms  │
│  [✓] Keyword Guard      0.1ms          pii_scan      0.3ms  │
│  [✓] Fast Guardian      0.3ms          keyword_scan  0.1ms  │
│  [✓] Enforcer (observe) 0.0ms          threat_read   0.2ms  │
│                                                              │
│  Total pre_call: 1.2ms                                       │
│  Total during_call: 1.2ms (parallel with LLM)                │
│                                                              │
│  Request: req-abc-123                                        │
│  Model: claude-sonnet → (no failover)                        │
│  Client: key:..def                                           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Stage indicators:**
- `[✓]` — passed (green)
- `[✗]` — blocked (red)
- `[~]` — shadow block (yellow)
- `[-]` — skipped / observe mode

The pipeline data comes from combining:
- `airlock_observation.signals` (during_call timing)
- `airlock_enforcement` (enforcer mode + decision)
- `success` field (did the request ultimately succeed?)
- `airlock_priority` and `airlock_failover` (if present in metadata)

#### Tab 3: Raw

Full JSON dump of the observation and enforcement metadata, pretty-printed.
For debugging and for data that doesn't fit the structured views.

```
┌─ Raw ────────────────────────────────────────────────────────┐
│ {                                                            │
│   "request_id": "req-abc-123",                               │
│   "model": "claude-sonnet",                                  │
│   "client_id": "key:..def",                                  │
│   "signals": [                                               │
│     {                                                        │
│       "guardrail_name": "pii_scan",                          │
│       "detected": false,                                     │
│       "score": 0.0,                                          │
│       ...                                                    │
│   ],                                                         │
│   "composite_score": 0.44,                                   │
│   "would_block": false,                                      │
│   "orchestrator_version": "2024-01-15T10:00:00Z"             │
│ }                                                            │
└──────────────────────────────────────────────────────────────┘
```

---

## Interaction Model

### Keyboard Bindings (Screen-Level)

| Key | Action | Context |
|-----|--------|---------|
| `space` | Toggle pause/resume | Always |
| `↑` / `↓` | Navigate rows | When paused (table has focus) |
| `Tab` | Move focus (table → detail tabs) | Always |
| `Enter` | Activate tab | When detail tabs focused |

**No screen-specific single-letter bindings** beyond `space` — avoids
conflict with the app-level `1`-`7` screen switchers.

### State Machine

```
                 space              space
  ┌──────────┐ ────────► ┌──────────┐ ────────► ┌──────────┐
  │   LIVE   │           │  PAUSED  │           │   LIVE   │
  │ (polling)│ ◄──────── │ (frozen) │ ◄──────── │ (polling)│
  └──────────┘   space   └──────────┘   space   └──────────┘
       │                      │
       │ on_mount              │ row_highlighted
       │ set_interval(1s)      │ → _show_detail()
       ▼                      ▼
   _poll_logs()          Detail pane updates
   _refresh_status()     with selected record
```

**Live mode:**
- 1-second poll interval (faster than other screens' 5s — this is a
  real-time monitor)
- Table auto-scrolls to top (newest first)
- Row cursor stays at row 0
- Detail pane shows the most recent entry

**Paused mode:**
- Poll stops — no new rows added
- User navigates freely with arrow keys
- `on_data_table_row_highlighted` → `_show_detail(record)`
- Status bar turns yellow with `⏸ PAUSED` and hint text

---

## Data Flow

### Polling Strategy

```
on_mount()
  ├── _poll_logs()         # Initial load + subsequent polls
  └── set_interval(1.0)   # 1s polling in live mode

_poll_logs()  [@work(exclusive=True, thread=True)]
  ├── Read JSONL files (today only — tail behavior)
  ├── Filter: only records with airlock_observation key
  ├── Track last-seen timestamp to avoid re-processing
  ├── Prepend new records to _records (newest first)
  ├── Cap at 500 in memory, 200 in table
  └── If not paused: _refresh_table() + _refresh_status()
```

**Incremental loading:** Unlike the Logs screen which loads all records
on mount, this screen tracks a `_last_seen_ts` watermark. Each poll only
reads lines newer than the watermark. This makes 1-second polling cheap.

**File watching:** Reads only today's file (`airlock-YYYY-MM-DD.jsonl`).
Seeks to end on first load, then reads new lines on each poll. Falls back
to reading the whole file if seek position is invalid (file rotation).

### Record Processing

Each JSONL record is processed into a `FlowEntry` (internal dataclass):

```python
@dataclass
class FlowEntry:
    timestamp: str
    request_id: str
    model: str
    client_id: str
    success: bool
    composite_score: float | None
    would_block: bool | None
    orchestrator_version: str | None
    signals: list[dict]
    enforcement: dict | None
    raw_observation: dict | None
    raw_record: dict
```

---

## TCSS Additions

```css
/* ── Flow ──────────────────────────────────────────── */

#flow-status {
    height: 3;
    border: solid $accent;
    padding: 0 2;
    content-align: left middle;
}

#flow-status.live {
    border: solid $success;
}

#flow-status.paused {
    border: solid $warning;
}

#flow-table {
    height: 2fr;
    border: solid $accent;
    margin-top: 1;
}

#flow-detail-tabs {
    height: 1fr;
    margin-top: 1;
}

.vote-detected {
    color: $error;
}

.vote-clean {
    color: $success;
}

.score-high {
    color: $error;
    text-style: bold;
}
```

---

## File Inventory

| File | Purpose |
|------|---------|
| `airlock/tui/screens/flow.py` | `FlowPane(Vertical)` — screen implementation |
| `airlock/tui/styles/app.tcss` | Append Flow CSS rules |
| `airlock/tui/app.py` | Add screen 7 to `_SCREENS`, bindings, compose |
| `tests/test_tui.py` | Add Flow screen tests |

---

## Scalability Notes

**Adding a new guardrail classifier:**

1. The classifier writes a `GuardrailSignal` to the observation's
   `signals` list (existing contract from `schemas.py`).
2. The Flow screen's Signals tab renders every signal in the list — no
   code change needed. A new row appears automatically in the vote table.
3. The knobs file gets a new weight entry. The weighted contribution
   column shows it immediately.
4. Pipeline tab: add the new classifier to the appropriate stage
   (pre_call or during_call). This is the only manual touch — a one-line
   addition to the stage mapping dict.

**The vote table is the key scalability mechanism.** It's a data-driven
rendering of `observation.signals` — not a hardcoded list of known
guardrails. Ten classifiers voting produces a ten-row table with the same
layout. The composite score math stays transparent: each row shows
`score × weight = contribution`, and the total is the sum divided by
total weight.
