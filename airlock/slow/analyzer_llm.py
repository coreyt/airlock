"""Privacy-bounded, advisory LLM presentation for slow analysis."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

logger = logging.getLogger("airlock.slow.analyzer_llm")

#: Outcome of the most recent tool loop, so a caller (CLI, report) can explain
#: why an advisory analysis fell back instead of silently producing nothing.
_last_tool_loop: dict[str, Any] | None = None


def last_tool_loop() -> dict[str, Any] | None:
    """Metadata from the most recent advisory tool loop, or None."""
    return _last_tool_loop


@dataclass
class LLMFinding:
    narrative: str
    evidence_references: list[str]
    confidence: float
    proposed_actions: list[str]


class AnalyzerLLMClient(Protocol):
    """Provider-neutral completion seam; implementations receive aggregates only."""

    def complete(self, *, model: str, messages: list[dict[str, str]]) -> str: ...

    def complete_with_tools(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Any: ...


class LiteLLMAnalyzerClient:
    def complete(self, *, model: str, messages: list[dict[str, str]]) -> str:
        import litellm

        response = litellm.completion(model=model, messages=messages, timeout=20)
        return str(response.choices[0].message.content or "")

    def complete_with_tools(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Any:
        import litellm

        return (
            litellm.completion(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                timeout=20,
            )
            .choices[0]
            .message
        )


_ALLOWED_TOOLS = frozenset(
    {"summary", "optimizations", "semantic_insights", "hypotheses"}
)
_REMOTE_DROP_KEYS = frozenset(
    {
        "messages",
        "message",
        "response",
        "responses",
        "credentials",
        "credential",
        "api_key",
        "authorization",
    }
)
_REMOTE_TEXT_CAP = 500
_MAX_TOOL_ROUNDS = 3
_ANTHROPIC_CODE_EXECUTION_BETA = "code-execution-2025-08-25"
_ANTHROPIC_CODE_EXECUTION_TOOL = "code_execution_20250825"


def _analysis_tools() -> list[dict[str, Any]]:
    """The complete read-only tool surface exposed to normal analyzer models."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Read the derived {name.replace('_', ' ')} analysis section.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }
        for name in sorted(_ALLOWED_TOOLS)
    ]


def _tool_calls(message: Any) -> list[tuple[str, str, str]]:
    """Normalize LiteLLM/OpenAI-compatible tool calls without executing them."""
    raw_calls = getattr(message, "tool_calls", None)
    if raw_calls is None and isinstance(message, dict):
        raw_calls = message.get("tool_calls")
    calls: list[tuple[str, str, str]] = []
    for raw in raw_calls or []:
        function = getattr(raw, "function", None) or raw.get("function", {})
        name = getattr(function, "name", None) or function.get("name", "")
        call_id = getattr(raw, "id", None) or raw.get("id", "analysis-tool")
        arguments = getattr(function, "arguments", None) or function.get(
            "arguments", "{}"
        )
        calls.append((str(call_id), str(name), str(arguments)))
    return calls


def _message_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return str(content or "")


@dataclass(frozen=True)
class ToolLoopBudget:
    """Explicit bounds on one advisory tool loop.

    A round count alone is not a bound: three rounds against a slow model is
    unbounded in wall-clock, which is the dimension that actually hurts a CLI
    invocation or a TUI action.
    """

    max_rounds: int = _MAX_TOOL_ROUNDS
    max_seconds: float = 60.0
    max_tool_calls: int = 8  # across all rounds, not per round
    max_result_bytes: int = 64_000  # cap on what is fed back per tool result


#: Stop reasons. Previously every one of these returned a bare ``None``, so a
#: fallback was indistinguishable from "the model had nothing to say".
STOP_COMPLETED = "completed"
STOP_MAX_ROUNDS = "max_rounds"
STOP_TIMEOUT = "timeout"
STOP_MAX_TOOL_CALLS = "max_tool_calls"
STOP_DISALLOWED_TOOL = "disallowed_tool"
STOP_BAD_ARGUMENTS = "bad_arguments"
STOP_NO_CONTENT = "no_content"


