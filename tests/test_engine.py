"""Rule engine: slides, skips, seasons, alternate weeks, and lookup mapping."""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import weekly

from pgpickup.engine import (
    coverage_warning,
    expand_schedule,
    resolve_service_date,
    schedule_from_lookup,
)
from pgpickup.errors import ConfigError
from pgpickup.holidays import load_holiday_table
from pgpickup.models import DayField, Holiday, LookupResult, StreamSchedule

TABLE = load_holiday_table()


def _by(occurrences, stream):
    return {item.nominal_date: item for item in occurrences if item.stream == stream}


def test_yard_waste_slides_on_mlk_and_stays_on_presidents_day():
    occurrences = expand_schedule(
        weekly(),
        TABLE.holidays,
        date(2026, 1, 1),
        date(2026, 2, 28),
    )
    yard = _by(occurrences, "yard_waste")
    trash = _by(occurrences, "trash")
    assert yard[date(2026, 1, 19)].service_date == date(2026, 1, 20)
    assert yard[date(2026, 2, 16)].service_date == date(2026, 2, 16)
    assert trash[date(2026, 1, 22)].service_date == date(2026, 1, 23)
    assert trash[date(2026, 1, 1)].service_date == date(2026, 1, 2)


def test_thanksgiving_and_christmas_follow_the_table():
    occurrences = expand_schedule(
        weekly(trash=(3, 4), recycling=(), yard=(), bulky_mode="none"),
        TABLE.holidays,
        date(2026, 11, 1),
        date(2026, 12, 31),
    )
    trash = _by(occurrences, "trash")
    assert trash[date(2026, 11, 5)].service_date == date(2026, 11, 5)
    assert trash[date(2026, 11, 26)].service_date == date(2026, 11, 27)
    assert trash[date(2026, 11, 27)].service_date == date(2026, 11, 28)
    assert trash[date(2026, 12, 24)].service_date == date(2026, 12, 24)
    assert trash[date(2026, 12, 25)].service_date == date(2026, 12, 26)


def test_slide_holiday_is_not_itself_a_service_day():
    occurrences = expand_schedule(
        weekly(trash=(0, 1, 2, 3, 4), recycling=(), yard=(), bulky_mode="none"),
        TABLE.holidays,
        date(2026, 1, 1),
        date(2026, 1, 31),
    )
    service_dates = {item.service_date for item in occurrences}
    assert date(2026, 1, 1) not in service_dates
    assert date(2026, 1, 19) not in service_dates
    assert date(2026, 1, 2) in service_dates
    assert date(2026, 1, 3) in service_dates


def test_range_keeps_a_friday_that_slides_into_the_window():
    occurrences = expand_schedule(
        weekly(trash=(4,), recycling=(), yard=(), bulky_mode="none"),
        TABLE.holidays,
        date(2026, 1, 3),
        date(2026, 1, 3),
    )
    assert len(occurrences) == 1
    assert occurrences[0].nominal_date == date(2026, 1, 2)
    assert occurrences[0].service_date == date(2026, 1, 3)


def test_skip_drops_one_day_and_does_not_slide_the_rest():
    holidays = (
        Holiday(
            name="Closure",
            date=date(2026, 3, 4),
            observe="skip",
            streams=frozenset({"trash", "recycling", "yard_waste", "bulky"}),
        ),
    )
    occurrences = expand_schedule(
        weekly(trash=(2, 3), recycling=(), yard=(), bulky_mode="none"),
        holidays,
        date(2026, 3, 1),
        date(2026, 3, 8),
    )
    nominals = {item.nominal_date: item.service_date for item in occurrences}
    assert date(2026, 3, 4) not in nominals
    assert nominals[date(2026, 3, 5)] == date(2026, 3, 5)


def test_tuesday_slide_moves_the_rest_of_the_week_only():
    holidays = (
        Holiday(
            name="Tuesday holiday",
            date=date(2026, 3, 3),
            observe="slide",
            streams=frozenset({"trash"}),
        ),
    )
    expected = {
        date(2026, 3, 2): date(2026, 3, 2),
        date(2026, 3, 3): date(2026, 3, 4),
        date(2026, 3, 4): date(2026, 3, 5),
        date(2026, 3, 5): date(2026, 3, 6),
        date(2026, 3, 6): date(2026, 3, 7),
    }
    for nominal, service in expected.items():
        got, _name = resolve_service_date(nominal, holidays, "trash")
        assert got == service


def test_saturday_slide_does_not_move_friday():
    holidays = (
        Holiday(
            name="Saturday",
            date=date(2026, 7, 4),
            observe="slide",
            streams=frozenset({"trash"}),
        ),
    )
    service, name = resolve_service_date(date(2026, 7, 3), holidays, "trash")
    assert service == date(2026, 7, 3)
    assert name is None


def test_two_trash_days_and_bulky_with_trash():
    occurrences = expand_schedule(
        weekly(trash=(1, 4), recycling=(), yard=(), bulky_mode="with_trash"),
        (),
        date(2026, 3, 2),
        date(2026, 3, 8),
    )
    trash = [item.service_date for item in occurrences if item.stream == "trash"]
    bulky = [item.service_date for item in occurrences if item.stream == "bulky"]
    assert trash == [date(2026, 3, 3), date(2026, 3, 6)]
    assert bulky == trash
    assert len({(item.stream, item.nominal_date) for item in occurrences}) == 4


