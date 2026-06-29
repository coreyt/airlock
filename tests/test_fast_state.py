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
    NO_CLIENT_ID,
    StateStore,
    MAX_SAMPLES,
    configure_breaker,
    normalize_client_id,
    policy_for,
    tail_jsonl,
    checkpoint_state,
    restore_state,
)


@pytest.fixture(autouse=True)
def _reset_store():
    from airlock.fast.state import set_store

    set_store(StateStore())
    yield
    set_store(None)


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


class TestClientNormalization:
    def test_missing_client_maps_to_no_client(self):
        assert normalize_client_id(None) == NO_CLIENT_ID
        assert normalize_client_id("") == NO_CLIENT_ID


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
            threading.Thread(target=worker, args=(f"client-{i}",)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(store.all_clients()) == 10

    def test_record_provider_rate_limit_quarantines_client(self):
        store = StateStore()
        now = time.time()
        outcome = store.record_provider_rate_limit(
            "client-a",
            "openai",
            now,
            "quota exhausted",
            "RateLimitError",
        )
        client_provider = store.get_client_provider("client-a", "openai")
        assert outcome["client_quarantined"] is True
        assert client_provider.is_quarantined(now)

    def test_record_provider_rate_limit_escalates_after_distinct_clients(self):
        store = StateStore()
        now = time.time()
        store.record_provider_rate_limit(
            "client-a",
            "openai",
            now,
            "quota exhausted",
            "RateLimitError",
        )
        outcome = store.record_provider_rate_limit(
            "client-b",
            "openai",
            now,
            "quota exhausted",
            "RateLimitError",
        )
        provider = store.get_provider("openai")
        assert outcome["provider_quarantined"] is True
        assert provider.is_quarantined(now)


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
        store.ingest_jsonl_record(
            {
                "model": "claude-sonnet",
                "success": True,
                "duration_ms": 250.0,
                "timestamp": self._now_iso(),
            }
        )
        models = store.all_models()
        assert "claude-sonnet" in models
        assert len(models["claude-sonnet"].success_times) == 1
        assert models["claude-sonnet"].recent_avg_latency() == 250.0

    def test_failure_record_populates_model(self):
        store = StateStore()
        store.ingest_jsonl_record(
            {
                "model": "gpt-4o",
                "success": False,
                "duration_ms": 0,
                "timestamp": self._now_iso(),
            }
        )
        models = store.all_models()
        assert "gpt-4o" in models
        assert len(models["gpt-4o"].failure_times) == 1

    def test_tracks_llm_call_type(self):
        store = StateStore()
        store.ingest_jsonl_record(
            {
                "model": "claude-sonnet",
                "success": True,
                "timestamp": self._now_iso(),
            }
        )
        llm, mcp = store.traffic_split()
        assert llm == 1
        assert mcp == 0

    def test_tracks_mcp_call_type(self):
        store = StateStore()
        store.ingest_jsonl_record(
            {
                "model": "mcp-tool",
                "success": True,
                "call_type": "call_mcp_tool",
                "mcp_tool_name": "read_file",
                "mcp_server_name": "fs",
                "timestamp": self._now_iso(),
            }
        )
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
            store.ingest_jsonl_record(
                {
                    "model": "claude-sonnet",
                    "success": True,
                    "duration_ms": 100.0 + i * 10,
                    "timestamp": self._now_iso(),
                }
            )
        model = store.all_models()["claude-sonnet"]
        assert len(model.success_times) == 5

    def test_ingests_gemini_outcome_stats(self):
        store = StateStore()
        store.ingest_jsonl_record(
            {
                "model": "gemini-pro",
                "success": True,
                "airlock_provider": "gemini",
                "airlock_client": "client-a",
                "airlock_gemini": {"mode": "deep_reasoning"},
                "airlock_gemini_response": {"output_shape": "thought_only"},
                "timestamp": self._now_iso(),
            }
        )
        client = store.all_clients()["client-a"]
        provider = store.all_providers()["gemini"]
        assert client.recent_gemini_outcome_count("thought_only") == 1
        assert provider.recent_gemini_outcome_count("thought_only") == 1
        assert provider.recent_gemini_mode() == "deep_reasoning"


# ---------------------------------------------------------------------------
# Circuit breaker race condition (P1 Fix #3)
# ---------------------------------------------------------------------------
class TestCircuitBreakerHalfOpenRace:
    def test_only_one_probe_admitted_concurrently(self):
        """When multiple threads call should_allow_request on OPEN->HALF_OPEN,
        only one should be admitted."""
        store = StateStore()
        model = store.get_model("test-model")
        now = time.time()
        # Force to OPEN state with recovery timeout elapsed
        for _ in range(5):
            model.record_failure(now - 60)
        assert model.circuit == CircuitState.OPEN
        model.last_state_change = now - 31  # past recovery timeout

        admitted = []
        barrier = threading.Barrier(10)

        def try_admit():
            barrier.wait()
            result = store.should_allow_request("test-model")
            admitted.append(result)

        threads = [threading.Thread(target=try_admit) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one thread should have been admitted
        assert admitted.count(True) == 1, (
            f"Expected 1 admitted, got {admitted.count(True)}"
        )


# ---------------------------------------------------------------------------
# Circuit breaker state persistence (P1 Fix #4)
# ---------------------------------------------------------------------------
class TestCircuitBreakerPersistence:
    def test_checkpoint_creates_file(self, tmp_path):
        store = StateStore()
        model = store.get_model("claude-sonnet")
        for _ in range(5):
            model.record_failure(time.time())
        assert model.circuit == CircuitState.OPEN

        path = tmp_path / "cb_state.json"
        checkpoint_state(store, str(path))
        assert path.exists()

        import json

        data = json.loads(path.read_text())
        assert "claude-sonnet" in data["models"]
        assert data["models"]["claude-sonnet"]["circuit"] == "open"

    def test_restore_loads_state(self, tmp_path):
        # Create and checkpoint
        store1 = StateStore()
        model1 = store1.get_model("gpt-4o")
        for _ in range(5):
            model1.record_failure(time.time())
        assert model1.circuit == CircuitState.OPEN

        path = tmp_path / "cb_state.json"
        checkpoint_state(store1, str(path))

        # Restore into a new store
        store2 = StateStore()
        restore_state(store2, str(path))
        model2 = store2.get_model("gpt-4o")
        assert model2.circuit == CircuitState.OPEN

    def test_restore_ignores_stale_file(self, tmp_path):
        """State files older than 5 minutes should be ignored."""
        import json

        path = tmp_path / "cb_state.json"
        path.write_text(
            json.dumps(
                {
                    "timestamp": time.time() - 400,  # > 5 min ago
                    "models": {
                        "old-model": {"circuit": "open", "consecutive_failures": 5}
                    },
                }
            )
        )

        store = StateStore()
        restore_state(store, str(path))
        # Should NOT have loaded old-model
        assert len(store.all_models()) == 0

    def test_restore_loads_recent_file(self, tmp_path):
        """State files within 5 minutes should be loaded."""
        import json

        path = tmp_path / "cb_state.json"
        path.write_text(
            json.dumps(
                {
                    "timestamp": time.time() - 60,  # 1 min ago, within threshold
                    "models": {
                        "recent-model": {"circuit": "open", "consecutive_failures": 5}
                    },
                }
            )
        )

        store = StateStore()
        restore_state(store, str(path))
        model = store.get_model("recent-model")
        assert model.circuit == CircuitState.OPEN

    def test_restore_handles_missing_file(self, tmp_path):
        """restore_state should not crash if file doesn't exist."""
        store = StateStore()
        restore_state(store, str(tmp_path / "nonexistent.json"))
        assert len(store.all_models()) == 0

    def test_restore_handles_corrupt_file(self, tmp_path):
        """restore_state should not crash on corrupt JSON."""
        path = tmp_path / "cb_state.json"
        path.write_text("not valid json{{{")

        store = StateStore()
        restore_state(store, str(path))
        assert len(store.all_models()) == 0


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
                f.write(
                    json.dumps(
                        {
                            "model": "claude-sonnet",
                            "success": True,
                            "duration_ms": 150.0,
                            "timestamp": self._now_iso(),
                        }
                    )
                    + "\n"
                )

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
                f.write(
                    json.dumps(
                        {
                            "model": "gpt-4o",
                            "success": True,
                            "duration_ms": 100.0,
                            "timestamp": self._now_iso(),
                        }
                    )
                    + "\n"
                )

            time.sleep(0.5)
            stop.set()
            t.join(timeout=2)

        # Bad line skipped, good line ingested
        assert "gpt-4o" in test_store.all_models()


# ---------------------------------------------------------------------------
# Per-client circuit-breaker policy (A1 + E) — Pack 0.5.0-RES-breaker
# ---------------------------------------------------------------------------
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_breaker_config():
    """Snapshot/restore module breaker config so tests don't leak policy."""
    import airlock.fast._state_core as _core

    saved_default, saved_clients = _core._breaker_default, _core._breaker_clients
    _core._breaker_default = _core.BreakerPolicy()
    _core._breaker_clients = {}
    yield
    _core._breaker_default, _core._breaker_clients = saved_default, saved_clients


class TestBreakerPolicy:
    def test_default_preserves_one_strike(self):
        """CC-3: no config -> threshold 1, escalation 2 (today's behaviour)."""
        p = policy_for("anything")
        assert p.rate_limit_threshold == 1
        assert p.provider_escalation_client_threshold == 2
        store = StateStore()
        out = store.record_provider_rate_limit("c", "openai", time.time(), "r", "RL")
        assert out["client_quarantined"] is True

    def test_threshold_gating(self):
        configure_breaker(
            {
                "airlock_settings": {
                    "circuit_breaker": {
                        "clients": {"key:batch": {"rate_limit_threshold": 3}}
                    }
                }
            }
        )
        store = StateStore()
        out = None
        for _ in range(2):
            out = store.record_provider_rate_limit(
                "key:batch", "openai", time.time(), "r", "RL"
            )
        assert out["client_quarantined"] is False  # below threshold
        out = store.record_provider_rate_limit(
            "key:batch", "openai", time.time(), "r", "RL"
        )
        assert out["client_quarantined"] is True  # Nth strike arms

    def test_window_expiry_does_not_count(self):
        configure_breaker(
            {
                "airlock_settings": {
                    "circuit_breaker": {
                        "clients": {
                            "key:b": {
                                "rate_limit_threshold": 2,
                                "rate_limit_window_seconds": 300,
                            }
                        }
                    }
                }
            }
        )
        store = StateStore()
        cp = store.get_client_provider("key:b", "openai")
        cp.rate_limit_times.append(time.time() - 400)  # outside the window
        out = store.record_provider_rate_limit(
            "key:b", "openai", time.time(), "r", "RL"
        )
        assert out["client_quarantined"] is False  # only 1 in-window 429

    def test_per_client_precedence(self):
        configure_breaker(
            {
                "airlock_settings": {
                    "circuit_breaker": {
                        "rate_limit_threshold": 5,
                        "clients": {"key:special": {"rate_limit_threshold": 9}},
                    }
                }
            }
        )
        assert policy_for("key:other").rate_limit_threshold == 5  # default
        assert policy_for("key:special").rate_limit_threshold == 9  # override

    def test_escalation_exempt_does_not_quarantine_provider(self):
        configure_breaker(
            {
                "airlock_settings": {
                    "circuit_breaker": {
                        "clients": {
                            "key:e1": {"escalation_exempt": True},
                            "key:e2": {"escalation_exempt": True},
                        }
                    }
                }
            }
        )
        store = StateStore()
        store.record_provider_rate_limit("key:e1", "openai", time.time(), "r", "RL")
        out = store.record_provider_rate_limit(
            "key:e2", "openai", time.time(), "r", "RL"
        )
        assert out["provider_quarantined"] is False  # both exempt -> no escalation
        assert store.get_provider("openai").is_quarantined() is False

    def test_disabled_never_arms(self):
        configure_breaker(
            {
                "airlock_settings": {
                    "circuit_breaker": {"clients": {"key:off": {"disabled": True}}}
                }
            }
        )
        store = StateStore()
        out = store.record_provider_rate_limit(
            "key:off", "openai", time.time(), "r", "RL"
        )
        assert out["client_quarantined"] is False
        assert store.get_client_provider("key:off", "openai").is_quarantined() is False

    def test_cleared_at_floors_client_count(self):
        store = StateStore()
        cp = store.get_client_provider("c", "openai")
        for _ in range(3):
            cp.rate_limit_times.append(time.time())
        assert cp.recent_rate_limit_count() == 3
        cp.cleared_at = time.time()
        assert cp.recent_rate_limit_count() == 0  # pre-clear events hidden

    def test_cleared_at_floors_impacted_clients(self):
        """CC-6: a provider clear must not re-arm escalation on pre-clear history."""
        store = StateStore()
        store.record_provider_rate_limit("c1", "openai", time.time(), "r", "RL")
        store.record_provider_rate_limit("c2", "openai", time.time(), "r", "RL")
        ps = store.get_provider("openai")
        assert len(ps.impacted_clients()) == 2
        ps.cleared_at = time.time()  # simulates an operator provider clear
        assert ps.impacted_clients() == set()  # pre-clear clients no longer counted

    def test_half_open_probe_success_closes(self):
        store = StateStore()
        cp = store.get_client_provider("c", "openai")
        cp.quarantine_until = time.time() + 100
        cp._half_open_probe = True
        cp.record_success(time.time())
        assert cp._half_open_probe is False
        assert cp.is_quarantined() is False  # closed

    def test_half_open_probe_failure_rearms(self):
        configure_breaker(
            {
                "airlock_settings": {
                    "circuit_breaker": {
                        "clients": {"key:b": {"rate_limit_threshold": 99}}
                    }
                }
            }
        )
        store = StateStore()
        cp = store.get_client_provider("key:b", "openai")
        cp._half_open_probe = True
        # A failed probe re-arms immediately even though threshold (99) is unmet.
        out = store.record_provider_rate_limit(
            "key:b", "openai", time.time(), "r", "RL"
        )
        assert out["client_quarantined"] is True
        assert cp.is_quarantined() is True

    def test_malformed_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BREAKER_OVERRIDES", "{not valid json")
        configure_breaker({})  # must not raise
        assert policy_for("x").rate_limit_threshold == 1  # default preserved

    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv(
            "AIRLOCK_BREAKER_OVERRIDES",
            '{"defaults":{"rate_limit_threshold":7}}',
        )
        configure_breaker(
            {"airlock_settings": {"circuit_breaker": {"rate_limit_threshold": 2}}}
        )
        assert policy_for("x").rate_limit_threshold == 7  # env wins


class TestBreakerPolicyFixes:
    """Regressions for the RES-breaker fix-1 (codex BLOCK round)."""

    def test_provider_half_open_failure_rearms(self):
        store = StateStore()
        ps = store.get_provider("openai")
        ps._half_open_probe = True
        out = store.record_provider_rate_limit("c", "openai", time.time(), "r", "RL")
        assert out["provider_quarantined"] is True  # failed probe re-arms (CC-7)
        assert ps._half_open_probe is False
        assert ps.is_quarantined() is True

    def test_disabled_excluded_from_escalation(self):
        configure_breaker(
            {
                "airlock_settings": {
                    "circuit_breaker": {"clients": {"key:off": {"disabled": True}}}
                }
            }
        )
        store = StateStore()
        # disabled client + one normal client = only 1 eligible -> no escalation
        store.record_provider_rate_limit("key:off", "openai", time.time(), "r", "RL")
        out = store.record_provider_rate_limit(
            "c-normal", "openai", time.time(), "r", "RL"
        )
        assert out["provider_quarantined"] is False
        # a second normal client -> 2 eligible -> escalate
        out = store.record_provider_rate_limit(
            "c-normal2", "openai", time.time(), "r", "RL"
        )
        assert out["provider_quarantined"] is True

    def test_per_client_bucket_floor_excludes_from_escalation(self):
        store = StateStore()
        store.record_provider_rate_limit("c1", "openai", time.time(), "r", "RL")
        store.record_provider_rate_limit("c2", "openai", time.time(), "r", "RL")
        # Clear c1's bucket; its pre-clear event must no longer drive escalation.
        store.get_client_provider("c1", "openai").cleared_at = time.time()
        impacted = store._escalation_impacted(
            "openai", store.get_provider("openai"), 300.0, time.time()
        )
        assert impacted == {"c2"}

    def test_env_default_flows_into_config_clients(self, monkeypatch):
        # config client only overrides cooldown; env raises the default threshold;
        # the config client must inherit the env default threshold (env > config).
        monkeypatch.setenv(
            "AIRLOCK_BREAKER_OVERRIDES", '{"defaults":{"rate_limit_threshold":6}}'
        )
        configure_breaker(
            {
                "airlock_settings": {
                    "circuit_breaker": {
                        "rate_limit_threshold": 2,
                        "clients": {"key:c": {"client_cooldown_seconds": 15}},
                    }
                }
            }
        )
        p = policy_for("key:c")
        assert p.rate_limit_threshold == 6  # env default flowed in
        assert p.client_cooldown_seconds == 15  # config client override kept

    def test_malformed_clients_shape_no_crash(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BREAKER_OVERRIDES", '{"clients":[]}')
        configure_breaker({})  # must not raise
        assert policy_for("x").rate_limit_threshold == 1


# ---------------------------------------------------------------------------
# StateProvider — injection seam
# ---------------------------------------------------------------------------
class TestStateProvider:
    def test_get_store_returns_default_singleton(self):
        # First call to get_store() returns a StateStore
        # Second call returns the same instance (lazy init, not re-created)
        from airlock.fast.state import get_store, set_store

        set_store(None)  # reset
        s1 = get_store()
        s2 = get_store()
        assert s1 is s2
        assert isinstance(s1, StateStore)

    def test_set_store_redirects_proxy(self):
        from airlock.fast.state import set_store, store

        fresh = StateStore()
        set_store(fresh)
        # proxy must delegate to fresh
        _ = store.get_client("redirect_test")
        assert "redirect_test" in fresh.all_clients()

    def test_set_store_none_resets(self):
        from airlock.fast.state import get_store, set_store

        fresh = StateStore()
        set_store(fresh)
        set_store(None)
        s = get_store()
        assert s is not fresh

    def test_proxy_repr_does_not_raise(self):
        from airlock.fast.state import store

        r = repr(store)
        assert "_StoreProxy" in r

    def test_injection_isolation(self):
        from airlock.fast.state import get_store, set_store, store

        set_store(StateStore())
        store.get_client("isolation_a")

        set_store(StateStore())
        clients_2 = set(get_store().all_clients().keys())
        # Fresh store has no clients from previous inject
        assert "isolation_a" not in clients_2


class TestStateSplit:
    def test_state_core_importable(self):
        from airlock.fast._state_core import StateStore, ClientState, BreakerPolicy

        assert all(
            isinstance(c, type) for c in [StateStore, ClientState, BreakerPolicy]
        )

    def test_state_spend_importable(self):
        from airlock.fast._state_spend import SpendStore, ProviderSpend

        assert isinstance(SpendStore, type) and isinstance(ProviderSpend, type)

    def test_state_mcp_importable(self):
        from airlock.fast._state_mcp import McpServerState, McpServerHealth

        assert isinstance(McpServerState, type) and isinstance(McpServerHealth, type)

    def test_state_persistence_importable(self):
        from airlock.fast._state_persistence import checkpoint_state, restore_state

        assert callable(checkpoint_state) and callable(restore_state)

    def test_facade_all_names_accessible(self):
        from airlock.fast import state

        for name in [
            "StateStore",
            "ClientState",
            "SpendStore",
            "ProviderSpend",
            "McpServerState",
            "McpServerHealth",
            "McpToolState",
            "ModelState",
            "ProviderRateLimitState",
            "ClientProviderState",
            "ProviderState",
            "CircuitState",
            "BreakerPolicy",
            "configure_breaker",
            "policy_for",
            "checkpoint_state",
            "restore_state",
            "checkpoint_spend",
            "restore_spend",
            "get_store",
            "set_store",
            "store",
            "normalize_client_id",
            "NO_CLIENT_ID",
            "WINDOW_SECONDS",
            "tail_jsonl",
        ]:
            assert hasattr(state, name), f"{name} missing from state facade"

    def test_no_circular_imports(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c", "from airlock.fast import state; print('ok')"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
