"""
Tests for C1 in-loop admission gate (IMPL-admission).

All tests are no-network: no litellm model calls are made.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from airlock.fast.admission import (
    AdmissionConfig,
    AdmissionGate,
    AdmissionStore,
    configure_admission,
)
from airlock.fast.settings import load_airlock_settings


# ---------------------------------------------------------------------------
# TestAdmissionConfig
# ---------------------------------------------------------------------------
class TestAdmissionConfig:
    def test_defaults(self):
        cfg = AdmissionConfig()
        assert cfg.enabled is False
        assert cfg.rpm == 60
        assert cfg.concurrency == 10
        assert cfg.boost_multiplier == 1.5

    def test_from_settings_loader(self):
        settings = load_airlock_settings(
            {"airlock_settings": {"admission": {"enabled": True, "rpm": 30}}}
        )
        assert settings.admission.enabled is True
        assert settings.admission.rpm == 30
        assert settings.admission.concurrency == 10  # default


# ---------------------------------------------------------------------------
# TestAdmissionStore
# ---------------------------------------------------------------------------
class TestAdmissionStore:
    def test_record_and_count(self):
        store = AdmissionStore()
        now = time.time()
        store.record_request("alice", now)
        store.record_request("alice", now)
        store.record_request("alice", now)
        assert store.recent_count("alice", 60.0, now) == 3

    def test_window_excludes_old(self):
        store = AdmissionStore()
        # record at t=0 (old) and t=100 (recent)
        store.record_request("bob", 0.0)
        store.record_request("bob", 100.0)
        # window=60s, now=100 → only the t=100 request is within the window
        count = store.recent_count("bob", 60.0, 100.0)
        assert count == 1

    def test_reset_clears(self):
        store = AdmissionStore()
        now = time.time()
        for _ in range(5):
            store.record_request("carol", now)
        store.reset("carol")
        assert store.recent_count("carol", 60.0, now) == 0

    def test_concurrent_correctness(self):
        store = AdmissionStore()
        now = time.time()
        errors: list[str] = []

        def worker():
            try:
                for _ in range(10):
                    store.record_request("dave", now)
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        count = store.recent_count("dave", 60.0, now)
        assert count == 80, f"Expected 80 got {count}"


# ---------------------------------------------------------------------------
# TestAdmissionGate
# ---------------------------------------------------------------------------
class TestAdmissionGate:
    def _gate(self, rpm=60, boost_multiplier=1.5, concurrency=10):
        cfg = AdmissionConfig(
            enabled=True, rpm=rpm, concurrency=concurrency, boost_multiplier=boost_multiplier
        )
        store = AdmissionStore()
        return AdmissionGate(cfg, store)

    def test_admit_under_limit(self):
        gate = self._gate(rpm=60)
        now = time.time()
        for _ in range(3):
            allowed, retry_after = gate.check("alice", boost=False, now=now)
            assert allowed is True
            assert retry_after == 0.0

    def test_shed_over_limit(self):
        gate = self._gate(rpm=60)
        now = time.time()
        for _ in range(60):
            allowed, _ = gate.check("bob", boost=False, now=now)
            assert allowed is True
        # 61st request
        allowed, retry_after = gate.check("bob", boost=False, now=now)
        assert allowed is False
        assert retry_after > 0

    def test_boost_raises_cap(self):
        # rpm=60, boost_multiplier=1.5 → cap=90 when boost=True
        gate = self._gate(rpm=60, boost_multiplier=1.5)
        now = time.time()
        for i in range(90):
            allowed, _ = gate.check("carol", boost=True, now=now)
            assert allowed is True, f"Request {i+1} should be admitted"
        # 91st request
        allowed, retry_after = gate.check("carol", boost=True, now=now)
        assert allowed is False
        assert retry_after > 0

    def test_fail_open_on_error(self):
        cfg = AdmissionConfig(enabled=True, rpm=60)
        store = AdmissionStore()
        gate = AdmissionGate(cfg, store)
        # Make the store raise on recent_count
        store.recent_count = MagicMock(side_effect=RuntimeError("store down"))
        allowed, retry_after = gate.check("dave", boost=False, now=time.time())
        assert allowed is True
        assert retry_after == 0.0

    def test_retry_after_positive(self):
        gate = self._gate(rpm=5)
        now = time.time()
        for _ in range(5):
            gate.check("eve", boost=False, now=now)
        allowed, retry_after = gate.check("eve", boost=False, now=now)
        assert not allowed
        assert retry_after > 0.0

    def test_429_not_a_hang(self):
        gate = self._gate(rpm=1)
        now = time.time()
        gate.check("frank", boost=False, now=now)
        start = time.monotonic()
        gate.check("frank", boost=False, now=now)
        elapsed_us = (time.monotonic() - start) * 1_000_000
        assert elapsed_us < 1000, f"Shed path took {elapsed_us:.1f}µs — too slow"


# ---------------------------------------------------------------------------
# TestAdmissionGateDisabled
# ---------------------------------------------------------------------------
class TestAdmissionGateDisabled:
    def test_none_gate_skipped(self):
        """When _admission_gate is None in guardian module, hook proceeds without
        admission metadata."""
        from litellm import DualCache

        from airlock.fast import guardian as guardian_mod

        # Build a minimal data dict and stub objects
        data: dict[str, Any] = {
            "model": "gpt-5-nano",
            "messages": [{"role": "user", "content": "hi"}],
        }

        class _FakeKey:
            user_api_key = "test-key"
            user_api_key_alias = None
            team_id = None
            user_id = None
            org_id = None
            metadata = {}

        with patch.object(guardian_mod, "_admission_gate", None):
            # We verify the hook runs without raising and does NOT stamp admission metadata
            # by mocking out all the heavy dependencies
            with (
                patch("airlock.fast.guardian.resolve_guardrail_decision"),
                patch("airlock.fast.guardian.assess_threat") as mock_threat,
                patch("airlock.fast.guardian.alias_table") as mock_alias,
                patch("airlock.fast.guardian.store") as mock_store,
                patch("airlock.fast.guardian.check_model_with_filters") as mock_cb,
                patch("airlock.fast.guardian.apply_routing", return_value=data),
                patch("airlock.fast.guardian.apply_gemini_request_semantics", return_value=data),
                patch("airlock.fast.guardian.normalize_reasoning_effort"),
                patch("airlock.fast.guardian.detect_dropped_params", return_value=[]),
                patch("airlock.fast.guardian.compute_priority") as mock_prio,
            ):
                mock_threat.return_value = MagicMock(blocked=False)
                mock_alias.resolve.return_value = None
                mock_store.get_client.return_value = MagicMock(
                    is_in_backoff=lambda: False,
                    record_request=lambda t: None,
                    backoff_until=0,
                )
                mock_store.all_providers.return_value = {}
                mock_store.get_client_provider.return_value = MagicMock(
                    is_quarantined=lambda t: False
                )
                mock_cb.return_value = MagicMock(allowed=True)
                mock_prio.return_value = MagicMock(score=0.1, boost=False, reasons=[])

                guardian = guardian_mod.AirlockFastGuardian()
                result = asyncio.get_event_loop().run_until_complete(
                    guardian.async_pre_call_hook(
                        _FakeKey(), DualCache(), data, "completion"
                    )
                )
            admission = result.get("metadata", {}).get("airlock_admission")
            assert admission is None, f"Expected no admission metadata, got {admission}"


# ---------------------------------------------------------------------------
# TestGuardianIntegration
# ---------------------------------------------------------------------------
class TestGuardianIntegration:
    """Integration tests that exercise the guardian hook with the admission gate."""

    def _fake_key(self):
        class _FakeKey:
            user_api_key = "test-key"
            user_api_key_alias = None
            team_id = None
            user_id = None
            org_id = None
            metadata = {}

        return _FakeKey()

    def _base_data(self):
        return {
            "model": "gpt-5-nano",
            "messages": [{"role": "user", "content": "hi"}],
        }

    def _mock_store(self):
        mock_store = MagicMock()
        mock_store.get_client.return_value = MagicMock(
            is_in_backoff=lambda: False,
            record_request=lambda t: None,
            backoff_until=0,
        )
        mock_store.all_providers.return_value = {}
        mock_store.get_client_provider.return_value = MagicMock(
            is_quarantined=lambda t: False
        )
        return mock_store

    def _run_hook(self, guardian, data, key):
        from litellm import DualCache

        return asyncio.get_event_loop().run_until_complete(
            guardian.async_pre_call_hook(key, DualCache(), data, "completion")
        )

    def test_admission_stamps_metadata_on_admit(self):
        """Gate admits → data["metadata"]["airlock_admission"]["action"] == "admitted"."""
        from airlock.fast import guardian as guardian_mod
        from airlock.fast.admission import AdmissionConfig, AdmissionGate, AdmissionStore

        cfg = AdmissionConfig(enabled=True, rpm=60)
        gate = AdmissionGate(cfg, AdmissionStore())
        data = self._base_data()

        with patch.object(guardian_mod, "_admission_gate", gate):
            with (
                patch("airlock.fast.guardian.resolve_guardrail_decision"),
                patch("airlock.fast.guardian.assess_threat") as mock_threat,
                patch("airlock.fast.guardian.alias_table") as mock_alias,
                patch("airlock.fast.guardian.store", self._mock_store()),
                patch("airlock.fast.guardian.check_model_with_filters") as mock_cb,
                patch("airlock.fast.guardian.apply_routing", return_value=data),
                patch(
                    "airlock.fast.guardian.apply_gemini_request_semantics",
                    return_value=data,
                ),
                patch("airlock.fast.guardian.normalize_reasoning_effort"),
                patch("airlock.fast.guardian.detect_dropped_params", return_value=[]),
                patch("airlock.fast.guardian.compute_priority") as mock_prio,
            ):
                mock_threat.return_value = MagicMock(blocked=False)
                mock_alias.resolve.return_value = None
                mock_cb.return_value = MagicMock(allowed=True)
                mock_prio.return_value = MagicMock(score=0.1, boost=False, reasons=[])

                guardian = guardian_mod.AirlockFastGuardian()
                result = self._run_hook(guardian, data, self._fake_key())

        admission = result["metadata"]["airlock_admission"]
        assert admission["action"] == "admitted"

    def test_admission_raises_429_on_shed(self):
        """Gate sheds → hook raises ValueError containing 'Too many requests'."""
        from airlock.fast import guardian as guardian_mod
        from airlock.fast.admission import AdmissionConfig, AdmissionGate, AdmissionStore

        # Set rpm=0 so every request is shed
        cfg = AdmissionConfig(enabled=True, rpm=0)
        gate = AdmissionGate(cfg, AdmissionStore())
        data = self._base_data()

        with patch.object(guardian_mod, "_admission_gate", gate):
            with (
                patch("airlock.fast.guardian.resolve_guardrail_decision"),
                patch("airlock.fast.guardian.assess_threat") as mock_threat,
                patch("airlock.fast.guardian.alias_table"),
                patch("airlock.fast.guardian.store", self._mock_store()),
                patch("airlock.fast.guardian.check_model_with_filters"),
                patch("airlock.fast.guardian.apply_routing", return_value=data),
                patch(
                    "airlock.fast.guardian.apply_gemini_request_semantics",
                    return_value=data,
                ),
                patch("airlock.fast.guardian.normalize_reasoning_effort"),
                patch("airlock.fast.guardian.detect_dropped_params", return_value=[]),
                patch("airlock.fast.guardian.compute_priority") as mock_prio,
            ):
                mock_threat.return_value = MagicMock(blocked=False)
                mock_prio.return_value = MagicMock(score=0.1, boost=False, reasons=[])

                guardian = guardian_mod.AirlockFastGuardian()
                with pytest.raises(ValueError, match="Too many requests"):
                    self._run_hook(guardian, data, self._fake_key())

    def test_gate_fails_open(self):
        """Gate configured but check() always raises → hook returns data normally."""
        from airlock.fast import guardian as guardian_mod
        from airlock.fast.admission import AdmissionConfig, AdmissionGate, AdmissionStore

        cfg = AdmissionConfig(enabled=True, rpm=60)
        store = AdmissionStore()
        gate = AdmissionGate(cfg, store)
        # Make check() raise an exception
        gate.check = MagicMock(side_effect=RuntimeError("gate broken"))
        data = self._base_data()

        with patch.object(guardian_mod, "_admission_gate", gate):
            with (
                patch("airlock.fast.guardian.resolve_guardrail_decision"),
                patch("airlock.fast.guardian.assess_threat") as mock_threat,
                patch("airlock.fast.guardian.alias_table") as mock_alias,
                patch("airlock.fast.guardian.store", self._mock_store()),
                patch("airlock.fast.guardian.check_model_with_filters") as mock_cb,
                patch("airlock.fast.guardian.apply_routing", return_value=data),
                patch(
                    "airlock.fast.guardian.apply_gemini_request_semantics",
                    return_value=data,
                ),
                patch("airlock.fast.guardian.normalize_reasoning_effort"),
                patch("airlock.fast.guardian.detect_dropped_params", return_value=[]),
                patch("airlock.fast.guardian.compute_priority") as mock_prio,
            ):
                mock_threat.return_value = MagicMock(blocked=False)
                mock_alias.resolve.return_value = None
                mock_cb.return_value = MagicMock(allowed=True)
                mock_prio.return_value = MagicMock(score=0.1, boost=False, reasons=[])

                guardian = guardian_mod.AirlockFastGuardian()
                # Should NOT raise — gate failure must fail open
                result = self._run_hook(guardian, data, self._fake_key())

        # Gate check raised — hook should have continued without admission metadata
        # OR the guardian itself caught the exception and failed open
        assert result is not None
