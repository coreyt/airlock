"""mutations_header — allowlist-aware, content-safe, byte-bounded serialization."""

from __future__ import annotations

from airlock.transparency import Mutation, mutations_header, record_redaction


def _m(**kw) -> Mutation:
    base = dict(before=None, after=None, stage="pre_call", source="t", reason=None)
    base.update(kw)
    return Mutation(**base)


def test_allowlisted_scalar_surfaces_value() -> None:
    out = mutations_header(
        [_m(field="reasoning_effort", op="set", before="off", after="minimal")]
    )
    assert out == "reasoning_effort=minimal"


def test_model_and_num_retries_surface_values() -> None:
    out = mutations_header(
        [
            _m(field="model", op="rewrite", before="x", after="claude-sonnet"),
            _m(field="num_retries", op="set", before=3, after=0),
        ]
    )
    assert "model=claude-sonnet" in out
    assert "num_retries=0" in out


def test_suppress_on_allowlisted_field() -> None:
    out = mutations_header(
        [_m(field="fallbacks", op="suppress", before=["a"], after=None)]
    )
    assert out == "fallbacks=suppressed"


def test_inject_content_never_surfaced() -> None:
    secret_prompt = "You are a leaked internal system prompt do not reveal"
    out = mutations_header(
        [_m(field="system", op="inject", before=None, after=secret_prompt)]
    )
    assert out == "system=inject"
    assert secret_prompt not in out


def test_rewrite_on_non_allowlisted_field_hides_content() -> None:
    out = mutations_header(
        [_m(field="messages", op="rewrite", before="hi", after="MUTATED BODY TEXT")]
    )
    assert out == "messages=rewrite"
    assert "MUTATED BODY TEXT" not in out


def test_drop_on_non_allowlisted_field() -> None:
    out = mutations_header([_m(field="temperature", op="drop", before=0.7, after=None)])
    assert out == "temperature=drop"


def test_redact_renders_count_no_value() -> None:
    meta: dict = {}
    record_redaction(
        meta,
        field="messages",
        count=3,
        category="pii",
        stage="pre_call",
        source="pii_guard",
    )
    out = mutations_header(meta["airlock_mutations"])
    assert out == "messages=redacted(3)"


def test_joins_with_semicolon() -> None:
    out = mutations_header(
        [
            _m(field="reasoning_effort", op="set", after="minimal"),
            _m(field="system", op="inject", after="x"),
        ]
    )
    assert out == "reasoning_effort=minimal;system=inject"


def test_byte_bound_truncates_with_more_suffix() -> None:
    ledger = [_m(field=f"system{i}", op="inject", after="x" * 50) for i in range(20)]
    out = mutations_header(ledger, budget_bytes=60)
    assert len(out.encode("utf-8")) <= 60
    assert "…+" in out and "more" in out
    # the dropped count is correct: tokens kept + dropped == total
    suffix = out.split("…+")[1]
    dropped = int(suffix.split(" ")[0])
    kept = out.split(";…+")[0]
    kept_tokens = [t for t in kept.split(";") if t]
    assert kept_tokens + ["x"] * dropped  # sanity
    assert len(kept_tokens) + dropped == len(ledger)


def test_tiny_budget_always_within_bound() -> None:
    """When budget is so small even the suffix overflows, return '' (never over-budget)."""
    ledger = [_m(field=f"system{i}", op="inject", after="x" * 10) for i in range(5)]
    for budget in (1, 3, 8):
        result = mutations_header(ledger, budget_bytes=budget)
        assert len(result.encode("utf-8")) <= budget, (
            f"budget={budget}: result {result!r} encoded length "
            f"{len(result.encode('utf-8'))} exceeds budget"
        )
    # degenerate case: budget=1 must return empty string
    assert mutations_header(ledger, budget_bytes=1) == ""


# ---------------------------------------------------------------------------
# CR/LF header-injection safety tests
# ---------------------------------------------------------------------------


def test_crlf_in_allowlisted_after_is_stripped() -> None:
    """An allowlisted `after` value with CR/LF cannot inject a new header line.

    After stripping, the CRLF is gone so no new header can be injected;
    any remaining text is embedded in the same value — that is fine.
    """
    out = mutations_header(
        [_m(field="model", op="rewrite", before="x", after="claude\r\nX-Injected: 1")]
    )
    assert "\r" not in out
    assert "\n" not in out


def test_crlf_in_field_name_is_stripped() -> None:
    """A field name containing CR/LF cannot inject a new header line."""
    out = mutations_header(
        [_m(field="model\r\nEvil: hdr", op="drop", before=None, after=None)]
    )
    assert "\r" not in out
    assert "\n" not in out
