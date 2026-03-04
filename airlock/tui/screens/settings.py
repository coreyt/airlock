"""Settings screen — configuration management."""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import (
    Button,
    Input,
    Label,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)


class SettingsPane(Vertical):
    """Configuration management with tabbed sections."""

    def compose(self) -> ComposeResult:
        with TabbedContent(id="settings-tabs"):
            with TabPane("Providers", id="tab-providers"):
                with Vertical(classes="settings-form"):
                    yield Label("Anthropic API Key")
                    yield Input(
                        value=self._mask_env("ANTHROPIC_API_KEY"),
                        password=True,
                        id="settings-anthropic-key",
                    )
                    yield Label("OpenAI API Key")
                    yield Input(
                        value=self._mask_env("OPENAI_API_KEY"),
                        password=True,
                        id="settings-openai-key",
                    )
                    yield Label("Master Key")
                    yield Input(
                        value=self._mask_env("AIRLOCK_MASTER_KEY"),
                        password=True,
                        id="settings-master-key",
                    )
            with TabPane("Guardrails", id="tab-guardrails"):
                with Vertical(classes="settings-form"):
                    yield Label("PII Guard")
                    yield Switch(
                        value=True,
                        id="settings-pii-enabled",
                    )
                    yield Label("PII Entity Types")
                    yield Input(
                        value=os.getenv(
                            "AIRLOCK_PII_ENTITIES",
                            "CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER",
                        ),
                        id="settings-pii-entities",
                    )
                    yield Label("Keyword Guard")
                    yield Switch(
                        value=True,
                        id="settings-kw-enabled",
                    )
                    yield Label("Blocked Keywords")
                    yield Input(
                        value=os.getenv("AIRLOCK_BLOCKED_KEYWORDS", ""),
                        id="settings-blocked-keywords",
                    )
            with TabPane("Logging", id="tab-logging"):
                with Vertical(classes="settings-form"):
                    yield Label("Log Directory")
                    yield Input(
                        value=os.getenv("AIRLOCK_LOG_DIR", "./logs"),
                        id="settings-log-dir",
                    )
                    yield Label("S3 Bucket (optional)")
                    yield Input(
                        value=os.getenv("AIRLOCK_S3_BUCKET", ""),
                        id="settings-s3-bucket",
                    )
                    yield Label("SQL URL (optional)")
                    yield Input(
                        value=os.getenv("AIRLOCK_SQL_URL", ""),
                        id="settings-sql-url",
                    )
            with TabPane("Advanced", id="tab-advanced"):
                with Vertical(classes="settings-form"):
                    yield Label("Host")
                    yield Input(
                        value=os.getenv("AIRLOCK_HOST", "0.0.0.0"),
                        id="settings-host",
                    )
                    yield Label("Port")
                    yield Input(
                        value=os.getenv("AIRLOCK_PORT", "4000"),
                        id="settings-port",
                    )
                    yield Label("Request Timeout (seconds)")
                    yield Input(value="300", id="settings-timeout")
                    yield Label("Failover Map (JSON)")
                    yield Input(
                        value=os.getenv("AIRLOCK_FAILOVER_MAP", ""),
                        id="settings-failover-map",
                    )
            with TabPane("MCP", id="tab-mcp"):
                with Vertical(classes="settings-form"):
                    yield Static(
                        self._load_mcp_config(), id="settings-mcp-config",
                    )
                    yield Label("Allowed Tools (comma-separated)")
                    yield Input(
                        value=os.getenv("AIRLOCK_MCP_ALLOWED_TOOLS", ""),
                        id="settings-mcp-allowed",
                    )
                    yield Label("Blocked Tools (comma-separated)")
                    yield Input(
                        value=os.getenv("AIRLOCK_MCP_BLOCKED_TOOLS", ""),
                        id="settings-mcp-blocked",
                    )
        yield Button(
            "Apply Changes", id="settings-apply", variant="primary"
        )
        yield Static("", id="settings-status")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-apply":
            self._apply_settings()

    def _apply_settings(self) -> None:
        status = self.query_one("#settings-status", Static)
        try:
            # Write non-empty values to environment (runtime only)
            env_map = {
                "AIRLOCK_PII_ENTITIES": "#settings-pii-entities",
                "AIRLOCK_BLOCKED_KEYWORDS": "#settings-blocked-keywords",
                "AIRLOCK_LOG_DIR": "#settings-log-dir",
                "AIRLOCK_S3_BUCKET": "#settings-s3-bucket",
                "AIRLOCK_SQL_URL": "#settings-sql-url",
                "AIRLOCK_HOST": "#settings-host",
                "AIRLOCK_PORT": "#settings-port",
                "AIRLOCK_FAILOVER_MAP": "#settings-failover-map",
                "AIRLOCK_MCP_ALLOWED_TOOLS": "#settings-mcp-allowed",
                "AIRLOCK_MCP_BLOCKED_TOOLS": "#settings-mcp-blocked",
            }
            for env_var, widget_id in env_map.items():
                val = self.query_one(widget_id, Input).value.strip()
                if val:
                    os.environ[env_var] = val

            status.update(
                "[green]Settings applied to runtime environment. "
                "Restart proxy for full effect.[/]"
            )
        except Exception as exc:
            status.update(f"[red]Error: {exc}[/]")

    @staticmethod
    def _load_mcp_config() -> str:
        """Read MCP server config from config.yaml (read-only display)."""
        config_path = os.getenv("AIRLOCK_CONFIG", "config.yaml")
        try:
            import yaml

            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            mcp = cfg.get("mcp_servers") or cfg.get("mcp", {})
            if mcp:
                import json

                return (
                    "[bold]MCP Server Config[/] (from config.yaml)\n"
                    + json.dumps(mcp, indent=2, default=str)[:500]
                )
            return "[bold]MCP Server Config[/]\n(none configured in config.yaml)"
        except Exception:
            return "[bold]MCP Server Config[/]\n(could not read config.yaml)"

    @staticmethod
    def _mask_env(var: str) -> str:
        val = os.getenv(var, "")
        if val:
            return val[:4] + "*" * max(0, len(val) - 4)
        return ""
