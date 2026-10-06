"""Load the curated Prince George's County holiday-shift table."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import yaml

from pgpickup.errors import ConfigError
from pgpickup.models import STREAMS, Holiday, HolidayTable

OBSERVE_VALUES = frozenset({"slide", "none", "skip"})


def default_holidays_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "holidays.yaml"


def load_holiday_table(path: Path | None = None) -> HolidayTable:
    holiday_path = path or default_holidays_path()
    if not holiday_path.is_file():
        raise ConfigError(f"Holiday file not found: {holiday_path}")
    loaded = yaml.safe_load(holiday_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ConfigError("Holiday file must be a YAML mapping.")
    source_url = str(loaded.get("source_url") or "").strip()
    if not source_url:
        raise ConfigError("Holiday file is missing source_url.")
    checked = _as_date(loaded.get("checked"), "checked")
    raw_holidays = loaded.get("holidays")
    if not isinstance(raw_holidays, list) or not raw_holidays:
        raise ConfigError("Holiday file needs a non-empty holidays list.")
    holidays: list[Holiday] = []
    seen: set[date] = set()
    for index, item in enumerate(raw_holidays, start=1):
        holidays.append(_parse_holiday(item, index, seen))
    holidays.sort(key=lambda holiday: holiday.date)
    return HolidayTable(source_url=source_url, checked=checked, holidays=tuple(holidays))


def _parse_holiday(item: object, index: int, seen: set[date]) -> Holiday:
    if not isinstance(item, dict):
        raise ConfigError(f"Holiday {index} must be a mapping.")
    name = str(item.get("name") or "").strip()
    if not name:
        raise ConfigError(f"Holiday {index} is missing a name.")
    observed = str(item.get("observe") or "").strip().casefold()
    if observed not in OBSERVE_VALUES:
        raise ConfigError(f"Holiday {name} observe must be slide, none, or skip.")
    holiday_date = _as_date(item.get("date"), f"{name} date")
    if holiday_date in seen:
        raise ConfigError(f"Duplicate holiday date {holiday_date.isoformat()}.")
    seen.add(holiday_date)
    streams = _parse_streams(item.get("streams"), name)
    note = str(item.get("note") or "").strip()
    return Holiday(
        name=name,
        date=holiday_date,
        observe=observed,
        streams=streams,
        note=note,
    )


def _parse_streams(value: object, name: str) -> frozenset[str]:
    if value is None:
        return frozenset(STREAMS)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"Holiday {name} streams must be a non-empty list.")
    streams: list[str] = []
    for item in value:
        stream = str(item).strip()
        if stream not in STREAMS:
            raise ConfigError(f"Holiday {name} has unknown stream {stream!r}.")
        streams.append(stream)
    return frozenset(streams)


def _as_date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"{field} must be YYYY-MM-DD.") from exc
    raise ConfigError(f"{field} must be a date.")
