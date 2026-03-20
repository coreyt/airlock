"""
S17 — Live proxy round-trip matrix for configured aliases and override behavior.
"""

from __future__ import annotations

import pytest

from tests.harness.live_matrix import (
    LiveMatrixCase,
    LiveProxyMatrixBase,
    configured_alias_cases,
)


pytestmark = pytest.mark.harness


_CONFIGURED_ALIAS_CASES = configured_alias_cases()
_SMART_OVERRIDE_CASES = [
    LiveMatrixCase(
        id="smart",
        request_model="smart",
        prompt="What is 2+2?",
        max_tokens=10,
        expect_override_header=True,
    ),
]

_TEXT_CONTENT_CASES = [
    LiveMatrixCase(
        id="gemini-pro-text",
        request_model="gemini-pro",
        provider="gemini",
        prompt=(
            "Return plain text only. Reply with exactly the single word OK. "
            "Do not think out loud. Do not use tools. Do not leave the answer blank."
        ),
        max_tokens=32,
        require_text_content=True,
        extra_payload={"reasoning_effort": "disable"},
    ),
]


class TestConfiguredAliasRoundTrip(LiveProxyMatrixBase):
    @pytest.mark.live
    @pytest.mark.parametrize(
        "case",
        _CONFIGURED_ALIAS_CASES,
        ids=lambda case: case.id,
    )
    async def test_configured_alias_round_trip(self, http_client, case: LiveMatrixCase):
        await self.assert_live_round_trip(http_client, case)


class TestOverrideHeaderRoundTrip(LiveProxyMatrixBase):
    @pytest.mark.live
    @pytest.mark.parametrize(
        "case",
        _SMART_OVERRIDE_CASES,
        ids=lambda case: case.id,
    )
    async def test_override_header_round_trip(self, http_client, case: LiveMatrixCase):
        await self.assert_live_round_trip(http_client, case)


class TestRequiredTextContentRoundTrip(LiveProxyMatrixBase):
    @pytest.mark.live
    @pytest.mark.parametrize(
        "case",
        _TEXT_CONTENT_CASES,
        ids=lambda case: case.id,
    )
    async def test_required_text_content_round_trip(self, http_client, case: LiveMatrixCase):
        await self.assert_live_round_trip(http_client, case)
