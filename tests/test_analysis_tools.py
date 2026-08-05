"""Tests for parameterized advisory tools (0.5.9 finding F-1, Part B).

Two properties carry the design and are asserted rather than assumed:

  - every log-backed argument is bounded before it reaches a scan, because
    unbounded parameters are precisely what finding F-4 closed;
  - truncation reaches the model, because a tool result that silently omits
    records invites confident conclusions about traffic never seen.
"""

from __future__ import annotations

import json

import pytest

from airlock.slow.analysis_tools import (
    DEFAULT_LIMIT,
    MAX_DAYS,
    MAX_LIMIT,
    ToolArgumentError,
    execute,
    tool_definitions,
    validate_arguments,
)


def _payload(optimizations=None, hypotheses=None):
    return {
        "summary": {"total_requests": 10},
        "semantic_insights": {"classifier_stats": []},
        "optimizations": optimizations if optimizations is not None else [],
        "hypotheses": hypotheses if hypotheses is not None else [],
    }


def _opt(category="cost", impact="high", n=0):
    return {
        "category": category,
        "impact": impact,
        "description": f"opt-{n}",
        "evidence": {},
    }


class TestSchemasAreStrict:
    def test_every_tool_declares_additional_properties_false(self):
        """An unconstrained schema is how an unexpected argument reaches a scan."""
        for definition in tool_definitions():
            params = definition["function"]["parameters"]
            assert params["additionalProperties"] is False, definition["function"][
                "name"
            ]

    def test_unknown_argument_is_rejected_with_an_actionable_message(self):
        with pytest.raises(ToolArgumentError) as excinfo:
            validate_arguments("optimizations", {"lmit": 5})
        message = str(excinfo.value)
        assert "lmit" in message
        assert "limit" in message  # tells the model what was allowed

    def test_wrong_type_is_rejected(self):
        with pytest.raises(ToolArgumentError):
            validate_arguments("optimizations", {"limit": "five"})

    def test_bool_is_not_accepted_as_an_integer(self):
        """bool subclasses int in Python; accepting it would silently mean 0/1."""
        with pytest.raises(ToolArgumentError):
            validate_arguments("optimizations", {"limit": True})

    def test_enum_violation_is_rejected(self):
        with pytest.raises(ToolArgumentError):
            validate_arguments("optimizations", {"category": "nonsense"})

    def test_non_object_arguments_are_rejected(self):
        with pytest.raises(ToolArgumentError):
            validate_arguments("summary", [1, 2, 3])

    def test_argument_free_tool_rejects_any_argument(self):
        with pytest.raises(ToolArgumentError):
            validate_arguments("summary", {"days": 30})

    def test_absent_arguments_are_allowed(self):
        assert validate_arguments("summary", {}) == {}
        assert validate_arguments("summary", None) == {}


class TestBoundsAreEnforcedNotTrusted:
    def test_oversized_limit_is_clamped(self):
        assert validate_arguments("optimizations", {"limit": 10_000})["limit"] == (
            MAX_LIMIT
        )

    def test_oversized_days_is_clamped(self):
        assert validate_arguments("query_requests", {"days": 100_000})["days"] == (
            MAX_DAYS
        )

    def test_nonpositive_limit_is_raised_to_the_floor(self):
        assert validate_arguments("optimizations", {"limit": 0})["limit"] == 1
        assert validate_arguments("optimizations", {"limit": -5})["limit"] == 1

    def test_confidence_is_clamped_to_zero_one(self):
        assert (
            validate_arguments("hypotheses", {"min_confidence": 5.0})["min_confidence"]
            == 1.0
        )


class TestFiltersAndLimits:
    def test_limit_bounds_returned_rows(self):
        payload = _payload(optimizations=[_opt(n=i) for i in range(50)])
        result = execute("optimizations", {"limit": 5}, payload=payload)
        assert result["returned"] == 5
        assert result["total_available"] == 50

    def test_default_limit_applies_when_absent(self):
        payload = _payload(optimizations=[_opt(n=i) for i in range(50)])
        result = execute("optimizations", {}, payload=payload)
        assert result["returned"] == DEFAULT_LIMIT

    def test_category_filter_applies_before_the_limit(self):
        payload = _payload(
            optimizations=[_opt(category="cost", n=i) for i in range(3)]
            + [_opt(category="reliability", n=i) for i in range(7)]
        )
        result = execute("optimizations", {"category": "reliability"}, payload=payload)
        assert result["total_available"] == 7
        assert all(r["category"] == "reliability" for r in result["data"])

    def test_min_confidence_filters_hypotheses(self):
        payload = _payload(
            hypotheses=[
                {"statement": "a", "confidence": 0.9},
                {"statement": "b", "confidence": 0.2},
            ]
        )
        result = execute("hypotheses", {"min_confidence": 0.5}, payload=payload)
        assert result["total_available"] == 1
        assert result["data"][0]["statement"] == "a"

    def test_single_object_section_is_returned_whole(self):
        result = execute("summary", {}, payload=_payload())
        assert result["truncated"] is False
        assert result["data"] == {"total_requests": 10}


