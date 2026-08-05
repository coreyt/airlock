"""Tests for airlock/timeutil.py — the canonical UTC timestamp shape.

The JSONL timestamp format is a compatibility surface: the TUI, the slow
analyzer, the advisor, and every retained evidence file parse it. 0.5.10
replaced ``datetime.utcnow()`` across ``airlock/``, and these tests are what
make that swap provably shape-preserving rather than merely plausible.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from airlock.timeutil import isoformat_z, parse_utc, utc_now

#: The shape every Airlock JSONL timestamp must have.
_CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


class TestIsoformatZ:
    def test_matches_the_shape_utcnow_used_to_produce(self):
        """The regression this whole module exists to prevent."""
        naive = datetime(2026, 8, 5, 12, 30, 45)
        # What the deprecated code produced, byte for byte.
        assert isoformat_z(naive) == naive.isoformat() + "Z"
        assert isoformat_z(naive) == "2026-08-05T12:30:45Z"

    def test_aware_input_does_not_produce_the_plus0000z_corruption(self):
        aware = datetime(2026, 8, 5, 12, 30, 45, tzinfo=timezone.utc)
        result = isoformat_z(aware)
        assert result == "2026-08-05T12:30:45Z"
        assert "+00:00" not in result
        # The naive spelling and the aware spelling must agree.
        assert result == isoformat_z(aware.replace(tzinfo=None))

    def test_non_utc_offset_is_converted_not_relabeled(self):
        eastern = datetime(2026, 8, 5, 8, 30, 45, tzinfo=timezone(timedelta(hours=-4)))
        assert isoformat_z(eastern) == "2026-08-05T12:30:45Z"

    def test_output_always_matches_the_canonical_shape(self):
        for value in (
            datetime(2026, 1, 1, 0, 0, 0),
            datetime(2026, 12, 31, 23, 59, 59, 999999),
            utc_now(),
        ):
            assert _CANONICAL.match(isoformat_z(value)), value


class TestParseUtc:
    def test_round_trips_with_isoformat_z(self):
        now = utc_now()
        assert parse_utc(isoformat_z(now)) == now

    def test_accepts_the_legacy_plus0000z_spelling(self):
        """Written before db76583; still on disk in retained logs.

        tui/screens/logs.py carries an explicit special case for this. Fixing
        the test fixtures that happened to emit it removed the incidental
        coverage, so it is asserted deliberately here.
        """
        assert parse_utc("2026-08-05T12:30:45+00:00Z") == datetime(
            2026, 8, 5, 12, 30, 45, tzinfo=timezone.utc
        )

    def test_naive_timestamp_is_assumed_utc(self):
        assert parse_utc("2026-08-05T12:30:45") == datetime(
            2026, 8, 5, 12, 30, 45, tzinfo=timezone.utc
        )

    def test_result_is_always_aware_so_arithmetic_cannot_raise(self):
        for raw in (
            "2026-08-05T12:30:45Z",
            "2026-08-05T12:30:45",
            "2026-08-05T12:30:45+00:00Z",
            "2026-08-05T08:30:45-04:00",
        ):
            parsed = parse_utc(raw)
            assert parsed is not None and parsed.tzinfo is not None, raw
            # The operation that TypeErrors on a naive/aware mismatch.
            assert isinstance(utc_now() - parsed, timedelta)

    def test_malformed_input_returns_none_rather_than_raising(self):
        for raw in ("", "not-a-timestamp", "2026-13-45T99:99:99Z", None, 12345):
            assert parse_utc(raw) is None, raw  # type: ignore[arg-type]


class TestUtcNow:
    def test_is_timezone_aware_utc(self):
        now = utc_now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)


class TestDeprecationBudgetDiscriminates:
    """The budget guard must distinguish first-party code from dependencies.

    Worth asserting explicitly: the obvious implementation — pytest's
    ``filterwarnings`` with a module pattern — is silently a no-op here, because
    the field is matched against a path-derived string anchored at the start.
    A guard that never fires is worse than no guard, so the discriminator gets
    its own test.
    """

    def test_classifies_airlock_source_as_first_party(self):
        from tests.conftest import _is_first_party

        import airlock.slow.analyzer as analyzer

        assert _is_first_party(analyzer.__file__)

    def test_classifies_vendored_dependency_as_third_party(self):
        from tests.conftest import _AIRLOCK_PKG_DIR, _is_first_party

        # The trap: this path contains "airlock" too, because the repository
        # root is named airlock. Only a resolved-path comparison rejects it.
        vendored = (
            _AIRLOCK_PKG_DIR.parent / ".venv/lib/python3.12/site-packages/litellm/x.py"
        )
        assert "airlock" in str(vendored)
        assert not _is_first_party(str(vendored))

    def test_unparseable_filename_is_not_first_party(self):
        from tests.conftest import _is_first_party

        assert not _is_first_party("")
        assert not _is_first_party("<stdin>")