def test_appointment_bulky_creates_no_events():
    occurrences = expand_schedule(
        weekly(bulky_mode="appointment"),
        (),
        date(2026, 3, 2),
        date(2026, 3, 8),
    )
    assert all(item.stream != "bulky" for item in occurrences)


def test_yard_season_window_and_year_wrap():
    in_season = expand_schedule(
        weekly(
            trash=(),
            recycling=(),
            yard=(0,),
            bulky_mode="none",
            season=((3, 1), (12, 15)),
        ),
        (),
        date(2026, 1, 1),
        date(2026, 12, 31),
    )
    nominals = {item.nominal_date for item in in_season}
    assert date(2026, 2, 23) not in nominals
    assert date(2026, 3, 2) in nominals
    assert date(2026, 12, 14) in nominals
    assert date(2026, 12, 21) not in nominals

    wrapped = expand_schedule(
        weekly(
            trash=(),
            recycling=(),
            yard=(0,),
            bulky_mode="none",
            season=((11, 15), (3, 1)),
        ),
        (),
        date(2026, 1, 1),
        date(2026, 12, 31),
    )
    wrapped_days = {item.nominal_date for item in wrapped}
    assert date(2026, 1, 5) in wrapped_days
    assert date(2026, 3, 2) not in wrapped_days
    assert date(2026, 11, 9) not in wrapped_days
    assert date(2026, 11, 16) in wrapped_days


def test_alternate_recycling_uses_the_anchor_week():
    occurrences = expand_schedule(
        weekly(
            trash=(),
            recycling=(3,),
            yard=(),
            bulky_mode="none",
            frequency="alternate",
            anchor=date(2026, 1, 8),
        ),
        TABLE.holidays,
        date(2026, 1, 1),
        date(2026, 1, 31),
    )
    nominals = [item.nominal_date for item in occurrences]
    assert nominals == [date(2026, 1, 8), date(2026, 1, 22)]


def test_alternate_anchor_on_new_year_keeps_the_nominal_uid_date():
    occurrences = expand_schedule(
        weekly(
            trash=(),
            recycling=(3,),
            yard=(),
            bulky_mode="none",
            frequency="alternate",
            anchor=date(2026, 1, 1),
        ),
        TABLE.holidays,
        date(2026, 1, 1),
        date(2026, 1, 10),
    )
    assert len(occurrences) == 1
    assert occurrences[0].nominal_date == date(2026, 1, 1)
    assert occurrences[0].service_date == date(2026, 1, 2)


def test_coverage_warning_after_the_published_table():
    assert coverage_warning(TABLE, date(2027, 1, 2)) is None
    warning = coverage_warning(TABLE, date(2027, 1, 31))
    assert warning is not None
    assert "2027-01-01" in warning
    assert "not shifted" in warning


def test_lookup_mapping_and_call_311():
    result = LookupResult(
        query_address="example",
        matched_address="EXAMPLE",
        score=100,
        longitude=0,
        latitude=0,
        trash=DayField(weekdays=(3,)),
        recycling=DayField(weekdays=(3,)),
        yard_waste=DayField(weekdays=(0,)),
        bulky=DayField(appointment=True),
        contractor="",
        tier="",
        polygon_count=1,
    )
    schedule = schedule_from_lookup(result, weekly(bulky_mode="derive"))
    assert schedule.trash.weekdays == (3,)
    assert schedule.yard_waste.weekdays == (0,)
    assert schedule.bulky_mode == "appointment"
    occurrences = expand_schedule(schedule, (), date(2026, 3, 2), date(2026, 3, 8))
    assert {item.stream for item in occurrences} == {"trash", "recycling", "yard_waste"}


def test_derive_cannot_expand_directly():
    with pytest.raises(ConfigError):
        expand_schedule(weekly(bulky_mode="derive"), (), date(2026, 3, 2), date(2026, 3, 8))


def test_stream_limited_slide_does_not_move_recycling():
    holidays = (
        Holiday(
            name="Trash only",
            date=date(2026, 3, 5),
            observe="slide",
            streams=frozenset({"trash"}),
        ),
    )
    trash, _name = resolve_service_date(date(2026, 3, 5), holidays, "trash")
    recycling, recycling_name = resolve_service_date(date(2026, 3, 5), holidays, "recycling")
    assert trash == date(2026, 3, 6)
    assert recycling == date(2026, 3, 5)
    assert recycling_name is None


def test_invalid_alternate_anchor_is_rejected():
    schedule = weekly(frequency="alternate", anchor=date(2026, 1, 7))
    with pytest.raises(ConfigError):
        expand_schedule(schedule, (), date(2026, 1, 1), date(2026, 1, 31))


def test_season_fields_are_month_days():
    spec = StreamSchedule(weekdays=(0,), season_start=(12, 1), season_end=(1, 15))
    assert spec.in_season(date(2026, 12, 1))
    assert spec.in_season(date(2026, 1, 15))
    assert not spec.in_season(date(2026, 6, 1))
