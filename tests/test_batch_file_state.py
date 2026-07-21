"""Unit tests for the file-scan state machine + async worker (to-do #2)."""

from __future__ import annotations

import json

from airlock.batch import scan, worker
from airlock.batch.store import (
    FILE_FAILED,
    FILE_READY,
    FILE_REJECTED,
    FILE_SCANNING,
    FILE_UPLOADED,
    BatchStore,
)


def _store(tmp_path):
    return BatchStore(str(tmp_path / "b.db"))


# ---------------------------------------------------------------------------
# Store: batch_files lifecycle
# ---------------------------------------------------------------------------
class TestFileStateStore:
    def test_record_then_get(self, tmp_path):
        s = _store(tmp_path)
        s.record_file_upload("file-1", byte_count=42)
        row = s.get_file("file-1")
        assert row["status"] == FILE_UPLOADED
        assert row["byte_count"] == 42

    def test_claim_is_won_once(self, tmp_path):
        s = _store(tmp_path)
        s.record_file_upload("file-1", byte_count=1)
        assert s.claim_file_scan("file-1") is True
        assert s.get_file("file-1")["status"] == FILE_SCANNING
        # Second claim while leased -> lost (scan runs once).
        assert s.claim_file_scan("file-1") is False

    def test_terminal_transitions(self, tmp_path):
        s = _store(tmp_path)
        s.record_file_upload("ready", byte_count=1)
        s.claim_file_scan("ready")
        s.set_file_ready("ready", row_count=7)
        assert s.get_file("ready")["status"] == FILE_READY
        assert s.get_file("ready")["row_count"] == 7

        s.record_file_upload("bad", byte_count=1)
        s.claim_file_scan("bad")
        s.set_file_rejected("bad", reason="blocked keyword: 'x'")
        assert s.get_file("bad")["status"] == FILE_REJECTED
        assert "blocked" in s.get_file("bad")["reason"]

    def test_claim_unknown_file_is_false(self, tmp_path):
        assert _store(tmp_path).claim_file_scan("nope") is False

    def test_scan_enabled_flag_is_persisted(self, tmp_path):
        s = _store(tmp_path)
        s.record_file_upload("scanned", byte_count=1)  # default True
        s.record_file_upload("raw", byte_count=1, status=FILE_READY, scan_enabled=False)
        assert s.get_file("scanned")["scan_enabled"] == 1
        assert s.get_file("raw")["scan_enabled"] == 0


# ---------------------------------------------------------------------------
# Worker: run_scan + await_file_ready (real thread pool, fake guards via profile)
# ---------------------------------------------------------------------------
def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(
        json.dumps(
            {
                "custom_id": "r1",
                "body": {"messages": [{"role": "user", "content": text}]},
            }
        )
        + "\n"
    )
    return p


class TestWorker:
    async def test_run_scan_marks_ready(self, tmp_path, monkeypatch):
        # No keywords / no PII -> clean passthrough, no Presidio.
        monkeypatch.delenv("AIRLOCK_BLOCKED_KEYWORDS", raising=False)
        s = _store(tmp_path)
        src = _write(tmp_path, "in.jsonl", "hello world")
        dst = tmp_path / "in.scrubbed.jsonl"
        s.record_file_upload("file-1", byte_count=src.stat().st_size)

        await worker.run_scan(
            s,
            "file-1",
            str(src),
            str(dst),
            {"scan_at_upload": True, "keyword_block": True, "pii_redact": False},
        )
        assert s.get_file("file-1")["status"] == FILE_READY
        assert dst.exists()

    async def test_run_scan_marks_rejected_on_keyword(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "classified")
        s = _store(tmp_path)
        src = _write(tmp_path, "in.jsonl", "this is classified material")
        dst = tmp_path / "in.scrubbed.jsonl"
        s.record_file_upload("file-1", byte_count=src.stat().st_size)

        await worker.run_scan(
            s,
            "file-1",
            str(src),
            str(dst),
            {"scan_at_upload": True, "keyword_block": True, "pii_redact": False},
        )
        assert s.get_file("file-1")["status"] == FILE_REJECTED
        assert not dst.exists()

    async def test_run_scan_failure_marks_failed(self, tmp_path, monkeypatch):
        s = _store(tmp_path)
        s.record_file_upload("file-1", byte_count=1)

        def boom(*a, **k):
            raise RuntimeError("executor blew up")

        monkeypatch.setattr(scan, "scan_file", boom)
        await worker.run_scan(s, "file-1", "x", "y", {})
        assert s.get_file("file-1")["status"] == FILE_FAILED
        assert "executor blew up" in s.get_file("file-1")["reason"]

    async def test_await_ready_returns_terminal_row(self, tmp_path):
        s = _store(tmp_path)
        s.record_file_upload("file-1", byte_count=1)
        s.claim_file_scan("file-1")
        s.set_file_ready("file-1", row_count=1)
        row = await worker.await_file_ready(s, "file-1", timeout=1.0)
        assert row["status"] == FILE_READY

    async def test_await_ready_times_out_while_scanning(self, tmp_path):
        s = _store(tmp_path)
        s.record_file_upload("file-1", byte_count=1)
        s.claim_file_scan("file-1")  # left SCANNING
        row = await worker.await_file_ready(s, "file-1", timeout=0.05, interval=0.01)
        assert row["status"] == FILE_SCANNING

    async def test_await_ready_unknown_file_is_none(self, tmp_path):
        assert (
            await worker.await_file_ready(_store(tmp_path), "x", timeout=0.05) is None
        )
