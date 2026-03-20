"""Shared live harness helpers for proxy round-trip matrix tests."""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Per-provider rate-limit protection
# ---------------------------------------------------------------------------

# Conservative minimum gap (seconds) between harness requests to each provider.
# Mistral magistral-medium-2509 has very tight limits; 10 s keeps the harness
# well inside observed quotas even across repeated pytest runs.
PROVIDER_MIN_INTERVAL: dict[str, float] = {
    "mistral": 10.0,
    "anthropic": 2.0,
    "openai": 2.0,
    "gemini": 2.0,
}
_DEFAULT_MIN_INTERVAL = 2.0

# File-backed throttle state — survives across pytest runs in the same session.
_THROTTLE_FILE = Path(tempfile.gettempdir()) / ".airlock_harness_throttle.json"

# Providers that returned 429 this run — remaining cases are skipped.
_provider_rate_limited: set[str] = set()


def _load_last_requests() -> dict[str, float]:
    try:
        return json.loads(_THROTTLE_FILE.read_text())
    except Exception:
        return {}


def _save_last_request(provider: str, ts: float) -> None:
    state = _load_last_requests()
    state[provider] = ts
    try:
        _THROTTLE_FILE.write_text(json.dumps(state))
    except Exception:
        pass


@dataclass(frozen=True)
class LiveMatrixCase:
    """Single live proxy round-trip scenario."""

    id: str
    request_model: str
    provider: str | None = None
    prompt: str = "Reply with exactly OK."
    max_tokens: int = 8
    expect_override_header: bool = False
    require_text_content: bool = False
    extra_payload: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        data = {
            "model": self.request_model,
            "messages": [{"role": "user", "content": self.prompt}],
            "max_tokens": self.max_tokens,
        }
        data.update(self.extra_payload)
        return data


def _config_path() -> Path:
    raw = os.getenv("AIRLOCK_CONFIG", "config.yaml")
    return Path(raw)


def _provider_from_entry(entry: dict[str, Any]) -> str | None:
    model = entry.get("litellm_params", {}).get("model", "")
    if isinstance(model, str) and "/" in model:
        return model.split("/", 1)[0]
    return None


def configured_alias_cases() -> list[LiveMatrixCase]:
    """Build live cases from the active Airlock config model_list."""
    path = _config_path()
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    cases: list[LiveMatrixCase] = []
    for entry in data.get("model_list", []):
        alias = entry.get("model_name")
        if not alias:
            continue
        cases.append(
            LiveMatrixCase(
                id=str(alias),
                request_model=str(alias),
                provider=_provider_from_entry(entry),
                prompt="Reply with exactly OK.",
                max_tokens=8,
            )
        )
    return cases


