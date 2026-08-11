"""0.5.11 C-1 — TUI ballast (#24 failover trail, #27 escalation, #28 Gemini %).

Plus the C-2 owner decision: the remote analyzer default tracks current Sonnet.
"""

import datetime
import json
import time

from airlock.fast.state import PROVIDER_ESCALATION_CLIENT_THRESHOLD, StateStore
from airlock.tui.failover_feed import FailoverFeed
from airlock.tui.screens.overview import _gemini_mode_summary, _impacted_display


def _write_records(path, records):
    with open(path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _log_path(tmp_path):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    return tmp_path / f"airlock-{today.isoformat()}.jsonl"


def _iso(offset_seconds=0.0):
    return (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=offset_seconds)
    ).isoformat()


# ---------------------------------------------------------------------------
# #24 — FailoverFeed
# ---------------------------------------------------------------------------
def test_failover_feed_collects_failover_records(tmp_path):
    _write_records(
        _log_path(tmp_path),
        [
            {"timestamp": _iso(), "model": "gpt-4o"},  # no failover — ignored
            {
                "timestamp": _iso(),
                "airlock_failover": {
                    "original_model": "gpt-4o",
                    "failover_model": "claude-sonnet",
                    "reason": "circuit open",
                },
            },
        ],
    )

    feed = FailoverFeed(log_dir=str(tmp_path))
    feed.poll()

    assert len(feed.events) == 1
    event = feed.events[0]
    assert event["original_model"] == "gpt-4o"
    assert event["failover_model"] == "claude-sonnet"
    assert event["reason"] == "circuit open"
    assert feed.recent_count(300.0) == 1


def test_failover_feed_ignores_non_object_json_records(tmp_path):
    _write_records(
        _log_path(tmp_path),
        ["not an event", ["not", "an event"], {"airlock_failover": "invalid"}],
    )

    feed = FailoverFeed(log_dir=str(tmp_path))
    feed.poll()

    assert list(feed.events) == []


def test_failover_feed_reads_incrementally(tmp_path):
    path = _log_path(tmp_path)
    _write_records(
        path,
        [
            {
                "timestamp": _iso(),
                "airlock_failover": {
                    "original_model": "a",
                    "failover_model": "b",
                    "reason": "r1",
                },
            }
        ],
    )
    feed = FailoverFeed(log_dir=str(tmp_path))
    feed.poll()
    assert len(feed.events) == 1

    # Appending and re-polling must not re-read (and re-append) the first event.
    _write_records(
        path,
        [
            {
                "timestamp": _iso(),
                "airlock_failover": {
                    "original_model": "c",
                    "failover_model": "d",
                    "reason": "r2",
                },
            }
        ],
    )
    feed.poll()

    assert [event["reason"] for event in feed.events] == ["r1", "r2"]


def test_failover_feed_recent_filters_by_window_and_model(tmp_path):
    _write_records(
        _log_path(tmp_path),
        [
            {
                "timestamp": _iso(-3600),
                "airlock_failover": {
                    "original_model": "old-model",
                    "failover_model": "x",
                    "reason": "stale",
                },
            },
            {
                "timestamp": _iso(),
                "airlock_failover": {
                    "original_model": "gpt-4o",
                    "failover_model": "claude-sonnet",
                    "reason": "fresh",
                },
            },
        ],
    )
    feed = FailoverFeed(log_dir=str(tmp_path))
    feed.poll()

    assert [e["reason"] for e in feed.recent(300.0)] == ["fresh"]
    # Model filter matches either side of the swap.
    assert feed.recent(300.0, model="claude-sonnet")
    assert feed.recent(300.0, model="gpt-4o")
    assert feed.recent(300.0, model="unrelated") == []


def test_failover_feed_missing_file_is_quiet(tmp_path):
    feed = FailoverFeed(log_dir=str(tmp_path / "nope"))
    feed.poll()
    assert feed.recent_count() == 0


