"""Tests for bounded request-scoped PII reverse-map storage."""

from airlock.guardrails.pii_mapping import PIIMapStore


def test_consume_on_read_removes_mapping():
    store = PIIMapStore(max_entries=1, ttl_seconds=60)
    handle = store.put({"<EMAIL_ADDRESS_1>": "canary@example.invalid"})
    assert isinstance(handle, str)
    assert store.take(handle) == {"<EMAIL_ADDRESS_1>": "canary@example.invalid"}
    assert store.take(handle) is None
    assert len(store) == 0


def test_store_sheds_when_bounded_capacity_is_full():
    store = PIIMapStore(max_entries=1, ttl_seconds=60)
    assert store.put({"<A_1>": "one"})
    assert store.put({"<A_1>": "two"}) is None
