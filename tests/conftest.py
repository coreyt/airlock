"""
Shared test fixtures for the Airlock test suite.

Six harnesses that simulate every external system Airlock interacts with:
  1. Environment variable isolation
  2. LLM provider mock (Anthropic, OpenAI)
  3. Presidio NLP engine
  4. File system (JSONL logs, config files)
  5. LiteLLM proxy runtime
  6. Fast subsystem state isolation
"""

from __future__ import annotations

import datetime
import json
import os
import random
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Harness 1: Environment Variable Isolation
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove all AIRLOCK_* vars before each test. Tests set what they need.

    Also neutralise ``load_dotenv`` in the CLI dispatcher so ``.env`` from
    the project root doesn't leak real keys into the test environment.
    """
    for var in [k for k in os.environ if k.startswith("AIRLOCK_")]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("airlock.cli.main.load_dotenv", lambda **kw: None)


# ---------------------------------------------------------------------------
# Harness 2: LLM Provider Mock (Anthropic, OpenAI)
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_cache():
    """DualCache stub — guardrails receive but don't use."""
    return MagicMock()


@pytest.fixture
def mock_user_api_key_dict():
    """API key metadata with .api_key attribute."""
    mock = MagicMock()
    mock.api_key = "sk-test-1234567890abcdef"
    return mock


@pytest.fixture
def sample_completion_data():
    """Standard completion request data dict."""
    return {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
        ],
        "model": "claude-sonnet",
    }


@pytest.fixture
def mock_response_obj():
    """MagicMock with .usage and .model_dump()."""
    response = MagicMock()
    response.usage.prompt_tokens = 25
    response.usage.completion_tokens = 50
    response.usage.total_tokens = 75
    response.model_dump.return_value = {
        "id": "chatcmpl-123",
        "choices": [{"message": {"content": "Paris"}}],
        "usage": {"prompt_tokens": 25, "completion_tokens": 50, "total_tokens": 75},
    }
    return response


@pytest.fixture
def mock_logger_kwargs():
    """kwargs dict matching what LiteLLM passes to log_success_event."""
    return {
        "model": "claude-sonnet",
        "messages": [{"role": "user", "content": "Hello"}],
        "litellm_call_id": "call-abc-123",
        "litellm_params": {
            "metadata": {
                "user_api_key_alias": "dev-alice",
                "user_api_key_user_id": "alice",
                "user_api_key_team_alias": "engineering",
            }
        },
    }


@pytest.fixture
def mock_failure_kwargs(mock_logger_kwargs):
    """kwargs dict with exception field for failure callbacks."""
    return {
        **mock_logger_kwargs,
        "exception": Exception("Model timeout after 300s"),
    }


@pytest.fixture
def mock_start_end_times():
    """(start, end) datetime pair, 1500ms apart."""
    start = datetime.datetime(2024, 1, 15, 10, 30, 0, 0)
    end = datetime.datetime(2024, 1, 15, 10, 30, 1, 500000)  # +1.5s
    return start, end


# ---------------------------------------------------------------------------
# Harness 3: Presidio NLP Engine
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def presidio_available():
    """True if Presidio + spaCy model installed.  Checked once per session."""
    try:
        from presidio_analyzer import AnalyzerEngine

        AnalyzerEngine()
        return True
    except (ImportError, OSError):
        return False


@pytest.fixture(scope="session")
def _presidio_engines(presidio_available):
    """Load Presidio engines once per session and share across all tests.

    This avoids reloading the ~560 MB spaCy model for every test, which
    was the root cause of OOM kills when running the full suite.
    """
    if not presidio_available:
        return None, None
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine

    return AnalyzerEngine(), AnonymizerEngine()


@pytest.fixture
def reset_presidio_singletons(_presidio_engines):
    """Point the module-level singletons at the shared session engines.

    Each test gets the pre-loaded engines (no spaCy reload) and the
    originals are restored on teardown for isolation.
    """
    import airlock.guardrails.pii_guard as pii_mod

    original_analyzer = pii_mod._analyzer
    original_anonymizer = pii_mod._anonymizer

    analyzer, anonymizer = _presidio_engines
    pii_mod._analyzer = analyzer
    pii_mod._anonymizer = anonymizer
    yield
    pii_mod._analyzer = original_analyzer
    pii_mod._anonymizer = original_anonymizer


