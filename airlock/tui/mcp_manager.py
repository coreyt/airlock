"""McpServerManager — manages MCP server health probes and local lifecycles."""

from __future__ import annotations

import collections
import os
import queue
import shutil
import subprocess
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from airlock.fast.state import McpServerHealth, McpServerState, store

_MAX_LOG_LINES = 500


# ---------------------------------------------------------------------------
# Per-server runtime entry
# ---------------------------------------------------------------------------
@dataclass
class McpServerEntry:
    """Runtime state for one MCP server."""

    name: str
    config: dict                                    # raw config from yaml
    transport: str                                  # "sse", "http", "stdio"
    url: str = ""
    is_managed: bool = False
    managed_config: dict | None = None              # airlock_managed sub-dict
    health_url: str = ""                            # resolved probe URL
    process: subprocess.Popen[str] | None = None
    output_queue: queue.Queue[str] = field(
        default_factory=lambda: queue.Queue(maxsize=1000),
    )
    ring: collections.deque[str] = field(
        default_factory=lambda: collections.deque(maxlen=_MAX_LOG_LINES),
    )
    reader_thread: threading.Thread | None = None
    log_file: IO[str] | None = None


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _resolve_env_value(val: str) -> str:
    """Resolve ``os.environ/VAR_NAME`` to the actual env value."""
    if val.startswith("os.environ/"):
        var = val[len("os.environ/"):]
        return os.environ.get(var, "")
    return val


def _classify_transport(cfg: dict) -> str:
    """Determine transport type from a server config entry."""
    url = cfg.get("url", "")
    if url:
        if url.rstrip("/").endswith("/sse"):
            return "sse"
        return "http"
    if cfg.get("command"):
        return "stdio"
    return "unknown"


