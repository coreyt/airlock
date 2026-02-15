# Airlock Project Plan

## Context

Airlock is an enterprise LLM proxy built on LiteLLM with five implemented subsystems
(proxy, guardrails, logging, fast adaptive processing, slow offline analysis) and zero
tests. The most critical gap is the absence of any test infrastructure — especially
given that guardrails are the data-protection boundary preventing PII from reaching
external providers. This plan establishes test harnesses representing external systems,
then builds outward through tests, production features, and deployment hardening.

## Phase 0 — Test Foundation and External System Harnesses

**Milestone:** pytest infrastructure and six reusable harnesses that simulate every
external system Airlock interacts with at runtime. All subsequent phases depend on this.

**Satisfies:** NFR-2 (env var config), NFR-6 (graceful degradation), NFR-7 (serialization)

### Files to create

**`pyproject.toml`** — add test dependencies:
```toml
[project.optional-dependencies]
test = ["pytest>=8.0", "pytest-asyncio>=0.23", "pytest-cov>=5.0"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**`tests/__init__.py`** — empty package marker

**`tests/conftest.py`** — all six harnesses below

### Harness 1: Environment Variable Isolation

Modules like `_configured_entities()`, `_blocked_keywords()`, `_load_failover_map()`,
and `LOG_DIR` all read env vars at call time. Tests must prevent cross-contamination.

```python
@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove all AIRLOCK_* vars before each test. Tests set what they need."""
    for var in [k for k in os.environ if k.startswith("AIRLOCK_")]:
        monkeypatch.delenv(var, raising=False)
```

**Simulates:** The deployment environment (Docker `.env`, systemd, K8s ConfigMap).
Using `monkeypatch` ensures automatic teardown even on test failure.

### Harness 2: LLM Provider Mock (Anthropic, OpenAI)

Guardrails and loggers never see raw HTTP — they receive normalized `data` dicts
(pre-call) and `kwargs`/`response_obj` (post-call) from LiteLLM. These fixtures
provide those exact shapes.

```python
@pytest.fixture
def mock_cache():                    # DualCache — guardrails receive but don't use
@pytest.fixture
def mock_user_api_key_dict():        # API key metadata with .api_key attribute
@pytest.fixture
def sample_completion_data():        # {"messages": [...], "model": "claude-sonnet"}
@pytest.fixture
def mock_response_obj():             # MagicMock with .usage and .model_dump()
@pytest.fixture
def mock_logger_kwargs():            # kwargs dict with litellm_params.metadata
@pytest.fixture
def mock_failure_kwargs():           # kwargs with exception field
@pytest.fixture
def mock_start_end_times():          # (start, end) datetime pair, 1500ms apart
```

**Simulates:** Anthropic API and OpenAI API responses as normalized by LiteLLM.
No real HTTP calls ever made.

### Harness 3: Presidio NLP Engine

Presidio requires the 560 MB `en_core_web_lg` spaCy model. CI environments may
not have it. Tests that need real Presidio are skipped when unavailable; degradation
tests mock the import to verify failure handling.

```python
@pytest.fixture
def presidio_available():            # True if Presidio + spaCy model installed
@pytest.fixture
def reset_presidio_singletons():     # Reset lazy-loaded globals between tests
```

**Simulates:** The Presidio AnalyzerEngine/AnonymizerEngine (which internally load
spaCy). Allows tests to run in both full and minimal environments.

### Harness 4: File System (JSONL Logs, Config Files)

The logger writes to `AIRLOCK_LOG_DIR`, the analyzer reads from it. Tests use
`tmp_path` and patch the module-level `LOG_DIR` constants in both modules.

```python
@pytest.fixture
def log_dir(tmp_path, monkeypatch):  # Temp dir, sets env + patches module constants
@pytest.fixture
def sample_log_records():            # Factory: _make_records(count, models, error_rate)
@pytest.fixture
def populated_log_dir():             # Writes 50 sample records across 7 daily files
```

**Simulates:** The filesystem the enterprise logger writes to and the analyzer reads
from. Uses real temporary directories for realistic I/O behavior.

### Harness 5: LiteLLM Proxy Runtime

`proxy.py` calls `subprocess.call()` to launch LiteLLM. Tests verify config
discovery and command construction without starting the actual server.

```python
@pytest.fixture
def config_file(tmp_path):           # Minimal config.yaml in temp dir
```

Tests mock `subprocess.call` to capture the command args. **Simulates:** The LiteLLM
proxy lifecycle — never actually started in tests.

### Harness 6: Fast Subsystem State Isolation

The fast subsystem uses a module-level singleton `store = StateStore()`. Tests must
get a fresh instance to prevent cross-contamination of request counts, error rates,
and circuit breaker states.

```python
@pytest.fixture
def fresh_state_store(monkeypatch):  # Replaces singleton in state, circuit_breaker,
                                     # guardian, and monitor modules
```

**Simulates:** The in-memory runtime state that accumulates across requests. Each
test starts with an empty `StateStore`.

---

## Phase 1 — Core Subsystem Tests

**Milestone:** Full test coverage of the data-protection boundary (guardrails),
audit trail (logger), and entry point (proxy). Most urgent phase — these are the
security-critical components.

**Satisfies:** FR-4, FR-5, FR-6, FR-7, FR-8, FR-9, FR-10, FR-13, FR-15, FR-16,
NFR-6, NFR-7, NFR-10

### `tests/test_pii_guard.py`

Tests `airlock/guardrails/pii_guard.py`: `_configured_entities()`, `_scrub_text()`,
`_scrub_messages()`, `AirlockPIIGuard.async_pre_call_hook()`

- Default and custom entity configuration (env var parsing)
- Redaction of SSN, credit card, email, phone (false negative checks)
- Safe text passes unchanged (false positive checks)
- Multi-part messages: text parts scrubbed, image parts preserved
- Empty messages, missing content field
- Graceful degradation when Presidio not installed
- Full async hook returns modified data dict

### `tests/test_keyword_guard.py`

Tests `airlock/guardrails/keyword_guard.py`: `_blocked_keywords()`, `_extract_text()`,
`AirlockKeywordGuard.async_pre_call_hook()`

- No keywords configured → all requests pass (zero overhead)
- Keyword match raises `ValueError`
- Case-insensitive and substring matching
- Multi-part message scanning (text parts only, images ignored)
- Error message does not echo the blocked keyword
- Whitespace trimming in keyword list
- Multiple keywords — any one triggers block

### `tests/test_enterprise_logger.py`

Tests `airlock/callbacks/enterprise_logger.py`: `_serialize()`, `_build_record()`,
`_write_log()`, all four callback methods

- Serialization cascade: datetime → isoformat, bytes → decode, pydantic v2 →
  model_dump, pydantic v1 → dict, unknown → str
- Record has all 13+ fields (FR-9 schema)
- `duration_ms` correctly computed from start/end times
- Handles missing `response_obj`, missing `.usage`
- Creates log dir if missing, names files `airlock-YYYY-MM-DD.jsonl`
- Each line is valid JSON, multiple writes append
- Async methods delegate to sync

### `tests/test_proxy.py`

Tests `airlock/proxy.py`: `_find_config()`, `main()`

- Config discovery: `AIRLOCK_CONFIG` env → project root → `/etc/airlock/`
- Missing config exits with `sys.exit(1)`
- `main()` builds correct subprocess command with host/port from env
- `load_dotenv()` called before config discovery

---

## Phase 2 — Fast Subsystem Tests

**Milestone:** Complete coverage of the real-time adaptive processing pipeline.
Each module tested in isolation, then the guardian tested as integration point.

**Satisfies:** `dev/feature-dynamic-processing.md` specification

### `tests/test_fast_state.py`

Tests `airlock/fast/state.py`: `ClientState`, `ModelState`, `StateStore`

- Record methods append to correct deques
- Sliding-window readers count/average only within window
- `is_in_backoff()` checks current time against `backoff_until`
- Deques bounded at `MAX_SAMPLES` (1000)
- Circuit breaker state machine: CLOSED → (5 failures) → OPEN → (30s) →
  HALF_OPEN → (3 successes) → CLOSED; failure in HALF_OPEN → OPEN
- StateStore lazy-creates clients/models, returns same instance on reaccess
- Thread-safe concurrent access

### `tests/test_fast_threat_detector.py`

Tests `airlock/fast/threat_detector.py`: `assess_threat()`

- Clean client scores ~0.0, not blocked
- Volume spike: 30s rate 10x above 5-min baseline
- Rapid-fire: 10+ requests with <100ms gaps → +0.35
- Large payload: >100k chars → score contribution
- Error probing: >80% error rate over 10+ requests → +0.3
- Composite score >0.7 → `blocked=True` with exponential backoff
- Backoff capped at 3600s, score decays by 0.95 factor
- `message_text=None` does not crash

### `tests/test_fast_circuit_breaker.py`

Tests `airlock/fast/circuit_breaker.py`: `check_model()`, `_load_failover_map()`

- Healthy (CLOSED) model → `allowed=True`
- OPEN model with healthy fallback → `failover_model` set
- All models OPEN → `allowed=False, failover_model=None`
- Custom failover map from `AIRLOCK_FAILOVER_MAP` env (JSON)
- Invalid JSON falls back to defaults
- HALF_OPEN allows probe request

### `tests/test_fast_priority.py`

Tests `airlock/fast/priority.py`: `compute_priority()`

- Idle client → score 0.0, `boost=False`
- Interactive cadence (≤60s gaps, ≥3 requests) → signal fires
- Recovery need (>30% error rate) → signal fires
- Latency spike (2x baseline) → signal fires
- Starvation (>5 requests, >50% errors) → signal fires
- Combined score ≥0.6 → `boost=True`; capped at 1.0

### `tests/test_fast_guardian.py`

Tests `airlock/fast/guardian.py`: `AirlockFastGuardian.async_pre_call_hook()`

- Normal request passes with priority metadata attached
- Client in backoff → `ValueError`
- High-threat client → `ValueError` with backoff
- Open circuit → model rewritten to fallback + failover metadata
- All circuits open, no fallback → `ValueError`
- Client ID extracted from API key (last 8 chars), fallback "unknown"
- `record_request()` called on entry

### `tests/test_fast_monitor.py`

Tests `airlock/fast/monitor.py`: `AirlockFastMonitor` callbacks

- Success → client `record_success` + model `record_success` with duration
- Failure → client `record_error` + model `record_failure`
- Client ID resolution: `user_api_key_alias` → `user_api_key_user_id` → key suffix
- Async delegates to sync

---

## Phase 3 — Slow Subsystem Tests

**Milestone:** Full coverage of the offline analysis engine and CLI.

**Satisfies:** `dev/feature-dynamic-processing.md` specification

### `tests/test_slow_analyzer.py`

Tests `airlock/slow/analyzer.py`: all analysis functions

- `_load_logs`: reads JSONL, skips missing days, skips malformed lines
- `_fingerprint_messages`: deterministic hashing, handles None
- `find_optimizations`: detects >10% error rate, p95 >30s, token outliers
- `find_cache_opportunities`: ≥3 identical prompts flagged, <3 not
- `find_trends`: volume shifts >10%, error rate >2pp, latency >15%
- `generate_hypotheses`: derives testable statements from findings
- `analyze`: full pipeline returns complete `AnalysisReport`
- Empty logs handled gracefully

### `tests/test_slow_cli.py`

Tests `airlock/slow/cli.py`: `main()`, `_format_text()`

- Default output is human-readable text with section headers
- `--json` produces valid JSON
- `--days N` passes through to `analyze()`
- `-o file` writes to specified path
- Text output contains expected sections (SUMMARY, OPTIMIZATIONS, etc.)

---

## Phase 4 — Integration and Guardrail Chain Tests

**Milestone:** Cross-component tests verifying the full request pipeline as described
in `dev/architecture.md` data flow diagrams.

**Satisfies:** FR-14 (guardrail registration), FR-15 (pre-call execution),
NFR-9 (execution order)

### `tests/test_integration.py`

- PII guard rewrites first, then keyword guard checks cleaned text (execution order)
- Full three-guardrail chain: PII → keyword → fast guardian
- PII redaction does not inadvertently remove a blocked keyword substring
- Logger receives scrubbed messages (never raw PII)
- Circuit breaker failover metadata recorded in logs
- Monitor feedback loop: failure recorded → guardian sees error in state store
- Full success pipeline: all guardrails → modified data shape verified
- Full block pipeline: PII scrubs → keyword blocks → failure logged

---

## Phase 5 — S3 and SQL Log Backends

**Milestone:** Implement the two log backend extensions declared as optional deps
in `pyproject.toml` but never built.

**Satisfies:** Architecture doc Section 8 ("Extension Points"), UN-4

### `airlock/callbacks/s3_logger.py` (new)

- `CustomLogger` subclass reading `AIRLOCK_S3_BUCKET` / `AIRLOCK_S3_PREFIX`
- Builds same record dict as enterprise logger
- Batches in memory, flushes to S3 as JSONL keyed `{prefix}/YYYY/MM/DD/airlock-{ts}.jsonl`
- Graceful degradation when boto3 unavailable or S3 unreachable

**Test harness:** Mock `boto3.client("s3")` — verify `put_object` calls with correct
bucket, key, body. Never hits real AWS.

**Test file:** `tests/test_s3_logger.py`

### `airlock/callbacks/sql_logger.py` (new)

- `CustomLogger` subclass reading `AIRLOCK_SQL_URL` (SQLAlchemy connection string)
- Defines `airlock_logs` table via SQLAlchemy Core
- Inserts record per callback, JSON-encodes `messages` and `response` columns
- Graceful degradation when sqlalchemy unavailable

**Test harness:** SQLite in-memory (`sqlite:///:memory:`) — no external database.

**Test file:** `tests/test_sql_logger.py`

---

## Phase 6 — Observability

**Milestone:** Prometheus metrics and OpenTelemetry trace context.

**Satisfies:** NFR-3 (extended to metrics), UN-7 (operational deployment)

### `airlock/callbacks/metrics.py` (new)

Prometheus counters/histograms:
- `airlock_requests_total{model, user, success}`
- `airlock_request_duration_seconds{model}`
- `airlock_pii_redactions_total{entity_type}`
- `airlock_keyword_blocks_total`
- `airlock_circuit_breaker_state{model}` (gauge)
- `airlock_threat_blocks_total`

### `airlock/callbacks/tracing.py` (new)

OpenTelemetry trace context propagation — spans for each guardrail execution
and upstream LLM call.

---

## Phase 7 — Deployment Hardening

**Milestone:** CI/CD pipeline, Kubernetes manifests, container security.

**Satisfies:** NFR-1, NFR-3, NFR-4

### `.github/workflows/ci.yml` (new)

1. Python 3.12, `pip install -e ".[test]"`
2. `pytest --cov --cov-report=xml`
3. Lint (ruff), type check (mypy for `fast/` subsystem)
4. Docker build verification
5. `pip audit` for dependency vulnerabilities

### `deploy/k8s/` (new directory)

- `deployment.yaml`, `service.yaml`, `configmap.yaml`, `secret.yaml`
- `ingress.yaml`, `hpa.yaml` (horizontal pod autoscaler)

### Dockerfile hardening

- `USER airlock` (non-root), `.dockerignore`, pinned base image digest

---

## Phase 8 — Advanced Guardrails (Future)

**Milestone:** Patterns from `dev/feature-guardrails-deterministic-control-loops.md`.

**Satisfies:** UN-8 (extensible guardrails)

- **Semantic alignment guard** — vector distance to known attack/forbidden patterns
- **Tool-call sandbox guard** — intercept and policy-check agentic tool calls
- **Auditor loop guard** — drafter-auditor pattern with checklist verification

---

## Phase Dependencies

```
Phase 0 (test foundation)
    ├──→ Phase 1 (core tests) ──────┐
    ├──→ Phase 2 (fast tests) ──────┼──→ Phase 4 (integration tests)
    └──→ Phase 3 (slow tests)       │
                                    ├──→ Phase 5 (S3 + SQL backends)
                                    ├──→ Phase 6 (observability)
                                    │        └──→ Phase 7 (deployment)
                                    └──→ Phase 8 (advanced guardrails)
```

Phases 1, 2, and 3 can proceed in parallel after Phase 0.

## Requirement Traceability

| Requirement | Phase | Test File |
|------------|-------|-----------|
| FR-4: PII redaction | 1 | `test_pii_guard.py` |
| FR-5: Configurable PII entities | 1 | `test_pii_guard.py` |
| FR-6: Keyword blocklist | 1 | `test_keyword_guard.py` |
| FR-7: Safe error reporting | 1 | `test_keyword_guard.py` |
| FR-8: JSONL logging | 1 | `test_enterprise_logger.py` |
| FR-9: 13-field log schema | 1 | `test_enterprise_logger.py` |
| FR-10: Configurable log dir | 1 | `test_enterprise_logger.py` |
| FR-13: Drop unsupported params | 1 | `test_proxy.py` |
| FR-14: Guardrail registration | 4 | `test_integration.py` |
| FR-15: Pre-call execution | 1, 4 | `test_pii_guard.py`, `test_keyword_guard.py`, `test_integration.py` |
| FR-16: Multi-part messages | 1 | `test_pii_guard.py`, `test_keyword_guard.py` |
| NFR-2: Env var config | 0 | `conftest.py` (autouse `clean_env`) |
| NFR-6: Graceful degradation | 1 | `test_pii_guard.py` |
| NFR-7: Serialization robustness | 1 | `test_enterprise_logger.py` |
| NFR-9: Guardrail execution order | 4 | `test_integration.py` |
| NFR-10: Log append safety | 1 | `test_enterprise_logger.py` |
| FR-11, FR-12: Virtual keys, budgets | — | Delegated to LiteLLM (not Airlock code) |
| Dynamic processing spec | 2, 3 | `test_fast_*.py`, `test_slow_*.py` |

## Verification

After each phase:
1. `pip install -e ".[test]"` installs cleanly
2. `pytest` passes with zero failures
3. `pytest --cov` shows coverage for the modules tested in that phase
4. No test depends on real LLM API calls, real AWS, or real databases
5. Tests run in any order (`pytest -p no:randomly` and `pytest --randomly` both pass)