@dataclass
class ToolLoopOutcome:
    """Result of a tool loop, including why it stopped."""

    content: str | None
    stop_reason: str
    rounds: int = 0
    tool_calls: int = 0
    elapsed_seconds: float = 0.0
    truncated_results: int = 0

    @property
    def succeeded(self) -> bool:
        return self.content is not None and self.stop_reason == STOP_COMPLETED

    def as_metadata(self) -> dict[str, Any]:
        return {
            "stop_reason": self.stop_reason,
            "rounds": self.rounds,
            "tool_calls": self.tool_calls,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "truncated_results": self.truncated_results,
        }


def _run_tool_loop(
    client: AnalyzerLLMClient,
    *,
    model: str,
    audience: str,
    payload: dict[str, Any],
    budget: ToolLoopBudget | None = None,
) -> ToolLoopOutcome:
    """Run aggregate-only tool rounds under an explicit budget.

    Returns a :class:`ToolLoopOutcome` so the caller can record *why* a
    fallback happened. The tools remain argument-free reveals of precomputed
    aggregates; parameterized querying is deferred to 0.5.10, where it can be
    served by the bounded reader in :mod:`airlock.log_query`.
    """
    budget = budget or ToolLoopBudget()
    started = time.monotonic()
    rounds = 0
    tool_calls = 0
    truncated_results = 0

    def elapsed() -> float:
        return time.monotonic() - started

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are an advisory Airlock analyst. Use only the supplied analysis "
                "tools. You cannot access files, shell commands, network, credentials, "
                "configuration, or enforcement. Return JSON findings after querying data."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Prepare a {audience} analysis. Return JSON only: "
                '[{"narrative":str,"evidence_references":[str],"confidence":number,'
                '"proposed_actions":[str]}]. Proposed actions are advisory only.'
            ),
        },
    ]

    def _outcome(content: str | None, reason: str) -> ToolLoopOutcome:
        return ToolLoopOutcome(
            content=content,
            stop_reason=reason,
            rounds=rounds,
            tool_calls=tool_calls,
            elapsed_seconds=elapsed(),
            truncated_results=truncated_results,
        )

    for _ in range(budget.max_rounds):
        if elapsed() > budget.max_seconds:
            return _outcome(None, STOP_TIMEOUT)
        rounds += 1
        message = client.complete_with_tools(
            model=model, messages=messages, tools=_analysis_tools()
        )
        calls = _tool_calls(message)
        if not calls:
            content = _message_content(message)
            if not content:
                return _outcome(None, STOP_NO_CONTENT)
            return _outcome(content, STOP_COMPLETED)
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls is None and isinstance(message, dict):
            raw_tool_calls = message.get("tool_calls")
        messages.append(
            {
                "role": "assistant",
                "content": _message_content(message),
                "tool_calls": raw_tool_calls,
            }
        )
        for call_id, name, arguments in calls:
            tool_calls += 1
            if tool_calls > budget.max_tool_calls:
                return _outcome(None, STOP_MAX_TOOL_CALLS)
            if name not in _ALLOWED_TOOLS:
                return _outcome(None, STOP_DISALLOWED_TOOL)
            try:
                parsed = json.loads(arguments)
            except json.JSONDecodeError:
                return _outcome(None, STOP_BAD_ARGUMENTS)
            # Tools are argument-free by design in Phase A.
            if parsed not in ({}, None):
                return _outcome(None, STOP_BAD_ARGUMENTS)
            serialized = json.dumps(payload[name], default=str)
            if len(serialized) > budget.max_result_bytes:
                serialized = serialized[: budget.max_result_bytes]
                truncated_results += 1
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": serialized,
                }
            )
    return _outcome(None, STOP_MAX_ROUNDS)


def reduced_dataset(report: Any) -> dict[str, Any]:
    """Return derived aggregates only; never serialize raw log records."""
    return {
        "summary": report.summary,
        "optimizations": [asdict(v) for v in report.optimizations],
        "semantic_insights": asdict(report.semantic_insights)
        if report.semantic_insights
        else None,
        "hypotheses": [asdict(v) for v in report.hypotheses],
        "guardrail_tuning": report.guardrail_tuning,
    }