class LiveProxyMatrixBase:
    """Reusable assertions for live proxy round-trip harness tests."""

    log_poll_timeout_seconds = 20.0
    log_poll_interval_seconds = 0.5

    def _live_log_dir(self) -> Path:
        raw = (
            os.getenv("AIRLOCK_LIVE_LOG_DIR")
            or os.getenv("AIRLOCK_LOG_DIR")
            or "logs"
        )
        return Path(raw)

    def _today_log_path(self) -> Path:
        today = datetime.date.today().isoformat()
        return self._live_log_dir() / f"airlock-{today}.jsonl"

    def _client_id_for_case(self, case: LiveMatrixCase) -> str:
        return f"harness-live:{case.id}"

    async def _throttle_for_provider(self, provider: str | None) -> None:
        if not provider:
            return
        min_interval = PROVIDER_MIN_INTERVAL.get(provider, _DEFAULT_MIN_INTERVAL)
        last = _load_last_requests().get(provider, 0.0)
        wait = min_interval - (time.time() - last)
        if wait > 0:
            await asyncio.sleep(wait)
        _save_last_request(provider, time.time())

    async def _send_case(self, http_client, case: LiveMatrixCase):
        provider = case.provider or ""
        if provider and provider in _provider_rate_limited:
            pytest.skip(
                f"{provider} rate-limited earlier in this run — skipping {case.id}"
            )
        await self._throttle_for_provider(case.provider)
        client_id = self._client_id_for_case(case)
        response = await http_client.post(
            "/v1/chat/completions",
            json=case.payload(),
            headers={"X-Airlock-Client": client_id},
        )
        if response.status_code == 429 and provider:
            _provider_rate_limited.add(provider)
        return client_id, response

    def _find_log_record(self, call_id: str) -> dict[str, Any] | None:
        log_path = self._today_log_path()
        if not log_path.is_file():
            return None
        lines = log_path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("request_id") == call_id:
                return record
        return None

    def _wait_for_log_record(self, call_id: str) -> dict[str, Any]:
        log_dir = self._live_log_dir()
        if not log_dir.exists():
            pytest.skip(f"live log dir not accessible: {log_dir}")

        deadline = time.time() + self.log_poll_timeout_seconds
        while time.time() < deadline:
            record = self._find_log_record(call_id)
            if record is not None:
                return record
            time.sleep(self.log_poll_interval_seconds)
        raise AssertionError(
            f"Timed out waiting for log record request_id={call_id} in {self._today_log_path()}"
        )

    def _assert_ingress_evidence(
        self,
        record: dict[str, Any],
        case: LiveMatrixCase,
        client_id: str,
    ) -> None:
        assert record["success"] is True
        assert record["airlock_client"] == client_id
        assert "airlock_priority" in record
        assert "airlock_request" in record

        airlock_request = record["airlock_request"]
        assert airlock_request["client_id"] == client_id
        assert airlock_request["requested_model"] == case.request_model
        if case.provider is not None:
            assert record["airlock_provider"] == case.provider

    def _assert_provider_response_success(
        self,
        response,
        case: LiveMatrixCase,
    ) -> dict[str, Any]:
        assert response.status_code == 200, response.text
        body = response.json()
        assert "choices" in body
        assert body["choices"]
        assert "usage" in body
        choice = body["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content")
        tool_calls = message.get("tool_calls")
        function_call = message.get("function_call")

        has_text_content = False
        if isinstance(content, str):
            has_text_content = bool(content.strip())
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    if str(part.get("text", "")).strip():
                        has_text_content = True
                        break

        has_tool_payload = bool(tool_calls) or bool(function_call)
        text_tokens = (
            (
                body.get("usage", {})
                .get("completion_tokens_details", {})
                .get("text_tokens")
            )
            or 0
        )

        if case.require_text_content:
            assert has_text_content, body
            assert text_tokens > 0, body
        else:
            assert (
                has_text_content
                or has_tool_payload
                or text_tokens == 0
            ), body
        return body

    def _assert_egress_evidence(
        self,
        response,
        record: dict[str, Any],
        case: LiveMatrixCase,
    ) -> None:
        response_headers = response.headers
        override = (
            response_headers.get("X-Airlock-Model-Override")
            or response_headers.get("x-airlock-model-override")
        )

        # Response-side evidence from the proxy pipeline/logging.
        assert (
            "airlock_observation" in record
            or "airlock_semantic" in record
            or "airlock_model_override" in record
        )

        if case.expect_override_header:
            assert override, "expected X-Airlock-Model-Override response header"
            assert "airlock_model_override" in record
            assert record["airlock_model_override"]["final_model"] == override
        else:
            assert not override

        if case.provider == "gemini":
            assert response_headers.get("X-Airlock-Provider-Mode") == "gemini"
            assert response_headers.get("X-Airlock-Provider-State")
            assert response_headers.get("X-Airlock-Reasoning-Mode")

    async def assert_live_round_trip(self, http_client, case: LiveMatrixCase) -> None:
        client_id, response = await self._send_case(http_client, case)
        self._assert_provider_response_success(response, case)

        call_id = response.headers.get("x-litellm-call-id")
        assert call_id, "expected x-litellm-call-id response header"

        record = self._wait_for_log_record(call_id)
        self._assert_ingress_evidence(record, case, client_id)
        self._assert_egress_evidence(response, record, case)