# ---------------------------------------------------------------------------
# Harness 4: File System (JSONL Logs, Config Files)
# ---------------------------------------------------------------------------
@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Temp dir, sets env + patches module constants."""
    log_path = tmp_path / "logs"
    log_path.mkdir()
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(log_path))
    return log_path


@pytest.fixture
def sample_log_records():
    """Factory: _make_records(count, models, error_rate)."""

    def _make_records(
        count: int = 10,
        models: list[str] | None = None,
        error_rate: float = 0.1,
        base_date: datetime.date | None = None,
    ) -> list[dict]:
        if models is None:
            models = ["claude-sonnet", "gpt-4o"]
        if base_date is None:
            base_date = datetime.date.today()

        rng = random.Random(42)  # deterministic for reproducibility
        records = []
        for i in range(count):
            model = models[i % len(models)]
            success = rng.random() >= error_rate
            ts = datetime.datetime.combine(
                base_date,
                datetime.time(hour=10 + (i % 8), minute=i % 60),
            )
            duration = rng.randint(200, 5000)
            prompt_tokens = rng.randint(10, 100)
            completion_tokens = rng.randint(20, 200) if success else 0
            total_tokens = prompt_tokens + completion_tokens if success else 0

            record = {
                "timestamp": ts.isoformat() + "Z",
                "success": success,
                "model": model,
                "user": f"user-{i % 3}",
                "team": "engineering",
                "request_id": f"req-{i:04d}",
                "messages": [{"role": "user", "content": f"Question {i}"}],
                "response": (
                    {"choices": [{"message": {"content": f"Answer {i}"}}]}
                    if success
                    else None
                ),
                "error": f"Error {i}" if not success else None,
                "start_time": ts.isoformat(),
                "end_time": (
                    ts + datetime.timedelta(milliseconds=duration)
                ).isoformat(),
                "duration_ms": duration,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
            records.append(record)
        return records

    return _make_records


@pytest.fixture
def populated_log_dir(log_dir, sample_log_records):
    """Writes 50 sample records across 7 daily files."""
    today = datetime.date.today()
    for day_offset in range(7):
        day = today - datetime.timedelta(days=day_offset)
        records = sample_log_records(
            count=7 if day_offset < 6 else 8,
            base_date=day,
        )
        log_path = log_dir / f"airlock-{day.isoformat()}.jsonl"
        with open(log_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
    return log_dir


# ---------------------------------------------------------------------------
# Harness 5: LiteLLM Proxy Runtime
# ---------------------------------------------------------------------------
@pytest.fixture
def config_file(tmp_path):
    """Minimal config.yaml in temp dir."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "model_list:\n"
        "  - model_name: claude-sonnet\n"
        "    litellm_params:\n"
        "      model: anthropic/claude-sonnet-4-20250514\n"
    )
    return config


# ---------------------------------------------------------------------------
# Harness 6: Fast Subsystem State Isolation
# ---------------------------------------------------------------------------
@pytest.fixture
def fresh_state_store(monkeypatch):
    """Replaces singleton in state, circuit_breaker, guardian, and monitor."""
    from airlock.fast.state import StateStore

    fresh = StateStore()

    import airlock.fast.circuit_breaker as cb_mod
    import airlock.fast.guardian as guardian_mod
    import airlock.fast.monitor as monitor_mod
    import airlock.fast.router as router_mod
    import airlock.fast.state as state_mod
    import airlock.guardrails.observer as observer_mod

    monkeypatch.setattr(state_mod, "store", fresh)
    monkeypatch.setattr(cb_mod, "store", fresh)
    monkeypatch.setattr(guardian_mod, "store", fresh)
    monkeypatch.setattr(monitor_mod, "store", fresh)
    monkeypatch.setattr(router_mod, "store", fresh)
    monkeypatch.setattr(observer_mod, "store", fresh)
    return fresh
