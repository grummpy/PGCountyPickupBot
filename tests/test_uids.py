"""UIDs stay stable across runs and holiday slides."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tests.conftest import weekly

from pgpickup.engine import expand_schedule
from pgpickup.gcal import google_body
from pgpickup.holidays import load_holiday_table
from pgpickup.ics import CalendarOptions, build_calendar
from pgpickup.models import occurrence_uid

OPTIONS = CalendarOptions(now=datetime(2026, 10, 6, tzinfo=UTC))


def _uids(occurrences):
    calendar = build_calendar(occurrences, OPTIONS)
    return [str(event.get("uid")) for event in calendar.walk("VEVENT")]


def test_uids_repeat_and_use_the_nominal_date():
    holidays = load_holiday_table().holidays
    schedule = weekly()
    first = expand_schedule(schedule, holidays, date(2026, 1, 1), date(2027, 1, 31))
    second = expand_schedule(schedule, holidays, date(2026, 1, 1), date(2027, 1, 31))
    assert _uids(first) == _uids(second)
    assert len(set(_uids(first))) == len(first)
    new_year = next(
        item for item in first if item.nominal_date == date(2026, 1, 1) and item.stream == "trash"
    )
    assert new_year.service_date == date(2026, 1, 2)
    assert occurrence_uid(new_year) == "trash-2026-01-01@pgcounty-pickup-bot.local"
    assert occurrence_uid(new_year) in _uids(first)
    body = google_body(new_year, OPTIONS)
    assert body["iCalUID"] == occurrence_uid(new_year)
    assert body["extendedProperties"]["private"]["pgpickup"] == "1"
    assert "123 Secret" not in body["description"]
