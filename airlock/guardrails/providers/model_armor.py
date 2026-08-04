"""Google Model Armor provider for semantic prompt-injection detection.

Calls the Model Armor ``sanitizeUserPrompt`` REST endpoint directly with Google
credentials. It never routes through Airlock itself — a classifier request that
re-entered the proxy would recurse through the very guardrails it serves.

Only the ``pi_and_jailbreak`` filter is consumed. Model Armor's RAI, CSAM,
malicious-URI, and SDP filters answer different policy questions; in particular
SDP inspects text that Airlock has already redacted, so it is structurally
uninformative here (see the access witness). Ignoring them keeps this adapter's
verdict meaning exactly one thing.

Configuration
-------------
``AIRLOCK_MODEL_ARMOR_ENABLED``
    ``true`` to activate. Absent or false leaves the provider disabled.
``AIRLOCK_MODEL_ARMOR_TEMPLATE``
    Full template resource name,
    ``projects/<p>/locations/<l>/templates/<t>``.
``AIRLOCK_MODEL_ARMOR_TIMEOUT_SECONDS``
    Bounded per-probe timeout, default ``2.0``.
``AIRLOCK_MODEL_ARMOR_CREDENTIALS``
    Optional service-account JSON path. Falls back to
    ``GOOGLE_APPLICATION_CREDENTIALS``, then to application-default credentials
    or workload identity.

No credential is ever read from YAML, written to metadata, or retained in
corpus evidence.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any

from .base import (
    Availability,
    InjectionProvider,
    PreflightResult,
    ProviderVerdict,
    Transport,
)

logger = logging.getLogger("airlock.guardrails.providers.model_armor")

PROVIDER_NAME = "model_armor"

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_TEMPLATE_RE = re.compile(
    r"^projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)/templates/(?P<template>[^/]+)$"
)
_DEFAULT_TIMEOUT_SECONDS = 2.0

#: Model Armor rejects oversized prompts; bound the payload before sending so a
#: pathological request fails locally and fast rather than burning the timeout.
_MAX_PROBE_CHARS = 100_000


class ModelArmorConfigError(ValueError):
    """Raised at construction when the template resource name is unusable."""


class _GoogleTokenProvider:
    """Mints and caches OAuth tokens from Google credentials.

    ``google.auth`` is synchronous and its refresh performs blocking network
    I/O, so refreshes are pushed to a worker thread. The cached token is reused
    until ``google-auth`` reports it expired.
    """

    def __init__(self, credentials_path: str | None = None) -> None:
        self._credentials_path = credentials_path
        self._credentials: Any | None = None
        self._lock = asyncio.Lock()

    def _load(self) -> Any:
        if self._credentials is not None:
            return self._credentials
        path = self._credentials_path or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if path:
            from google.oauth2 import service_account

            self._credentials = service_account.Credentials.from_service_account_file(
                path, scopes=_SCOPES
            )
        else:
            import google.auth

            self._credentials, _ = google.auth.default(scopes=_SCOPES)
        return self._credentials

    def _refresh_blocking(self) -> str:
        import google.auth.transport.requests as gtr

        credentials = self._load()
        if not credentials.valid:
            credentials.refresh(gtr.Request())
        return str(credentials.token)

    async def token(self) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._refresh_blocking)


class ModelArmorProvider:
    """:class:`~airlock.guardrails.providers.base.InjectionProvider` for Model Armor."""

    def __init__(
        self,
        *,
        template: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: Transport | None = None,
        token_provider: Any | None = None,
        endpoint: str | None = None,
    ) -> None:
        match = _TEMPLATE_RE.match(template.strip())
        if not match:
            raise ModelArmorConfigError(
                "AIRLOCK_MODEL_ARMOR_TEMPLATE must be "
                "projects/<project>/locations/<location>/templates/<template>"
            )
        self._template = template.strip()
        self._project = match.group("project")
        self._location = match.group("location")
        self._template_label = match.group("template")
        self._timeout = max(0.1, float(timeout_seconds))
        self._transport = transport
        self._token_provider = token_provider
        self._endpoint = endpoint or (
            f"https://modelarmor.{self._location}.rep.googleapis.com/v1"
        )
        #: Filter version observed on the most recent successful probe. Recorded
        #: per verdict because it is the classifier's effective revision, and
        #: the access witness measured different detection behavior between
        #: filter versions.
        self._filter_version: str | None = None

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def describe(self) -> dict[str, str]:
        """Full provider identity, for retention in corpus/review evidence."""
        described = {
            "provider": PROVIDER_NAME,
            "service": "google_model_armor",
            "api_version": "v1",
            "template": self._template,
            "location": self._location,
            "timeout_seconds": str(self._timeout),
        }
        if self._filter_version:
            described["filter_version"] = self._filter_version
        return described

    # -- internals ---------------------------------------------------------
    def _ensure_transport(self) -> Transport:
        if self._transport is None:
            from .http import HttpxTransport

            self._transport = HttpxTransport()
        return self._transport

    def _ensure_token_provider(self) -> Any:
        if self._token_provider is None:
            self._token_provider = _GoogleTokenProvider(
                os.getenv("AIRLOCK_MODEL_ARMOR_CREDENTIALS")
            )
        return self._token_provider

    async def _headers(self) -> dict[str, str]:
        token = await self._ensure_token_provider().token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _parse(payload: dict[str, Any]) -> tuple[bool | None, str | None, str]:
        """Extract a verdict from a sanitize response.

        Returns ``(detected, confidence, error)``. ``detected is None`` always
        pairs with a non-empty ``error`` describing why no verdict exists.

        Strictness here is the point. In proto3 JSON an absent enum decodes as
        value 0 — ``FILTER_MATCH_STATE_UNSPECIFIED``, *not* ``NO_MATCH_FOUND``
        — so a response missing the filter block must never be read as clean.
        A template configured ``INSPECT_ONLY`` returns exactly that: HTTP 200,
        ``invocationResult: SUCCESS``, and no ``filterResults`` whatsoever.
        """
        sanitization = payload.get("sanitizationResult")
        if not isinstance(sanitization, dict):
            return None, None, "missing_sanitization_result"

        # InvocationResult: UNSPECIFIED(0) | SUCCESS(1) | PARTIAL(2) | FAILURE(3).
        # PARTIAL is accepted because it means *some* filter failed — the
        # per-filter executionState check below decides whether ours did.
        # FAILURE (and an absent/unspecified value) means the service did not
        # complete the request, so there is no verdict to read.
        invocation = sanitization.get("invocationResult")
        if invocation not in ("SUCCESS", "PARTIAL"):
            return None, None, f"invocation_result:{invocation or 'unspecified'}"

        filter_results = sanitization.get("filterResults")
        if not isinstance(filter_results, dict) or not filter_results:
            # The INSPECT_ONLY signature. Reported distinctly because the fix
            # is a template setting, not a credential or network problem.
            return None, None, "no_filter_results"

        node = filter_results.get("pi_and_jailbreak")
        result = (
            node.get("piAndJailbreakFilterResult") if isinstance(node, dict) else None
        )
        if not isinstance(result, dict):
            return None, None, "pi_and_jailbreak_filter_absent"

        execution_state = result.get("executionState")
        if execution_state != "EXECUTION_SUCCESS":
            return None, None, f"execution_state:{execution_state or 'unspecified'}"

        match_state = result.get("matchState")
        if match_state == "MATCH_FOUND":
            return True, result.get("confidenceLevel"), ""
        if match_state == "NO_MATCH_FOUND":
            return False, None, ""
        return None, None, f"match_state:{match_state or 'unspecified'}"

    @staticmethod
    def _sanitization_metadata(payload: dict[str, Any]) -> dict[str, Any]:
        sanitization = payload.get("sanitizationResult")
        if not isinstance(sanitization, dict):
            return {}
        metadata = sanitization.get("sanitizationMetadata")
        return metadata if isinstance(metadata, dict) else {}

    @classmethod
    def _filter_version_of(cls, payload: dict[str, Any]) -> str | None:
        version_config = cls._sanitization_metadata(payload).get("filterVersionConfig")
        if not isinstance(version_config, dict):
            return None
        version = version_config.get("filterVersion")
        return str(version) if version else None

    @classmethod
    def _service_error_code(cls, payload: dict[str, Any]) -> str | None:
        """Service-reported error code carried inside a 200 response.

        ``errorMessage`` is deliberately not read: it is free text that has
        been observed to echo request content, and everything here can reach
        JSONL and corpus artifacts.
        """
        code = cls._sanitization_metadata(payload).get("errorCode")
        return str(code) if code not in (None, "", 0) else None

    def _verdict_metadata(self) -> dict[str, Any]:
        """Per-verdict metadata: safe labels only, no project/resource paths."""
        metadata: dict[str, Any] = {
            "service": "google_model_armor",
            "template_label": self._template_label,
            "api_version": "v1",
        }
        if self._filter_version:
            metadata["filter_version"] = self._filter_version
        return metadata

    # -- interface ---------------------------------------------------------
    async def preflight(self) -> PreflightResult:
        """Validate the template before it is trusted to serve verdicts.

        The check that matters is ``enforcementType``. An ``INSPECT_ONLY``
        template returns success with no verdict, so a provider pointed at one
        is silently inert — the exact failure this guard exists to prevent.
        Airlock derives its own action from the verdict and ignores Google's
        block signal, so ``INSPECT_AND_BLOCK`` is required for reporting, not
        because Airlock wants Model Armor to block anything.

        Reading the template needs ``modelarmor.templates.get``, which the
        sanitize role does not include. A runtime authorized only to classify
        is therefore reported available with an explanatory detail rather than
        failed closed on a missing diagnostic permission.
        """
        url = f"{self._endpoint}/{self._template}"
        try:
            headers = await self._headers()
            status, payload = await asyncio.wait_for(
                self._ensure_transport().get(
                    url, headers=headers, timeout=self._timeout
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            return PreflightResult(
                PROVIDER_NAME, Availability.UNAVAILABLE, "preflight_timeout"
            )
        except Exception as exc:  # noqa: BLE001 - preflight must never raise
            return PreflightResult(
                PROVIDER_NAME,
                Availability.UNAVAILABLE,
                f"preflight_error:{type(exc).__name__}",
            )

        if status in (401, 403):
            return PreflightResult(
                PROVIDER_NAME,
                Availability.AVAILABLE,
                "template_not_readable: grant roles/modelarmor.viewer to validate "
                "enforcement type at startup",
                {"template_label": self._template_label},
            )
        if status != 200:
            return PreflightResult(
                PROVIDER_NAME, Availability.UNAVAILABLE, f"preflight_http_{status}"
            )

        metadata = payload.get("templateMetadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        enforcement = metadata.get("enforcementType")
        details: dict[str, str] = {"template_label": self._template_label}
        selector = metadata.get("filterVersionSelector")
        if isinstance(selector, dict):
            configured = selector.get("version") or selector.get("alias")
            if configured:
                details["filter_version_selector"] = str(configured)

        if enforcement == "INSPECT_ONLY":
            return PreflightResult(
                PROVIDER_NAME,
                Availability.UNAVAILABLE,
                "template_inspect_only: an INSPECT_ONLY template returns no "
                "verdict; set enforcement to INSPECT_AND_BLOCK (Airlock still "
                "decides the action itself)",
                details,
            )

        filter_config = payload.get("filterConfig")
        filter_config = filter_config if isinstance(filter_config, dict) else {}
        pi_settings = filter_config.get("piAndJailbreakFilterSettings")
        pi_settings = pi_settings if isinstance(pi_settings, dict) else {}
        if pi_settings.get("filterEnforcement") != "ENABLED":
            return PreflightResult(
                PROVIDER_NAME,
                Availability.UNAVAILABLE,
                "pi_and_jailbreak_filter_disabled",
                details,
            )
        if pi_settings.get("confidenceLevel"):
            details["confidence_level"] = str(pi_settings["confidenceLevel"])

        return PreflightResult(PROVIDER_NAME, Availability.AVAILABLE, "", details)

    async def inspect(self, text: str, *, kind: str = "user_prompt") -> ProviderVerdict:
        started = time.monotonic()

        def elapsed() -> float:
            return (time.monotonic() - started) * 1000

        probe = text[:_MAX_PROBE_CHARS]
        url = (
            f"{self._endpoint}/projects/{self._project}/locations/{self._location}"
            f"/templates/{self._template_label}:sanitizeUserPrompt"
        )
        try:
            headers = await self._headers()
            status, payload = await asyncio.wait_for(
                self._ensure_transport().post(
                    url,
                    json={"user_prompt_data": {"text": probe}},
                    headers=headers,
                    timeout=self._timeout,
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            return ProviderVerdict.unavailable(
                PROVIDER_NAME,
                error="timeout",
                duration_ms=elapsed(),
                metadata=self._verdict_metadata(),
            )
        except Exception as exc:  # noqa: BLE001 - inspect must never raise
            # Exception type only: messages can embed URLs and request context.
            return ProviderVerdict.unavailable(
                PROVIDER_NAME,
                error=f"transport_error:{type(exc).__name__}",
                duration_ms=elapsed(),
                metadata=self._verdict_metadata(),
            )

        if status != 200:
            return ProviderVerdict.unavailable(
                PROVIDER_NAME,
                error=f"http_{status}",
                duration_ms=elapsed(),
                metadata=self._verdict_metadata(),
            )

        version = self._filter_version_of(payload)
        if version:
            self._filter_version = version

        detected, confidence, error = self._parse(payload)
        metadata = self._verdict_metadata()
        metadata["request_kind"] = kind
        service_error = self._service_error_code(payload)
        if service_error:
            # Retained even alongside a usable verdict: a degraded invocation
            # is context an operator needs when reviewing a run.
            metadata["service_error_code"] = service_error
        if detected is None:
            return ProviderVerdict.unavailable(
                PROVIDER_NAME,
                error=error,
                duration_ms=elapsed(),
                metadata=metadata,
            )
        return ProviderVerdict(
            provider=PROVIDER_NAME,
            detected=detected,
            confidence=confidence,
            duration_ms=elapsed(),
            metadata=metadata,
        )

    async def aclose(self) -> None:
        if self._transport is not None:
            await self._transport.aclose()


def build_from_env(env: dict[str, str] | None = None) -> InjectionProvider | None:
    """Construct the provider from environment, or ``None`` when disabled.

    A misconfigured-but-enabled provider raises :class:`ModelArmorConfigError`
    rather than degrading to disabled: an operator who set
    ``AIRLOCK_MODEL_ARMOR_ENABLED=true`` asked for a working classifier, and
    silently running without one is the failure mode this design exists to
    prevent.
    """
    source = os.environ if env is None else env
    enabled = source.get("AIRLOCK_MODEL_ARMOR_ENABLED", "").strip().lower()
    if enabled not in ("1", "true", "yes", "on"):
        return None

    template = source.get("AIRLOCK_MODEL_ARMOR_TEMPLATE", "").strip()
    if not template:
        raise ModelArmorConfigError(
            "AIRLOCK_MODEL_ARMOR_ENABLED is set but "
            "AIRLOCK_MODEL_ARMOR_TEMPLATE is missing"
        )

    raw_timeout = source.get("AIRLOCK_MODEL_ARMOR_TIMEOUT_SECONDS", "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else _DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        logger.warning(
            "invalid AIRLOCK_MODEL_ARMOR_TIMEOUT_SECONDS=%r; using %.1fs",
            raw_timeout,
            _DEFAULT_TIMEOUT_SECONDS,
        )
        timeout = _DEFAULT_TIMEOUT_SECONDS

    return ModelArmorProvider(template=template, timeout_seconds=timeout)
