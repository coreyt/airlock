"""Tests for airlock.guardrails.reasoning_stripper."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from airlock.guardrails.reasoning_stripper import (
    AirlockReasoningStripper,
    _StreamStripper,
    _strip_blocks,
)

START = "◁think▷"
END = "◁/think▷"


def _msg_response(content: str):
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _delta_chunk(content: str | None, index: int = 0):
    delta = SimpleNamespace(content=content)
    choice = SimpleNamespace(delta=delta, index=index)
    return SimpleNamespace(choices=[choice])


# ---------------------------------------------------------------------------
# _strip_blocks
# ---------------------------------------------------------------------------
class TestStripBlocks:
    def test_no_markers_untouched(self):
        assert _strip_blocks("hello world") == "hello world"

    def test_single_block_removed(self):
        text = f"{START}reasoning here{END}\nthe answer"
        assert _strip_blocks(text) == "the answer"

    def test_multiple_blocks_removed(self):
        text = f"a{START}r1{END}b{START}r2{END}c"
        assert _strip_blocks(text) == "abc"

    def test_orphan_end_marker_strips_preceding(self):
        # Kimi sometimes omits the opening marker.
        text = f"unwrapped reasoning{END}\nfinal answer"
        assert _strip_blocks(text) == "final answer"

    def test_unterminated_start_drops_tail(self):
        text = f"prefix{START}reasoning never closed"
        assert _strip_blocks(text) == "prefix"

    def test_preserves_normal_content_when_no_markers(self):
        # < and > look like XML-ish text but aren't the unicode markers.
        text = "<think>not a real marker</think> stays"
        assert _strip_blocks(text) == text


# ---------------------------------------------------------------------------
# _StreamStripper
# ---------------------------------------------------------------------------
class TestStreamStripper:
    def _run(self, chunks: list[str]) -> str:
        s = _StreamStripper()
        out = "".join(s.feed(c) for c in chunks)
        return out + s.flush()

    def test_clean_passthrough(self):
        assert self._run(["hello ", "world"]) == "hello world"

    def test_full_block_in_one_chunk(self):
        assert self._run([f"{START}r{END}\nanswer"]) == "answer"

    def test_block_split_across_chunks(self):
        # Marker split mid-character at every plausible point.
        chunks = [f"prefix {START[:3]}", f"{START[3:]}think part {END[:2]}", f"{END[2:]}\nfinal"]
        assert self._run(chunks) == "prefix final"

    def test_only_emits_after_marker_resolves(self):
        # A chunk that *could* be the start of a marker is held until we
        # know it's not.
        s = _StreamStripper()
        out1 = s.feed("◁")  # could be ◁think▷ start
        assert out1 == ""
        out2 = s.feed("not a marker")
        # Once buffer no longer prefix-matches either marker, flush it.
        assert "◁not a marker" in (out2 + s.flush())

    def test_in_think_drops_content(self):
        chunks = [f"{START}", "secret ", "reasoning ", "more", f"{END}done"]
        assert self._run(chunks) == "done"


# ---------------------------------------------------------------------------
# Guardrail integration
# ---------------------------------------------------------------------------
class _FakeKey:
    pass


@pytest.fixture
def stripper(monkeypatch):
    monkeypatch.setenv("AIRLOCK_REASONING_STRIP_MODELS", "kimi-dev")
    return AirlockReasoningStripper(guardrail_name="airlock-reasoning-stripper")


class TestNonStreaming:
    @pytest.mark.asyncio
    async def test_strips_for_target_model(self, stripper):
        resp = _msg_response(f"{START}thinking{END}\nthe answer is 42")
        out = await stripper.async_post_call_success_hook(
            {"model": "kimi-dev"}, _FakeKey(), resp
        )
        assert out.choices[0].message.content == "the answer is 42"

    @pytest.mark.asyncio
    async def test_strips_for_prefixed_model(self, stripper):
        # litellm internally prefixes with provider, e.g. "openai/kimi-dev"
        resp = _msg_response(f"{START}r{END}\nans")
        out = await stripper.async_post_call_success_hook(
            {"model": "openai/kimi-dev"}, _FakeKey(), resp
        )
        assert out.choices[0].message.content == "ans"

    @pytest.mark.asyncio
    async def test_skips_other_models(self, stripper):
        content = f"{START}thinking{END}\nthe answer"
        resp = _msg_response(content)
        out = await stripper.async_post_call_success_hook(
            {"model": "gemma-4"}, _FakeKey(), resp
        )
        # Unchanged: gemma-4 isn't in the target set.
        assert out.choices[0].message.content == content

    @pytest.mark.asyncio
    async def test_no_content_passthrough(self, stripper):
        msg = SimpleNamespace(content=None)
        resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)])
        out = await stripper.async_post_call_success_hook(
            {"model": "kimi-dev"}, _FakeKey(), resp
        )
        assert out.choices[0].message.content is None


class TestStreaming:
    @pytest.mark.asyncio
    async def test_strips_streaming_chunks(self, stripper):
        async def gen():
            for c in [f"{START}thinking ", "more ", f"thoughts{END}\n", "the ", "answer"]:
                yield _delta_chunk(c)

        out_text: list[str] = []
        async for chunk in stripper.async_post_call_streaming_iterator_hook(
            _FakeKey(), gen(), {"model": "kimi-dev"}
        ):
            piece = chunk.choices[0].delta.content
            if piece:
                out_text.append(piece)
        assert "".join(out_text) == "the answer"

    @pytest.mark.asyncio
    async def test_streaming_passthrough_for_other_model(self, stripper):
        async def gen():
            yield _delta_chunk(f"{START}peek{END}\nhi")

        out_text: list[str] = []
        async for chunk in stripper.async_post_call_streaming_iterator_hook(
            _FakeKey(), gen(), {"model": "gemma-4"}
        ):
            piece = chunk.choices[0].delta.content
            if piece:
                out_text.append(piece)
        # Untouched.
        assert "".join(out_text) == f"{START}peek{END}\nhi"


class TestTargetConfig:
    def test_env_var_controls_targets(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_REASONING_STRIP_MODELS", "model-a, model-b")
        g = AirlockReasoningStripper(guardrail_name="x")
        assert g._is_target({"model": "model-a"})
        assert g._is_target({"model": "openai/model-b"})
        assert not g._is_target({"model": "kimi-dev"})


def _ledger(data):
    return data.get("metadata", {}).get("airlock_mutations", [])


class TestStripperLedger:
    @pytest.mark.asyncio
    async def test_non_stream_strip_records_rewrite(self, stripper):
        data = {"model": "kimi-dev"}
        resp = _msg_response(f"{START}thinking{END}\nthe answer")
        await stripper.async_post_call_success_hook(data, _FakeKey(), resp)
        muts = [m for m in _ledger(data) if m.field == "messages"]
        assert len(muts) == 1
        assert muts[0].op == "rewrite"
        assert muts[0].stage == "post_call"
        assert muts[0].source == "reasoning_stripper"
        assert muts[0].before is None and muts[0].after is None

    @pytest.mark.asyncio
    async def test_non_stream_no_strip_no_record(self, stripper):
        data = {"model": "kimi-dev"}
        resp = _msg_response("plain answer, no markers here")
        await stripper.async_post_call_success_hook(data, _FakeKey(), resp)
        assert _ledger(data) == []

    @pytest.mark.asyncio
    async def test_stream_strip_records_once(self, stripper):
        request_data = {"model": "kimi-dev"}

        async def gen():
            for c in [f"{START}hidden ", f"thoughts{END}\n", "visible answer"]:
                yield _delta_chunk(c)

        async for _chunk in stripper.async_post_call_streaming_iterator_hook(
            _FakeKey(), gen(), request_data
        ):
            pass
        muts = [
            m for m in _ledger(request_data) if m.source == "reasoning_stripper.stream"
        ]
        assert len(muts) == 1
        assert muts[0].op == "rewrite"
        assert muts[0].field == "messages"
        assert muts[0].stage == "post_call"

    @pytest.mark.asyncio
    async def test_stream_no_strip_no_record(self, stripper):
        request_data = {"model": "kimi-dev"}

        async def gen():
            for c in ["plain ", "visible ", "answer"]:
                yield _delta_chunk(c)

        async for _chunk in stripper.async_post_call_streaming_iterator_hook(
            _FakeKey(), gen(), request_data
        ):
            pass
        assert _ledger(request_data) == []
