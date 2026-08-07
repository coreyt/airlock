"""0.5.11 B-2 — per-client erasure (CLI + admin API).

The axis is the authenticated client id (A-2's ``source_id``). The audit
record carries the ``EraseReport``; a partial erasure is never reported as
complete; repeating an erasure is safe.
"""

import json
from unittest.mock import patch

import pytest

import airlock.datastore
from airlock.admin.erase import EraseIncomplete, erase_client
from airlock.admin.http import handle_admin_request
from airlock.admin.policy import Principal, configure_admin
from airlock.datastore import init_engine


@pytest.fixture
def engine(tmp_path, monkeypatch):
    engine = init_engine(str(tmp_path / "airlock-fathom.db"))
    assert engine is not None
    monkeypatch.setattr(airlock.datastore, "get_engine", lambda: engine)
    yield engine
    engine.close()


def _write_rows(engine):
    engine.write(
        [
            {
                "kind": "RequestLog",
                "logical_id": f"{source}-{i}",
                "source_id": source,
                "body": json.dumps({"model": "gpt-4", "n": i, "source": source}),
            }
            for source in ("key:erase1", "key:keep02")
            for i in range(2)
        ]
    )


def _remaining_ids(engine):
    from fathomdb import read

    return {row.logical_id for row in read.list(engine, "RequestLog", limit=100)}


# ---------------------------------------------------------------------------
# erase_client — the operation itself
# ---------------------------------------------------------------------------
def test_erase_client_removes_only_that_client(engine):
    _write_rows(engine)

    record = erase_client("key:erase1", "loopback", confirm="key:erase1")

    assert record["record_type"] == "admin_action"
    assert record["op"] == "erase_client"
    assert record["outcome"] == "complete"
    # The receipt, not a bare ok.
    assert record["erase_report"]["nodes_excised"] == 2
    assert record["erase_report"]["source_ref"] == "key:erase1"
    assert (
        "JSONL" in record["scope_note"]
        or "AIRLOCK_MAX_LOG_DAYS" in record["scope_note"]
    )
    assert _remaining_ids(engine) == {"key:keep02-0", "key:keep02-1"}


def test_erase_client_is_idempotent(engine):
    _write_rows(engine)

    erase_client("key:erase1", "loopback", confirm="key:erase1")
    record = erase_client("key:erase1", "loopback", confirm="key:erase1")

    assert record["outcome"] == "complete"
    assert record["erase_report"]["nodes_excised"] == 0
    assert _remaining_ids(engine) == {"key:keep02-0", "key:keep02-1"}


def test_erase_client_requires_matching_confirmation(engine):
    _write_rows(engine)

    with pytest.raises(ValueError, match="confirmation mismatch"):
        erase_client("key:erase1", "loopback", confirm=None)
    with pytest.raises(ValueError, match="confirmation mismatch"):
        erase_client("key:erase1", "loopback", confirm="key:erase2")
    assert len(_remaining_ids(engine)) == 4


def test_erase_client_without_datastore_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(airlock.datastore, "get_engine", lambda: None)

    with pytest.raises(ValueError, match="not enabled"):
        erase_client("key:erase1", "loopback", confirm="key:erase1")


def test_partial_erasure_is_never_reported_complete(monkeypatch):
    from fathomdb.errors import ErasureIncompleteError

    class HalfwayEngine:
        def erase_source(self, source_id):
            raise ErasureIncompleteError("fts shadow excision interrupted")

    monkeypatch.setattr(airlock.datastore, "get_engine", lambda: HalfwayEngine())

    with pytest.raises(EraseIncomplete) as excinfo:
        erase_client("key:erase1", "loopback", confirm="key:erase1")

    record = excinfo.value.record
    assert record["outcome"] == "incomplete"
    assert record["retry_safe"] is True
    assert "interrupted" in record["error"]
    assert "erase_report" not in record


# ---------------------------------------------------------------------------
# The admin API surface
# ---------------------------------------------------------------------------
@pytest.fixture
def admin_on():
    configure_admin({"admin": {"enabled": True}})
    yield
    configure_admin({})


