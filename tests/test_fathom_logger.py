import json
from unittest.mock import MagicMock, patch

from airlock.callbacks.fathom_logger import AirlockFathomLogger
from airlock.callbacks.request_event import build_request_event
from airlock.client_identity import NO_CLIENT_ID


# The historical fathom ``log_*_event``/``_log_event``/``_fathom_properties`` builders
# were deleted in the 0.5.4 cutover; the fathom sink now consumes the canonical event
# via ``record_event`` (project_fathom). This shim re-points the legacy behavior tests
# through that live path so their assertions on the emitted FathomDB row still hold.
def _emit(logger, kwargs, response_obj, start_time, end_time, *, success):
    event = build_request_event(
        kwargs, response_obj, start_time, end_time, success=success
    )
    logger.record_event(event)


def _written_item(engine_mock):
    """Return the single dict item of the single batch written to the engine."""
    engine_mock.write.assert_called_once()
    (batch,) = engine_mock.write.call_args[0]
    assert isinstance(batch, list) and len(batch) == 1
    return batch[0]


class MockUsage:
    def __init__(self, total_tokens):
        self.total_tokens = total_tokens


class MockResponse:
    def __init__(self, total_tokens):
        self.usage = MockUsage(total_tokens)


def test_fathom_logger_success():
    engine_mock = MagicMock()
    logger = AirlockFathomLogger(engine=engine_mock)

    kwargs = {
        "model": "gpt-4",
        "response_cost": 0.05,
        "litellm_call_id": "call-123",
        # The guardian stamps the authenticated identity at pre-call; the
        # sink must carry it through as the row's provenance.
        "litellm_params": {"metadata": {"airlock_source_id": "key:90abcdef"}},
    }
    response_obj = MockResponse(total_tokens=100)

    _emit(logger, kwargs, response_obj, None, None, success=True)

    item = _written_item(engine_mock)
    assert item["kind"] == "RequestLog"
    assert item["logical_id"] == "call-123"
    assert item["source_id"] == "key:90abcdef"
    properties = json.loads(item["body"])
    assert properties["model"] == "gpt-4"
    assert properties["total_tokens"] == 100
    assert properties["success"] is True
    assert properties["cost"] == 0.05
    assert properties["error_flag"] is False
    assert properties["call_id"] == "call-123"
    assert properties["request_id"] == "call-123"
    assert "timestamp" in properties


def test_fathom_logger_failure():
    engine_mock = MagicMock()
    logger = AirlockFathomLogger(engine=engine_mock)

    kwargs = {
        "model": "gpt-3.5",
        "response_cost": 0.01,
        "litellm_call_id": "call-456",
        "litellm_params": {"metadata": {}},
        "exception": RuntimeError("boom"),
    }
    response_obj = MockResponse(total_tokens=50)

    _emit(logger, kwargs, response_obj, None, None, success=False)

    item = _written_item(engine_mock)
    assert item["kind"] == "RequestLog"
    assert item["logical_id"] == "call-456"
    # No stamp on this event — the sink collapses to the no_client sentinel
    # rather than ever writing a row without a source_id.
    assert item["source_id"] == NO_CLIENT_ID
    properties = json.loads(item["body"])
    assert properties["model"] == "gpt-3.5"
    assert properties["total_tokens"] == 50
    assert properties["success"] is False
    assert properties["cost"] == 0.01
    assert properties["error_flag"] is True
    assert properties["call_id"] == "call-456"
    assert "timestamp" in properties


def test_fathom_logger_without_engine_writes_nothing():
    """No engine (fathomdb absent or disabled) means the sink is a no-op."""
    logger = AirlockFathomLogger()
    kwargs = {"model": "gpt-4", "litellm_call_id": "call-999"}

    with patch("airlock.datastore.get_engine", return_value=None) as mock_get:
        _emit(logger, kwargs, MockResponse(total_tokens=1), None, None, success=True)

    mock_get.assert_called_once()


def test_fathom_logger_skips_duplicate_call_ids():
    engine_mock = MagicMock()
    logger = AirlockFathomLogger(engine=engine_mock)

    kwargs = {"model": "gpt-4", "response_cost": 0.05, "litellm_call_id": "call-123"}
    response_obj = MockResponse(total_tokens=100)

    _emit(logger, kwargs, response_obj, None, None, success=True)
    _emit(logger, kwargs, response_obj, None, None, success=False)

    engine_mock.write.assert_called_once()


def test_fathom_logger_skips_when_metadata_requests_suppression():
    engine_mock = MagicMock()
    logger = AirlockFathomLogger(engine=engine_mock)
    kwargs = {
        "model": "gpt-4",
        "litellm_call_id": "call-123",
        "litellm_params": {"metadata": {"airlock_skip_fathom_logger": True}},
    }

    _emit(logger, kwargs, MockResponse(total_tokens=100), None, None, success=True)

    engine_mock.write.assert_not_called()


def test_fathom_logger_write_error_does_not_raise():
    """A typed engine error in the sink is logged, never propagated."""
    engine_mock = MagicMock()
    engine_mock.write.side_effect = RuntimeError("DatabaseLockedError: locked")
    logger = AirlockFathomLogger(engine=engine_mock)
    kwargs = {"model": "gpt-4", "litellm_call_id": "call-321"}

    _emit(logger, kwargs, MockResponse(total_tokens=1), None, None, success=True)

    engine_mock.write.assert_called_once()


def test_fathom_logger_opt_in_fields(monkeypatch):
    engine_mock = MagicMock()
    logger = AirlockFathomLogger(engine=engine_mock)
    monkeypatch.setenv("AIRLOCK_FATHOM_STORE_MESSAGES", "1")
    monkeypatch.setenv("AIRLOCK_FATHOM_STORE_RESPONSE_TEXT", "1")
    monkeypatch.setenv("AIRLOCK_FATHOM_STORE_HEADERS", "1")
    monkeypatch.setenv("AIRLOCK_FATHOM_STORE_CLIENT", "1")
    monkeypatch.setenv("AIRLOCK_FATHOM_STORE_USER_TEAM", "1")
    monkeypatch.setenv("AIRLOCK_FATHOM_STORE_ERROR_DETAILS", "1")
    monkeypatch.setenv("AIRLOCK_FATHOM_STORE_MCP_PAYLOADS", "1")

    kwargs = {
        "model": "gpt-4",
        "response_cost": 0.05,
        "litellm_call_id": "call-789",
        "messages": [{"role": "user", "content": "hi"}],
        "headers": {"x-test": "1"},
        "mcp_arguments": {"query": "secret"},
        "litellm_params": {
            "metadata": {
                "airlock_client": "client-1",
                "user_api_key_alias": "user-1",
                "user_api_key_team_alias": "team-1",
            }
        },
        "exception": RuntimeError("boom"),
    }

    _emit(logger, kwargs, MockResponse(total_tokens=100), None, None, success=False)

    properties = json.loads(_written_item(engine_mock)["body"])
    assert properties["airlock_client"] == "client-1"
    assert properties["user"] == "user-1"
    assert properties["team"] == "team-1"
    assert properties["error_type"] == "RuntimeError"
    assert properties["error"] == "boom"
    assert properties["messages_json"] is not None
    assert properties.get("response_text") is None or isinstance(
        properties.get("response_text"), str
    )
    assert properties["headers_json"] is not None
    assert properties["mcp_arguments_json"] is not None