def _resolve_health_url(cfg: dict, managed_cfg: dict | None) -> str:
    """Determine the URL to probe for health checks."""
    if managed_cfg and managed_cfg.get("health_url"):
        return managed_cfg["health_url"]
    return cfg.get("url", "")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class McpServerManager:
    """Start, stop, health-check, and monitor MCP servers."""

    def __init__(self) -> None:
        self._servers: dict[str, McpServerEntry] = {}
        self._health_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # -- config loading -----------------------------------------------------

    def load_config(self, config_path: Path | None = None) -> list[str]:
        """Parse ``mcp_servers`` from config.yaml and populate entries.

        Returns the list of server names loaded.
        """
        import yaml

        if config_path is None:
            config_path = self._find_config()
        if config_path is None or not config_path.is_file():
            return []

        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        mcp_servers = cfg.get("mcp_servers")
        if not isinstance(mcp_servers, list):
            return []

        names: list[str] = []
        for srv_cfg in mcp_servers:
            name = srv_cfg.get("name", "")
            if not name:
                continue

            transport = _classify_transport(srv_cfg)
            managed_cfg = srv_cfg.get("airlock_managed")
            is_managed = managed_cfg is not None and isinstance(managed_cfg, dict)
            url = srv_cfg.get("url", "")
            health_url = _resolve_health_url(srv_cfg, managed_cfg)

            entry = McpServerEntry(
                name=name,
                config=srv_cfg,
                transport=transport,
                url=url,
                is_managed=is_managed,
                managed_config=managed_cfg if is_managed else None,
                health_url=health_url,
            )
            self._servers[name] = entry

            # Seed StateStore
            state = McpServerState(
                name=name,
                transport=transport,
                url=url,
                is_managed=is_managed,
            )
            store.set_mcp_server(name, state)
            names.append(name)

        return names

    # -- health probing -----------------------------------------------------

    def probe_server(self, name: str) -> tuple[bool, float]:
        """Probe a single server's health.

        Returns ``(healthy, latency_ms)``.
        """
        entry = self._servers.get(name)
        if entry is None:
            return False, 0.0

        if entry.transport == "stdio" and not entry.is_managed:
            # stdio servers: just check binary exists
            cmd = entry.config.get("command", "")
            healthy = bool(cmd and shutil.which(cmd))
            return healthy, 0.0

        # HTTP/SSE or managed server: probe the URL
        url = entry.health_url or entry.url
        if not url:
            return False, 0.0

        return probe_http(url)

    def probe_all(self) -> dict[str, tuple[bool, float]]:
        """Health-check all configured servers (sequential)."""
        results: dict[str, tuple[bool, float]] = {}
        for name in list(self._servers):
            healthy, latency = self.probe_server(name)
            now = time.time()
            srv_state = store.get_mcp_server(name)
            srv_state.record_health_check(now, healthy, latency)
            results[name] = (healthy, latency)
        return results

    def start_health_loop(self, interval: float = 15.0) -> None:
        """Start a background thread that periodically probes all servers."""
        if self._health_thread is not None:
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.wait(timeout=interval):
                self._check_crashes()
                self.probe_all()

        self._health_thread = threading.Thread(target=_loop, daemon=True)
        self._health_thread.start()

    def stop_health_loop(self) -> None:
        """Stop the background health-check thread."""
        self._stop_event.set()
        if self._health_thread is not None:
            self._health_thread.join(timeout=3)
            self._health_thread = None

    # -- lifecycle (managed servers only) -----------------------------------

    def start_server(self, name: str) -> str | None:
        """Launch a managed MCP server subprocess.

        Returns an error message string, or ``None`` on success.
        """
        entry = self._servers.get(name)
        if entry is None:
            return f"Unknown server: {name}"
        if not entry.is_managed or entry.managed_config is None:
            return f"Server '{name}' is not airlock-managed."
        if entry.process is not None and entry.process.poll() is None:
            return f"Server '{name}' is already running."

        mcfg = entry.managed_config
        command = mcfg.get("command", "")
        if not command:
            return f"Server '{name}': no command in airlock_managed config."

        args = mcfg.get("args", [])
        cwd_raw = mcfg.get("cwd", ".")
        cwd = str(Path(cwd_raw).expanduser().resolve())

        if not Path(cwd).is_dir():
            return f"Server '{name}': cwd does not exist: {cwd}"

        # Resolve environment variables
        env = dict(os.environ)
        for key, val in (mcfg.get("env") or {}).items():
            env[key] = _resolve_env_value(str(val))

        cmd = [command] + [str(a) for a in args]

        # Update state to STARTING
        srv_state = store.get_mcp_server(name)
        srv_state.health = McpServerHealth.STARTING
        srv_state.started_at = time.time()

        # Open log file
        log_path = self._log_path(name)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            entry.log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        except OSError:
            entry.log_file = None

        try:
            entry.process = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            srv_state.health = McpServerHealth.STOPPED
            srv_state.started_at = 0.0
            return f"Server '{name}': failed to start: {exc}"

        srv_state.pid = entry.process.pid

        # Start reader thread
        entry.reader_thread = threading.Thread(
            target=self._reader_loop, args=(entry,), daemon=True,
        )
        entry.reader_thread.start()

        return None

    def stop_server(self, name: str) -> None:
        """Stop a managed server: SIGTERM → wait(5s) → SIGKILL."""
        entry = self._servers.get(name)
        if entry is None:
            return

        proc = entry.process
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        entry.process = None

        # Wait for reader thread to drain
        if entry.reader_thread is not None:
            entry.reader_thread.join(timeout=2)
            entry.reader_thread = None

        # Close log file
        if entry.log_file is not None:
            try:
                entry.log_file.close()
            except OSError:
                pass
            entry.log_file = None

        # Update state
        srv_state = store.get_mcp_server(name)
        srv_state.health = McpServerHealth.STOPPED
        srv_state.pid = 0
        srv_state.started_at = 0.0

    def restart_server(self, name: str) -> str | None:
        """Stop then start a managed server."""
        self.stop_server(name)
        return self.start_server(name)

    def stop_all(self) -> None:
        """Stop all managed servers and the health loop."""
        self.stop_health_loop()
        for name, entry in self._servers.items():
            if entry.is_managed:
                self.stop_server(name)

    # -- queries ------------------------------------------------------------

    @property
    def server_names(self) -> list[str]:
        return list(self._servers)

    def is_managed(self, name: str) -> bool:
        entry = self._servers.get(name)
        return entry.is_managed if entry else False

    def is_running(self, name: str) -> bool:
        entry = self._servers.get(name)
        if entry is None:
            return False
        proc = entry.process
        return proc is not None and proc.poll() is None

    def get_entry(self, name: str) -> McpServerEntry | None:
        return self._servers.get(name)

    # -- internals ----------------------------------------------------------

    def _reader_loop(self, entry: McpServerEntry) -> None:
        """Read subprocess stdout, tee to ring buffer, log file, and queue."""
        if entry.process is None:
            return
        stdout = entry.process.stdout
        if stdout is None:
            return
        try:
            for raw_line in stdout:
                line = raw_line.rstrip("\n")
                entry.ring.append(line)
                try:
                    entry.output_queue.put_nowait(line)
                except queue.Full:
                    pass  # ring buffer preserves history; discard live line
                if entry.log_file is not None:
                    try:
                        entry.log_file.write(line + "\n")
                        entry.log_file.flush()
                    except OSError:
                        pass
        except ValueError:
            pass  # stream closed

    def _check_crashes(self) -> None:
        """Detect crashed managed servers and clean up via stop_server."""
        for name, entry in list(self._servers.items()):
            if not entry.is_managed:
                continue
            proc = entry.process
            if proc is not None and proc.poll() is not None:
                self.stop_server(name)

    def _log_path(self, name: str) -> Path:
        return Path(os.getenv("AIRLOCK_LOG_DIR", "./logs")) / f"mcp-{name}.log"

    @staticmethod
    def _find_config() -> Path | None:
        from airlock.tui.proxy_manager import ProxyManager
        return ProxyManager().find_config()


# ---------------------------------------------------------------------------
# Standalone HTTP probe (also used by POST checks)
# ---------------------------------------------------------------------------

def probe_http(url: str, timeout: float = 5.0) -> tuple[bool, float]:
    """HTTP GET probe. Returns ``(healthy, latency_ms)``."""
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        elapsed = (time.monotonic() - t0) * 1000
        return True, elapsed
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - t0) * 1000
        # 4xx might mean auth needed but server is up; 5xx = unhealthy
        return exc.code < 500, elapsed
    except Exception:
        elapsed = (time.monotonic() - t0) * 1000
        return False, elapsed
