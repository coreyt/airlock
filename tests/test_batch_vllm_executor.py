"""Unit tests — vLLM executor core (Slice 1; injected transport, no network)."""

from __future__ import annotations

import asyncio
import json

from airlock.batch.vllm import execute_batch


def _write_input(tmp_path, rows):
    p = tmp_path / "in.provider.jsonl"
    p.write_text(
        "\n".join(
            json.dumps({"custom_id": cid, "body": {"messages": [{"content": cid}]}})
            for cid in rows
        )
        + "\n"
    )
    return p


def _paths(tmp_path):
    return (
        str(tmp_path / "x.results.jsonl"),
        str(tmp_path / "x.results.done"),
    )


def _echo_sender(record=None):
    async def send(body):
        if record is not None:
            record.append(body)
        return {"id": "cmpl", "choices": [{"message": {"content": "PONG"}}]}

    return send


class TestExecutorHappyPath:
    async def test_all_rows_executed_and_done_marked(self, tmp_path):
        src = _write_input(tmp_path, ["r1", "r2", "r3"])
        results, done = _paths(tmp_path)
        calls = []
        await execute_batch(
            idem="x",
            input_path=str(src),
            results_path=results,
            done_path=done,
            send_chat=_echo_sender(calls),
            semaphore=asyncio.Semaphore(4),
            cancel_event=asyncio.Event(),
        )
        lines = [json.loads(line) for line in open(results)]
        assert {line["custom_id"] for line in lines} == {"r1", "r2", "r3"}
        assert all("response" in line for line in lines)
        assert len(calls) == 3
        assert open(done).read().strip() == "done"


class TestExecutorPartialFailure:
    async def test_failed_row_becomes_error_line_not_batch_failure(self, tmp_path):
        src = _write_input(tmp_path, ["ok", "bad"])
        results, done = _paths(tmp_path)

        async def send(body):
            if body["messages"][0]["content"] == "bad":
                raise RuntimeError("vllm 500")
            return {"id": "c", "choices": []}

        await execute_batch(
            idem="x",
            input_path=str(src),
            results_path=results,
            done_path=done,
            send_chat=send,
            semaphore=asyncio.Semaphore(4),
            cancel_event=asyncio.Event(),
            retries=0,
        )
        by_id = {
            json.loads(line)["custom_id"]: json.loads(line) for line in open(results)
        }
        assert by_id["ok"]["response"]["status_code"] == 200
        assert by_id["bad"]["error"]["code"] == "execution_error"
        assert "vllm 500" in by_id["bad"]["error"]["message"]
        # A partial failure is NOT a batch failure: the batch still completes.
        assert (tmp_path / "x.results.done").exists()


class TestExecutorResume:
    async def test_only_missing_rows_fire_on_resume(self, tmp_path):
        src = _write_input(tmp_path, ["r1", "r2", "r3"])
        results, done = _paths(tmp_path)
        # Pre-seed r1 as already done (a prior, crashed run).
        with open(results, "w") as f:
            f.write(json.dumps({"custom_id": "r1", "response": {"body": {}}}) + "\n")

        calls = []
        await execute_batch(
            idem="x",
            input_path=str(src),
            results_path=results,
            done_path=done,
            send_chat=_echo_sender(calls),
            semaphore=asyncio.Semaphore(4),
            cancel_event=asyncio.Event(),
        )
        fired = {c["messages"][0]["content"] for c in calls}
        assert fired == {"r2", "r3"}  # r1 skipped
        ids = {json.loads(line)["custom_id"] for line in open(results)}
        assert ids == {"r1", "r2", "r3"}
        assert (tmp_path / "x.results.done").exists()

    async def test_corrupt_partial_line_is_retried_not_skipped(self, tmp_path):
        """A crash mid-write leaves a truncated line; that row must re-execute on
        resume (the diff only trusts well-formed result lines)."""
        src = _write_input(tmp_path, ["r1", "r2"])
        results, done = _paths(tmp_path)
        with open(results, "w") as f:
            f.write(json.dumps({"custom_id": "r1", "response": {"body": {}}}) + "\n")
            f.write('{"custom_id": "r2", "respo')  # truncated, no newline

        calls = []
        await execute_batch(
            idem="x",
            input_path=str(src),
            results_path=results,
            done_path=done,
            send_chat=_echo_sender(calls),
            semaphore=asyncio.Semaphore(4),
            cancel_event=asyncio.Event(),
        )
        fired = {c["messages"][0]["content"] for c in calls}
        assert fired == {"r2"}  # r1 trusted, r2 (corrupt) retried
        # Compaction repaired the file: every line is valid JSON and converges.
        ids = {json.loads(line)["custom_id"] for line in open(results) if line.strip()}
        assert ids == {"r1", "r2"}
        assert (tmp_path / "x.results.done").exists()


class TestExecutorCancel:
    async def test_preset_cancel_writes_no_done_and_no_calls(self, tmp_path):
        src = _write_input(tmp_path, ["r1", "r2"])
        results, done = _paths(tmp_path)
        ev = asyncio.Event()
        ev.set()
        calls = []
        await execute_batch(
            idem="x",
            input_path=str(src),
            results_path=results,
            done_path=done,
            send_chat=_echo_sender(calls),
            semaphore=asyncio.Semaphore(4),
            cancel_event=ev,
        )
        assert calls == []
        assert not (tmp_path / "x.results.done").exists()

    async def test_cancel_mid_execution_withholds_done_marker(self, tmp_path):
        """Cancel set after rows start: remaining rows are skipped and .done is
        NOT written (the batch stays in_progress; the HTTP cancel handler is what
        flips the store to CANCELLED for the client)."""
        src = _write_input(tmp_path, ["r1", "r2", "r3"])
        results, done = _paths(tmp_path)
        ev = asyncio.Event()
        calls = []

        async def send(body):
            calls.append(body)
            ev.set()  # trip cancel after the first row dispatches
            return {"id": "c", "choices": []}

        await execute_batch(
            idem="x",
            input_path=str(src),
            results_path=results,
            done_path=done,
            send_chat=send,
            semaphore=asyncio.Semaphore(1),  # serialize so cancel lands mid-run
            cancel_event=ev,
        )
        assert not (tmp_path / "x.results.done").exists()
        assert len(calls) < 3  # not all rows executed


class TestExecutorConcurrencyBound:
    async def test_semaphore_caps_inflight(self, tmp_path):
        src = _write_input(tmp_path, [f"r{i}" for i in range(20)])
        results, done = _paths(tmp_path)
        state = {"cur": 0, "max": 0}

        async def send(body):
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
            await asyncio.sleep(0.01)
            state["cur"] -= 1
            return {"id": "c", "choices": []}

        await execute_batch(
            idem="x",
            input_path=str(src),
            results_path=results,
            done_path=done,
            send_chat=send,
            semaphore=asyncio.Semaphore(3),
            cancel_event=asyncio.Event(),
        )
        assert state["max"] <= 3
        assert len(list(open(results))) == 20
