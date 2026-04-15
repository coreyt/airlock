from unittest.mock import MagicMock, patch

from airlock.callbacks.fathom_logger import AirlockFathomLogger


class MockUsage:
    def __init__(self, total_tokens):
        self.total_tokens = total_tokens


class MockResponse:
    def __init__(self, total_tokens):
        self.usage = MockUsage(total_tokens)


def test_fathom_logger_success():
    engine_mock = MagicMock()
    logger = AirlockFathomLogger(engine=engine_mock)

    kwargs = {"model": "gpt-4", "response_cost": 0.05, "litellm_call_id": "call-123"}
    response_obj = MockResponse(total_tokens=100)

    with patch("airlock.callbacks.fathom_logger.WriteRequestBuilder") as MockBuilder:
        builder_instance = MockBuilder.return_value
        builder_instance.build.return_value = "mock_request"

        logger.log_success_event(kwargs, response_obj, None, None)

        builder_instance.add_node.assert_called_once()
        call_kwargs = builder_instance.add_node.call_args[1]
        assert call_kwargs["kind"] == "RequestLog"
        assert call_kwargs["logical_id"] == "call-123"
        assert call_kwargs["upsert"] is True
        assert call_kwargs["row_id"] != "call-123"
        assert call_kwargs["properties"]["model"] == "gpt-4"
        assert call_kwargs["properties"]["total_tokens"] == 100
        assert call_kwargs["properties"]["cost"] == 0.05
        assert call_kwargs["properties"]["error_flag"] is False
        assert call_kwargs["properties"]["call_id"] == "call-123"
        assert "timestamp" in call_kwargs["properties"]

        engine_mock.write.assert_called_with("mock_request")


def test_fathom_logger_failure():
    engine_mock = MagicMock()
    logger = AirlockFathomLogger(engine=engine_mock)

    kwargs = {"model": "gpt-3.5", "response_cost": 0.01, "litellm_call_id": "call-456"}
    response_obj = MockResponse(total_tokens=50)

    with patch("airlock.callbacks.fathom_logger.WriteRequestBuilder") as MockBuilder:
        builder_instance = MockBuilder.return_value
        builder_instance.build.return_value = "mock_request"

        logger.log_failure_event(kwargs, response_obj, None, None)

        builder_instance.add_node.assert_called_once()
        call_kwargs = builder_instance.add_node.call_args[1]
        assert call_kwargs["kind"] == "RequestLog"
        assert call_kwargs["logical_id"] == "call-456"
        assert call_kwargs["upsert"] is True
        assert call_kwargs["row_id"] != "call-456"
        assert call_kwargs["properties"]["model"] == "gpt-3.5"
        assert call_kwargs["properties"]["total_tokens"] == 50
        assert call_kwargs["properties"]["cost"] == 0.01
        assert call_kwargs["properties"]["error_flag"] is True
        assert call_kwargs["properties"]["call_id"] == "call-456"
        assert "timestamp" in call_kwargs["properties"]

        engine_mock.write.assert_called_with("mock_request")


def test_fathom_logger_no_fathomdb():
    engine_mock = MagicMock()
    logger = AirlockFathomLogger(engine=engine_mock)
    kwargs = {"model": "gpt-4"}

    with patch("airlock.callbacks.fathom_logger.WriteRequestBuilder", None):
        logger.log_success_event(kwargs, None, None, None)
        engine_mock.write.assert_not_called()
