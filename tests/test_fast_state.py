"""Tests for airlock/fast/state.py"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from airlock.fast.state import (
    CircuitState,
    ClientState,
    McpToolState,
    ModelState,
    StateStore,
    MAX_SAMPLES,
    tail_jsonl,
)


# ---------------------------------------------------------------------------
# ClientState
# ---------------------------------------------------------------------------
class TestClientState:
    def test_record_request(self):
        client = ClientState(client_id="test")
        now = time.time()
        client.record_request(now)
        assert len(client.request_times) == 1
        assert client.request_times[0] == now

    def test_record_success(self):
        client = ClientState(client_id="test")
        now = time.time()
        client.record_success(now, 150.0)
        assert len(client.successes) == 1
        assert len(client.latencies_ms) == 1
        assert client.latencies_ms[0] == (now, 150.0)

    def test_record_error(self):
        client = ClientState(client_id="test")
        now = time.time()
        client.record_error(now, "TimeoutError")
        assert len(client.errors) == 1
        assert client.errors[0] == (now, "TimeoutError")

    def test_recent_request_count_within_window(self):
        client = ClientState(client_id="test")
        now = time.time()
        for i in range(5):
            client.record_request(now - i)
        assert client.recent_request_count(window_seconds=10) == 5

    def test_recent_request_count_outside_window(self):
        client = ClientState(client_id="test")
        now = time.time()
        client.record_request(now - 600)  # 10 min ago
        assert client.recent_request_count(window_seconds=300) == 0

    def test_recent_error_rate_mixed(self):
        client = ClientState(client_id="test")
        now = time.time()
        for i in range(7):
            client.record_success(now - i, 100.0)
        for i in range(3):
            client.record_error(now - i, "Error")
        rate = client.recent_error_rate(window_seconds=300)
        assert abs(rate - 0.3) < 0.01

    def test_recent_error_rate_no_data(self):
        client = ClientState(client_id="test")
        assert client.recent_error_rate() == 0.0

    def test_recent_avg_latency(self):
        client = ClientState(client_id="test")
        now = time.time()
        client.record_success(now, 100.0)
        client.record_success(now, 200.0)
        client.record_success(now, 300.0)
        avg = client.recent_avg_latency(window_seconds=300)
        assert avg == 200.0

    def test_recent_avg_latency_no_data(self):
        client = ClientState(client_id="test")
        assert client.recent_avg_latency() is None

    def test_is_in_backoff_true(self):
        client = ClientState(client_id="test")
        client.backoff_until = time.time() + 60
        assert client.is_in_backoff() is True

    def test_is_in_backoff_false(self):
        client = ClientState(client_id="test")
        client.backoff_until = time.time() - 60
        assert client.is_in_backoff() is False

    def test_is_in_backoff_default(self):
        client = ClientState(client_id="test")
        assert client.is_in_backoff() is False

    def test_deque_bounded_at_max_samples(self):
        client = ClientState(client_id="test")
        for i in range(MAX_SAMPLES + 100):
            client.record_request(float(i))
        assert len(client.request_times) == MAX_SAMPLES


# ---------------------------------------------------------------------------
# ModelState
# ---------------------------------------------------------------------------
class TestModelState:
    def test_record_success_resets_consecutive_failures(self):
        model = ModelState(model_name="test")
        model.consecutive_failures = 3
        model.record_success(time.time(), 100.0)
        assert model.consecutive_failures == 0

    def test_record_failure_increments(self):
        model = ModelState(model_name="test")
        model.record_failure(time.time())
        model.record_failure(time.time())
        assert model.consecutive_failures == 2

    def test_circuit_closed_to_open(self):
        model = ModelState(model_name="test")
        assert model.circuit == CircuitState.CLOSED
        for _ in range(5):
            model.record_failure(time.time())
        assert model.circuit == CircuitState.OPEN

    def test_circuit_open_to_half_open(self):
        model = ModelState(model_name="test")
        now = time.time()
        for _ in range(5):
            model.record_failure(now - 60)
        assert model.circuit == CircuitState.OPEN
        model.last_state_change = now - 31  # past recovery timeout
        assert model.should_allow_request() is True
        assert model.circuit == CircuitState.HALF_OPEN

    def test_circuit_half_open_to_closed(self):
        model = ModelState(model_name="test")
        now = time.time()
        # Force to HALF_OPEN
        model.circuit = CircuitState.HALF_OPEN
        model.last_state_change = now - 1
        # Record 3 successes after state change
        for i in range(3):
            model.record_success(now + i, 100.0)
        assert model.circuit == CircuitState.CLOSED

    def test_circuit_half_open_failure_reopens(self):
        model = ModelState(model_name="test")
        model.circuit = CircuitState.HALF_OPEN
        model.last_state_change = time.time()
        model.record_failure(time.time())
        assert model.circuit == CircuitState.OPEN

    def test_should_allow_request_closed(self):
        model = ModelState(model_name="test")
        assert model.should_allow_request() is True

    def test_should_allow_request_open_before_timeout(self):
        model = ModelState(model_name="test")
        model.circuit = CircuitState.OPEN
        model.last_state_change = time.time()
        assert model.should_allow_request() is False

    def test_should_allow_request_half_open(self):
        model = ModelState(model_name="test")
        model.circuit = CircuitState.HALF_OPEN
        assert model.should_allow_request() is True

    def test_recent_avg_latency(self):
        model = ModelState(model_name="test")
        now = time.time()
        model.record_success(now, 100.0)
        model.record_success(now, 200.0)
        assert model.recent_avg_latency() == 150.0

    def test_deque_bounded(self):
        model = ModelState(model_name="test")
        for i in range(MAX_SAMPLES + 100):
            model.record_failure(float(i))
        assert len(model.failure_times) == MAX_SAMPLES


# ---------------------------------------------------------------------------
# StateStore
# ---------------------------------------------------------------------------
class TestStateStore:
    def test_get_client_creates_new(self):
        store = StateStore()
        client = store.get_client("alice")
        assert client.client_id == "alice"

    def test_get_client_returns_same_instance(self):
        store = StateStore()
        c1 = store.get_client("alice")
        c2 = store.get_client("alice")
        assert c1 is c2

    def test_get_model_creates_new(self):
        store = StateStore()
        model = store.get_model("claude-sonnet")
        assert model.model_name == "claude-sonnet"

    def test_get_model_returns_same_instance(self):
        store = StateStore()
        m1 = store.get_model("claude-sonnet")
        m2 = store.get_model("claude-sonnet")
        assert m1 is m2

    def test_all_clients(self):
        store = StateStore()
        store.get_client("alice")
        store.get_client("bob")
        clients = store.all_clients()
        assert set(clients.keys()) == {"alice", "bob"}

    def test_all_models(self):
        store = StateStore()
        store.get_model("gpt-4o")
        store.get_model("claude-sonnet")
        models = store.all_models()
        assert set(models.keys()) == {"gpt-4o", "claude-sonnet"}

    def test_thread_safe_concurrent_access(self):
        store = StateStore()
        errors = []

        def worker(client_id):
            try:
                for _ in range(100):
                    client = store.get_client(client_id)
                    client.record_request(time.time())
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"client-{i}",))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(store.all_clients()) == 10


# ---------------------------------------------------------------------------
# McpToolState
# ---------------------------------------------------------------------------
class TestMcpToolState:
    def test_record_success(self):
        tool = McpToolState(tool_name="read_file", server_name="fs")
        now = time.time()
        tool.record_success(now, 50.0)
        assert len(tool.success_times) == 1
        assert len(tool.latencies_ms) == 1

    def test_record_failure(self):
        tool = McpToolState(tool_name="read_file")
        now = time.time()
        tool.record_failure(now)
        assert len(tool.failure_times) == 1

    def test_recent_error_rate(self):
        tool = McpToolState(tool_name="read_file")
        now = time.time()
        for i in range(7):
            tool.record_success(now - i, 100.0)
        for i in range(3):
            tool.record_failure(now - i)
        rate = tool.recent_error_rate(window_seconds=300)
        assert abs(rate - 0.3) < 0.01

    def test_recent_error_rate_no_data(self):
        tool = McpToolState(tool_name="x")
        assert tool.recent_error_rate() == 0.0

    def test_recent_call_count(self):
        tool = McpToolState(tool_name="read_file")
        now = time.time()
        tool.record_success(now, 10.0)
        tool.record_success(now, 20.0)
        tool.record_failure(now)
        assert tool.recent_call_count(window_seconds=300) == 3

    def test_recent_call_count_window(self):
        tool = McpToolState(tool_name="x")
        now = time.time()
        tool.record_success(now - 600, 10.0)  # outside window
        tool.record_success(now, 10.0)
        assert tool.recent_call_count(window_seconds=300) == 1

    def test_recent_avg_latency(self):
        tool = McpToolState(tool_name="x")
        now = time.time()
        tool.record_success(now, 100.0)
        tool.record_success(now, 200.0)
        assert tool.recent_avg_latency() == 150.0

    def test_recent_avg_latency_no_data(self):
        tool = McpToolState(tool_name="x")
        assert tool.recent_avg_latency() is None

    def test_deque_bounded(self):
        tool = McpToolState(tool_name="x")
        for i in range(MAX_SAMPLES + 100):
            tool.record_success(float(i), 1.0)
        assert len(tool.success_times) == MAX_SAMPLES


# ---------------------------------------------------------------------------
# StateStore MCP methods
# ---------------------------------------------------------------------------
class TestStateStoreMcp:
    def test_get_mcp_tool_creates_new(self):
        store = StateStore()
        tool = store.get_mcp_tool("read_file", "fs")
        assert tool.tool_name == "read_file"
        assert tool.server_name == "fs"

    def test_get_mcp_tool_returns_same_instance(self):
        store = StateStore()
        t1 = store.get_mcp_tool("read_file", "fs")
        t2 = store.get_mcp_tool("read_file", "fs")
        assert t1 is t2

    def test_get_mcp_tool_different_servers(self):
        store = StateStore()
        t1 = store.get_mcp_tool("read_file", "fs1")
        t2 = store.get_mcp_tool("read_file", "fs2")
        assert t1 is not t2

    def test_all_mcp_tools(self):
        store = StateStore()
        store.get_mcp_tool("read_file", "fs")
        store.get_mcp_tool("write_file", "fs")
        tools = store.all_mcp_tools()
        assert len(tools) == 2

    def test_traffic_split_initial(self):
        store = StateStore()
        llm, mcp = store.traffic_split()
        assert llm == 0
        assert mcp == 0

    def test_record_call_type_llm(self):
        store = StateStore()
        store.record_call_type(is_mcp=False)
        store.record_call_type(is_mcp=False)
        llm, mcp = store.traffic_split()
        assert llm == 2
        assert mcp == 0

    def test_record_call_type_mcp(self):
        store = StateStore()
        store.record_call_type(is_mcp=True)
        llm, mcp = store.traffic_split()
        assert llm == 0
        assert mcp == 1

    def test_traffic_split_mixed(self):
        store = StateStore()
        store.record_call_type(is_mcp=False)
        store.record_call_type(is_mcp=True)
        store.record_call_type(is_mcp=False)
        llm, mcp = store.traffic_split()
        assert llm == 2
        assert mcp == 1


# ---------------------------------------------------------------------------
# JSONL ingestion (cross-process state sync for TUI)
# ---------------------------------------------------------------------------
class TestIngestJsonlRecord:
    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def test_success_record_populates_model(self):
        store = StateStore()
        store.ingest_jsonl_record({
            "model": "claude-sonnet",
            "success": True,
            "duration_ms": 250.0,
            "timestamp": self._now_iso(),
        })
        models = store.all_models()
        assert "claude-sonnet" in models
        assert len(models["claude-sonnet"].success_times) == 1
        assert models["claude-sonnet"].recent_avg_latency() == 250.0

    def test_failure_record_populates_model(self):
        store = StateStore()
        store.ingest_jsonl_record({
            "model": "gpt-4o",
            "success": False,
            "duration_ms": 0,
            "timestamp": self._now_iso(),
        })
        models = store.all_models()
        assert "gpt-4o" in models
        assert len(models["gpt-4o"].failure_times) == 1

    def test_tracks_llm_call_type(self):
        store = StateStore()
        store.ingest_jsonl_record({
            "model": "claude-sonnet",
            "success": True,
            "timestamp": self._now_iso(),
        })
        llm, mcp = store.traffic_split()
        assert llm == 1
        assert mcp == 0

    def test_tracks_mcp_call_type(self):
        store = StateStore()
        store.ingest_jsonl_record({
            "model": "mcp-tool",
            "success": True,
            "call_type": "call_mcp_tool",
            "mcp_tool_name": "read_file",
            "mcp_server_name": "fs",
            "timestamp": self._now_iso(),
        })
        llm, mcp = store.traffic_split()
        assert llm == 0
        assert mcp == 1
        tools = store.all_mcp_tools()
        assert len(tools) == 1

    def test_skips_record_without_model(self):
        store = StateStore()
        store.ingest_jsonl_record({"success": True, "timestamp": self._now_iso()})
        assert len(store.all_models()) == 0

    def test_handles_missing_timestamp(self):
        store = StateStore()
        store.ingest_jsonl_record({"model": "test", "success": True})
        assert len(store.all_models()) == 1

    def test_multiple_records(self):
        store = StateStore()
        for i in range(5):
            store.ingest_jsonl_record({
                "model": "claude-sonnet",
                "success": True,
                "duration_ms": 100.0 + i * 10,
                "timestamp": self._now_iso(),
            })
        model = store.all_models()["claude-sonnet"]
        assert len(model.success_times) == 5


class TestTailJsonl:
    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def test_tails_new_lines(self, tmp_path):
        """tail_jsonl picks up new lines appended to today's log."""
        import json
        from datetime import date

        today = date.today().isoformat()
        log_file = tmp_path / f"airlock-{today}.jsonl"

        # Write initial line before tailer starts
        log_file.write_text("")

        stop = threading.Event()

        # Patch the global store so we can inspect it
        test_store = StateStore()
        with patch("airlock.fast.state.store", test_store):
            # Start tailer
            t = threading.Thread(
                target=tail_jsonl,
                args=(str(tmp_path), stop, 0.1),
                daemon=True,
            )
            t.start()

            # Give tailer time to open file and seek to end
            time.sleep(0.3)

            # Append a record
            with open(log_file, "a") as f:
                f.write(json.dumps({
                    "model": "claude-sonnet",
                    "success": True,
                    "duration_ms": 150.0,
                    "timestamp": self._now_iso(),
                }) + "\n")

            # Wait for tailer to pick it up
            time.sleep(0.5)

            stop.set()
            t.join(timeout=2)

        assert "claude-sonnet" in test_store.all_models()

    def test_ignores_malformed_lines(self, tmp_path):
        """tail_jsonl skips invalid JSON without crashing."""
        import json
        from datetime import date

        today = date.today().isoformat()
        log_file = tmp_path / f"airlock-{today}.jsonl"
        log_file.write_text("")

        stop = threading.Event()
        test_store = StateStore()

        with patch("airlock.fast.state.store", test_store):
            t = threading.Thread(
                target=tail_jsonl,
                args=(str(tmp_path), stop, 0.1),
                daemon=True,
            )
            t.start()
            time.sleep(0.3)

            with open(log_file, "a") as f:
                f.write("not valid json\n")
                f.write(json.dumps({
                    "model": "gpt-4o",
                    "success": True,
                    "duration_ms": 100.0,
                    "timestamp": self._now_iso(),
                }) + "\n")

            time.sleep(0.5)
            stop.set()
            t.join(timeout=2)

        # Bad line skipped, good line ingested
        assert "gpt-4o" in test_store.all_models()
