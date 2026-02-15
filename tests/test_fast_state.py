"""Tests for airlock/fast/state.py"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from airlock.fast.state import (
    CircuitState,
    ClientState,
    ModelState,
    StateStore,
    MAX_SAMPLES,
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
