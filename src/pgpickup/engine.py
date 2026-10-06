"""Turn a weekly pattern and the holiday table into concrete pickup dates."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from pgpickup.errors import ConfigError
from pgpickup.models import (
    STREAM_ORDER,
    WEEKDAY_NAMES,
    Holiday,
    HolidayTable,
    LookupResult,
    Occurrence,
    Schedule,
    StreamSchedule,
)

LOOKBACK = timedelta(days=6)


def resolve_service_date(
    nominal: date,
    holidays: Sequence[Holiday],
    stream: str,
) -> tuple[date | None, str | None]:
    """Apply skip and one-day holiday slides to a regular collection date.

    A ``skip`` holiday removes only that calendar day. A ``slide`` holiday
    moves every Monday–Friday regular day on or after the holiday, inside the
    Monday–Sunday week that contains the regular day, one day later for each
    such holiday. Saturday and Sunday regular days are left alone. ``none``
    records a holiday the county still collects and does not move anything.
    """
    week_start = nominal - timedelta(days=nominal.weekday())
    week_end = week_start + timedelta(days=6)
    relevant = [
        holiday
        for holiday in holidays
        if week_start <= holiday.date <= week_end and stream in holiday.streams
    ]
    for holiday in relevant:
        if holiday.observe == "skip" and holiday.date == nominal:
            return None, holiday.name
    if nominal.weekday() > 4:
        return nominal, None
    slides = [
        holiday for holiday in relevant if holiday.observe == "slide" and holiday.date <= nominal
    ]
    if not slides:
        return nominal, None
    slides.sort(key=lambda holiday: holiday.date)
    return nominal + timedelta(days=len(slides)), slides[-1].name


def expand_schedule(
    schedule: Schedule,
    holidays: Sequence[Holiday],
    start: date,
    end: date,
) -> list[Occurrence]:
    if end < start:
        raise ConfigError("range.end is before range.start.")
    if schedule.bulky_mode == "derive":
        raise ConfigError("Resolve a lookup result before building pickup dates.")
    validate_resolved_schedule(schedule)
    nominal_start = start - LOOKBACK
    found: list[Occurrence] = []
    for stream, spec in schedule.stream_specs():
        for weekday in spec.weekdays:
            for nominal in _iter_weekday(nominal_start, end, weekday):
                if not spec.in_season(nominal):
                    continue
                if spec.frequency == "alternate" and not _on_anchor_week(nominal, spec.anchor):
                    continue
                service, holiday_name = resolve_service_date(nominal, holidays, stream)
                if service is None or service < start or service > end:
                    continue
                found.append(
                    Occurrence(
                        stream=stream,
                        nominal_date=nominal,
                        service_date=service,
                        holiday_name=holiday_name,
                    )
                )
    found.sort(
        key=lambda item: (
            item.service_date,
            STREAM_ORDER[item.stream],
            item.nominal_date,
        )
    )
    return found


def upcoming(occurrences: Sequence[Occurrence], today: date, limit: int) -> list[Occurrence]:
    if limit < 1:
        raise ConfigError("Limit must be at least 1.")
    return [item for item in occurrences if item.service_date >= today][:limit]


def coverage_warning(table: HolidayTable, end: date) -> str | None:
    if not table.holidays:
        return "No holidays are loaded, so no days will shift."
    last = max(holiday.date for holiday in table.holidays)
    if end > last + timedelta(days=6):
        return (
            f"The holiday table runs through {last.isoformat()} "
            f"(county page checked {table.checked.isoformat()}). "
            f"This range ends {end.isoformat()}, so later dates are not shifted. "
            f"See {table.source_url}"
        )
    return None


def schedule_from_lookup(result: LookupResult, template: Schedule) -> Schedule:
    """Fill weekdays from a county lookup. Frequency and season stay local."""
    recycling = StreamSchedule(
        weekdays=result.recycling.weekdays,
        frequency=template.recycling.frequency,
        anchor=template.recycling.anchor,
    )
    yard = StreamSchedule(
        weekdays=result.yard_waste.weekdays,
        season_start=template.yard_waste.season_start,
        season_end=template.yard_waste.season_end,
    )
    mode, bulky_days = _bulky_from_lookup(result, template)
    schedule = Schedule(
        trash=StreamSchedule(weekdays=result.trash.weekdays),
        recycling=recycling,
        yard_waste=yard,
        bulky=StreamSchedule(weekdays=bulky_days),
        bulky_mode=mode,
    )
    validate_resolved_schedule(schedule)
    return schedule


def validate_manual_schedule(schedule: Schedule) -> None:
    if schedule.bulky_mode == "derive":
        raise ConfigError("bulky.mode derive is only valid when source is lookup.")
    validate_resolved_schedule(schedule)


def validate_lookup_template(schedule: Schedule) -> None:
    if schedule.bulky_mode not in {"with_trash", "weekdays", "appointment", "none", "derive"}:
        raise ConfigError("bulky.mode is not recognized.")
    _validate_frequency(schedule.recycling, require_weekdays=False)


def validate_resolved_schedule(schedule: Schedule) -> None:
    if schedule.bulky_mode not in {"with_trash", "weekdays", "appointment", "none"}:
        raise ConfigError("bulky.mode is not recognized.")
    if schedule.bulky_mode == "weekdays" and not schedule.bulky.weekdays:
        raise ConfigError("bulky.mode weekdays needs at least one weekday.")
    _validate_frequency(schedule.recycling, require_weekdays=True)
    for spec in (schedule.trash, schedule.recycling, schedule.yard_waste, schedule.bulky):
        if spec.frequency not in {"weekly", "alternate"}:
            raise ConfigError("frequency must be weekly or alternate.")


def _bulky_from_lookup(result: LookupResult, template: Schedule) -> tuple[str, tuple[int, ...]]:
    if template.bulky_mode == "derive":
        if result.bulky.appointment:
            return "appointment", ()
        if result.bulky.weekdays:
            return "weekdays", result.bulky.weekdays
        return "none", ()
    if template.bulky_mode == "with_trash":
        return "with_trash", ()
    if template.bulky_mode == "appointment":
        return "appointment", ()
    if template.bulky_mode == "none":
        return "none", ()
    days = template.bulky.weekdays or result.bulky.weekdays
    return "weekdays", days


def _validate_frequency(spec: StreamSchedule, *, require_weekdays: bool) -> None:
    if spec.frequency not in {"weekly", "alternate"}:
        raise ConfigError("recycling.frequency must be weekly or alternate.")
    if spec.frequency != "alternate":
        return
    if spec.anchor is None:
        raise ConfigError("Alternate recycling needs an anchor date.")
    if not require_weekdays and not spec.weekdays:
        return
    if len(spec.weekdays) != 1:
        raise ConfigError("Alternate recycling needs exactly one weekday.")
    if spec.anchor.weekday() != spec.weekdays[0]:
        raise ConfigError(
            f"Recycling anchor {spec.anchor.isoformat()} is a "
            f"{WEEKDAY_NAMES[spec.anchor.weekday()]}; recycling weekday is "
            f"{WEEKDAY_NAMES[spec.weekdays[0]]}."
        )


def _on_anchor_week(nominal: date, anchor: date | None) -> bool:
    if anchor is None:
        raise ConfigError("Alternate recycling needs an anchor date.")
    weeks = (nominal - anchor).days // 7
    return weeks % 2 == 0


def _iter_weekday(start: date, end: date, weekday: int):
    if start > end:
        return
    delta = (weekday - start.weekday()) % 7
    current = start + timedelta(days=delta)
    while current <= end:
        yield current
        current += timedelta(days=7)
