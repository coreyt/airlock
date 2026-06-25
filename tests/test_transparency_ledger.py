"""Mutation ledger helpers — record_mutation / record_redaction + CC-T2 invariant."""

from __future__ import annotations

import pytest

from airlock.transparency import Mutation, record_mutation, record_redaction


def test_record_mutation_appends_one_record() -> None:
    meta: dict = {}
    record_mutation(
        meta,
        field="reasoning_effort",
        op="set",
        before="off",
        after="minimal",
        stage="pre_call",
        source="reasoning_effort.normalize",
        reason="openai has no 'off'; floored to minimal",
    )
    ledger = meta["airlock_mutations"]
    assert len(ledger) == 1
    m = ledger[0]
    assert isinstance(m, Mutation)
    assert m.field == "reasoning_effort"
    assert m.op == "set"
    assert m.before == "off"
    assert m.after == "minimal"
    assert m.stage == "pre_call"
    assert m.source == "reasoning_effort.normalize"
    assert m.reason == "openai has no 'off'; floored to minimal"


def test_records_preserve_call_order_single_ledger() -> None:
    meta: dict = {}
    record_mutation(
        meta,
        field="model",
        op="rewrite",
        before="a",
        after="b",
        stage="pre_call",
        source="router",
    )
    record_mutation(
        meta,
        field="fallbacks",
        op="suppress",
        before=["x"],
        after=None,
        stage="pre_call",
        source="guardian",
    )
    record_redaction(
        meta,
        field="messages",
        count=3,
        category="pii",
        stage="pre_call",
        source="pii_guard",
    )
    ledger = meta["airlock_mutations"]
    assert [m.field for m in ledger] == ["model", "fallbacks", "messages"]
    # one and only one ledger key (CC-T1)
    assert list(meta.keys()) == ["airlock_mutations"]


def test_redact_via_ctor_with_value_raises() -> None:
    with pytest.raises(ValueError):
        Mutation(
            field="messages",
            op="redact",
            before="secret",
            after=None,
            stage="pre_call",
            source="pii_guard",
        )
    with pytest.raises(ValueError):
        Mutation(
            field="messages",
            op="redact",
            before=None,
            after="secret",
            stage="pre_call",
            source="pii_guard",
        )


def test_record_redaction_is_value_free_and_secret_absent() -> None:
    secret = "hunter2-super-secret-token"
    meta: dict = {}
    record_redaction(
        meta,
        field="messages",
        count=2,
        category="pii",
        stage="pre_call",
        source="pii_guard",
    )
    m = meta["airlock_mutations"][0]
    assert m.op == "redact"
    assert m.before is None
    assert m.after is None
    assert m.count == 2
    assert m.category == "pii"
    # the secret never appears anywhere in the ledger record
    assert secret not in repr(meta["airlock_mutations"])
