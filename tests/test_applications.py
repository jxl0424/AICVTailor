"""Application tracking: staleness, stats definitions, CSV export."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone

import pytest

from aicvtailor.applications import (
    STALE_AFTER_DAYS,
    compute_stats,
    days_since_movement,
    is_stale,
    reached,
    to_csv,
)
from aicvtailor.models import Application, ApplicationStatus as S

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def app(
    company="Acme",
    role="Engineer",
    status=S.APPLIED,
    days_ago=0,
    applied_on=date(2026, 5, 1),
    **kwargs,
) -> Application:
    return Application(
        company=company,
        role=role,
        status=status,
        applied_on=applied_on,
        last_movement_at=NOW - timedelta(days=days_ago),
        **kwargs,
    )


class TestStaleness:
    def test_recent_movement_is_not_stale(self):
        assert not is_stale(app(days_ago=3), now=NOW)

    def test_exactly_the_threshold_is_not_yet_stale(self):
        """'More than 14 days', so day 14 itself still counts as fresh."""
        assert not is_stale(app(days_ago=STALE_AFTER_DAYS), now=NOW)

    def test_past_the_threshold_is_stale(self):
        assert is_stale(app(days_ago=STALE_AFTER_DAYS + 1), now=NOW)

    @pytest.mark.parametrize("status", [S.REJECTED, S.GHOSTED, S.WITHDRAWN, S.OFFER])
    def test_a_finished_application_is_never_stale(self, status):
        """A rejection from a year ago is finished, not neglected. Flagging it
        would bury the ones that actually need chasing."""
        assert not is_stale(app(status=status, days_ago=400), now=NOW)

    def test_days_since_movement_is_computed_not_stored(self):
        assert days_since_movement(app(days_ago=9), now=NOW) == 9

    def test_a_naive_timestamp_is_treated_as_utc(self):
        """SQLite hands back naive datetimes; comparing them would raise."""
        application = app()
        application.last_movement_at = datetime(2026, 5, 20, 12, 0)  # no tzinfo
        assert days_since_movement(application, now=NOW) == 12


class TestRankings:
    def test_reached_compares_along_the_ladder(self):
        assert reached(S.INTERVIEW_1, S.APPLIED)
        assert reached(S.OFFER, S.INTERVIEW_1)
        assert not reached(S.APPLIED, S.INTERVIEW_1)

    def test_outcomes_are_not_on_the_ladder(self):
        """Rejection is an outcome, not a stage, so it is classified
        explicitly rather than ranked."""
        assert not reached(S.REJECTED, S.APPLIED)


class TestStats:
    def test_saved_applications_are_not_counted_as_sent(self):
        stats = compute_stats([app(status=S.SAVED), app(status=S.APPLIED)], now=NOW)
        assert stats.total == 2
        assert stats.sent == 1

    def test_a_rejection_counts_as_a_response(self):
        """You heard back. That is the thing being measured."""
        stats = compute_stats([app(status=S.REJECTED)], now=NOW)
        assert stats.responded == 1
        assert stats.response_rate == 100.0

    def test_being_ghosted_is_not_a_response(self):
        stats = compute_stats([app(status=S.GHOSTED)], now=NOW)
        assert stats.responded == 0
        assert stats.response_rate == 0.0

    def test_a_take_home_counts_as_an_interview_stage(self):
        stats = compute_stats([app(status=S.TAKE_HOME)], now=NOW)
        assert stats.interviewed == 1

    def test_rates_are_computed_over_sent_not_total(self):
        rows = [app(status=S.SAVED)] * 8 + [app(status=S.SCREENING), app(status=S.APPLIED)]
        stats = compute_stats(rows, now=NOW)

        assert stats.sent == 2
        assert stats.response_rate == 50.0  # not 10%

    def test_no_applications_does_not_divide_by_zero(self):
        stats = compute_stats([], now=NOW)
        assert stats.response_rate == 0.0
        assert stats.total == 0

    def test_every_rate_ships_with_its_definition(self):
        """The same rule as the coverage score: no bare percentages."""
        stats = compute_stats([app()], now=NOW)
        for key in ("sent", "responded", "response_rate", "interview_rate", "stale"):
            assert key in stats.definitions
            assert stats.definitions[key]

    def test_counts_by_status(self):
        stats = compute_stats([app(status=S.APPLIED), app(status=S.APPLIED), app(status=S.OFFER)], now=NOW)
        assert stats.by_status == {"applied": 2, "offer": 1}

    def test_active_excludes_finished_applications(self):
        stats = compute_stats([app(status=S.APPLIED), app(status=S.REJECTED)], now=NOW)
        assert stats.active == 1

    def test_stale_count_reflects_the_rule(self):
        rows = [app(days_ago=20), app(days_ago=2), app(status=S.REJECTED, days_ago=99)]
        assert compute_stats(rows, now=NOW).stale == 1


class TestPerCompanyHistory:
    def test_groups_repeat_applications(self):
        """The point is spotting that you have applied here before."""
        rows = [
            app(company="Acme", role="Engineer"),
            app(company="Acme", role="Senior Engineer", status=S.REJECTED),
            app(company="Globex"),
        ]
        stats = compute_stats(rows, now=NOW)

        acme = next(c for c in stats.per_company if c.company == "Acme")
        assert acme.applications == 2
        assert sorted(acme.statuses) == ["applied", "rejected"]
        assert acme.responded == 1

    def test_companies_are_ordered_by_how_often_you_applied(self):
        rows = [app(company="Globex"), app(company="Acme"), app(company="Acme")]
        assert compute_stats(rows, now=NOW).per_company[0].company == "Acme"

    def test_whitespace_does_not_split_a_company(self):
        rows = [app(company="Acme"), app(company="Acme ")]
        assert len(compute_stats(rows, now=NOW).per_company) == 1


class TestCSV:
    def test_includes_the_derived_columns(self):
        """days_since_movement and stale do not exist in the database; they are
        the reason to open the export."""
        rows = list(csv.DictReader(io.StringIO(to_csv([app(days_ago=20)], now=NOW))))

        assert rows[0]["days_since_movement"] == "20"
        assert rows[0]["stale"] == "yes"

    def test_a_fresh_application_has_a_blank_stale_column(self):
        rows = list(csv.DictReader(io.StringIO(to_csv([app(days_ago=1)], now=NOW))))
        assert rows[0]["stale"] == ""

    def test_newlines_in_notes_do_not_break_rows(self):
        text = to_csv([app(notes="line one\nline two")], now=NOW)
        assert len(list(csv.DictReader(io.StringIO(text)))) == 1

    def test_empty_export_still_has_a_header(self):
        text = to_csv([], now=NOW)
        assert text.strip().startswith("company,role,status")

    def test_dates_are_iso_formatted(self):
        rows = list(csv.DictReader(io.StringIO(to_csv([app()], now=NOW))))
        assert rows[0]["applied_on"] == "2026-05-01"