# ---------------------------------------------------------------------------
# #28 — Gemini mode distribution
# ---------------------------------------------------------------------------
def test_gemini_summary_empty_window():
    assert "none in window" in _gemini_mode_summary({"text": 0, "thought_only": 0})


def test_gemini_summary_shows_percentages():
    out = _gemini_mode_summary({"text": 3, "thought_only": 1, "tool": 0})
    assert "text: 3 (75%)" in out
    assert "thought_only: 1 (25%)" in out
    assert "tool" not in out  # zero-count modes are omitted
    assert "skew" not in out  # sample too small to flag


def test_gemini_summary_flags_skew_on_dominant_mode():
    out = _gemini_mode_summary({"text": 1, "thought_only": 17, "tool": 2})
    assert "⚠ mode skew: thought_only at 85%" in out


def test_gemini_summary_no_skew_flag_below_threshold():
    # 75% dominant — under the 80% flag line.
    out = _gemini_mode_summary({"text": 15, "thought_only": 5})
    assert "skew" not in out


# ---------------------------------------------------------------------------
# #27 — escalation display, snapshot field, alert rule
# ---------------------------------------------------------------------------
def test_impacted_display_prefers_live_snapshot():
    # Local replica says 0 (it cannot see live rate_limit_events); the
    # snapshot is authoritative.
    assert _impacted_display({"impacted_clients": ["a"]}, 0, 2) == "1"
    assert _impacted_display({"impacted_clients": ["a", "b", "c"]}, 0, 2) == "3 ⚠ESC"


def test_impacted_display_falls_back_to_local():
    assert _impacted_display({}, 1, 2) == "1"
    assert _impacted_display({}, 2, 2) == "2 ⚠ESC"


def test_admin_provider_snapshot_carries_impacted_clients(fresh_state_store):
    from airlock.admin.http import _view_providers
    from airlock.fast.state import store

    provider = store.get_provider("openai")
    now = time.time()
    provider.rate_limit_events.append((now, "key:aaaa1111"))
    provider.rate_limit_events.append((now, "key:bbbb2222"))

    snapshot = _view_providers()["providers"]["openai"]

    assert snapshot["impacted_clients"] == ["key:aaaa1111", "key:bbbb2222"]


def test_provider_escalation_alert_fires_from_snapshot():
    from airlock.tui.alert_engine import (
        _check_provider_escalation,
        set_provider_snapshot,
    )

    impacted = [f"key:{i}" for i in range(PROVIDER_ESCALATION_CLIENT_THRESHOLD)]
    set_provider_snapshot({"openai": {"impacted_clients": impacted}})
    try:
        alerts = _check_provider_escalation(StateStore())
        assert len(alerts) == 1
        assert alerts[0].rule_name == "provider_escalation"
        assert alerts[0].entity_id == "openai"
        assert str(len(impacted)) in alerts[0].title
    finally:
        set_provider_snapshot(None)


def test_provider_escalation_alert_quiet_below_threshold():
    from airlock.tui.alert_engine import (
        _check_provider_escalation,
        set_provider_snapshot,
    )

    set_provider_snapshot({"openai": {"impacted_clients": ["key:only1"]}, "gemini": {}})
    try:
        assert _check_provider_escalation(StateStore()) == []
    finally:
        set_provider_snapshot(None)


def test_provider_escalation_rule_registered():
    from airlock.tui.alert_engine import _DEFAULT_RULES

    assert any(rule.name == "provider_escalation" for rule in _DEFAULT_RULES)


# ---------------------------------------------------------------------------
# C-2 — remote analyzer default tracks current Sonnet (owner decision)
# ---------------------------------------------------------------------------
def test_remote_analyzer_default_is_current_sonnet():
    from airlock.slow.analyzer_llm import ANALYZER_REMOTE_MODEL_DEFAULT

    assert ANALYZER_REMOTE_MODEL_DEFAULT == "claude-sonnet-5"
