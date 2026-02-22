"""ProxyManager — owns a LiteLLM subprocess on behalf of the TUI."""

from __future__ import annotations

import collections
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO

from dotenv import load_dotenv

_MAX_LOG_LINES = 1000


class ProxyManager:
    """Start, stop, and monitor a LiteLLM proxy subprocess."""

    def __init__(self, host: str = "0.0.0.0", port: str = "4000") -> None:
        self._host = host
        self._port = port
        self._process: subprocess.Popen[str] | None = None
        self._output_queue: queue.Queue[str] = queue.Queue()
        self._ring: collections.deque[str] = collections.deque(maxlen=_MAX_LOG_LINES)
        self._reader_thread: threading.Thread | None = None

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
        """Read subprocess stdout, tee to ring buffer and output queue."""
        assert self._process is not None
        stdout = self._process.stdout
        if stdout is None:
            return
        try:
            for raw_line in stdout:
                line = raw_line.rstrip("\n")
                self._ring.append(line)
                self._output_queue.put(line)
        except ValueError:
            pass  # stream closed

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

        # Load existing log lines into ring buffer
        self._load_ring()

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

        # Persist ring buffer to disk
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

    @property
    def stdout_stream(self) -> IO[str] | None:
        """Stdout pipe of the managed process, or *None*.

        .. deprecated:: Use :attr:`output_queue` instead.  The raw pipe is
           now consumed by the internal reader thread.
        """
        return None
