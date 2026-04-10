"""Advisor agent loop -- query LLMs about Airlock operational data."""

from __future__ import annotations

import inspect
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from airlock.advisor.audit import log_action
from airlock.advisor.model_select import select_advisor_model
from airlock.advisor.prompts import (
    build_system_prompt,
    build_tool_descriptions,
    format_tool_result,
)
from airlock.advisor.tools import TOOL_REGISTRY
from airlock.fast.state import StateStore


@dataclass
class AdvisorResult:
    answer: str
    tool_calls_made: list[str] = field(default_factory=list)
    actions_proposed: list[dict] = field(default_factory=list)
    model_used: str = ""
    is_local: bool = True
    iterations: int = 0
    error: str | None = None


def _load_config(config_path: str | None = None) -> dict:
    """Load config.yaml for model selection."""
    import yaml

    path = config_path or os.getenv("AIRLOCK_CONFIG", "config.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _call_llm(
    messages: list[dict],
    tools: list[dict],
    model: str,
    proxy_host: str,
    proxy_port: str,
    master_key: str | None,
) -> dict:
    """Send a chat completion request to the proxy. Returns parsed response."""
    url = f"http://{proxy_host}:{proxy_port}/v1/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools

    headers = {"Content-Type": "application/json"}
    if master_key:
        headers["Authorization"] = f"Bearer {master_key}"

    body_bytes = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    resp = urllib.request.urlopen(req, timeout=300)
    return json.loads(resp.read().decode("utf-8", errors="replace"))


def _parse_actions(text: str) -> list[dict]:
    """Extract ACTION: {...} blocks from response text."""
    actions = []
    for match in re.finditer(r"ACTION:\s*(\{[^}]+(?:\{[^}]*\}[^}]*)*\})", text):
        try:
            actions.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    return actions


def _execute_tool(
    tool_name: str,
    arguments: dict,
    store: StateStore | None,
    log_dir: str,
    config_path: str,
) -> str:
    """Execute a tool from TOOL_REGISTRY, injecting store/log_dir as needed."""
    if tool_name not in TOOL_REGISTRY:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    func, _ = TOOL_REGISTRY[tool_name]

    sig = inspect.signature(func)
    kwargs = dict(arguments)

    if "store" in sig.parameters and store is not None:
        kwargs["store"] = store
    if "log_dir" in sig.parameters:
        kwargs["log_dir"] = log_dir
    if "config_path" in sig.parameters:
        kwargs["config_path"] = config_path

    try:
        result = func(**kwargs)
        return format_tool_result(tool_name, result)
    except Exception as e:
        return json.dumps({"error": f"Tool {tool_name} failed: {e!s}"})


def run_advisor(
    question: str,
    *,
    proxy_host: str = "localhost",
    proxy_port: str = "4000",
    master_key: str | None = None,
    model: str | None = None,
    local_only: bool = False,
    max_iterations: int = 5,
    store: StateStore | None = None,
    config_path: str | None = None,
    log_dir: str | None = None,
) -> AdvisorResult:
    """Run the advisor agent loop."""
    result = AdvisorResult(answer="")

    # Resolve paths
    actual_config = config_path or os.getenv("AIRLOCK_CONFIG", "config.yaml")
    actual_log_dir = log_dir or os.getenv("AIRLOCK_LOG_DIR", "./logs")
    actual_master_key = master_key or os.getenv("AIRLOCK_MASTER_KEY")

    # Select model
    try:
        config = _load_config(actual_config)
        model_name, is_local = select_advisor_model(
            config,
            local_only=local_only,
            model_override=model,
        )
        result.model_used = model_name
        result.is_local = is_local
    except ValueError as e:
        result.error = str(e)
        return result

    # Build messages and tools
    system_prompt = build_system_prompt()
    tools = build_tool_descriptions(TOOL_REGISTRY)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    content = ""

    # Agent loop
    for iteration in range(max_iterations):
        result.iterations = iteration + 1

        try:
            response = _call_llm(
                messages,
                tools,
                model_name,
                proxy_host,
                proxy_port,
                actual_master_key,
            )
        except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
            result.error = f"LLM call failed: {e!s}"
            break

        # Extract response
        choices = response.get("choices", [])
        if not choices:
            result.error = "Empty response from LLM"
            break

        msg = choices[0].get("message", {})
        finish_reason = choices[0].get("finish_reason", "")
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls", [])

        # Add assistant message to conversation
        messages.append(msg)

        # If no tool calls, we're done
        if not tool_calls or finish_reason == "stop":
            result.answer = content
            result.actions_proposed = _parse_actions(content)
            break

        # Execute tool calls
        for tc in tool_calls:
            func_info = tc.get("function", {})
            tc_name = func_info.get("name", "")
            tc_id = tc.get("id", "")

            try:
                tc_args = json.loads(func_info.get("arguments", "{}"))
            except json.JSONDecodeError:
                tc_args = {}

            result.tool_calls_made.append(tc_name)

            tool_output = _execute_tool(
                tc_name,
                tc_args,
                store,
                actual_log_dir,
                actual_config,
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": tool_output,
                }
            )
    else:
        # Max iterations reached
        if not result.answer:
            result.answer = content if content else "(max iterations reached)"
            result.actions_proposed = _parse_actions(result.answer)

    # Audit log
    log_action(
        action_type="query",
        description=question[:200],
        outcome="success" if not result.error else "error",
        model_used=result.model_used,
        details={
            "iterations": result.iterations,
            "tool_calls": result.tool_calls_made,
            "actions_proposed": len(result.actions_proposed),
            "is_local": result.is_local,
        },
        log_dir=actual_log_dir,
    )

    return result
