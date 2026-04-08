"""Test — send diagnostic requests through the proxy to verify connectivity and diagnose issues reported by users.

Provides a split-pane interface for administrators to send test prompts
to any configured model and inspect the full request/response cycle,
with airlock-specific header highlighting for diagnostics.

Layout (named like quadrants on a line graph):

    Q2  User Query        |  Q1  Response Content
    ----------------------+----------------------
    Q3  Request Sent      |  Q4  Response Received
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from rich.markup import escape as _esc

import yaml

from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)

from airlock.tui.param_schemas import (
    defaults_for_schema,
    get_schema,
)


# ---------------------------------------------------------------------------
# Airlock diagnostic headers
# ---------------------------------------------------------------------------

_AIRLOCK_HEADERS = {
    "x-airlock-client",
    "x-airlock-model-override",
    "x-airlock-provider-mode",
    "x-airlock-provider-state",
    "x-airlock-failover",
}


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


def _is_local_model(entry: dict) -> bool:
    """Return True if a model entry points at a local/self-hosted endpoint.

    Models that use a custom ``api_base`` (vLLM, Ollama, OpenLLaMA, etc.)
    are considered local -- they run on infrastructure the operator controls
    rather than a cloud provider's API.
    """
    params = entry.get("litellm_params") or {}
    return bool(params.get("api_base"))


def _extract_providers(model_list: list[dict]) -> list[tuple[str, str]]:
    """Derive unique (display_name, value) provider pairs from model entries.

    Models with a custom ``api_base`` (vLLM, Ollama, etc.) are grouped
    under a ``local`` provider so the user can easily find self-hosted
    models.  Cloud-hosted models are grouped by their litellm prefix
    (``anthropic``, ``openai``, ``gemini``, ...).
    """
    providers: dict[str, str] = {}  # value -> display
    for entry in model_list:
        params = entry.get("litellm_params") or {}
        model_str = params.get("model", "")
        if "/" not in model_str:
            continue
        if _is_local_model(entry):
            if "local" not in providers:
                providers["local"] = "local"
        else:
            prefix = model_str.split("/", 1)[0]
            if prefix not in providers:
                providers[prefix] = prefix
    return sorted(providers.items(), key=lambda t: t[0])


def _models_for_provider(model_list: list[dict], provider: str) -> list[str]:
    """Return model_name values whose provider prefix matches.

    The special ``local`` provider matches any model with a custom
    ``api_base`` regardless of its litellm prefix.
    """
    if provider == "all":
        return [e["model_name"] for e in model_list if "model_name" in e]
    result = []
    for entry in model_list:
        params = entry.get("litellm_params") or {}
        model_str = params.get("model", "")
        if "/" not in model_str:
            continue
        if provider == "local":
            if _is_local_model(entry):
                result.append(entry["model_name"])
        else:
            # Only match cloud models (no custom api_base) by prefix
            if not _is_local_model(entry) and model_str.split("/", 1)[0] == provider:
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
        f"[bold]POST[/] {_esc(url)}",
        "",
        "[bold]Headers[/]",
    ]
    for k, v in headers.items():
        # Mask auth tokens
        display_v = v
        if k.lower() == "authorization" and len(v) > 20:
            display_v = v[:15] + "..." + v[-4:]
        lines.append(f"  {_esc(k)}: {_esc(display_v)}")
    lines.append("")
    lines.append("[bold]Body[/]")
    lines.append(_esc(_fmt_json(body)))
    return "\n".join(lines)


def _fmt_response(status: int, headers: list[tuple[str, str]], body: str) -> str:
    """Format the incoming response for display.

    Airlock-specific headers (x-airlock-*) are highlighted with bold cyan
    markup to make them easy to spot in diagnostic output.
    """
    lines = [
        f"[bold]HTTP {status}[/]",
        "",
        "[bold]Headers[/]",
    ]
    for k, v in headers:
        if k.lower() in _AIRLOCK_HEADERS:
            lines.append(f"  [bold cyan]{_esc(k)}[/]: {_esc(v)}")
        else:
            lines.append(f"  {_esc(k)}: {_esc(v)}")
    lines.append("")
    lines.append("[bold]Body[/]")
    # Try to pretty-print if it's JSON
    try:
        parsed = json.loads(body)
        lines.append(_esc(_fmt_json(parsed)))
    except (json.JSONDecodeError, TypeError):
        lines.append(_esc(body[:4000]))
    return "\n".join(lines)


def _build_routing_notice(
    resp_headers: list[tuple[str, str]],
    requested_model: str,
) -> str:
    """Build a routing notice if airlock rerouted the request.

    Returns a Rich-markup string or empty string if no rerouting occurred.
    """
    header_map = {k.lower(): v for k, v in resp_headers}
    override = header_map.get("x-airlock-model-override", "")
    failover = header_map.get("x-airlock-failover", "")

    if not override and not failover:
        return ""

    final = override or failover or requested_model
    return f"[yellow]Routed: {_esc(requested_model)} \u2192 {_esc(final)}[/]\n"


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class TestPane(Vertical):
    """Test -- send diagnostic requests through the proxy."""

    BINDINGS = [
        Binding("ctrl+s", "send_request", "Send", priority=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model_list: list[dict] = []
        self._params: dict = {}
        self._params_memory: dict[tuple[str, str], dict] = {}

    def compose(self) -> ComposeResult:
        # -- Top control bar --
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

        # -- Four quadrants --
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
        self._sync_params_to_selection()

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
    # Provider / model resolution
    # ------------------------------------------------------------------

    def _resolve_provider(self, model_name: str) -> str:
        """Return the effective provider for a model_name."""
        for entry in self._model_list:
            if entry.get("model_name") == model_name:
                if _is_local_model(entry):
                    return "local"
                params = entry.get("litellm_params") or {}
                model_str = params.get("model", "")
                if "/" in model_str:
                    return model_str.split("/", 1)[0]
        return "openai"  # fallback

    def _current_selection(self) -> tuple[str, str, str]:
        """Return (provider, model, effective_provider) from current dropdowns."""
        provider_sel = self.query_one("#chat-provider-select", Select)
        model_sel = self.query_one("#chat-model-select", Select)
        provider = str(provider_sel.value) if provider_sel.value not in (None, Select.BLANK) else "all"
        model = str(model_sel.value) if model_sel.value not in (None, Select.BLANK) else ""
        effective = provider if provider != "all" else self._resolve_provider(model)
        return provider, model, effective

    def _sync_params_to_selection(self) -> None:
        """Load remembered params (or defaults) for the current selection."""
        _provider, model, effective = self._current_selection()
        key = (effective, model)
        schema = get_schema(effective, model)

        if key in self._params_memory:
            self._params = dict(self._params_memory[key])
        else:
            self._params = defaults_for_schema(schema)

        self.query_one("#chat-params-input", Input).value = json.dumps(self._params)

        # Rebuild builder overlay if currently open
        if self.query("#chat-builder-overlay"):
            self._dismiss_builder()
            self._show_param_builder()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "chat-provider-select":
            value = event.value
            if value is None or value is Select.BLANK:
                return
            self._refresh_model_options(str(value))
            # _refresh_model_options sets a new model value which triggers
            # on_select_changed for the model select, so _sync is handled there.
        elif event.select.id == "chat-model-select":
            if event.value is None or event.value is Select.BLANK:
                return
            self._sync_params_to_selection()

    def on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+j" and self.query_one("#chat-user-input", TextArea).has_focus:
            event.prevent_default()
            event.stop()
            self._send_request()

    def action_send_request(self) -> None:
        self._send_request()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "chat-send-btn":
            self._send_request()
        elif event.button.id == "chat-builder-btn":
            self._show_param_builder()
        elif event.button.id == "chat-builder-apply":
            self._apply_builder_params()
        elif event.button.id == "chat-builder-reset":
            self._reset_builder_to_defaults()
        elif event.button.id == "chat-builder-cancel":
            self._dismiss_builder()

    # ------------------------------------------------------------------
    # Parameter builder modal
    # ------------------------------------------------------------------

    def _show_param_builder(self) -> None:
        """Mount the parameter builder overlay with provider-specific fields."""
        if self.query("#chat-builder-overlay"):
            return

        _provider, model, effective = self._current_selection()
        schema = get_schema(effective, model)

        try:
            current = json.loads(self.query_one("#chat-params-input", Input).value)
        except (json.JSONDecodeError, TypeError):
            current = defaults_for_schema(schema)

        widgets: list[Static | Label | Input | Select | Horizontal] = [
            Static(f"[bold]Parameters: {_esc(effective)}/{_esc(model)}[/]", classes="builder-title"),
        ]

        for field in schema.fields:
            # Build hint from constraints
            hint = ""
            if field.min is not None and field.max is not None:
                hint = f" ({field.min:g}\u2013{field.max:g})"
            elif field.choices:
                hint = f" ({'/'.join(field.choices)})"

            widgets.append(Label(f"{field.label}{hint}"))

            cur_val = current.get(field.name, field.default)

            if field.type == "bool":
                widgets.append(Select(
                    [("True", "true"), ("False", "false")],
                    value="true" if cur_val else "false",
                    id=f"builder-{field.name}",
                    allow_blank=True,
                ))
            elif field.type == "enum":
                options = [(c, c) for c in field.choices]
                widgets.append(Select(
                    options,
                    value=str(cur_val) if cur_val else Select.BLANK,
                    id=f"builder-{field.name}",
                    allow_blank=True,
                ))
            else:
                display_val = "" if cur_val is None else str(cur_val)
                if field.name == "stop" and isinstance(cur_val, list):
                    display_val = ",".join(cur_val)
                widgets.append(Input(value=display_val, id=f"builder-{field.name}"))

        widgets.append(Horizontal(
            Button("Apply", id="chat-builder-apply", variant="success"),
            Button("Reset", id="chat-builder-reset", variant="warning"),
            Button("Cancel", id="chat-builder-cancel", variant="error"),
            classes="builder-buttons",
        ))

        overlay = VerticalScroll(*widgets, id="chat-builder-overlay")
        self.mount(overlay)

    def _apply_builder_params(self) -> None:
        """Read builder inputs and update the params field."""
        _provider, model, effective = self._current_selection()
        schema = get_schema(effective, model)
        params: dict[str, Any] = {}

        for field in schema.fields:
            try:
                widget = self.query_one(f"#builder-{field.name}")
            except Exception:
                continue

            if isinstance(widget, Select):
                val = widget.value
                if val is None or val is Select.BLANK:
                    continue
                if field.type == "bool":
                    params[field.name] = str(val).lower() == "true"
                else:
                    params[field.name] = str(val)
            elif isinstance(widget, Input):
                raw = widget.value.strip()
                if not raw:
                    continue
                if field.type == "float":
                    try:
                        params[field.name] = float(raw)
                    except ValueError:
                        pass
                elif field.type == "int":
                    try:
                        params[field.name] = int(raw)
                    except ValueError:
                        pass
                elif field.name == "stop":
                    params[field.name] = [s.strip() for s in raw.split(",") if s.strip()]
                else:
                    params[field.name] = raw

        self._params = params
        self._params_memory[(effective, model)] = dict(params)
        self.query_one("#chat-params-input", Input).value = json.dumps(params)
        self._dismiss_builder()

    def _reset_builder_to_defaults(self) -> None:
        """Reset builder to schema defaults for the current selection."""
        _provider, model, effective = self._current_selection()
        self._params_memory.pop((effective, model), None)
        schema = get_schema(effective, model)
        self._params = defaults_for_schema(schema)
        self.query_one("#chat-params-input", Input).value = json.dumps(self._params)
        self._dismiss_builder()
        self._show_param_builder()

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

        # Build messages -- copy params so .pop() doesn't mutate caller's dict
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
        resp_headers: list[tuple[str, str]] = []

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
                    response_content = _esc(msg.get("content", ""))
                    if not response_content:
                        response_content = _esc(_fmt_json(msg))
                else:
                    response_content = "(no choices in response)"

                # Append usage info
                usage = parsed.get("usage")
                if usage:
                    response_content += (
                        f"\n\n[dim]\u2500\u2500 usage \u2500\u2500\n"
                        f"prompt_tokens: {usage.get('prompt_tokens', '?')}\n"
                        f"completion_tokens: {usage.get('completion_tokens', '?')}\n"
                        f"total_tokens: {usage.get('total_tokens', '?')}[/]"
                    )
            except (json.JSONDecodeError, TypeError):
                response_content = _esc(resp_body[:2000])

        except urllib.error.HTTPError as exc:
            status_code = exc.code
            resp_headers = list(exc.headers.items())
            resp_body = exc.read().decode("utf-8", errors="replace")
            response_display = _fmt_response(status_code, resp_headers, resp_body)
            error_msg = f"[red]HTTP {status_code}[/]\n\n"
            try:
                parsed = json.loads(resp_body)
                error_msg += _esc(_fmt_json(parsed))
            except (json.JSONDecodeError, TypeError):
                error_msg += _esc(resp_body[:2000])

        except Exception as exc:
            response_display = f"[red]Connection Error[/]\n\n{_esc(str(exc))}"
            error_msg = f"[red]Error:[/] {_esc(str(exc))}"

        # Build routing notice for Q1 if airlock rerouted the request
        routing_notice = _build_routing_notice(resp_headers, model)

        # Update Q1 and Q4 from main thread
        def _update_ui() -> None:
            q1 = self.query_one("#chat-response-content", Static)
            q4 = self.query_one("#chat-response-detail", Static)
            send_btn = self.query_one("#chat-send-btn", Button)

            q4.update(response_display)

            if error_msg:
                q1.update(routing_notice + error_msg)
            else:
                content = response_content or "[dim](empty response)[/]"
                q1.update(routing_notice + content)

            send_btn.disabled = False
            send_btn.label = "Send"

        self.app.call_from_thread(_update_ui)
