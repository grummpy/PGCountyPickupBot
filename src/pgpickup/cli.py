"""Command line for PG County pickup reminders."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from pgpickup.config import (
    AppConfig,
    apply_address_env,
    assert_safe_save_path,
    load_config,
    render_saved_config,
)
from pgpickup.engine import (
    OverridePreview,
    coverage_warning,
    expand_schedule,
    reconcile_occurrences,
    schedule_from_lookup,
    upcoming,
)
from pgpickup.errors import ConfigError, LookupError
from pgpickup.gcal import push_calendar
from pgpickup.holidays import load_holiday_table
from pgpickup.ics import CalendarOptions, render_ics
from pgpickup.lookup import lookup_address
from pgpickup.models import (
    STREAM_LABELS,
    WEEKDAY_NAMES,
    DayField,
    LookupResult,
    Occurrence,
    Schedule,
    occurrence_dict,
)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (ConfigError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _main(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(
        prog="pgpickup",
        description=(
            "Prince George's County trash, recycling, yard-trim, and bulky pickup reminders."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config.yaml. Defaults to PGPICKUP_CONFIG or ./config.yaml.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    lookup = sub.add_parser("lookup", help="Look up collection days for an address.")
    lookup.add_argument("--address", required=True)
    lookup.add_argument("--json", action="store_true")
    lookup.add_argument("--refresh", action="store_true", help="Bypass the local cache.")
    lookup.add_argument(
        "--save",
        nargs="?",
        const="config.yaml",
        help="Write weekdays to a gitignored config file (default: config.yaml).",
    )

    schedule = sub.add_parser("schedule", help="Show the weekly pattern and upcoming shifts.")
    schedule.add_argument("--limit", type=int, default=16)
    schedule.add_argument("--json", action="store_true")
    schedule.add_argument("--start")
    schedule.add_argument("--end")

    reconcile = sub.add_parser(
        "reconcile",
        help="Preview local dated collection overrides without network or calendar writes.",
    )
    reconcile.add_argument("--json", action="store_true")
    reconcile.add_argument("--start")
    reconcile.add_argument("--end")
    reconcile.add_argument("--as-of", help="Review expiry as YYYY-MM-DD (defaults to today).")

    ics = sub.add_parser("ics", help="Write an RFC 5545 calendar.")
    ics.add_argument("--out", default="pickups.ics")
    ics.add_argument("--start")
    ics.add_argument("--end")

    nxt = sub.add_parser("next", help="Show the next pickups.")
    nxt.add_argument("--limit", type=int, default=8)
    nxt.add_argument("--json", action="store_true")

    push = sub.add_parser("push", help="Push events to Google Calendar. Dry-run unless --apply.")
    push.add_argument("--apply", action="store_true")
    push.add_argument("--start")
    push.add_argument("--end")

    args = parser.parse_args(argv)
    if args.command == "lookup":
        config = _load_optional_config(args.config)
        return _lookup(args, config)
    config = apply_address_env(_require_config(args.config))
    if args.command == "schedule":
        return _schedule(args, config)
    if args.command == "reconcile":
        return _reconcile(args, config)
    if args.command == "ics":
        return _ics(args, config)
    if args.command == "next":
        return _next(args, config)
    if args.command == "push":
        return _push(args, config)
    parser.error(f"Unknown command {args.command}")
    return 2


def _lookup(args, config: AppConfig | None) -> int:
    cache_dir = config.cache_dir if config is not None else Path.home() / ".cache" / "pgpickup"
    cache_days = config.lookup_cache_days if config is not None else 7
    result = lookup_address(
        args.address,
        cache_dir=cache_dir,
        cache_days=cache_days,
        refresh=args.refresh,
    )
    if args.json:
        print(json.dumps(_lookup_dict(result), indent=2))
    else:
        print(_format_lookup(result))
    if args.save:
        if config is None:
            raise ConfigError("lookup --save needs a config file. Copy config.example.yaml first.")
        destination = Path(args.save)
        assert_safe_save_path(destination)
        if result.missing_streams:
            missing = ", ".join(_stream_label(item) for item in result.missing_streams)
            raise ConfigError(
                "County layer omitted collection fields for "
                f"{missing}. Not writing a partial schedule; confirm it with the county."
            )
        if not _any_collection(result):
            raise ConfigError(
                "County layer reports no collection days. Not writing a schedule. "
                "A municipality may serve this address; enter weekdays in config.yaml if so."
            )
        schedule = schedule_from_lookup(result, config.schedule)
        saved = replace_address(config, result.query_address)
        destination.write_text(render_saved_config(saved, schedule), encoding="utf-8")
        print(f"Wrote {destination} (gitignored; do not commit it).", file=sys.stderr)
    return 0


def replace_address(config: AppConfig, address: str) -> AppConfig:
    from dataclasses import replace

    return replace(config, address=address)


def _schedule(args, config: AppConfig) -> int:
    table = load_holiday_table(config.holidays_file)
    start, end = _range(args, config)
    schedule, occurrences = _reconciled_occurrences(config, table.holidays, start, end)
    rows = upcoming(occurrences, date.today(), args.limit)
    warning = coverage_warning(table, end)
    if args.json:
        print(json.dumps([occurrence_dict(item) for item in rows], indent=2))
        return 0
    print(_format_pattern(config, schedule))
    if warning:
        print(f"\nNote: {warning}")
    print("\nUpcoming")
    if not rows:
        print("  No upcoming pickups in the configured range.")
    for item in rows:
        print(f"  {format_occurrence(item)}")
    return 0


def _ics(args, config: AppConfig) -> int:
    table = load_holiday_table(config.holidays_file)
    start, end = _range(args, config)
    _schedule, occurrences = _reconciled_occurrences(config, table.holidays, start, end)
    payload = render_ics(occurrences, _options(config))
    destination = Path(args.out)
    destination.write_bytes(payload)
    print(f"Wrote {destination} ({len(occurrences)} events).")
    return 0


def _next(args, config: AppConfig) -> int:
    table = load_holiday_table(config.holidays_file)
    _schedule, occurrences = _reconciled_occurrences(
        config,
        table.holidays,
        config.range_start,
        config.range_end,
    )
    rows = upcoming(occurrences, date.today(), args.limit)
    if args.json:
        print(json.dumps([occurrence_dict(item) for item in rows], indent=2))
        return 0
    if not rows:
        print("No upcoming pickups in the configured range.")
        return 0
    for item in rows:
        print(format_occurrence(item))
    return 0


def _push(args, config: AppConfig) -> int:
    table = load_holiday_table(config.holidays_file)
    start, end = _range(args, config)
    _schedule, occurrences = _reconciled_occurrences(config, table.holidays, start, end)
    report = push_calendar(
        enabled=config.google_enabled,
        dry_run=config.google_dry_run,
        calendar_id=config.google_calendar_id,
        timezone_name=config.timezone,
        occurrences=occurrences,
        options=_options(config),
        apply=args.apply,
        oauth_client_file=config.google_oauth_client_file,
        token_file=config.google_token_file,
        range_start=start,
        range_end=end,
    )
    counts = report.counts
    if report.mutated:
        print(
            "Updated Google Calendar: "
            f"{counts['create']} created, {counts['update']} updated, "
            f"{counts['noop']} unchanged, {counts['skip_foreign']} left untouched."
        )
    else:
        print(
            "Dry run: no Google Calendar request was made. "
            f"{counts['create']} events would be created or updated. "
            "Set google_calendar.enabled and dry_run: false, then pass --apply."
        )
    return 0


def _reconcile(args, config: AppConfig) -> int:
    """Inspect local overrides without invoking lookup, ICS, or calendar APIs."""
    if config.source != "manual":
        raise ConfigError(
            "reconcile is offline-only and requires source: manual. "
            "Run lookup --save first, then review the saved local schedule."
        )
    table = load_holiday_table(config.holidays_file)
    start, end = _range(args, config)
    if args.as_of:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError as exc:
            raise ConfigError("--as-of must be YYYY-MM-DD.") from exc
    else:
        as_of = date.today()
    occurrences = expand_schedule(config.schedule, table.holidays, start, end)
    reconciliation = reconcile_occurrences(
        occurrences,
        config.overrides,
        as_of=as_of,
        range_start=start,
        range_end=end,
    )
    if args.json:
        print(json.dumps(_reconciliation_dict(reconciliation), indent=2))
    else:
        print(_format_reconciliation(reconciliation, as_of))
    return 0


def _active_schedule(config: AppConfig) -> Schedule:
    if config.source == "manual":
        return config.schedule
    if not config.address:
        raise ConfigError("source lookup requires an address.")
    result = lookup_address(
        config.address,
        cache_dir=config.cache_dir,
        cache_days=config.lookup_cache_days,
    )
    return schedule_from_lookup(result, config.schedule)


def _reconciled_occurrences(
    config: AppConfig,
    holidays,
    start: date,
    end: date,
) -> tuple[Schedule, list[Occurrence]]:
    schedule = _active_schedule(config)
    base = expand_schedule(schedule, holidays, start, end)
    reconciliation = reconcile_occurrences(
        base,
        config.overrides,
        as_of=date.today(),
        range_start=start,
        range_end=end,
    )
    if reconciliation.conflicts:
        raise ConfigError(
            "Confirmed collection overrides are blocked by conflicts. "
            "Run pgpickup reconcile to review them before exporting or pushing."
        )
    return schedule, list(reconciliation.occurrences)


def _options(config: AppConfig) -> CalendarOptions:
    return CalendarOptions(
        timezone=config.timezone,
        reminder_time=config.reminder_time,
        collection_start=config.collection_start,
        collection_end=config.collection_end,
        include_location=config.include_location,
        location=config.address if config.include_location else None,
        now=datetime.now().astimezone(),
    )


def _range(args, config: AppConfig) -> tuple[date, date]:
    try:
        start = (
            date.fromisoformat(args.start) if getattr(args, "start", None) else config.range_start
        )
        end = date.fromisoformat(args.end) if getattr(args, "end", None) else config.range_end
    except ValueError as exc:
        raise ConfigError("Dates must be YYYY-MM-DD.") from exc
    if end < start:
        raise ConfigError("end is before start.")
    return start, end


def _require_config(path: Path | None) -> AppConfig:
    resolved = _config_path(path)
    if not resolved.is_file():
        raise ConfigError(
            f"Config file not found at {resolved}. Copy config.example.yaml to config.yaml."
        )
    return load_config(resolved)


def _load_optional_config(path: Path | None) -> AppConfig | None:
    resolved = _config_path(path)
    if path is None and not resolved.is_file() and not os.environ.get("PGPICKUP_CONFIG"):
        return None
    if not resolved.is_file():
        return None
    return apply_address_env(load_config(resolved))


def _config_path(path: Path | None) -> Path:
    if path is not None:
        return path
    env = os.environ.get("PGPICKUP_CONFIG")
    if env:
        return Path(env)
    return Path("config.yaml")


def format_occurrence(occurrence: Occurrence) -> str:
    label = STREAM_LABELS[occurrence.stream]
    when = f"{occurrence.service_date.isoformat()} {occurrence.service_date:%A}"
    if not occurrence.shifted:
        return f"{when}  {label}"
    moved = f"moved from {occurrence.nominal_date:%A} {occurrence.nominal_date.isoformat()}"
    holiday = f" ({occurrence.holiday_name})" if occurrence.holiday_name else ""
    return f"{when}  {label}  {moved}{holiday}"


def _format_pattern(config: AppConfig, schedule: Schedule) -> str:
    lines = [
        "PG County pickups",
        f"Timezone: {config.timezone}",
        f"Reminder: {config.reminder_time.strftime('%H:%M')} the evening before",
        (
            "Collection window: "
            f"{config.collection_start.strftime('%H:%M')}–{config.collection_end.strftime('%H:%M')}"
        ),
        f"Source: {config.source}",
        "",
        "Weekly pattern",
        f"  Trash: {_day_phrase(schedule.trash.weekdays)}",
        "  Recycling: " + _recycling_phrase(schedule),
        "  Yard trim and food scraps: " + _yard_phrase(schedule),
        "  Bulky trash: " + _bulky_phrase(schedule),
    ]
    return "\n".join(lines)


def _day_phrase(weekdays: tuple[int, ...]) -> str:
    if not weekdays:
        return "none"
    return ", ".join(WEEKDAY_NAMES[day].capitalize() for day in weekdays)


def _recycling_phrase(schedule: Schedule) -> str:
    days = _day_phrase(schedule.recycling.weekdays)
    if schedule.recycling.frequency == "alternate":
        anchor = schedule.recycling.anchor
        anchor_text = anchor.isoformat() if anchor else "missing anchor"
        return f"{days} (every other week, anchored {anchor_text})"
    return f"{days} (every week)"


def _yard_phrase(schedule: Schedule) -> str:
    days = _day_phrase(schedule.yard_waste.weekdays)
    season = schedule.yard_waste
    if season.season_start and season.season_end:
        start = f"{season.season_start[0]:02d}-{season.season_start[1]:02d}"
        end = f"{season.season_end[0]:02d}-{season.season_end[1]:02d}"
        return f"{days} (in season {start} through {end})"
    return f"{days} (year-round)"


def _bulky_phrase(schedule: Schedule) -> str:
    if schedule.bulky_mode == "with_trash":
        return "with trash, up to 4 items (appliances and tires need a PGC311 appointment)"
    if schedule.bulky_mode == "appointment":
        return "appointment only through PGC311 (not on this calendar)"
    if schedule.bulky_mode == "none":
        return "no curbside bulky day"
    return _day_phrase(schedule.bulky.weekdays) + ", up to 4 items"


def _format_lookup(result: LookupResult) -> str:
    cached = "cached" if result.cached else "live"
    lines = [
        f"Matched: {result.matched_address} (score {result.score:.0f}, {cached})",
        f"Trash: {_format_field(result.trash)}",
        f"Recycling: {_format_field(result.recycling)}",
        f"Yard trim and food scraps: {_format_field(result.yard_waste)}",
        f"Bulky: {_format_field(result.bulky)}",
    ]
    if result.contractor:
        lines.append(f"Contractor: {result.contractor}")
    if result.tier:
        lines.append(f"Tier: {result.tier}")
    if not result.in_layer:
        lines.append("This point is not inside a county collection polygon.")
    elif not _any_collection(result):
        lines.append("This address is in the county layer but has no county collection days.")
    if result.missing_streams:
        missing = ", ".join(_stream_label(item) for item in result.missing_streams)
        lines.append(f"County layer omitted fields for: {missing}. Do not save a partial schedule.")
    return "\n".join(lines)


def _format_field(field: DayField) -> str:
    if field.appointment:
        return "Call 311 to schedule"
    if not field.weekdays:
        return "No Service"
    return ", ".join(WEEKDAY_NAMES[day].capitalize() for day in field.weekdays)


def _lookup_dict(result: LookupResult) -> dict:
    def field(day: DayField) -> dict:
        return {
            "weekdays": [WEEKDAY_NAMES[item] for item in day.weekdays],
            "appointment": day.appointment,
        }

    return {
        "matched_address": result.matched_address,
        "score": result.score,
        "cached": result.cached,
        "in_layer": result.in_layer,
        "incomplete": bool(result.missing_streams),
        "missing_streams": list(result.missing_streams),
        "contractor": result.contractor,
        "tier": result.tier,
        "trash": field(result.trash),
        "recycling": field(result.recycling),
        "yard_waste": field(result.yard_waste),
        "bulky": field(result.bulky),
    }


def _any_collection(result: LookupResult) -> bool:
    return any(
        (
            result.trash.weekdays,
            result.recycling.weekdays,
            result.yard_waste.weekdays,
            result.bulky.weekdays,
            result.bulky.appointment,
        )
    )


def _stream_label(stream: str) -> str:
    return STREAM_LABELS.get(stream, stream)


def _reconciliation_dict(reconciliation) -> dict:
    return {
        "offline": True,
        "mutated": False,
        "occurrences": [occurrence_dict(item) for item in reconciliation.occurrences],
        "overrides": [_override_preview_dict(item) for item in reconciliation.previews],
    }


def _override_preview_dict(preview: OverridePreview) -> dict:
    override = preview.override
    return {
        "stream": override.stream,
        "nominal_date": override.nominal_date.isoformat(),
        "service_date": override.service_date.isoformat() if override.service_date else None,
        "expires_on": override.expires_on.isoformat(),
        "state": override.state,
        "outcome": preview.outcome,
        "detail": preview.detail or None,
        "note": override.note or None,
    }


def _format_reconciliation(reconciliation, as_of: date) -> str:
    lines = [
        "Offline reconciliation preview",
        "  No county lookup, ICS export, cache write, or calendar mutation was performed.",
        f"  Expiry reviewed as of: {as_of.isoformat()}",
    ]
    if not reconciliation.previews:
        lines.append("  No local overrides are configured.")
        return "\n".join(lines)
    lines.append("\nOverrides")
    for preview in reconciliation.previews:
        override = preview.override
        if preview.before is None:
            before, after = "not in range", "not evaluated"
        else:
            before = preview.before.service_date.isoformat()
            after = "cancel" if preview.after is None else preview.after.service_date.isoformat()
        detail = f" — {preview.detail}" if preview.detail else ""
        lines.append(
            "  "
            f"{override.stream} {override.nominal_date.isoformat()}: {before} -> {after} "
            f"[{override.state}; {preview.outcome}]{detail}"
        )
    return "\n".join(lines)
