"""Gemini request mapping and response classification for Airlock."""

from __future__ import annotations

from typing import Any


_GEMINI_MODES = {"balanced", "deep_reasoning", "text_only", "tool_oriented"}
_GEMINI_VISIBILITY = {"final_only", "provider_native"}


def is_gemini_provider(model_name: str | None = None, provider: str | None = None) -> bool:
    """Return True when the request/response targets Gemini."""
    if provider == "gemini":
        return True
    return bool(model_name and model_name.startswith("gemini"))


def _normalize_mode(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in _GEMINI_MODES else "balanced"


def _normalize_visibility(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in _GEMINI_VISIBILITY else "final_only"


def _metadata_airlock_gemini(data: dict[str, Any]) -> dict[str, Any]:
    metadata = data.setdefault("metadata", {})
    airlock_meta = metadata.setdefault("airlock", {})
    gemini_meta = airlock_meta.get("gemini")
    if isinstance(gemini_meta, dict):
        return gemini_meta
    gemini_meta = {}
    airlock_meta["gemini"] = gemini_meta
    return gemini_meta


def apply_gemini_request_semantics(
    data: dict[str, Any],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """Map Airlock Gemini semantic controls onto LiteLLM-compatible params."""
    model_name = str(data.get("model") or "")
    if not is_gemini_provider(model_name, provider):
        return data

    gemini_meta = _metadata_airlock_gemini(data)
    mode = _normalize_mode(gemini_meta.get("mode"))
    visibility = _normalize_visibility(gemini_meta.get("visibility"))

    explicit_controls: list[str] = []
    for key in ("reasoning_effort", "thinking"):
        if key in data:
            explicit_controls.append(key)

    allow_empty_text = gemini_meta.get("allow_empty_text")
    if allow_empty_text is None:
        allow_empty_text = mode in {"deep_reasoning", "tool_oriented"}
    allow_empty_text = bool(allow_empty_text)

    mapping_source = "client_explicit" if explicit_controls else "airlock_semantic"
    warnings: list[str] = []

    if not explicit_controls:
        if mode == "deep_reasoning":
            data["reasoning_effort"] = "high"
        elif mode == "text_only":
            data["reasoning_effort"] = "disable"
        elif mode == "tool_oriented":
            # Preserve Gemini's native reasoning/tool behavior while signaling
            # that successful non-text completions are acceptable.
            pass
    elif mode != "balanced":
        warnings.append(
            "client_explicit_controls_override_airlock_gemini_mode"
        )

    metadata = data.setdefault("metadata", {})
    metadata["airlock_gemini"] = {
        "mode": mode,
        "visibility": visibility,
        "allow_empty_text": allow_empty_text,
        "mapping_source": mapping_source,
        "explicit_controls": explicit_controls,
        "provider": "gemini",
        "model": model_name,
    }
    if warnings:
        metadata["airlock_gemini"]["warnings"] = warnings

    return data


def _extract_text_content(message: dict[str, Any]) -> tuple[bool, str | None]:
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip()), content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = str(part.get("text", ""))
                if text.strip():
                    parts.append(text)
        joined = "\n".join(parts) if parts else None
        return bool(joined), joined
    return False, None


def classify_gemini_response_body(body: dict[str, Any]) -> dict[str, Any]:
    """Classify the normalized OpenAI-format Gemini response body."""
    choice = ((body.get("choices") or [{}])[0]) if isinstance(body, dict) else {}
    message = choice.get("message") or {}
    has_text_content, text_content = _extract_text_content(message)
    has_tool_calls = bool(message.get("tool_calls")) or bool(message.get("function_call"))

    completion_details = (
        (body.get("usage") or {}).get("completion_tokens_details") or {}
    )
    text_tokens = completion_details.get("text_tokens")
    reasoning_tokens = completion_details.get("reasoning_tokens")
    finish_reason = choice.get("finish_reason")

    if has_text_content and has_tool_calls:
        output_shape = "mixed"
    elif has_tool_calls:
        output_shape = "tool"
    elif has_text_content:
        output_shape = "text"
    elif text_tokens == 0 and (reasoning_tokens or 0) > 0:
        output_shape = "thought_only"
    else:
        output_shape = "empty"

    empty_text_success = output_shape in {"thought_only", "empty"}
    return {
        "output_shape": output_shape,
        "has_text_content": has_text_content,
        "has_tool_calls": has_tool_calls,
        "text_content": text_content,
        "text_tokens": text_tokens,
        "reasoning_tokens": reasoning_tokens,
        "finish_reason": finish_reason,
        "empty_text_success": empty_text_success,
    }


def classify_gemini_response(response_obj: Any) -> dict[str, Any] | None:
    """Serialize and classify a Gemini response object."""
    if response_obj is None:
        return None
    if hasattr(response_obj, "model_dump"):
        body = response_obj.model_dump()
    elif isinstance(response_obj, dict):
        body = response_obj
    else:
        return None
    if not isinstance(body, dict):
        return None
    return classify_gemini_response_body(body)


def build_gemini_response_headers(
    request_meta: dict[str, Any] | None,
    response_meta: dict[str, Any] | None,
) -> dict[str, str]:
    """Build outbound response headers for Gemini-aware requests."""
    request_meta = request_meta or {}
    response_meta = response_meta or {}
    headers: dict[str, str] = {
        "X-Airlock-Provider-Mode": "gemini",
        "X-Airlock-Reasoning-Mode": str(request_meta.get("mode") or "balanced"),
        "X-Airlock-Provider-State": str(response_meta.get("output_shape") or "unknown"),
        "X-Airlock-Empty-Text-Success": (
            "true" if response_meta.get("empty_text_success") else "false"
        ),
    }
    return headers
