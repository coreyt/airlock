"""
Airlock Reasoning Stripper — post-call guardrail that removes inline
"thinking" / reasoning blocks emitted by models whose delimiters vLLM
cannot natively parse.

Currently targets Kimi-Dev-72B, which wraps reasoning in non-standard
Unicode markers ``◁think▷ … ◁/think▷``. Those markers are NOT single
tokens in the Kimi tokenizer (`◁` + `think` + `▷`), so vLLM's
``--reasoning-parser`` machinery — which matches start/end by single
token id — cannot strip them. We do it gateway-side instead, so
downstream agents see only the post-thought content and JSON / tool-call
parsers don't choke on partial schema in the reasoning trace.

Scope: only applied to models listed in ``AIRLOCK_REASONING_STRIP_MODELS``
(comma-separated; default ``kimi-dev``). All other models pass through
untouched.

Both non-streaming and streaming response paths are handled.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from airlock.transparency import record_mutation

logger = logging.getLogger("airlock.guardrails.reasoning_stripper")

_START = "◁think▷"
_END = "◁/think▷"
_MAX_MARKER = max(len(_START), len(_END))


def _target_models() -> set[str]:
    raw = os.getenv("AIRLOCK_REASONING_STRIP_MODELS", "kimi-dev")
    return {m.strip() for m in raw.split(",") if m.strip()}


def _strip_blocks(text: str) -> str:
    """Remove all ``◁think▷ … ◁/think▷`` blocks from a complete string.

    Also strips an orphan trailing ``◁/think▷`` (some Kimi outputs begin
    the response with reasoning but omit the opening marker).
    """
    if _START not in text and _END not in text:
        return text

    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        start = text.find(_START, i)
        if start == -1:
            # Orphan end marker: drop everything up to and including it.
            orphan = text.find(_END, i)
            if orphan != -1:
                i = orphan + len(_END)
                # consume one leading newline after the closing marker
                if i < n and text[i] == "\n":
                    i += 1
                continue
            out.append(text[i:])
            break
        out.append(text[i:start])
        end = text.find(_END, start + len(_START))
        if end == -1:
            # Unterminated think block — drop the rest.
            break
        i = end + len(_END)
        if i < n and text[i] == "\n":
            i += 1
    return "".join(out)


class _StreamStripper:
    """Stateful filter that strips think blocks across streaming chunks.

    Keeps a small lookbehind buffer so a marker straddling two chunks is
    not emitted before being recognized.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False
        self.stripped_any = False

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out: list[str] = []
        while True:
            if self._in_think:
                end = self._buf.find(_END)
                if end != -1:
                    self._buf = self._buf[end + len(_END):]
                    if self._buf.startswith("\n"):
                        self._buf = self._buf[1:]
                    self._in_think = False
                    continue
                # Hold back up to MAX-1 chars in case a partial end marker
                # is split across chunks; drop the rest.
                if len(self._buf) >= _MAX_MARKER:
                    self._buf = self._buf[-(_MAX_MARKER - 1):]
                break

            start = self._buf.find(_START)
            if start != -1:
                out.append(self._buf[:start])
                self._buf = self._buf[start + len(_START):]
                self._in_think = True
                self.stripped_any = True
                continue

            # No start marker visible. Hold back only the longest suffix
            # of the buffer that could be the start of a marker; emit the
            # rest. This is what prevents a marker straddling chunks from
            # being emitted prematurely.
            keep = 0
            max_keep = min(len(self._buf), _MAX_MARKER - 1)
            for k in range(max_keep, 0, -1):
                suffix = self._buf[-k:]
                if _START.startswith(suffix) or _END.startswith(suffix):
                    keep = k
                    break
            emit_up_to = len(self._buf) - keep
            if emit_up_to:
                out.append(self._buf[:emit_up_to])
                self._buf = self._buf[emit_up_to:]
            break
        return "".join(out)

    def flush(self) -> str:
        if self._in_think:
            self._buf = ""
            return ""
        tail = self._buf
        self._buf = ""
        return tail


class AirlockReasoningStripper(CustomGuardrail):
    """Strip non-standard reasoning blocks for configured models only."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            supported_event_hooks=[GuardrailEventHooks.post_call], **kwargs
        )

    def _is_target(self, data: dict) -> bool:
        model = (data or {}).get("model") or ""
        # Match exact alias or alias prefix (e.g. "kimi-dev" matches
        # "kimi-dev" and "openai/kimi-dev").
        targets = _target_models()
        if model in targets:
            return True
        last = model.rsplit("/", 1)[-1]
        return last in targets

    async def async_post_call_success_hook(
        self,
        data: dict,
        user_api_key_dict: Any,  # noqa: ARG002
        response: Any,
    ) -> Any:
        if not self._is_target(data) or not hasattr(response, "choices"):
            return response

        stripped_any = False
        for choice in response.choices:
            msg = getattr(choice, "message", None)
            if not msg:
                continue
            content = getattr(msg, "content", None)
            if not content or not isinstance(content, str):
                continue
            new_content = _strip_blocks(content)
            if new_content != content:
                msg.content = new_content
                stripped_any = True

        if stripped_any:
            logger.debug("reasoning_stripped model=%s", data.get("model"))
            record_mutation(
                data.setdefault("metadata", {}),
                field="messages",
                op="rewrite",
                before=None,
                after=None,
                stage="post_call",
                source="reasoning_stripper",
            )
        return response

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Any,  # noqa: ARG002
        response: Any,
        request_data: dict,
    ) -> AsyncGenerator:
        if not self._is_target(request_data):
            async for chunk in response:
                yield chunk
            return

        strippers: dict[int, _StreamStripper] = {}
        recorded = False
        async for chunk in response:
            for choice in getattr(chunk, "choices", []) or []:
                delta = getattr(choice, "delta", None)
                if not delta:
                    continue
                content = getattr(delta, "content", None)
                if content is None or not isinstance(content, str):
                    continue
                idx = getattr(choice, "index", 0) or 0
                stripper = strippers.setdefault(idx, _StreamStripper())
                delta.content = stripper.feed(content)
                if not recorded and stripper.stripped_any:
                    record_mutation(
                        request_data.setdefault("metadata", {}),
                        field="messages",
                        op="rewrite",
                        before=None,
                        after=None,
                        stage="post_call",
                        source="reasoning_stripper.stream",
                    )
                    recorded = True
            yield chunk