def _post_erase(client_id, body, *, loopback=True):
    return handle_admin_request(
        "POST",
        f"/airlock/admin/clients/{client_id}/erase",
        json.dumps(body).encode(),
        Principal(loopback=loopback, actor="loopback" if loopback else "remote"),
    )


def test_admin_api_erase_is_audited(engine, admin_on):
    _write_rows(engine)

    with patch("airlock.admin.http.write_admin_action_record") as mock_write:
        status, payload, _ = _post_erase("key:erase1", {"confirm": "key:erase1"})

    assert status == 200
    assert payload["outcome"] == "complete"
    assert payload["erase_report"]["nodes_excised"] == 2
    mock_write.assert_called_once_with(payload)


def test_admin_api_erase_is_loopback_only(engine, admin_on):
    _write_rows(engine)

    status, payload, _ = _post_erase(
        "key:erase1", {"confirm": "key:erase1"}, loopback=False
    )

    assert status == 403
    assert "loopback" in payload["error"]
    assert len(_remaining_ids(engine)) == 4


def test_admin_api_erase_confirmation_mismatch_is_400(engine, admin_on):
    _write_rows(engine)

    status, payload, _ = _post_erase("key:erase1", {})

    assert status == 400
    assert "confirmation mismatch" in payload["error"]
    assert len(_remaining_ids(engine)) == 4


def test_admin_api_partial_erasure_is_409_and_audited(monkeypatch, admin_on):
    from fathomdb.errors import ErasureIncompleteError

    class HalfwayEngine:
        def erase_source(self, source_id):
            raise ErasureIncompleteError("interrupted")

    monkeypatch.setattr(airlock.datastore, "get_engine", lambda: HalfwayEngine())

    with patch("airlock.admin.http.write_admin_action_record") as mock_write:
        status, payload, _ = _post_erase("key:erase1", {"confirm": "key:erase1"})

    assert status == 409
    assert payload["outcome"] == "incomplete"
    assert payload["retry_safe"] is True
    mock_write.assert_called_once()


# ---------------------------------------------------------------------------
# The CLI surface
# ---------------------------------------------------------------------------
def test_cli_refuses_mismatched_confirmation(capsys):
    from types import SimpleNamespace

    from airlock.cli.admin_cmd import run

    args = SimpleNamespace(
        admin_action="erase-client",
        client_id="key:erase1",
        confirm="key:oops",
        host=None,
        port=None,
    )
    with pytest.raises(SystemExit):
        run(args)
    assert "must repeat the client id" in capsys.readouterr().err


def test_cli_reports_incomplete_as_outstanding(monkeypatch, capsys):
    import io
    import urllib.error

    from types import SimpleNamespace

    from airlock.cli.admin_cmd import run

    body = json.dumps(
        {"outcome": "incomplete", "error": "interrupted", "retry_safe": True}
    ).encode()

    def fake_urlopen(request, timeout=30):
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", {}, io.BytesIO(body)
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    args = SimpleNamespace(
        admin_action="erase-client",
        client_id="key:erase1",
        confirm="key:erase1",
        host=None,
        port=None,
    )
    with pytest.raises(SystemExit):
        run(args)
    err = capsys.readouterr().err
    assert "INCOMPLETE" in err
    assert "outstanding" in err
    assert "safe" in err


def test_cli_success_prints_receipt_and_scope(monkeypatch, capsys):
    import io

    from types import SimpleNamespace

    from airlock.cli.admin_cmd import run

    payload = json.dumps(
        {
            "outcome": "complete",
            "erase_report": {
                "nodes_excised": 3,
                "edges_excised": 0,
                "projections_invalidated": 9,
            },
        }
    ).encode()

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    captured = {}

    def fake_urlopen(request, timeout=30):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    args = SimpleNamespace(
        admin_action="erase-client",
        client_id="key:erase1",
        confirm="key:erase1",
        host=None,
        port="4100",
    )
    run(args)

    out = capsys.readouterr().out
    assert "3 nodes" in out
    assert "JSONL logs are NOT touched" in out
    assert captured["url"] == (
        "http://127.0.0.1:4100/airlock/admin/clients/key%3Aerase1/erase"
    )
    assert captured["body"] == {"confirm": "key:erase1"}
