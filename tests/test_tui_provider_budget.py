"""Tests for provider spend and budget utilization on Overview (#23, pack B-2).

The data existed in ``fast/state.py`` and the admin API already computed
``budget_utilization``; the TUI showed spend against cap but never the
utilization, and the provider detail pane fetched the snapshot and discarded it.
"""

from __future__ import annotations

from airlock.tui.screens.overview import (
    _BUDGET_ALERT_UTILIZATION,
    _budget_utilization,
    _format_spend,
    _render_budget_detail,
)


class TestBudgetUtilization:
    def test_prefers_the_value_the_admin_api_computed(self):
        """The TUI must not disagree with the API about the same number."""
        snapshot = {"spend_usd": 5.0, "budget_cap_usd": 10.0, "budget_utilization": 0.5}
        assert _budget_utilization(snapshot) == 0.5

    def test_derives_only_when_the_field_is_absent(self):
        assert _budget_utilization({"spend_usd": 2.5, "budget_cap_usd": 10.0}) == 0.25

    def test_uncapped_provider_has_no_utilization(self):
        assert _budget_utilization({"spend_usd": 2.5, "budget_cap_usd": None}) is None
        assert _budget_utilization({"spend_usd": 2.5}) is None

    def test_zero_cap_does_not_divide_by_zero(self):
        assert _budget_utilization({"spend_usd": 2.5, "budget_cap_usd": 0}) is None


class TestFormatSpend:
    def test_includes_utilization_percentage(self):
        cell = _format_spend({"spend_usd": 2.5, "budget_cap_usd": 10.0})
        assert cell == "$2.50/$10.00 (25%)"

    def test_flags_a_provider_near_its_cap(self):
        cell = _format_spend({"spend_usd": 9.0, "budget_cap_usd": 10.0})
        assert cell.endswith("!")

    def test_does_not_flag_a_provider_with_headroom(self):
        assert not _format_spend({"spend_usd": 1.0, "budget_cap_usd": 10.0}).endswith(
            "!"
        )

    def test_uncapped_provider_shows_spend_only(self):
        assert _format_spend({"spend_usd": 2.5, "budget_cap_usd": None}) == "$2.50"

    def test_missing_spend_renders_empty(self):
        assert _format_spend({}) == ""

    def test_cell_contains_no_rich_markup(self):
        """DataTable renders cell strings literally.

        A Rich tag here would print as the literal text "[red]" in the table
        rather than colouring anything.
        """
        for snapshot in (
            {"spend_usd": 9.9, "budget_cap_usd": 10.0},
            {"spend_usd": 1.0, "budget_cap_usd": 10.0},
            {"spend_usd": 1.0},
        ):
            assert "[" not in _format_spend(snapshot), snapshot


class TestBudgetDetail:
    def test_reports_when_the_admin_api_is_unavailable(self):
        """Blank would read as "no spend"; it means "could not ask"."""
        detail = _render_budget_detail({})
        assert "unavailable" in detail

    def test_shows_spend_cap_and_percentage(self):
        detail = _render_budget_detail(
            {"spend_usd": 2.5, "budget_cap_usd": 10.0, "budget_utilization": 0.25}
        )
        assert "$2.50" in detail
        assert "$10.00" in detail
        assert "25%" in detail

    def test_warns_with_a_consequence_when_near_the_cap(self):
        detail = _render_budget_detail(
            {"spend_usd": 9.5, "budget_cap_usd": 10.0, "budget_utilization": 0.95}
        )
        assert "[red bold]" in detail
        # The operator needs to know what happens next, not just that a number
        # is large.
        assert "will be refused" in detail

    def test_does_not_warn_below_the_threshold(self):
        detail = _render_budget_detail(
            {
                "spend_usd": 1.0,
                "budget_cap_usd": 10.0,
                "budget_utilization": _BUDGET_ALERT_UTILIZATION - 0.1,
            }
        )
        assert "will be refused" not in detail

    def test_uncapped_provider_says_so_rather_than_showing_a_bare_number(self):
        detail = _render_budget_detail({"spend_usd": 2.5, "budget_cap_usd": None})
        assert "no cap configured" in detail

    def test_includes_rate_limit_and_token_headroom(self):
        detail = _render_budget_detail(
            {
                "spend_usd": 1.0,
                "budget_cap_usd": 10.0,
                "remaining_requests": 40,
                "limit_requests": 100,
                "remaining_tokens": 9000,
                "limit_tokens": 10000,
            }
        )
        assert "40/100" in detail
        assert "9000/10000" in detail

    def test_omits_headroom_lines_when_the_provider_reports_none(self):
        detail = _render_budget_detail({"spend_usd": 1.0, "budget_cap_usd": 10.0})
        assert "headroom" not in detail
