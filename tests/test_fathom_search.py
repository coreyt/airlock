"""0.5.11 B-1 — hybrid log search (#11), served by the engine.

The standing constraint under test: a degraded search branch is never
presented as a complete result. Unavailable is not clean, and lexical-only
is not hybrid.
"""

import datetime
import json

import pytest

from airlock.api.queries import search_request_logs
from airlock.datastore import init_engine


@pytest.fixture
def engine(tmp_path):
    engine = init_engine(str(tmp_path / "airlock-fathom.db"))
    assert engine is not None
    yield engine
    engine.close()


def _write_log(engine, logical_id, properties):
    engine.write(
        [
            {
                "kind": "RequestLog",
                "logical_id": logical_id,
                "source_id": "key:aaaa1111",
                "body": json.dumps(properties),
            }
        ]
    )


# ---------------------------------------------------------------------------
# The normal path in this deployment: lexical-only, labelled, with the reason
# ---------------------------------------------------------------------------
def test_search_is_lexical_only_without_embedder(engine):
    """No embedder -> no vector projection -> lexical-only, and it says so.

    A bare engine.search() here would return text-branch hits with
    soft_fallback=None — a lexical-only result presenting itself as hybrid.
    The reader must ask first and label honestly.
    """
    _write_log(engine, "r1", {"model": "gpt-4", "error": "rate limit exceeded"})
    _write_log(engine, "r2", {"model": "claude", "response_text": "all good"})

    out = search_request_logs(engine, "rate limit")

    assert out["mode"] == "lexical_only"
    assert out["degraded_reason"] is not None
    assert "vector" in out["degraded_reason"] or "embedder" in out["degraded_reason"]
    assert [r["logical_id"] for r in out["results"]] == ["r1"]
    assert out["results"][0]["properties"]["model"] == "gpt-4"
    assert out["results"][0]["source_id"] == "key:aaaa1111"


def test_search_covers_all_fts_fields(engine):
    """error, messages_json, and response_text are all searchable (A-2 FTS)."""
    _write_log(engine, "e1", {"error": "quota exhausted for tenant"})
    _write_log(
        engine, "m1", {"messages_json": '[{"content": "summarize the quota report"}]'}
    )
    _write_log(engine, "t1", {"response_text": "your quota resets monthly"})
    _write_log(engine, "x1", {"error": "unrelated failure"})

    out = search_request_logs(engine, "quota")

    assert {r["logical_id"] for r in out["results"]} == {"e1", "m1", "t1"}


def test_search_respects_limit(engine):
    # Distinct bodies: the engine collapses content-identical rows to one
    # canonical hit (verified against the real engine).
    for i in range(5):
        _write_log(engine, f"r{i}", {"error": f"timeout while connecting, attempt {i}"})

    out = search_request_logs(engine, "timeout", limit=2)

    assert len(out["results"]) == 2


# ---------------------------------------------------------------------------
# Degraded engine (dense_disabled): never presented as hybrid
# ---------------------------------------------------------------------------
class _DegradedEngine:
    """An engine whose open-time self-check disabled dense retrieval."""

    def __init__(self, result):
        self._result = result

    def dense_disabled(self):
        return True

    def dense_disabled_reason(self):
        return "vector-equivalence divergence at open"

    def search_text_only(self, query, view=None):
        return self._result

    def search(self, *args, **kwargs):  # pragma: no cover - the assertion
        raise AssertionError(
            "search() must not run on a degraded engine — ask first, "
            "then call search_text_only()"
        )


def test_degraded_engine_result_is_never_presented_as_hybrid():
    from fathomdb import SearchResult

    out = search_request_logs(
        _DegradedEngine(SearchResult(projection_cursor=0, results=[])), "anything"
    )

    assert out["mode"] == "lexical_only"
    assert out["mode"] != "hybrid"
    assert out["degraded_reason"] == "vector-equivalence divergence at open"


# ---------------------------------------------------------------------------
# Hybrid path: soft_fallback carried through, not swallowed
# ---------------------------------------------------------------------------
class _HybridEngine:
    def __init__(self, result):
        self._result = result

    def dense_disabled(self):
        return False

    def search(self, query, view=None):
        return self._result

    def search_text_only(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("hybrid-capable engine should use search()")


def test_hybrid_result_carries_soft_fallback(monkeypatch):
    from fathomdb import SearchResult, SoftFallback

    import airlock.api.queries as queries

    monkeypatch.setattr(queries, "_dense_available", lambda engine: (True, None))

    result = SearchResult(
        projection_cursor=0,
        soft_fallback=SoftFallback(branch="vector"),
        results=[],
    )
    out = search_request_logs(_HybridEngine(result), "anything")

    assert out["mode"] == "hybrid"
    assert out["soft_fallback"] == "vector"
    assert out["degraded_reason"] is None


# ---------------------------------------------------------------------------
# The advisor seam
# ---------------------------------------------------------------------------
def test_advisor_search_logs_uses_engine(monkeypatch, log_dir, tmp_path):
    import airlock.datastore
    from airlock.advisor.tools import search_logs

    engine = init_engine(str(tmp_path / "airlock-fathom.db"))
    try:
        _write_log(engine, "r1", {"model": "gpt-4", "error": "rate limit exceeded"})
        monkeypatch.setattr(airlock.datastore, "get_engine", lambda: engine)

        out = search_logs(str(log_dir), "rate limit")

        assert out["backend"] == "fathomdb"
        assert out["mode"] == "lexical_only"
        assert [r["logical_id"] for r in out["results"]] == ["r1"]
    finally:
        engine.close()


def test_advisor_search_logs_jsonl_fallback_is_labelled(monkeypatch, log_dir):
    """Without the datastore the tool still answers — as a labelled substring scan."""
    import airlock.datastore
    from airlock.advisor.tools import search_logs

    monkeypatch.setattr(airlock.datastore, "get_engine", lambda: None)

    today = datetime.date.today()
    log_file = log_dir / f"airlock-{today.isoformat()}.jsonl"
    records = [
        {"timestamp": "2025-01-01T10:00:00Z", "model": "gpt-4", "error": "rate limit"},
        {"timestamp": "2025-01-01T10:01:00Z", "model": "claude", "error": "boom"},
    ]
    log_file.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    out = search_logs(str(log_dir), "rate limit")

    assert out["backend"] == "jsonl"
    assert out["mode"] == "substring"
    assert out["mode"] not in ("hybrid", "lexical_only")
    assert out["degraded_reason"] is not None
    assert len(out["results"]) == 1
    assert out["results"][0]["model"] == "gpt-4"
    assert out["window"] == {"truncated": False, "limit_hit": None}