class TestTruncationReachesTheModel:
    def test_partial_result_is_flagged_and_counted(self):
        payload = _payload(optimizations=[_opt(n=i) for i in range(50)])
        result = execute("optimizations", {"limit": 5}, payload=payload)
        assert result["truncated"] is True
        assert result["returned"] == 5
        assert result["total_available"] == 50
        # Not merely a flag — the model is told not to treat it as complete.
        assert "complete picture" in result["note"]

    def test_complete_result_is_not_flagged(self):
        payload = _payload(optimizations=[_opt(n=i) for i in range(3)])
        result = execute("optimizations", {"limit": 10}, payload=payload)
        assert result["truncated"] is False
        assert "note" not in result

    def test_envelope_is_always_valid_json(self):
        payload = _payload(optimizations=[_opt(n=i) for i in range(50)])
        result = execute("optimizations", {"limit": 5}, payload=payload)
        assert json.loads(json.dumps(result, default=str))


class TestShrinkKeepsResultsParseable:
    def test_oversized_result_is_shrunk_by_rows_not_bytes(self):
        """The Part A bug: slicing serialized JSON at a byte offset produced a
        document the model could neither parse nor tell had been cut."""
        from airlock.slow.analyzer_llm import _shrink_result

        rows = [{"description": "x" * 200, "n": i} for i in range(200)]
        result = {
            "tool": "optimizations",
            "returned": len(rows),
            "total_available": len(rows),
            "truncated": False,
            "data": rows,
        }
        shrunk = _shrink_result(result, 2_000)

        serialized = json.dumps(shrunk, default=str)
        assert len(serialized) <= 2_000
        assert json.loads(serialized)  # still parseable — the whole point
        assert shrunk["truncated"] is True
        assert shrunk["returned"] == len(shrunk["data"]) < len(rows)
        assert "complete picture" in shrunk["note"]

    def test_shrink_leaves_a_fitting_result_alone(self):
        from airlock.slow.analyzer_llm import _shrink_result

        result = {
            "tool": "summary",
            "returned": 1,
            "truncated": False,
            "data": {"a": 1},
        }
        assert _shrink_result(result, 10_000) == result


class TestQueryRequestsIsBounded:
    def test_reports_unavailable_rather_than_scanning_an_unknown_directory(self):
        result = execute("query_requests", {}, payload=_payload(), log_dir=None)
        assert result["returned"] == 0
        assert "unavailable" in result["note"]

    def test_filters_by_model_and_reports_counts(self, tmp_path):
        records = [
            {
                "timestamp": "2026-08-05T12:00:00Z",
                "request_id": f"r{i}",
                "model": "gpt-4o" if i % 2 else "claude-opus-5",
                "airlock_client": "cli",
                "success": True,
            }
            for i in range(10)
        ]
        (tmp_path / "airlock-2026-08-05.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )
        result = execute(
            "query_requests",
            {"model": "gpt-4o", "days": 3650},
            payload=_payload(),
            log_dir=str(tmp_path),
        )
        assert result["matched"] == 5
        assert set(result["by_model"]) == {"gpt-4o"}

    def test_samples_carry_identifiers_never_message_content(self, tmp_path):
        secret = "ThisIsThePromptBody"
        (tmp_path / "airlock-2026-08-05.jsonl").write_text(
            json.dumps(
                {
                    "timestamp": "2026-08-05T12:00:00Z",
                    "request_id": "r1",
                    "model": "gpt-4o",
                    "airlock_client": "cli",
                    "success": True,
                    "messages": [{"role": "user", "content": secret}],
                }
            )
            + "\n"
        )
        result = execute(
            "query_requests",
            {"days": 3650},
            payload=_payload(),
            log_dir=str(tmp_path),
        )
        assert secret not in json.dumps(result)
        assert result["data"][0]["request_id"] == "r1"
