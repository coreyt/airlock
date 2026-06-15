"""Unit tests for the batch content-scan pipeline (to-do #2).

The pure core (``scan_stream``) and the IO wrapper (``scan_file``) are tested
with **injected** guards — no Presidio/spaCy, no network — so they are fast and
hermetic. A separate test exercises the real keyword adapter (env-driven list).
"""

from __future__ import annotations

import json

import pytest

from airlock.batch import scan


def _line(custom_id: str, text: str, model: str = "m") -> str:
    return json.dumps(
        {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {"model": model, "messages": [{"role": "user", "content": text}]},
        }
    )


# ---------------------------------------------------------------------------
# Pure core: scan_stream
# ---------------------------------------------------------------------------
class TestScanStream:
    def test_clean_passthrough_yields_every_row(self):
        lines = [_line("r1", "hello"), _line("r2", "world")]
        out = list(
            scan.scan_stream(
                lines,
                keyword_check=None,
                redact_messages=None,
                max_rows=100,
                max_bytes=10_000,
            )
        )
        assert [json.loads(o)["custom_id"] for o in out] == ["r1", "r2"]

    def test_blank_and_nonjson_lines_are_skipped(self):
        lines = ["", "  ", "--multipart-boundary--", _line("r1", "hi")]
        out = list(
            scan.scan_stream(
                lines,
                keyword_check=None,
                redact_messages=None,
                max_rows=100,
                max_bytes=10_000,
            )
        )
        assert len(out) == 1
        assert json.loads(out[0])["custom_id"] == "r1"

    def test_keyword_hit_rejects_whole_upload(self):
        def check(text):
            return "secret" if "secret" in text else None

        gen = scan.scan_stream(
            [_line("r1", "ok"), _line("r2", "this is secret")],
            keyword_check=check,
            redact_messages=None,
            max_rows=100,
            max_bytes=10_000,
        )
        # r1 streams fine; r2 trips the guard.
        assert json.loads(next(gen))["custom_id"] == "r1"
        with pytest.raises(scan.ContentRejected, match="blocked keyword"):
            next(gen)

    def test_redactor_rewrites_message_content(self):
        def redact(messages):
            return [{**m, "content": "[REDACTED]"} for m in messages]

        out = list(
            scan.scan_stream(
                [_line("r1", "my ssn is 123")],
                keyword_check=None,
                redact_messages=redact,
                max_rows=100,
                max_bytes=10_000,
            )
        )
        body = json.loads(out[0])["body"]
        assert body["messages"][0]["content"] == "[REDACTED]"

    def test_max_rows_cap_rejects(self):
        gen = scan.scan_stream(
            [_line("r1", "a"), _line("r2", "b"), _line("r3", "c")],
            keyword_check=None,
            redact_messages=None,
            max_rows=2,
            max_bytes=10_000,
        )
        assert json.loads(next(gen))["custom_id"] == "r1"
        assert json.loads(next(gen))["custom_id"] == "r2"
        with pytest.raises(scan.ContentRejected, match="max_rows"):
            next(gen)

    def test_max_bytes_cap_rejects(self):
        big = _line("r1", "x" * 500)
        with pytest.raises(scan.ContentRejected, match="max_bytes"):
            list(
                scan.scan_stream(
                    [big],
                    keyword_check=None,
                    redact_messages=None,
                    max_rows=100,
                    max_bytes=100,
                )
            )

    def test_list_content_parts_are_scanned(self):
        line = json.dumps(
            {
                "custom_id": "r1",
                "body": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "find badword here"}],
                        }
                    ]
                },
            }
        )
        seen = {}

        def check(text):
            seen["text"] = text
            return None

        list(
            scan.scan_stream(
                [line],
                keyword_check=check,
                redact_messages=None,
                max_rows=100,
                max_bytes=10_000,
            )
        )
        assert "badword" in seen["text"]


# ---------------------------------------------------------------------------
# IO wrapper: scan_file
# ---------------------------------------------------------------------------
class TestScanFile:
    def test_ready_writes_scrubbed_file_atomically(self, tmp_path):
        src = tmp_path / "in.jsonl"
        dst = tmp_path / "out.jsonl"
        src.write_text(_line("r1", "a") + "\n" + _line("r2", "b") + "\n")

        result = scan.scan_file(str(src), str(dst), {}, guards=(None, None))
        assert result.status == scan.READY
        assert result.row_count == 2
        assert dst.exists()
        assert not (tmp_path / "out.jsonl.partial").exists()
        ids = [json.loads(line)["custom_id"] for line in dst.read_text().splitlines()]
        assert ids == ["r1", "r2"]

    def test_rejected_removes_partial_and_reports_reason(self, tmp_path):
        src = tmp_path / "in.jsonl"
        dst = tmp_path / "out.jsonl"
        src.write_text(_line("r1", "trip the guard") + "\n")

        def check(text):
            return "guard" if "guard" in text else None

        result = scan.scan_file(str(src), str(dst), {}, guards=(check, None))
        assert result.status == scan.REJECTED
        assert "blocked keyword" in result.reason
        # No "clean" output left behind for create to ship.
        assert not dst.exists()
        assert not (tmp_path / "out.jsonl.partial").exists()


# ---------------------------------------------------------------------------
# Real keyword adapter (env-driven; still no Presidio)
# ---------------------------------------------------------------------------
class TestRealKeywordAdapter:
    def test_no_keywords_configured_is_noop(self, monkeypatch):
        # Empty (not unset): the package re-loads .env on import, which would
        # otherwise restore the project's default block list.
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "")
        assert scan._real_keyword_check() is None

    def test_configured_keyword_is_detected(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "projectzeus, falcon")
        check = scan._real_keyword_check()
        assert check is not None
        assert check("nothing here") is None
        assert check("mentions ProjectZeus casually") == "projectzeus"

    def test_build_guards_respects_profile_flags(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "zzz")
        kw, redact = scan.build_guards({"keyword_block": True, "pii_redact": False})
        assert kw is not None and redact is None
        kw2, redact2 = scan.build_guards({"keyword_block": False, "pii_redact": False})
        assert kw2 is None and redact2 is None