def _sanitize_remote(value: Any, key: str = "") -> Any:
    """Minimize remote input: remove raw payload fields and cap evidence text."""
    if key.lower() in _REMOTE_DROP_KEYS:
        return "[omitted]"
    if isinstance(value, dict):
        return {str(k): _sanitize_remote(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_remote(v) for v in value]
    if isinstance(value, str):
        # Derived evidence can still contain accidental secret-shaped strings.
        text = value.replace("sk-", "[redacted]-")
        return text[:_REMOTE_TEXT_CAP]
    return value


def remote_dataset(report: Any) -> dict[str, Any]:
    """Remote-safe subset of the deterministic report; never includes JSONL rows."""
    return _sanitize_remote(reduced_dataset(report))


class AnthropicSandboxExecutor:
    """Explicit opt-in remote executor over minimized derived analysis data.

    The standard path requires both the Anthropic opt-in and an explicit
    ``code_execution`` capability opt-in. It uses Anthropic's server-side code
    execution sandbox beta; Airlock exposes no filesystem, shell, network, or
    configuration tool. Failures return ``None`` so deterministic analysis is
    always the authority.
    """

    def __init__(self, executor: Any | None = None) -> None:
        self._executor = executor

    def run(self, report: Any, audience: str) -> str | None:
        if os.getenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX", "").lower() != "anthropic":
            return None
        if (
            os.getenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY", "").lower()
            != "code_execution"
        ):
            return None
        dataset = remote_dataset(report)
        if self._executor is not None:
            try:
                return self._executor(dataset=dataset, audience=audience)
            except Exception:
                return None
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        try:
            from anthropic import Anthropic

            client = Anthropic()
            response = client.beta.messages.create(
                model=os.getenv("AIRLOCK_ANALYZER_REMOTE_MODEL", "claude-sonnet-4-5"),
                max_tokens=1000,
                betas=[_ANTHROPIC_CODE_EXECUTION_BETA],
                tools=[
                    {"type": _ANTHROPIC_CODE_EXECUTION_TOOL, "name": "code_execution"}
                ],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Provide advisory JSON findings only from this minimized dataset. "
                            "The code-execution sandbox has no Airlock file, shell, network, "
                            "credential, configuration, or enforcement capability. Do not request "
                            "external data. " + json.dumps(dataset)
                        ),
                    }
                ],
            )
            return "".join(getattr(part, "text", "") for part in response.content)
        except Exception:
            return None


def analyze_with_llm(
    report: Any, audience: str, client: AnalyzerLLMClient | None = None
) -> list[LLMFinding] | None:
    """Ask an opted-in model for advisory findings; fail closed to ``None``.

    The small fixed query surface is a bounded client-side tool loop: no model
    supplied tool name is executed unless it belongs to ``_ALLOWED_TOOLS``.
    """
    if audience not in {"ops", "security", "executive"}:
        raise ValueError("audience must be ops, security, or executive")
    remote_enabled = (
        os.getenv("AIRLOCK_ANALYZER_REMOTE_SANDBOX", "").lower() == "anthropic"
    )
    if not remote_enabled and not os.getenv("AIRLOCK_ANALYZER_MODEL"):
        return None
    payload = reduced_dataset(report)
    try:
        raw = AnthropicSandboxExecutor().run(report, audience)
        if raw is None:
            model = os.getenv("AIRLOCK_ANALYZER_MODEL")
            if not model:
                return None
            llm_client = client or LiteLLMAnalyzerClient()
            outcome = _run_tool_loop(
                llm_client, model=model, audience=audience, payload=payload
            )
            # Record why a loop ended so a fallback is attributable rather than
            # silent; previously every failure path returned a bare None.
            global _last_tool_loop
            _last_tool_loop = outcome.as_metadata()
            if not outcome.succeeded:
                logger.info(
                    "analyzer_tool_loop_incomplete stop_reason=%s rounds=%d",
                    outcome.stop_reason,
                    outcome.rounds,
                )
                return None
            raw = outcome.content
        value = json.loads(raw)
        if not isinstance(value, list):
            return None
        findings = []
        for item in value:
            if not isinstance(item, dict):
                return None
            refs = item.get("evidence_references", [])
            actions = item.get("proposed_actions", [])
            if (
                not isinstance(refs, list)
                or not all(isinstance(reference, str) for reference in refs)
                or any(reference not in _ALLOWED_TOOLS for reference in refs)
                or not isinstance(actions, list)
                or not all(isinstance(action, str) for action in actions)
            ):
                return None
            confidence = float(item.get("confidence", 0))
            if not 0 <= confidence <= 1:
                return None
            findings.append(
                LLMFinding(
                    str(item.get("narrative", "")),
                    refs,
                    confidence,
                    actions,
                )
            )
        return findings
    except Exception:
        return None
