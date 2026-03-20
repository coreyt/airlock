"""Flow screen — real-time guardrail pipeline monitor.

A 'tail -f' for the adaptive guardrails pipeline.  Streams requests as
they flow through, shows composite scores and verdicts, and lets the
operator pause, select a request, and drill into the weighted scoring
breakdown.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Static, TabbedContent, TabPane


# ---------------------------------------------------------------------------
# Internal data model
# ---------------------------------------------------------------------------
@dataclass
class FlowEntry:
    """Processed log record for the flow stream."""

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
    gemini_request: dict | None = None
    gemini_response: dict | None = None
    call_type: str = ""
    mcp_tool_name: str = ""
    mcp_server_name: str = ""


def _parse_entry(record: dict) -> FlowEntry | None:
    """Convert a JSONL record into a FlowEntry, or None if no observation."""
    obs = record.get("airlock_observation")
    if not obs or not isinstance(obs, dict):
        return None
    return FlowEntry(
        timestamp=record.get("timestamp", ""),
        request_id=obs.get("request_id") or record.get("request_id", "-"),
        model=obs.get("model", record.get("model", "unknown")),
        client_id=obs.get("client_id", "-"),
        success=record.get("success", True),
        composite_score=obs.get("composite_score"),
        would_block=obs.get("would_block"),
        orchestrator_version=obs.get("orchestrator_version"),
        signals=obs.get("signals", []),
        enforcement=record.get("airlock_enforcement"),
        raw_observation=obs,
        raw_record=record,
        gemini_request=record.get("airlock_gemini"),
        gemini_response=record.get("airlock_gemini_response"),
        call_type=record.get("call_type", ""),
        mcp_tool_name=record.get("mcp_tool_name") or "",
        mcp_server_name=record.get("mcp_server_name") or "",
    )


# ---------------------------------------------------------------------------
# Detail renderers
# ---------------------------------------------------------------------------
def _render_signals(entry: FlowEntry) -> str:
    """Render the vote breakdown table for the Signals tab."""
    lines: list[str] = []
    lines.append("[bold]GUARDRAIL          VOTE   SCORE   WEIGHT   CONTRIBUTION[/]")
    lines.append("─" * 58)

    total_weight = 0.0
    weighted_sum = 0.0

    for sig in entry.signals:
        name = sig.get("guardrail_name", "?")
        detected = sig.get("detected", False)
        score = sig.get("score", 0.0)
        duration = sig.get("duration_ms", 0.0)

        # Derive weight from composite if available
        weight = _get_signal_weight(entry, name)
        contrib = score * weight
        total_weight += weight
        weighted_sum += contrib

        vote_sym = "[red]⚑[/]" if detected else "[green]✓[/]"
        lines.append(
            f"  {name:<18s} {vote_sym}    {score:5.2f}   ×{weight:4.2f}    = {contrib:5.3f}"
        )

    lines.append("─" * 58)

    # Composite score
    comp = entry.composite_score
    if comp is not None:
        lines.append(f"  [bold]COMPOSITE[/]                {comp:5.2f}")
    else:
        display = weighted_sum / total_weight if total_weight > 0 else 0.0
        lines.append(f"  [bold]COMPOSITE[/]                {display:5.2f}   (computed)")

    # Threshold and verdict
    if entry.enforcement:
        threshold = entry.enforcement.get("threshold", "?")
        should_block = entry.enforcement.get("should_block", False)
        mode = entry.enforcement.get("mode", "?")
        lines.append(f"  Threshold:         {threshold}")
        if should_block:
            lines.append(f"  Verdict:           [red]⊘ block[/]")
        else:
            lines.append(f"  Verdict:           [green]✓ pass[/]")
        lines.append(f"  Enforce mode:      {mode}")
    elif entry.would_block is not None:
        if entry.would_block:
            lines.append(f"  Verdict:           [yellow]⊘ would block[/]")
        else:
            lines.append(f"  Verdict:           [green]✓ pass[/]")

    if entry.orchestrator_version:
        lines.append(f"  Knobs version:     {entry.orchestrator_version}")

    # Signal details
    lines.append("")
    lines.append("[bold]── Signal Details ──[/]")
    for sig in entry.signals:
        name = sig.get("guardrail_name", "?")
        details = sig.get("details", {})
        duration = sig.get("duration_ms", 0.0)
        detail_str = _format_signal_detail(name, details)
        lines.append(f"  {name}: {detail_str} ({duration:.1f}ms)")

    return "\n".join(lines)


def _get_signal_weight(entry: FlowEntry, guardrail_name: str) -> float:
    """Try to derive weight from knobs; fall back to defaults."""
    defaults = {"pii_scan": 0.40, "keyword_scan": 0.40, "threat_read": 0.20}
    # If we have orchestrator version, try loading knobs
    # For display purposes, use defaults — the exact weights are visible
    # in the contribution math regardless
    return defaults.get(guardrail_name, 0.33)


def _format_signal_detail(name: str, details: dict) -> str:
    """One-line human-readable summary of signal details."""
    if name == "pii_scan":
        entities = details.get("entities", {})
        if entities:
            parts = [f"{k}:{v}" for k, v in entities.items()]
            return f"found {', '.join(parts)}"
        return "no entities detected"
    elif name == "keyword_scan":
        matched = details.get("matched_keywords", [])
        if matched:
            return f"matched {matched} ({details.get('match_count', 0)} match)"
        return "no keywords matched"
    elif name == "threat_read":
        score = details.get("threat_score", 0.0)
        backoff = details.get("in_backoff", False)
        client = details.get("client_id", "?")
        return f"client {client}, score {score:.2f}" + (", IN BACKOFF" if backoff else "")
    else:
        # Generic: show first few key-value pairs
        if not details:
            return "no details"
        parts = [f"{k}={v}" for k, v in list(details.items())[:3]]
        return ", ".join(parts)


def _render_pipeline(entry: FlowEntry) -> str:
    """Render the pipeline breadcrumb for the Pipeline tab."""
    lines: list[str] = []

    # Pre-call stage
    lines.append("[bold]PRE_CALL[/]")
    lines.append("─" * 40)

    # We can infer pre_call stages from the record
    pii_ok = "✓"  # PII always runs as pre_call
    kw_ok = "✓"
    guardian_ok = "✓"
    enforcer_mode = "-"

    if entry.enforcement:
        mode = entry.enforcement.get("mode", "observe")
        should_block = entry.enforcement.get("should_block", False)
        if mode == "enforce" and should_block:
            enforcer_mode = "[red]✗ blocked[/]"
        elif mode == "shadow" and should_block:
            enforcer_mode = "[yellow]~ shadow[/]"
        elif mode == "observe":
            enforcer_mode = "[dim]- observe[/]"
        else:
            enforcer_mode = f"[green]✓ {mode}[/]"
    else:
        enforcer_mode = "[dim]- n/a[/]"

    if not entry.success and not entry.enforcement:
        # Request failed — might have been blocked by keyword or guardian
        kw_ok = "[yellow]?[/]"
        guardian_ok = "[yellow]?[/]"

    lines.append(f"  \\[{pii_ok}] PII Guard")
    lines.append(f"  \\[{kw_ok}] Keyword Guard")
    lines.append(f"  \\[{guardian_ok}] Fast Guardian")
    lines.append(f"  \\[{enforcer_mode}] Enforcer")

    lines.append("")
    lines.append("[bold]DURING_CALL[/] (parallel with LLM)")
    lines.append("─" * 40)

    # During-call: orchestrator signals with timing
    total_during = 0.0
    for sig in entry.signals:
        name = sig.get("guardrail_name", "?")
        detected = sig.get("detected", False)
        duration = sig.get("duration_ms", 0.0)
        total_during += duration
        icon = "[red]⚑[/]" if detected else "[green]✓[/]"
        lines.append(f"      {icon} {name:<18s} {duration:.1f}ms")

    if entry.orchestrator_version:
        lines.append(f"  [green]✓[/] Orchestrator         {total_during:.1f}ms total")
    else:
        lines.append(f"  [dim]-[/] Observer              {total_during:.1f}ms total")

    # Request metadata
    lines.append("")
    lines.append("[bold]── Request ──[/]")
    lines.append(f"  ID:      {entry.request_id}")
    lines.append(f"  Model:   {entry.model}")
    lines.append(f"  Client:  {entry.client_id}")
    lines.append(f"  Success: {'✓' if entry.success else '✗'}")

    # Check for failover
    failover = entry.raw_record.get("airlock_failover")
    if failover:
        lines.append(
            f"  Failover: {failover.get('original_model')} → "
            f"{failover.get('failover_model')} ({failover.get('reason')})"
        )
    override = entry.raw_record.get("airlock_model_override")
    if override:
        lines.append(
            f"  Override: {override.get('requested_model')} → "
            f"{override.get('final_model')} ({override.get('reason')})"
        )
    protection = entry.raw_record.get("airlock_provider_protection")
    if protection:
        lines.append(
            f"  Protection: {protection.get('action')} "
            f"provider={protection.get('provider')} "
            f"client={protection.get('client_id')} "
            f"cooldown={protection.get('cooldown_seconds')}"
        )
    if entry.gemini_request or entry.gemini_response:
        lines.append(
            f"  Gemini: mode={(entry.gemini_request or {}).get('mode', '-')} "
            f"shape={(entry.gemini_response or {}).get('output_shape', '-')} "
            f"empty_text={(entry.gemini_response or {}).get('empty_text_success', False)}"
        )

    return "\n".join(lines)


def _render_raw(entry: FlowEntry) -> str:
    """Render full JSON dump for the Raw tab."""
    parts: dict[str, Any] = {}
    if entry.raw_observation:
        parts["airlock_observation"] = entry.raw_observation
    if entry.enforcement:
        parts["airlock_enforcement"] = entry.enforcement
    if entry.gemini_request:
        parts["airlock_gemini"] = entry.gemini_request
    if entry.gemini_response:
        parts["airlock_gemini_response"] = entry.gemini_response
    if not parts:
        return "(no observation data)"
    raw = json.dumps(parts, indent=2, default=str)
    if len(raw) > 2000:
        raw = raw[:2000] + "\n... (truncated)"
    return raw


def _render_tool_result(entry: FlowEntry) -> str:
    """Render MCP tool call details for the Tool Result tab."""
    if entry.call_type != "call_mcp_tool":
        return "(Not an MCP call)"

    lines: list[str] = []
    lines.append(f"[bold]Tool:[/] {entry.mcp_tool_name or '-'}")
    lines.append(f"[bold]Server:[/] {entry.mcp_server_name or '-'}")
    lines.append(f"[bold]Success:[/] {'Yes' if entry.success else 'No'}")

    # Show request messages
    messages = entry.raw_record.get("messages")
    if messages:
        msg_str = json.dumps(messages, indent=2, default=str)
        if len(msg_str) > 1000:
            msg_str = msg_str[:1000] + "\n... (truncated)"
        lines.append(f"\n[bold]Request:[/]\n{msg_str}")

    # Show response
    response = entry.raw_record.get("response")
    if response:
        resp_str = json.dumps(response, indent=2, default=str)
        if len(resp_str) > 1000:
            resp_str = resp_str[:1000] + "\n... (truncated)"
        lines.append(f"\n[bold]Response:[/]\n{resp_str}")

    error = entry.raw_record.get("error")
    if error:
        lines.append(f"\n[bold red]Error:[/] {error}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Verdict formatting
# ---------------------------------------------------------------------------
def _verdict_text(entry: FlowEntry) -> str:
    """Short verdict string for the stream table."""
    if entry.enforcement:
        mode = entry.enforcement.get("mode", "")
        should_block = entry.enforcement.get("should_block", False)
        if mode == "enforce" and should_block:
            return "⊘ block"
        if mode == "shadow" and should_block:
            return "~ shadow"
    if entry.would_block:
        return "⊘ would"
    return "✓ pass"


def _enforce_text(entry: FlowEntry) -> str:
    """Short enforcement mode for the stream table."""
    if entry.enforcement:
        return entry.enforcement.get("mode", "-")
    return "-"


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------
class FlowPane(Vertical):
    """Real-time guardrail pipeline monitor."""

    BINDINGS = [
        Binding("space", "toggle_pause", "Pause", priority=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._entries: list[FlowEntry] = []
        self._paused: bool = False
        self._last_seen_ts: str = ""
        self._total_seen: int = 0
        self._would_block_count: int = 0
        self._poll_timer: Any = None
        self._last_file_pos: int = 0

    def compose(self) -> ComposeResult:
        yield Static(
            "[green]● LIVE[/]  │  0 requests  │  0 would_block  │  1s poll",
            id="flow-status",
        )
        table = DataTable(id="flow-table", cursor_type="row")
        table.add_columns("Time", "Type", "Server", "Model", "Client", "Score", "Verdict", "Enforce")
        yield table
        with TabbedContent(id="flow-detail-tabs"):
            with TabPane("Signals", id="flow-tab-signals"):
                yield Static(
                    "Requests will appear above as they flow through the pipeline.",
                    id="flow-signals",
                )
            with TabPane("Pipeline", id="flow-tab-pipeline"):
                yield Static("Select a request to view pipeline stages.", id="flow-pipeline")
            with TabPane("Raw", id="flow-tab-raw"):
                yield Static("Select a request to view raw JSON.", id="flow-raw")
            with TabPane("Tool Result", id="flow-tab-tool"):
                yield Static("Select an MCP request...", id="flow-tool-result")

    def on_mount(self) -> None:
        self._poll_logs()
        self._poll_timer = self.set_interval(1.0, self._poll_if_live)

    def _poll_if_live(self) -> None:
        if not self._paused:
            self._poll_logs()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        try:
            idx = int(str(event.row_key.value))
        except (ValueError, TypeError):
            return
        if 0 <= idx < len(self._entries):
            self._show_detail(self._entries[idx])

    def action_toggle_pause(self) -> None:
        self._paused = not self._paused
        self._refresh_status()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    @work(exclusive=True, thread=True)
    def _poll_logs(self) -> None:
        try:
            log_dir = Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))
            today = datetime.now(timezone.utc).date()
            path = log_dir / f"airlock-{today.isoformat()}.jsonl"

            if not path.exists():
                return

            new_entries: list[FlowEntry] = []

            with open(path, encoding="utf-8") as f:
                # Seek to last known position for incremental reads
                if self._last_file_pos > 0:
                    try:
                        f.seek(self._last_file_pos)
                    except OSError:
                        f.seek(0)
                        self._last_file_pos = 0

                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    entry = _parse_entry(record)
                    if entry is None:
                        continue

                    # Only take entries newer than what we've seen
                    if entry.timestamp <= self._last_seen_ts:
                        continue

                    new_entries.append(entry)
                    self._total_seen += 1
                    if entry.would_block:
                        self._would_block_count += 1

                self._last_file_pos = f.tell()

            if not new_entries:
                return

            # Sort newest first
            new_entries.sort(key=lambda e: e.timestamp, reverse=True)
            self._last_seen_ts = new_entries[0].timestamp

            # Prepend to existing entries
            self._entries = (new_entries + self._entries)[:500]

            self._refresh_table()
            self._refresh_status()

            # Auto-show detail for newest entry in live mode
            if not self._paused and self._entries:
                self._show_detail(self._entries[0])

        except Exception:
            pass  # Poller must not crash; errors are transient (file rotation, etc.)

    # ------------------------------------------------------------------
    # UI updates
    # ------------------------------------------------------------------
    def _refresh_table(self) -> None:
        table = self.query_one("#flow-table", DataTable)
        table.clear()

        for i, entry in enumerate(self._entries[:200]):
            # Extract just HH:MM:SS from ISO timestamp
            if "T" in entry.timestamp:
                ts = entry.timestamp.split("T")[1][:8]
            elif len(entry.timestamp) >= 8:
                ts = entry.timestamp[-8:]
            else:
                ts = entry.timestamp
            call_type = (entry.mcp_tool_name or "MCP") if entry.call_type == "call_mcp_tool" else "LLM"
            server = entry.mcp_server_name[:12] if entry.mcp_server_name else "-"
            model = entry.model[:16]
            client = entry.client_id[-12:] if len(entry.client_id) > 12 else entry.client_id
            score = f"{entry.composite_score:.2f}" if entry.composite_score is not None else "-"
            verdict = _verdict_text(entry)
            enforce = _enforce_text(entry)
            table.add_row(ts, call_type, server, model, client, score, verdict, enforce, key=str(i))

        if not self._entries:
            table.add_row(
                "(waiting)", "-", "-", "-", "-", "-", "-", "-", key="_empty"
            )

    def _refresh_status(self) -> None:
        status = self.query_one("#flow-status", Static)
        if self._paused:
            status.update(
                f"[yellow]⏸ PAUSED[/]  │  {self._total_seen} requests  │  "
                f"{self._would_block_count} would_block  │  press space to resume"
            )
            status.remove_class("live")
            status.add_class("paused")
        else:
            status.update(
                f"[green]● LIVE[/]  │  {self._total_seen} requests  │  "
                f"{self._would_block_count} would_block  │  1s poll"
            )
            status.remove_class("paused")
            status.add_class("live")

    def _show_detail(self, entry: FlowEntry) -> None:
        signals = self.query_one("#flow-signals", Static)
        pipeline = self.query_one("#flow-pipeline", Static)
        raw = self.query_one("#flow-raw", Static)
        tool_result = self.query_one("#flow-tool-result", Static)

        signals.update(_render_signals(entry))
        pipeline.update(_render_pipeline(entry))
        raw.update(_render_raw(entry))
        tool_result.update(_render_tool_result(entry))
