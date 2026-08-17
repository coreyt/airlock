"""Unit tests for value-free PII rehydration egress decisions."""

from airlock.guardrails.pii_egress import decide, egress_mode


def test_unknown_tool_is_denied(monkeypatch):
    monkeypatch.delenv("AIRLOCK_PII_EGRESS_TOOL_BANDS", raising=False)
    decision = decide(
        tool="new_runtime_tool", path="/recipient", placeholder="<EMAIL_ADDRESS_1>"
    )
    assert decision.allow is False
    assert decision.reason == "unknown_tool"


def test_round_trip_tool_allows_email(monkeypatch):
    monkeypatch.setenv("AIRLOCK_PII_EGRESS_TOOL_BANDS", '{"gmail_search":"round_trip"}')
    decision = decide(
        tool="gmail_search", path="/from", placeholder="<EMAIL_ADDRESS_1>"
    )
    assert decision.allow is True
    assert decision.reason == "round_trip"


def test_known_bad_class_vetoes_round_trip(monkeypatch):
    monkeypatch.setenv("AIRLOCK_PII_EGRESS_TOOL_BANDS", '{"card_lookup":"round_trip"}')
    decision = decide(tool="card_lookup", path="/card", placeholder="<CREDIT_CARD_1>")
    assert decision.allow is False
    assert decision.reason == "sensitive_class"


def test_exfil_requires_residual_allow(monkeypatch):
    monkeypatch.setenv("AIRLOCK_PII_EGRESS_TOOL_BANDS", '{"send_mail":"exfil"}')
    monkeypatch.setenv(
        "AIRLOCK_PII_EGRESS_ALLOWLIST",
        '[{"tool":"send_mail","path":"/to","entity_type":"EMAIL_ADDRESS"}]',
    )
    allowed = decide(tool="send_mail", path="/to", placeholder="<EMAIL_ADDRESS_1>")
    denied = decide(tool="send_mail", path="/body", placeholder="<EMAIL_ADDRESS_1>")
    assert allowed.allow is True
    assert denied.allow is False
    assert denied.reason == "exfil_not_allowlisted"


def test_default_egress_mode_is_observe(monkeypatch):
    monkeypatch.delenv("AIRLOCK_PII_EGRESS_MODE", raising=False)
    assert egress_mode() == "observe"


def test_mode_can_be_configured_to_enforce(monkeypatch):
    monkeypatch.setenv("AIRLOCK_PII_EGRESS_MODE", "enforce")
    assert egress_mode() == "enforce"


def test_known_bad_blocklist_vetoes_residual_allow(monkeypatch):
    """An incident block entry always wins over an otherwise scoped grant."""
    monkeypatch.setenv("AIRLOCK_PII_EGRESS_TOOL_BANDS", '{"send_mail":"exfil"}')
    monkeypatch.setenv(
        "AIRLOCK_PII_EGRESS_ALLOWLIST",
        '[{"tool":"send_mail","path":"/to","entity_type":"EMAIL_ADDRESS"}]',
    )
    monkeypatch.setenv(
        "AIRLOCK_PII_EGRESS_BLOCKLIST",
        '[{"tool":"send_mail","path":"/to","entity_type":"EMAIL_ADDRESS"}]',
    )

    decision = decide(tool="send_mail", path="/to", placeholder="<EMAIL_ADDRESS_1>")

    assert decision.allow is False
    assert decision.reason == "known_bad"
