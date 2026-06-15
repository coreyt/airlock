"""Tests for airlock/guardrails/extract.py — unified text extraction."""

from __future__ import annotations

from airlock.guardrails.extract import (
    extract_text,
    extract_text_from_mcp,
    extract_text_from_messages,
    is_batch_call,
    is_mcp_call,
)


# ---------------------------------------------------------------------------
# extract_text_from_messages()
# ---------------------------------------------------------------------------
class TestExtractTextFromMessages:
    def test_string_content(self):
        messages = [{"role": "user", "content": "Hello world"}]
        assert extract_text_from_messages(messages) == "Hello world"

    def test_multipart_text(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Part A"},
                    {"type": "text", "text": "Part B"},
                ],
            }
        ]
        result = extract_text_from_messages(messages)
        assert "Part A" in result
        assert "Part B" in result

    def test_image_parts_ignored(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                    },
                ],
            }
        ]
        result = extract_text_from_messages(messages)
        assert "Describe this" in result
        assert "base64" not in result

    def test_multiple_messages(self):
        messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Question"},
        ]
        result = extract_text_from_messages(messages)
        assert "Be helpful" in result
        assert "Question" in result

    def test_empty(self):
        assert extract_text_from_messages([]) == ""

    def test_missing_content(self):
        assert extract_text_from_messages([{"role": "user"}]) == ""


# ---------------------------------------------------------------------------
# extract_text_from_mcp()
# ---------------------------------------------------------------------------
class TestExtractTextFromMCP:
    def test_tool_name_included(self):
        data = {"mcp_tool_name": "read_file", "mcp_arguments": {}}
        result = extract_text_from_mcp(data)
        assert "read_file" in result

    def test_string_arguments(self):
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"query": "secret project", "limit": "10"},
        }
        result = extract_text_from_mcp(data)
        assert "search" in result
        assert "secret project" in result
        assert "10" in result

    def test_numeric_arguments(self):
        data = {
            "mcp_tool_name": "calculate",
            "mcp_arguments": {"value": 42, "factor": 3.14},
        }
        result = extract_text_from_mcp(data)
        assert "42" in result
        assert "3.14" in result

    def test_empty_arguments(self):
        data = {"mcp_tool_name": "list_tools", "mcp_arguments": {}}
        result = extract_text_from_mcp(data)
        assert "list_tools" in result

    def test_string_args_value(self):
        data = {"mcp_tool_name": "exec", "mcp_arguments": "raw command string"}
        result = extract_text_from_mcp(data)
        assert "raw command string" in result

    def test_synthetic_messages_included(self):
        data = {
            "mcp_tool_name": "read_file",
            "mcp_arguments": {"path": "/tmp/file"},
            "messages": [{"role": "user", "content": "synthetic message"}],
        }
        result = extract_text_from_mcp(data)
        assert "read_file" in result
        assert "/tmp/file" in result
        assert "synthetic message" in result

    def test_no_tool_name(self):
        data = {"mcp_arguments": {"key": "val"}}
        result = extract_text_from_mcp(data)
        assert "val" in result

    def test_bool_arguments(self):
        data = {"mcp_tool_name": "toggle", "mcp_arguments": {"flag": True}}
        result = extract_text_from_mcp(data)
        assert "True" in result

    def test_nested_dict_arguments(self):
        """Nested dict values must be extracted — not silently dropped."""
        data = {
            "mcp_tool_name": "config",
            "mcp_arguments": {
                "options": {"path": "/etc/passwd", "recursive": True},
            },
        }
        result = extract_text_from_mcp(data)
        assert "/etc/passwd" in result
        assert "True" in result

    def test_nested_list_arguments(self):
        """List values in arguments must be extracted."""
        data = {
            "mcp_tool_name": "batch",
            "mcp_arguments": {
                "files": ["secret-doc.txt", "internal-plan.md"],
            },
        }
        result = extract_text_from_mcp(data)
        assert "secret-doc.txt" in result
        assert "internal-plan.md" in result

    def test_deeply_nested_arguments(self):
        """Multi-level nesting should still be extracted."""
        data = {
            "mcp_tool_name": "complex",
            "mcp_arguments": {
                "config": {"nested": {"deep": "hidden-value"}},
            },
        }
        result = extract_text_from_mcp(data)
        assert "hidden-value" in result

    def test_none_argument_value(self):
        """None values in arguments should be handled gracefully."""
        data = {
            "mcp_tool_name": "test",
            "mcp_arguments": {"key": None, "other": "value"},
        }
        result = extract_text_from_mcp(data)
        assert "value" in result

    def test_empty_synthetic_messages_not_added(self):
        """Empty message extraction should not add blank lines."""
        data = {
            "mcp_tool_name": "test",
            "mcp_arguments": {"k": "v"},
            "messages": [{"role": "user"}],  # no content field
        }
        result = extract_text_from_mcp(data)
        assert result == "test\nv"  # no trailing newline from empty messages


