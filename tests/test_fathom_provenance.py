"""0.5.11 A-2 — provenance and projections.

``source_id`` is the erasure axis: the authenticated client ID, stamped by the
guardian at pre-call, mandatory on every canonical row. Projections are
declared to the engine (``configure_projections``); Airlock maintains no
derived index of its own.
"""

import json

import pytest

from airlock.datastore import init_engine
from airlock.fast.guardian import AirlockFastGuardian


@pytest.fixture
def engine(tmp_path):
    engine = init_engine(str(tmp_path / "airlock-fathom.db"))
    assert engine is not None
    yield engine
    engine.close()


# ---------------------------------------------------------------------------
# The engine guarantee, pinned directly (not just Airlock's side of it)
# ---------------------------------------------------------------------------
def test_engine_rejects_row_without_source_id(engine):
    """A row without a source_id is unerasable — the engine must refuse it."""
    from fathomdb.errors import WriteValidationError

    with pytest.raises(WriteValidationError):
        engine.write(
            [{"kind": "RequestLog", "logical_id": "x", "body": json.dumps({})}]
        )


def test_engine_rejects_empty_source_id(engine):
    from fathomdb.errors import EngineError

    with pytest.raises(EngineError):
        engine.write(
            [
                {
                    "kind": "RequestLog",
                    "logical_id": "x",
                    "source_id": "",
                    "body": json.dumps({}),
                }
            ]
        )


# ---------------------------------------------------------------------------
# Projections are declared, engine-owned, and idempotent
# ---------------------------------------------------------------------------
def test_projections_declared_on_init(engine):
    from fathomdb import ProjectionRole, read

    declared = {spec.name: spec for spec in read.projections(engine)}

    assert ProjectionRole.RANKABLE in declared["timestamp"].roles
    assert ProjectionRole.FILTERABLE in declared["timestamp"].roles
    assert ProjectionRole.FILTERABLE in declared["model"].roles
    assert ProjectionRole.RANKABLE in declared["cost"].roles
    for fts_field in ("error", "messages_json", "response_text"):
        assert declared[fts_field].fts is True
        assert ProjectionRole.SEARCHABLE in declared[fts_field].roles
    # No vector projections: no embedder is configured in this deployment.
    assert not any(spec.vector for spec in declared.values())


def test_projections_reapply_unchanged_on_reopen(tmp_path):
    """Re-opening the same file re-declares the same specs without error."""
    db_path = str(tmp_path / "airlock-fathom.db")
    first = init_engine(db_path)
    first.close()

    reopened = init_engine(db_path)
    try:
        from fathomdb import read

        assert {spec.name for spec in read.projections(reopened)} >= {
            "timestamp",
            "model",
            "cost",
        }
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# The guardian stamp: authenticated, never client-chosen
# ---------------------------------------------------------------------------
async def test_guardian_stamps_authenticated_source_id(
    fresh_state_store, mock_cache, mock_user_api_key_dict
):
    guardian = AirlockFastGuardian()
    data = {
        "messages": [{"role": "user", "content": "Hello"}],
        "model": "claude-sonnet",
    }
    result = await guardian.async_pre_call_hook(
        mock_user_api_key_dict, mock_cache, data, "completion"
    )
    # mock key ends in 90abcdef -> key:90abcdef
    assert result["metadata"]["airlock_source_id"] == "key:90abcdef"


async def test_guardian_overwrites_forged_source_id(
    fresh_state_store, mock_cache, mock_user_api_key_dict
):
    """metadata is client-controllable; a forged source_id must not survive.

    Erasure is keyed on source_id — a caller choosing its own value could
    erase another client's rows (B-2). Same stance as the guardrail decision
    stamp: the verified result always wins.
    """
    guardian = AirlockFastGuardian()
    data = {
        "messages": [{"role": "user", "content": "Hello"}],
        "model": "claude-sonnet",
        "metadata": {"airlock_source_id": "key:victim01"},
    }
    result = await guardian.async_pre_call_hook(
        mock_user_api_key_dict, mock_cache, data, "completion"
    )
    assert result["metadata"]["airlock_source_id"] == "key:90abcdef"


async def test_guardian_stamps_no_client_when_unauthenticated(
    fresh_state_store, mock_cache
):
    from airlock.client_identity import NO_CLIENT_ID

    guardian = AirlockFastGuardian()
    data = {
        "messages": [{"role": "user", "content": "Hello"}],
        "model": "claude-sonnet",
        # Forgeable header identity present — it must play no part in the stamp.
        "headers": {"X-Airlock-Client": "somebody-else"},
    }
    result = await guardian.async_pre_call_hook(None, mock_cache, data, "completion")
    assert result["metadata"]["airlock_source_id"] == NO_CLIENT_ID


# ---------------------------------------------------------------------------
# End to end: stamped identity becomes row provenance on a real engine
# ---------------------------------------------------------------------------
def test_stamped_identity_reaches_the_row(engine):
    from fathomdb import read

    from airlock.callbacks.fathom_logger import AirlockFathomLogger
    from airlock.callbacks.request_event import build_request_event

    logger = AirlockFathomLogger(engine=engine)
    kwargs = {
        "model": "gpt-4",
        "litellm_call_id": "prov-1",
        "litellm_params": {"metadata": {"airlock_source_id": "key:90abcdef"}},
    }
    event = build_request_event(kwargs, None, None, None, success=True)
    logger.record_event(event)

    rows = read.list(engine, "RequestLog", limit=10)
    assert [row.logical_id for row in rows] == ["prov-1"]

    # The row's provenance IS the stamped identity: erasing that source_id
    # removes it. This is the axis B-2's erasure surfaces will stand on.
    engine.erase_source("key:90abcdef")
    assert read.list(engine, "RequestLog", limit=10) == []
