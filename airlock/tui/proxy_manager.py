"""ProxyManager — owns a LiteLLM subprocess on behalf of the TUI."""

from __future__ import annotations

import collections
import os
import queue
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import IO

import yaml
from dotenv import load_dotenv

_ENV_REF_PREFIX = "os.environ/"

_MAX_LOG_LINES = 1000

# Matches lines that already have a timestamp prefix (HH:MM:SS or ISO-like)
_HAS_TIMESTAMP = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}[T ])?(\d{2}:\d{2}:\d{2})"
)


class ProxyManager:
    """Start, stop, and monitor a LiteLLM proxy subprocess."""

    def __init__(self, host: str = "0.0.0.0", port: str = "4000") -> None:
        self._host = host
        self._port = port
        self._process: subprocess.Popen[str] | None = None
        self._output_queue: queue.Queue[str] = queue.Queue(maxsize=1000)
        self._ring: collections.deque[str] = collections.deque(maxlen=_MAX_LOG_LINES)
        self._reader_thread: threading.Thread | None = None
        self._log_file: IO[str] | None = None
        self._line_count: int = 0

    # -- config discovery (same logic as proxy.py, without sys.exit) ------

    def find_config(self) -> Path | None:
        """Locate config.yaml, returning *None* if not found."""
        candidates = [
            Path(os.getenv("AIRLOCK_CONFIG", "config.yaml")),
            Path(__file__).resolve().parent.parent.parent / "config.yaml",
            Path("/etc/airlock/config.yaml"),
        ]
        for path in candidates:
            if path.is_file():
                return path
        return None

    # -- pre-flight -------------------------------------------------------

    def preflight(self) -> str | None:
        """Validate prerequisites.  Returns an error message or *None*."""
        config_path = self.find_config()
        if config_path is None:
            return "config.yaml not found. Run 'airlock init' first."

        missing = self._check_mcp_env_refs(config_path)
        if missing:
            return (
                "Missing environment variables for MCP servers:\n"
                + "\n".join(missing)
                + "\nSet these in .env or export them in your shell."
            )
        return None

    @staticmethod
    def _check_mcp_env_refs(config_path: Path) -> list[str]:
        """Return list of error strings for missing os.environ/ refs in MCP config."""
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return []

        errors: list[str] = []
        for server_name, server_cfg in (cfg.get("mcp_servers") or {}).items():
            if not isinstance(server_cfg, dict):
                continue
            for _key, value in (server_cfg.get("env") or {}).items():
                if not isinstance(value, str) or not value.startswith(_ENV_REF_PREFIX):
                    continue
                var_name = value[len(_ENV_REF_PREFIX):]
                if not os.environ.get(var_name):
                    errors.append(
                        f"  MCP server '{server_name}' requires {var_name} "
                        f"(set in .env or shell environment)"
                    )
        return errors

    # -- log file ---------------------------------------------------------

    @property
    def log_path(self) -> Path:
        """Path to the proxy console ring log."""
        return Path(os.getenv("AIRLOCK_LOG_DIR", "./logs")) / "proxy-console.log"

    def _load_ring(self) -> None:
        """Load existing log file lines into the ring buffer."""
        path = self.log_path
        if path.is_file():
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        self._ring.append(line.rstrip("\n"))
            except OSError:
                pass

    def _flush_ring(self) -> None:
        """Write ring buffer contents to the log file."""
        path = self.log_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                for line in self._ring:
                    f.write(line + "\n")
        except OSError:
            pass

    def _reader_loop(self) -> None:
        """Read subprocess stdout, tee to ring buffer, log file, and queue."""
        assert self._process is not None
        stdout = self._process.stdout
        if stdout is None:
            return
        try:
            for raw_line in stdout:
                line = raw_line.rstrip("\n")
                if line and not _HAS_TIMESTAMP.match(line):
                    line = f"{datetime.now():%H:%M:%S} {line}"
                self._ring.append(line)
                self._output_queue.put(line)
                self._line_count += 1
                # Write to log file immediately
                if self._log_file is not None:
                    try:
                        self._log_file.write(line + "\n")
                        self._log_file.flush()
                    except OSError:
                        pass
                # Compact log file periodically to enforce ring limit
                if self._line_count >= _MAX_LOG_LINES:
                    self._flush_ring()
                    self._line_count = 0
        except ValueError:
            pass  # stream closed

    # -- lifecycle --------------------------------------------------------

    def start(self) -> str | None:
        """Launch the proxy subprocess.  Returns error message or *None*."""
        if self._process is not None and self._process.poll() is None:
            return "Proxy is already running."

        # Close stale log file from a previous crashed run
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

        err = self.preflight()
        if err:
            return err

        config_path = self.find_config()
        assert config_path is not None  # preflight passed

        _project_env = Path(__file__).resolve().parent.parent.parent / ".env"
        load_dotenv(_project_env)

        litellm_bin = str(Path(sys.executable).parent / "litellm")

        cmd = [
            litellm_bin,
            "--config",
            str(config_path),
            "--host",
            self._host,
            "--port",
            self._port,
        ]

        # Load existing log lines into ring buffer
        self._load_ring()

        # Open log file for live writing
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
            self._line_count = 0
        except OSError:
            self._log_file = None

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True,
        )
        self._reader_thread.start()

        return None

    def stop(self) -> None:
        """Terminate the proxy subprocess (SIGTERM → wait → SIGKILL)."""
        if self._process is None or self._process.poll() is not None:
            self._process = None
        else:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            self._process = None

        # Wait for reader thread to drain remaining output
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None

        # Close live log file handle
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

        # Compact log file to ring buffer (enforces max lines on disk)
        self._flush_ring()

    # -- properties -------------------------------------------------------

    @property
    def is_tui_owned(self) -> bool:
        """True when a TUI-started process is still alive."""
        return self._process is not None and self._process.poll() is None

    @property
    def output_queue(self) -> queue.Queue[str]:
        """Queue of output lines from the proxy subprocess."""
        return self._output_queue

    @staticmethod
    def find_config_static() -> Path | None:
        """Locate config.yaml without instantiating a ProxyManager."""
        candidates = [
            Path(os.getenv("AIRLOCK_CONFIG", "config.yaml")),
            Path(__file__).resolve().parent.parent.parent / "config.yaml",
            Path("/etc/airlock/config.yaml"),
        ]
        for path in candidates:
            if path.is_file():
                return path
        return None
