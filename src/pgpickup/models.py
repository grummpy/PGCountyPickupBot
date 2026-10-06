"""Shared schedule, holiday, and lookup records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

WEEKDAY_NAMES: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

STREAMS: tuple[str, ...] = ("trash", "recycling", "yard_waste", "bulky")

STREAM_ORDER: dict[str, int] = {name: index for index, name in enumerate(STREAMS)}

STREAM_LABELS: dict[str, str] = {
    "trash": "Trash",
    "recycling": "Recycling",
    "yard_waste": "Yard trim and food scraps",
    "bulky": "Bulky trash",
}

UID_DOMAIN = "pgcounty-pickup-bot.local"

BULKY_MODES: frozenset[str] = frozenset({"with_trash", "weekdays", "appointment", "none", "derive"})


def parse_weekday(value: str) -> int:
    key = value.strip().casefold()
    if key not in WEEKDAY_NAMES:
        raise ValueError(f"Unknown weekday: {value}")
    return WEEKDAY_NAMES.index(key)


def weekday_label(index: int) -> str:
    return WEEKDAY_NAMES[index].capitalize()


@dataclass(frozen=True)
class StreamSchedule:
    weekdays: tuple[int, ...] = ()
    frequency: str = "weekly"
    anchor: date | None = None
    season_start: tuple[int, int] | None = None
    season_end: tuple[int, int] | None = None

    def in_season(self, day: date) -> bool:
        if self.season_start is None or self.season_end is None:
            return True
        month_day = (day.month, day.day)
        if self.season_start <= self.season_end:
            return self.season_start <= month_day <= self.season_end
        return month_day >= self.season_start or month_day <= self.season_end


@dataclass(frozen=True)
class Schedule:
    trash: StreamSchedule
    recycling: StreamSchedule
    yard_waste: StreamSchedule
    bulky: StreamSchedule
    bulky_mode: str = "with_trash"

    def stream_specs(self) -> tuple[tuple[str, StreamSchedule], ...]:
        specs: list[tuple[str, StreamSchedule]] = [
            ("trash", self.trash),
            ("recycling", self.recycling),
            ("yard_waste", self.yard_waste),
        ]
        if self.bulky_mode == "with_trash":
            specs.append(("bulky", StreamSchedule(weekdays=self.trash.weekdays)))
        elif self.bulky_mode == "weekdays":
            specs.append(("bulky", self.bulky))
        return tuple(specs)


@dataclass(frozen=True)
class Holiday:
    name: str
    date: date
    observe: str
    streams: frozenset[str]
    note: str = ""


@dataclass(frozen=True)
class HolidayTable:
    source_url: str
    checked: date
    holidays: tuple[Holiday, ...]


@dataclass(frozen=True)
class Occurrence:
    stream: str
    nominal_date: date
    service_date: date
    holiday_name: str | None = None

    @property
    def shifted(self) -> bool:
        return self.service_date != self.nominal_date


def occurrence_uid(occurrence: Occurrence) -> str:
    """Stable id for one logical pickup, based on the regular weekday date."""
    nominal = occurrence.nominal_date.isoformat()
    return f"{occurrence.stream}-{nominal}@{UID_DOMAIN}"


def occurrence_dict(occurrence: Occurrence) -> dict[str, object]:
    return {
        "stream": occurrence.stream,
        "nominal_date": occurrence.nominal_date.isoformat(),
        "service_date": occurrence.service_date.isoformat(),
        "shifted": occurrence.shifted,
        "holiday": occurrence.holiday_name,
        "uid": occurrence_uid(occurrence),
    }


@dataclass(frozen=True)
class DayField:
    weekdays: tuple[int, ...] = ()
    appointment: bool = False

    @property
    def served(self) -> bool:
        return bool(self.weekdays) or self.appointment


@dataclass(frozen=True)
class LookupResult:
    query_address: str
    matched_address: str
    score: float
    longitude: float
    latitude: float
    trash: DayField
    recycling: DayField
    yard_waste: DayField
    bulky: DayField
    contractor: str
    tier: str
    polygon_count: int
    cached: bool = False

    @property
    def in_layer(self) -> bool:
        return self.polygon_count > 0
