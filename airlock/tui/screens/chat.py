"""Basic Chat screen — test LLM connectivity and interaction.

Provides a split-pane interface for administrators to send test prompts
to any configured model and inspect the full request/response cycle.

Layout (named like quadrants on a line graph):

    Q2  User Query        │  Q1  Response Content
    ──────────────────────┼──────────────────────
    Q3  Request Sent      │  Q4  Response Received
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import yaml

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_models_from_config() -> list[dict]:
    """Read model_list from config.yaml."""
    config_path = os.getenv("AIRLOCK_CONFIG", "config.yaml")
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("model_list", [])
    except Exception:
        return []


def _extract_providers(model_list: list[dict]) -> list[tuple[str, str]]:
    """Derive unique (display_name, value) provider pairs from model entries.

    The display name shows the litellm provider prefix.  For models using
    ``openai/`` with a custom ``api_base`` (vLLM, Ollama, etc.) the display
    name includes the model_name in parentheses so the user can distinguish
    local endpoints from OpenAI proper.
    """
    providers: dict[str, str] = {}  # value -> display
    for entry in model_list:
        params = entry.get("litellm_params") or {}
        model_str = params.get("model", "")
        if "/" not in model_str:
            continue
        prefix = model_str.split("/", 1)[0]
        if prefix not in providers:
            providers[prefix] = prefix
    return sorted(providers.items(), key=lambda t: t[0])


def _models_for_provider(model_list: list[dict], provider: str) -> list[str]:
    """Return model_name values whose provider prefix matches."""
    if provider == "all":
        return [e["model_name"] for e in model_list if "model_name" in e]
    result = []
    for entry in model_list:
        params = entry.get("litellm_params") or {}
        model_str = params.get("model", "")
        if "/" in model_str and model_str.split("/", 1)[0] == provider:
            result.append(entry["model_name"])
    return result


# ---------------------------------------------------------------------------
# JSON formatting helpers
# ---------------------------------------------------------------------------

def _fmt_json(obj: Any) -> str:
    """Pretty-print a dict/list as indented JSON."""
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _fmt_request(method: str, url: str, headers: dict, body: dict) -> str:
    """Format the outgoing request for display."""
    lines = [
        f"[bold]POST[/] {url}",
        "",
        "[bold]Headers[/]",
    ]
    for k, v in headers.items():
        # Mask auth tokens
        display_v = v
        if k.lower() == "authorization" and len(v) > 20:
            display_v = v[:15] + "..." + v[-4:]
        lines.append(f"  {k}: {display_v}")
    lines.append("")
    lines.append("[bold]Body[/]")
    lines.append(_fmt_json(body))
    return "\n".join(lines)


def _fmt_response(status: int, headers: list[tuple[str, str]], body: str) -> str:
    """Format the incoming response for display."""
    lines = [
        f"[bold]HTTP {status}[/]",
        "",
        "[bold]Headers[/]",
    ]
    for k, v in headers:
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("[bold]Body[/]")
    # Try to pretty-print if it's JSON
    try:
        parsed = json.loads(body)
        lines.append(_fmt_json(parsed))
    except (json.JSONDecodeError, TypeError):
        lines.append(body[:4000])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default parameters
# ---------------------------------------------------------------------------

_DEFAULT_PARAMS = {
    "temperature": 0.7,
    "max_tokens": 1024,
}


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class ChatPane(Vertical):
    """Basic Chat — test LLM connectivity and interaction."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model_list: list[dict] = []
        self._params: dict = dict(_DEFAULT_PARAMS)

    def compose(self) -> ComposeResult:
        # ── Top control bar ──────────────────────────────
        with Horizontal(id="chat-controls"):
            yield Label("Provider", classes="chat-label")
            yield Select(
                [("(loading)", "")],
                value="",
                allow_blank=False,
                id="chat-provider-select",
            )
            yield Label("Model", classes="chat-label")
            yield Select(
                [("(loading)", "")],
                value="",
                allow_blank=False,
                id="chat-model-select",
            )
            yield Label("Params", classes="chat-label")
            yield Input(
                value=json.dumps(self._params),
                id="chat-params-input",
            )
            yield Button("Builder", id="chat-builder-btn", variant="default")
            yield Button("Send", id="chat-send-btn", variant="success")

        # ── Four quadrants ───────────────────────────────
        # Top row:  Q2 (user query)  |  Q1 (response content)
        # Bot row:  Q3 (request out) |  Q4 (response in)
        with Horizontal(id="chat-top-row"):
            with Vertical(id="chat-q2"):
                yield Static("[bold]User Query[/]", classes="quadrant-header")
                yield TextArea(
                    "",
                    language="markdown",
                    id="chat-user-input",
                )
            with VerticalScroll(id="chat-q1"):
                yield Static("[bold]Response Content[/]", classes="quadrant-header")
                yield Static(
                    "[dim]Response will appear here...[/]",
                    id="chat-response-content",
                )
        with Horizontal(id="chat-bottom-row"):
            with VerticalScroll(id="chat-q3"):
                yield Static("[bold]Request Sent[/]", classes="quadrant-header")
                yield Static(
                    "[dim]Request details will appear here...[/]",
                    id="chat-request-detail",
                )
            with VerticalScroll(id="chat-q4"):
                yield Static("[bold]Response Received[/]", classes="quadrant-header")
                yield Static(
                    "[dim]Raw response will appear here...[/]",
                    id="chat-response-detail",
                )

    def on_mount(self) -> None:
        self._load_config()

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        self._model_list = _load_models_from_config()
        provider_pairs = _extract_providers(self._model_list)

        provider_select = self.query_one("#chat-provider-select", Select)
        options: list[tuple[str, str]] = [("All", "all")]
        options.extend((display, value) for value, display in provider_pairs)
        provider_select.set_options(options)
        provider_select.value = "all"

        self._refresh_model_options("all")

    def _refresh_model_options(self, provider: str) -> None:
        models = _models_for_provider(self._model_list, provider)
        model_select = self.query_one("#chat-model-select", Select)
        if models:
            model_select.set_options([(m, m) for m in models])
            model_select.value = models[0]
        else:
            model_select.set_options([("(none)", "")])
            model_select.value = ""

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "chat-provider-select":
            value = event.value
            if value is None or value is Select.BLANK:
                return
            self._refresh_model_options(str(value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "chat-send-btn":
            self._send_request()
        elif event.button.id == "chat-builder-btn":
            self._show_param_builder()
        elif event.button.id == "chat-builder-apply":
            self._apply_builder_params()
        elif event.button.id == "chat-builder-cancel":
            self._dismiss_builder()

    # ------------------------------------------------------------------
    # Parameter builder modal
    # ------------------------------------------------------------------

    def _show_param_builder(self) -> None:
        """Mount the parameter builder overlay."""
        # Don't duplicate if already open
        existing = self.query("#chat-builder-overlay")
        if existing:
            return

        # Read current params
        try:
            current = json.loads(
                self.query_one("#chat-params-input", Input).value
            )
        except (json.JSONDecodeError, TypeError):
            current = dict(_DEFAULT_PARAMS)

        overlay = Vertical(
            Static("[bold]Parameter Builder[/]", classes="builder-title"),
            Label("temperature"),
            Input(
                value=str(current.get("temperature", 0.7)),
                id="builder-temperature",
            ),
            Label("max_tokens"),
            Input(
                value=str(current.get("max_tokens", 1024)),
                id="builder-max-tokens",
            ),
            Label("top_p"),
            Input(
                value=str(current.get("top_p", "")),
                id="builder-top-p",
            ),
            Label("top_k"),
            Input(
                value=str(current.get("top_k", "")),
                id="builder-top-k",
            ),
            Label("stop (comma-separated)"),
            Input(
                value=",".join(current.get("stop", [])) if isinstance(current.get("stop"), list) else str(current.get("stop", "")),
                id="builder-stop",
            ),
            Label("system prompt"),
            Input(
                value=str(current.get("system", "")),
                id="builder-system",
            ),
            Horizontal(
                Button("Apply", id="chat-builder-apply", variant="success"),
                Button("Cancel", id="chat-builder-cancel", variant="error"),
                classes="builder-buttons",
            ),
            id="chat-builder-overlay",
        )
        self.mount(overlay)

    def _apply_builder_params(self) -> None:
        """Read builder inputs and update the params field."""
        params: dict[str, Any] = {}

        temp = self.query_one("#builder-temperature", Input).value.strip()
        if temp:
            try:
                params["temperature"] = float(temp)
            except ValueError:
                pass

        max_tok = self.query_one("#builder-max-tokens", Input).value.strip()
        if max_tok:
            try:
                params["max_tokens"] = int(max_tok)
            except ValueError:
                pass

        top_p = self.query_one("#builder-top-p", Input).value.strip()
        if top_p:
            try:
                params["top_p"] = float(top_p)
            except ValueError:
                pass

        top_k = self.query_one("#builder-top-k", Input).value.strip()
        if top_k:
            try:
                params["top_k"] = int(top_k)
            except ValueError:
                pass

        stop = self.query_one("#builder-stop", Input).value.strip()
        if stop:
            params["stop"] = [s.strip() for s in stop.split(",") if s.strip()]

        system = self.query_one("#builder-system", Input).value.strip()
        if system:
            params["system"] = system

        self._params = params
        self.query_one("#chat-params-input", Input).value = json.dumps(params)
        self._dismiss_builder()

    def _dismiss_builder(self) -> None:
        """Remove the builder overlay."""
        overlay = self.query("#chat-builder-overlay")
        if overlay:
            overlay.first().remove()

    # ------------------------------------------------------------------
    # Send request
    # ------------------------------------------------------------------

    def _send_request(self) -> None:
        model_select = self.query_one("#chat-model-select", Select)
        model = str(model_select.value)
        if not model:
            return

        user_input = self.query_one("#chat-user-input", TextArea)
        query_text = user_input.text.strip()
        if not query_text:
            q1 = self.query_one("#chat-response-content", Static)
            q1.update("[yellow]Enter a query in the User Query pane first.[/]")
            return

        # Read params
        try:
            params = json.loads(
                self.query_one("#chat-params-input", Input).value
            )
        except (json.JSONDecodeError, TypeError):
            params = {}

        # Show "Sending..." in Q1 (Response Content)
        q1 = self.query_one("#chat-response-content", Static)
        q1.update("[dim]Sending...[/]")

        # Disable send button while in flight
        send_btn = self.query_one("#chat-send-btn", Button)
        send_btn.disabled = True
        send_btn.label = "Sending..."

        self._do_send(model, query_text, params)

    @work(exclusive=True, thread=True, group="chat-send")
    def _do_send(self, model: str, query_text: str, params: dict) -> None:
        """Send the chat completion request on a worker thread."""
        host = os.getenv("AIRLOCK_HOST", "0.0.0.0")
        port = os.getenv("AIRLOCK_PORT", "4000")
        master_key = os.getenv("AIRLOCK_MASTER_KEY", "")

        # Build request URL
        # Connect via localhost even if proxy binds 0.0.0.0
        connect_host = "127.0.0.1" if host == "0.0.0.0" else host
        url = f"http://{connect_host}:{port}/v1/chat/completions"

        # Build messages — copy params so .pop() doesn't mutate caller's dict
        send_params = dict(params)
        messages = []
        system_prompt = send_params.pop("system", None)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": query_text})

        # Build body
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        body.update(send_params)

        # Build headers
        headers = {"Content-Type": "application/json"}
        if master_key:
            headers["Authorization"] = f"Bearer {master_key}"

        # Format request for Q3 display
        request_display = _fmt_request("POST", url, headers, body)

        # Show request in Q3 immediately (before sending)
        def _show_request() -> None:
            q3 = self.query_one("#chat-request-detail", Static)
            q3.update(request_display)
            q4 = self.query_one("#chat-response-detail", Static)
            q4.update("[dim]Waiting for response...[/]")

        self.app.call_from_thread(_show_request)

        body_bytes = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=headers,
            method="POST",
        )

        response_content = ""
        response_display = ""
        error_msg = ""

        try:
            resp = urllib.request.urlopen(req, timeout=300)
            status_code = resp.status
            resp_headers = list(resp.getheaders())
            resp_body = resp.read().decode("utf-8", errors="replace")

            response_display = _fmt_response(status_code, resp_headers, resp_body)

            # Extract content text from response
            try:
                parsed = json.loads(resp_body)
                choices = parsed.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    response_content = msg.get("content", "")
                    if not response_content:
                        response_content = _fmt_json(msg)
                else:
                    response_content = "(no choices in response)"

                # Append usage info
                usage = parsed.get("usage")
                if usage:
                    response_content += (
                        f"\n\n[dim]── usage ──\n"
                        f"prompt_tokens: {usage.get('prompt_tokens', '?')}\n"
                        f"completion_tokens: {usage.get('completion_tokens', '?')}\n"
                        f"total_tokens: {usage.get('total_tokens', '?')}[/]"
                    )
            except (json.JSONDecodeError, TypeError):
                response_content = resp_body[:2000]

        except urllib.error.HTTPError as exc:
            status_code = exc.code
            resp_headers = list(exc.headers.items())
            resp_body = exc.read().decode("utf-8", errors="replace")
            response_display = _fmt_response(status_code, resp_headers, resp_body)
            error_msg = f"[red]HTTP {status_code}[/]\n\n"
            try:
                parsed = json.loads(resp_body)
                error_msg += _fmt_json(parsed)
            except (json.JSONDecodeError, TypeError):
                error_msg += resp_body[:2000]

        except Exception as exc:
            response_display = f"[red]Connection Error[/]\n\n{exc}"
            error_msg = f"[red]Error:[/] {exc}"

        # Update Q1 and Q4 from main thread
        def _update_ui() -> None:
            q1 = self.query_one("#chat-response-content", Static)
            q4 = self.query_one("#chat-response-detail", Static)
            send_btn = self.query_one("#chat-send-btn", Button)

            q4.update(response_display)

            if error_msg:
                q1.update(error_msg)
            else:
                q1.update(response_content or "[dim](empty response)[/]")

            send_btn.disabled = False
            send_btn.label = "Send"

        self.app.call_from_thread(_update_ui)
