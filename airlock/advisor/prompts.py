"""Advisor prompt construction."""

from __future__ import annotations

import json
from typing import Any

_MAX_RESULT_CHARS = 50_000


def build_system_prompt() -> str:
    return (
        "You are the Airlock Advisor. You help administrators diagnose "
        "and resolve issues with the Airlock LLM proxy.\n\n"
        "Use tools to query operational data. Never guess.\n\n"
        "Key concepts:\n"
        "- Circuit breaker: CLOSED->OPEN (5 failures)->HALF_OPEN->CLOSED\n"
        "- Threat detector: scores 0-1, blocks at >=0.7\n"
        "- Guardrail chain: PII->keyword->guardian->enforcer->semantic->orchestrator\n"
        "- Smart router: complexity->cost tier\n"
        "- Enforcement: observe vs enforce\n\n"
        'When you identify a fix, output: ACTION: {"type": "config_change", ...}\n'
    )


def build_tool_descriptions(registry: dict) -> list[dict]:
    result = []
    for name, (_, schema) in registry.items():
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": schema.get("description", ""),
                    "parameters": schema,
                },
            }
        )
    return result


def format_tool_result(tool_name: str, result: Any) -> str:
    text = json.dumps(result, indent=2, default=str)
    if len(text) > _MAX_RESULT_CHARS:
        return text[:_MAX_RESULT_CHARS] + f"\n... (truncated, {len(text)} chars total)"
    return text
