"""Load and validate the local YAML config."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from pgpickup.engine import validate_lookup_template, validate_manual_schedule
from pgpickup.errors import ConfigError
from pgpickup.holidays import default_holidays_path
from pgpickup.models import BULKY_MODES, Schedule, StreamSchedule, parse_weekday

EXAMPLE_CONFIG_NAMES = frozenset({"config.example.yaml"})


@dataclass(frozen=True)
class AppConfig:
    timezone: str
    reminder_time: time
    collection_start: time
    collection_end: time
    include_location: bool
    address: str | None
    source: str
    range_start: date
    range_end: date
    schedule: Schedule
    holidays_file: Path
    cache_dir: Path
    lookup_cache_days: int
    google_enabled: bool
    google_dry_run: bool
    google_calendar_id: str
    google_oauth_client_file: Path | None
    google_token_file: Path | None
    config_path: Path | None = None


def load_config(path: Path) -> AppConfig:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ConfigError("Config must be a YAML mapping.")
    return parse_config(loaded, config_path=path)


def apply_address_env(config: AppConfig) -> AppConfig:
    override = os.environ.get("PGPICKUP_ADDRESS", "").strip()
    if not override:
        return config
    return replace(config, address=override)


def parse_config(data: dict, *, config_path: Path | None = None) -> AppConfig:
    if not isinstance(data, dict):
        raise ConfigError("Config must be a mapping.")
    base = config_path.parent if config_path is not None else Path.cwd()
    timezone_name = str(data.get("timezone") or "America/New_York")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Unknown timezone {timezone_name}.") from exc

    reminder = parse_hhmm(data.get("reminder_time") or "19:00", "reminder_time")
    collection_start = parse_hhmm(data.get("collection_start") or "06:00", "collection_start")
    collection_end = parse_hhmm(data.get("collection_end") or "20:00", "collection_end")
    if (collection_end.hour, collection_end.minute) <= (
        collection_start.hour,
        collection_start.minute,
    ):
        raise ConfigError("collection_end must be after collection_start on the same day.")

    include_location = parse_bool(data.get("include_location"), "include_location", default=False)
    address = _clean_address(data.get("address"))
    source = str(data.get("source") or "manual").strip().casefold()
    if source not in {"manual", "lookup"}:
        raise ConfigError("source must be manual or lookup.")

    range_data = data.get("range") or {}
    if not isinstance(range_data, dict):
        raise ConfigError("range must be a mapping.")
    range_start = parse_iso_date(range_data.get("start") or "2026-01-01", "range.start")
    range_end = parse_iso_date(range_data.get("end") or "2027-01-31", "range.end")
    if range_end < range_start:
        raise ConfigError("range.end is before range.start.")

    schedule_data = data.get("schedule") or {}
    if not isinstance(schedule_data, dict):
        raise ConfigError("schedule must be a mapping.")
    schedule = parse_schedule(schedule_data, lookup_source=source == "lookup")
    if source == "manual":
        validate_manual_schedule(schedule)
    else:
        validate_lookup_template(schedule)
        if not address:
            raise ConfigError("source lookup requires address in the config or PGPICKUP_ADDRESS.")

    holidays_raw = data.get("holidays_file")
    if holidays_raw:
        holidays_file = _resolve_path(base, holidays_raw, "holidays_file")
    else:
        holidays_file = default_holidays_path()

    cache_raw = data.get("cache_dir")
    if cache_raw:
        cache_dir = _resolve_path(base, cache_raw, "cache_dir")
    else:
        cache_dir = Path.home() / ".cache" / "pgpickup"

    cache_days = data.get("lookup_cache_days", 7)
    if isinstance(cache_days, bool) or not isinstance(cache_days, int) or cache_days < 0:
        raise ConfigError("lookup_cache_days must be a non-negative integer.")

    google = data.get("google_calendar") or {}
    if not isinstance(google, dict):
        raise ConfigError("google_calendar must be a mapping.")
    google_enabled = parse_bool(google.get("enabled"), "google_calendar.enabled", default=False)
    google_dry_run = parse_bool(google.get("dry_run"), "google_calendar.dry_run", default=True)
    calendar_id = str(google.get("calendar_id") or "primary")
    oauth = _optional_path(
        base, google.get("oauth_client_file"), "google_calendar.oauth_client_file"
    )
    token = _optional_path(base, google.get("token_file"), "google_calendar.token_file")
    if google_enabled and (oauth is None or token is None):
        raise ConfigError("google_calendar.enabled requires oauth_client_file and token_file.")

    return AppConfig(
        timezone=timezone_name,
        reminder_time=reminder,
        collection_start=collection_start,
        collection_end=collection_end,
        include_location=include_location,
        address=address,
        source=source,
        range_start=range_start,
        range_end=range_end,
        schedule=schedule,
        holidays_file=holidays_file,
        cache_dir=cache_dir,
        lookup_cache_days=cache_days,
        google_enabled=google_enabled,
        google_dry_run=google_dry_run,
        google_calendar_id=calendar_id,
        google_oauth_client_file=oauth,
        google_token_file=token,
        config_path=config_path,
    )


def parse_schedule(data: dict, *, lookup_source: bool) -> Schedule:
    trash = parse_stream(data.get("trash") or {}, "trash")
    recycling = parse_stream(
        data.get("recycling") or {},
        "recycling",
        allow_frequency=True,
    )
    yard = parse_stream(data.get("yard_waste") or {}, "yard_waste", allow_season=True)
    bulky_data = data.get("bulky") or {}
    if not isinstance(bulky_data, dict):
        raise ConfigError("schedule.bulky must be a mapping.")
    if "mode" in bulky_data and bulky_data.get("mode") is not None:
        mode = str(bulky_data.get("mode")).strip().casefold()
    elif lookup_source:
        mode = "derive"
    else:
        mode = "with_trash"
    if mode not in BULKY_MODES:
        raise ConfigError("bulky.mode must be with_trash, weekdays, appointment, or none.")
    if mode == "derive" and not lookup_source:
        raise ConfigError("bulky.mode derive is only valid when source is lookup.")
    bulky = parse_stream(bulky_data, "bulky")
    return Schedule(
        trash=trash,
        recycling=recycling,
        yard_waste=yard,
        bulky=bulky,
        bulky_mode=mode,
    )


def parse_stream(
    data: object,
    name: str,
    *,
    allow_frequency: bool = False,
    allow_season: bool = False,
) -> StreamSchedule:
    if not isinstance(data, dict):
        raise ConfigError(f"schedule.{name} must be a mapping.")
    raw_days = data.get("weekdays") or []
    if isinstance(raw_days, str):
        raw_days = [raw_days]
    if not isinstance(raw_days, list):
        raise ConfigError(f"schedule.{name}.weekdays must be a list.")
    weekdays: list[int] = []
    for item in raw_days:
        try:
            weekdays.append(parse_weekday(str(item)))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    if len(set(weekdays)) != len(weekdays):
        raise ConfigError(f"schedule.{name}.weekdays has a duplicate.")

    frequency = "weekly"
    anchor = None
    if allow_frequency:
        frequency = str(data.get("frequency") or "weekly").strip().casefold()
        if frequency not in {"weekly", "alternate"}:
            raise ConfigError("recycling.frequency must be weekly or alternate.")
        if data.get("anchor") is not None:
            anchor = parse_iso_date(data.get("anchor"), "recycling.anchor")
    elif "frequency" in data or "anchor" in data:
        raise ConfigError(f"frequency is only supported on recycling, not {name}.")

    season_start = None
    season_end = None
    if allow_season:
        if "season" in data and data.get("season") not in (None, {}):
            season = data.get("season")
            if not isinstance(season, dict):
                raise ConfigError(
                    "yard_waste.season must be a mapping with start and end, or null."
                )
            season_start = parse_month_day(season.get("start"), "yard_waste.season.start")
            season_end = parse_month_day(season.get("end"), "yard_waste.season.end")
    elif data.get("season"):
        raise ConfigError(f"season is only supported on yard_waste, not {name}.")

    return StreamSchedule(
        weekdays=tuple(sorted(weekdays)),
        frequency=frequency,
        anchor=anchor,
        season_start=season_start,
        season_end=season_end,
    )


def assert_safe_save_path(path: Path) -> None:
    if path.name in EXAMPLE_CONFIG_NAMES:
        raise ConfigError(f"Refusing to write {path.name}.")
    blocked = {"docs", "tests", "src"}
    if blocked.intersection(path.resolve().parts):
        raise ConfigError(f"Refusing to write a schedule config under {path}.")


def render_saved_config(config: AppConfig, schedule: Schedule) -> str:
    """YAML for a gitignored config.yaml. Weekdays are explicit so later runs stay offline."""
    lines = [
        "# Written by `pgpickup lookup --save`.",
        "# This file is gitignored. Do not commit a home address.",
        "source: manual",
        "include_location: false",
        f"timezone: {config.timezone}",
        f"reminder_time: {config.reminder_time.strftime('%H:%M')}",
        f"collection_start: {config.collection_start.strftime('%H:%M')}",
        f"collection_end: {config.collection_end.strftime('%H:%M')}",
        f"address: {json.dumps(config.address) if config.address else 'null'}",
        "range:",
        f"  start: '{config.range_start.isoformat()}'",
        f"  end: '{config.range_end.isoformat()}'",
        "schedule:",
        *_stream_lines("trash", schedule.trash),
        *_stream_lines("recycling", schedule.recycling, frequency=True),
        *_stream_lines("yard_waste", schedule.yard_waste, season=True),
        "  bulky:",
        f"    mode: {schedule.bulky_mode if schedule.bulky_mode != 'derive' else 'none'}",
    ]
    if schedule.bulky.weekdays and schedule.bulky_mode == "weekdays":
        lines.append(f"    weekdays: [{_weekday_list(schedule.bulky.weekdays)}]")
    lines.extend(
        [
            "google_calendar:",
            "  enabled: false",
            "  dry_run: true",
            "  calendar_id: primary",
            "  oauth_client_file: secrets/google-oauth-client.json",
            "  token_file: secrets/google-token.json",
            "",
        ]
    )
    return "\n".join(lines)


def parse_hhmm(value: object, field: str) -> time:
    if not isinstance(value, str):
        raise ConfigError(f"{field} must be HH:MM.")
    parts = value.split(":")
    if len(parts) != 2 or len(parts[0]) != 2 or len(parts[1]) != 2:
        raise ConfigError(f"{field} must be HH:MM (24-hour).")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        return time(hour, minute)
    except ValueError as exc:
        raise ConfigError(f"{field} must be HH:MM (24-hour).") from exc


def parse_iso_date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"{field} must be YYYY-MM-DD.") from exc
    raise ConfigError(f"{field} must be YYYY-MM-DD.")


def parse_month_day(value: object, field: str) -> tuple[int, int]:
    if not isinstance(value, str) or value.count("-") != 1:
        raise ConfigError(f"{field} must be MM-DD.")
    month_s, day_s = value.split("-")
    try:
        month, day = int(month_s), int(day_s)
        date(2000, month, day)
    except ValueError as exc:
        raise ConfigError(f"{field} must be MM-DD.") from exc
    return month, day


def parse_bool(value: object, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    raise ConfigError(f"{field} must be true or false.")


def _clean_address(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_path(base: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a path.")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path


def _optional_path(base: Path, value: object, field: str) -> Path | None:
    if value is None or value == "":
        return None
    return _resolve_path(base, value, field)


def _weekday_list(weekdays: tuple[int, ...]) -> str:
    from pgpickup.models import WEEKDAY_NAMES

    return ", ".join(WEEKDAY_NAMES[day] for day in weekdays)


def _stream_lines(
    name: str,
    spec: StreamSchedule,
    *,
    frequency: bool = False,
    season: bool = False,
) -> list[str]:
    from pgpickup.models import WEEKDAY_NAMES

    lines = [f"  {name}:"]
    if spec.weekdays:
        quoted = ", ".join(WEEKDAY_NAMES[day] for day in spec.weekdays)
        lines.append(f"    weekdays: [{quoted}]")
    else:
        lines.append("    weekdays: []")
    if frequency:
        lines.append(f"    frequency: {spec.frequency}")
        if spec.anchor is not None:
            lines.append(f"    anchor: '{spec.anchor.isoformat()}'")
    if season:
        if spec.season_start and spec.season_end:
            start = f"{spec.season_start[0]:02d}-{spec.season_start[1]:02d}"
            end = f"{spec.season_end[0]:02d}-{spec.season_end[1]:02d}"
            lines.append("    season:")
            lines.append(f"      start: '{start}'")
            lines.append(f"      end: '{end}'")
        else:
            lines.append("    season: null")
    return lines
