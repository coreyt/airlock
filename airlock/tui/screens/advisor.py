"""Advisor — ask LLMs about Airlock operational data."""

from __future__ import annotations

import os

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Input,
    Label,
    Select,
    Static,
)

from airlock.advisor.agent import AdvisorResult, run_advisor
from airlock.advisor.model_select import is_local_model

_ADVISOR_DOCSTRING = """Advisor — ask LLMs about Airlock operational data.

Provides a chat-style interface for administrators to query an LLM
about Airlock operational state.  The advisor selects a model (local
preferred), runs a tool-calling loop against the proxy, and displays
the answer with any proposed config changes.
"""


def _load_models_from_config() -> list[dict]:
    """Read model_list from config.yaml."""
    import yaml

    config_path = os.getenv("AIRLOCK_CONFIG", "config.yaml")
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("model_list", [])
    except Exception:
        return []


def _get_model_choices(model_list: list[dict]) -> list[tuple[str, str]]:
    """Build (display_name, value) pairs for model selector."""
    choices: list[tuple[str, str]] = [("Auto (local preferred)", "auto")]
    for entry in model_list:
        name = entry.get("model_name", "")
        if name:
            local_tag = " [local]" if is_local_model(entry) else ""
            choices.append((f"{name}{local_tag}", name))
    return choices


class AdvisorPane(Vertical):
    """Advisor screen — ask questions about Airlock operational data."""

    def compose(self) -> ComposeResult:
        model_list = _load_models_from_config()
        choices = _get_model_choices(model_list)

        yield Label("Advisor", id="advisor-title")

        yield Select(
            choices,
            id="advisor-model-select",
            value="auto",
        )

        with VerticalScroll(id="advisor-output-scroll"):
            yield Static(
                "Ask a question about Airlock operational data.",
                id="advisor-output",
            )

        yield Input(
            placeholder="Ask a question...",
            id="advisor-input",
        )
        yield Button("Ask", id="advisor-submit", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "advisor-submit":
            self._submit_question()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "advisor-input":
            self._submit_question()

    def _submit_question(self) -> None:
        input_widget = self.query_one("#advisor-input", Input)
        question = input_widget.value.strip()
        if not question:
            return

        input_widget.value = ""
        output = self.query_one("#advisor-output", Static)
        output.update("Thinking...")

        # Get selected model
        model_select = self.query_one("#advisor-model-select", Select)
        model = None if model_select.value == "auto" else str(model_select.value)

        self._run_advisor(question, model)

    @work(thread=True)
    def _run_advisor(self, question: str, model: str | None) -> None:
        """Run advisor in background thread."""
        try:
            app = self.app
            host = getattr(app, "_proxy_host", "localhost")
            port = getattr(app, "_proxy_port", "4000")

            result = run_advisor(
                question,
                proxy_host=host,
                proxy_port=port,
                model=model,
            )

            self.app.call_from_thread(self._display_result, result)
        except Exception as e:
            self.app.call_from_thread(self._display_error, str(e))

    def _display_result(self, result: AdvisorResult) -> None:
        output = self.query_one("#advisor-output", Static)
        if result.error:
            output.update(f"Error: {result.error}")
            return

        text = result.answer
        if result.tool_calls_made:
            text += f"\n\n[dim]Tools used: {', '.join(result.tool_calls_made)}[/dim]"
        if not result.is_local:
            text = (
                "[bold yellow]WARNING: Remote model used. "
                "Operational data was sent externally.[/bold yellow]\n\n" + text
            )

        output.update(text)

    def _display_error(self, error: str) -> None:
        output = self.query_one("#advisor-output", Static)
        output.update(f"[bold red]Error:[/bold red] {error}")
