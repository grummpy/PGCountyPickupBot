"""ICS bytes parse with icalendar, including the November 2026 DST change."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from icalendar import Calendar
from tests.conftest import weekly

from pgpickup.engine import expand_schedule
from pgpickup.holidays import load_holiday_table
from pgpickup.ics import (
    CalendarOptions,
    reminder_at,
    render_ics,
)
from pgpickup.models import Occurrence

OPTIONS = CalendarOptions(
    reminder_time=time(19, 0),
    collection_start=time(6, 0),
    collection_end=time(20, 0),
    now=datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
)


def _event_by_uid(calendar: Calendar, nominal: date, stream: str = "trash"):
    token = f"{stream}-{nominal.isoformat()}@"
    matches = [event for event in calendar.walk("VEVENT") if token in str(event.get("uid"))]
    assert len(matches) == 1
    return matches[0]


def _parsed(occurrences: list[Occurrence]) -> Calendar:
    return Calendar.from_ical(render_ics(occurrences, OPTIONS))


def test_calendar_has_new_york_zone_and_folded_lines():
    raw = render_ics(
        [Occurrence("trash", date(2026, 11, 5), date(2026, 11, 5))],
        OPTIONS,
    )
    text = raw.decode("utf-8")
    assert raw.endswith(b"\r\n")
    assert "VERSION:2.0" in text
    assert "PRODID:-//PG County Pickup Bot//pgpickup//EN" in text
    assert "TZID:America/New_York" in text
    assert "TZOFFSETTO:-0400" in text
    assert "TZOFFSETTO:-0500" in text
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
    calendar = Calendar.from_ical(raw)
    assert calendar.get("version") == "2.0"
    timezones = calendar.walk("VTIMEZONE")
    assert [str(item.get("tzid")) for item in timezones] == ["America/New_York"]


def test_dst_offsets_around_november_2026_and_march_2026():
    occurrences = [
        Occurrence("trash", date(2026, 10, 29), date(2026, 10, 29)),
        Occurrence("trash", date(2026, 11, 1), date(2026, 11, 1)),
        Occurrence("trash", date(2026, 11, 5), date(2026, 11, 5)),
        Occurrence("recycling", date(2026, 3, 8), date(2026, 3, 8)),
    ]
    calendar = _parsed(occurrences)
    expected = {
        ("trash", date(2026, 10, 29)): (
            datetime(2026, 10, 29, 10, 0, tzinfo=UTC),
            datetime(2026, 10, 28, 23, 0, tzinfo=UTC),
        ),
        ("trash", date(2026, 11, 5)): (
            datetime(2026, 11, 5, 11, 0, tzinfo=UTC),
            datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
        ),
        # Sunday collection crosses the fall-back. 7:00 p.m. Saturday is EDT,
        # while 6:00 a.m. Sunday is EST, so the gap is 12 hours, not 11.
        ("trash", date(2026, 11, 1)): (
            datetime(2026, 11, 1, 11, 0, tzinfo=UTC),
            datetime(2026, 10, 31, 23, 0, tzinfo=UTC),
        ),
        # Spring forward: 7:00 p.m. Saturday EST to 6:00 a.m. Sunday EDT is 10 hours.
        ("recycling", date(2026, 3, 8)): (
            datetime(2026, 3, 8, 10, 0, tzinfo=UTC),
            datetime(2026, 3, 8, 0, 0, tzinfo=UTC),
        ),
    }
    for (stream, nominal), (start_utc, reminder_utc) in expected.items():
        event = _event_by_uid(calendar, nominal, stream)
        start = event.decoded("dtstart")
        assert start.hour == 6
        assert start.astimezone(UTC) == start_utc
        assert "TZID=America/New_York" in event.to_ical().decode("utf-8")
        trigger = event.walk("VALARM")[0].decoded("trigger")
        assert isinstance(trigger, datetime)
        assert trigger.astimezone(UTC) == reminder_utc
        local_reminder = reminder_at(nominal, OPTIONS)
        assert local_reminder.hour == 19
        assert local_reminder.astimezone(UTC) == reminder_utc


def test_address_stays_out_unless_include_location_is_set():
    occurrence = Occurrence("trash", date(2026, 11, 5), date(2026, 11, 5))
    hidden = render_ics([occurrence], OPTIONS).decode("utf-8")
    assert "123 Secret Lane" not in hidden
    shown = render_ics(
        [occurrence],
        CalendarOptions(
            include_location=True,
            location="123 Secret Lane, Clinton, MD",
            now=OPTIONS.now,
        ),
    ).decode("utf-8")
    assert "123 Secret Lane" in shown


def test_new_year_shift_is_a_valid_event():
    occurrences = expand_schedule(
        weekly(recycling=(), yard=(), bulky_mode="none"),
        load_holiday_table().holidays,
        date(2026, 1, 1),
        date(2026, 1, 3),
    )
    calendar = _parsed(occurrences)
    event = _event_by_uid(calendar, date(2026, 1, 1))
    start = event.decoded("dtstart")
    assert start.date() == date(2026, 1, 2)
    assert "New Year" in str(event.get("summary"))
    assert str(event.get("uid")).startswith("trash-2026-01-01@")
