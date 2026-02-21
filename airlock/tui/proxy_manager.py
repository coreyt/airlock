"""ProxyManager — owns a LiteLLM subprocess on behalf of the TUI."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from io import TextIOWrapper
from pathlib import Path
from typing import IO

from dotenv import load_dotenv


class ProxyManager:
    """Start, stop, and monitor a LiteLLM proxy subprocess."""

    def __init__(self, host: str = "0.0.0.0", port: str = "4000") -> None:
        self._host = host
        self._port = port
        self._process: subprocess.Popen[str] | None = None

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
        if self.find_config() is None:
            return "config.yaml not found. Run 'airlock init' first."
        return None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> str | None:
        """Launch the proxy subprocess.  Returns error message or *None*."""
        if self._process is not None and self._process.poll() is None:
            return "Proxy is already running."

        err = self.preflight()
        if err:
            return err

        config_path = self.find_config()
        assert config_path is not None  # preflight passed

        load_dotenv()

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

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        return None

    def stop(self) -> None:
        """Terminate the proxy subprocess (SIGTERM → wait → SIGKILL)."""
        if self._process is None or self._process.poll() is not None:
            self._process = None
            return

        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        self._process = None

    # -- properties -------------------------------------------------------

    @property
    def is_tui_owned(self) -> bool:
        """True when a TUI-started process is still alive."""
        return self._process is not None and self._process.poll() is None

    @property
    def stdout_stream(self) -> IO[str] | None:
        """Stdout pipe of the managed process, or *None*."""
        if self._process is not None and self._process.stdout is not None:
            return self._process.stdout
        return None