# ---------------------------------------------------------------------------
# is_mcp_call()
# ---------------------------------------------------------------------------
class TestIsMCPCall:
    def test_call_type_matches(self):
        assert is_mcp_call({}, "call_mcp_tool") is True

    def test_mcp_tool_name_in_data(self):
        assert is_mcp_call({"mcp_tool_name": "search"}) is True

    def test_regular_call(self):
        assert is_mcp_call({"messages": []}, "completion") is False

    def test_empty_data(self):
        assert is_mcp_call({}) is False


# ---------------------------------------------------------------------------
# is_batch_call()
# ---------------------------------------------------------------------------
class TestIsBatchCall:
    def test_acreate_batch_call_type(self):
        assert is_batch_call({}, "acreate_batch") is True

    def test_create_batch_call_type(self):
        assert is_batch_call({}, "create_batch") is True

    def test_aretrieve_batch_call_type(self):
        assert is_batch_call({}, "aretrieve_batch") is True

    def test_acreate_file_call_type(self):
        assert is_batch_call({}, "acreate_file") is True

    def test_create_file_call_type(self):
        assert is_batch_call({}, "create_file") is True

    def test_afile_content_call_type(self):
        assert is_batch_call({}, "afile_content") is True

    def test_input_file_id_in_data(self):
        assert is_batch_call({"input_file_id": "file-abc"}) is True

    def test_purpose_batch_in_data(self):
        assert is_batch_call({"purpose": "batch"}) is True

    def test_regular_completion(self):
        assert is_batch_call({"messages": []}, "completion") is False

    def test_mcp_call(self):
        assert is_batch_call({"mcp_tool_name": "search"}, "call_mcp_tool") is False

    def test_empty_data(self):
        assert is_batch_call({}) is False

    def test_purpose_non_batch(self):
        assert is_batch_call({"purpose": "fine-tune"}) is False


# ---------------------------------------------------------------------------
# extract_text() dispatch
# ---------------------------------------------------------------------------
class TestExtractTextDispatch:
    def test_llm_path(self):
        data = {"messages": [{"role": "user", "content": "Hello"}]}
        result = extract_text(data, "completion")
        assert result == "Hello"

    def test_mcp_path_by_call_type(self):
        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"query": "find stuff"},
        }
        result = extract_text(data, "call_mcp_tool")
        assert "search" in result
        assert "find stuff" in result

    def test_mcp_path_by_data_key(self):
        data = {
            "mcp_tool_name": "read_file",
            "mcp_arguments": {"path": "/tmp/x"},
        }
        result = extract_text(data)
        assert "read_file" in result

    def test_empty_data_empty_result(self):
        assert extract_text({}) == ""

    def test_default_call_type(self):
        data = {"messages": [{"role": "user", "content": "test"}]}
        assert extract_text(data) == "test"
