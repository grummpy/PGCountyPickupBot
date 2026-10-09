"""RFC 5545 calendars for Prince George's County pickup reminders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event

from pgpickup.models import STREAM_LABELS, Occurrence, occurrence_uid

HOLIDAY_SCHEDULE_URL = (
    "https://www.princegeorgescountymd.gov/departments-offices/environment/"
    "waste-recycling/holiday-waste-collection-schedule"
)
PGC311_URL = "https://www.pgc311.com/"

PRODID = "-//PG County Pickup Bot//pgpickup//EN"
CALENDAR_NAME = "PG County Pickups"

# US daylight-saving rule in effect since 2007: second Sunday in March,
# first Sunday in November. Enough for the 2026–2027 dates this project emits.
_NEW_YORK_CALENDAR = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PG County Pickup Bot//pgpickup//EN
BEGIN:VTIMEZONE
TZID:America/New_York
X-LIC-LOCATION:America/New_York
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:EDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:EST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE
END:VCALENDAR
"""


@dataclass(frozen=True)
class CalendarOptions:
    timezone: str = "America/New_York"
    reminder_time: time = time(19, 0)
    collection_start: time = time(6, 0)
    collection_end: time = time(20, 0)
    include_location: bool = False
    location: str | None = None
    now: datetime | None = None


def collection_start_at(service_date: date, options: CalendarOptions) -> datetime:
    return datetime.combine(
        service_date,
        options.collection_start,
        tzinfo=ZoneInfo(options.timezone),
    )


def collection_end_at(service_date: date, options: CalendarOptions) -> datetime:
    return datetime.combine(
        service_date,
        options.collection_end,
        tzinfo=ZoneInfo(options.timezone),
    )


def reminder_at(service_date: date, options: CalendarOptions) -> datetime:
    """Local clock time on the calendar day before collection."""
    return datetime.combine(
        service_date - timedelta(days=1),
        options.reminder_time,
        tzinfo=ZoneInfo(options.timezone),
    )


def reminder_minutes(service_date: date, options: CalendarOptions) -> int:
    # Convert both instants to UTC first. Subtracting two datetimes that share
    # a ZoneInfo ignores the DST offset and counts wall-clock hours instead.
    start = collection_start_at(service_date, options).astimezone(UTC)
    reminder = reminder_at(service_date, options).astimezone(UTC)
    delta = start - reminder
    minutes = int(delta.total_seconds() // 60)
    if minutes <= 0:
        raise ValueError("Reminder time must be before collection starts.")
    return minutes


def build_calendar(occurrences: list[Occurrence], options: CalendarOptions) -> Calendar:
    if options.timezone != "America/New_York":
        raise ValueError(
            "ICS export currently supports only America/New_York; choose that timezone."
        )
    calendar = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("x-wr-calname", CALENDAR_NAME)
    calendar.add("x-wr-timezone", options.timezone)
    timezone_component = _new_york_timezone()
    calendar.add_component(timezone_component)
    stamp = options.now or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    for occurrence in occurrences:
        calendar.add_component(_event(occurrence, options, stamp.astimezone(UTC)))
    return calendar


def render_ics(occurrences: list[Occurrence], options: CalendarOptions) -> bytes:
    """Return a CRLF calendar, which is what RFC 5545 requires."""
    raw = build_calendar(occurrences, options).to_ical()
    if not raw.endswith(b"\r\n"):
        raw += b"\r\n"
    return raw


def _event(occurrence: Occurrence, options: CalendarOptions, stamp: datetime) -> Event:
    event = Event()
    event.add("uid", occurrence_uid(occurrence))
    event.add("dtstamp", stamp)
    event.add("dtstart", collection_start_at(occurrence.service_date, options))
    event.add("dtend", collection_end_at(occurrence.service_date, options))
    event.add("summary", summary_for(occurrence))
    event.add("description", description_for(occurrence, options))
    event.add("status", "CONFIRMED")
    event.add("transp", "TRANSPARENT")
    event.add("sequence", 0)
    event.add("categories", occurrence.stream.upper())
    if options.include_location and options.location:
        event.add("location", options.location)
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", _alarm_text(occurrence))
    alarm.add("trigger", reminder_at(occurrence.service_date, options).astimezone(UTC))
    event.add_component(alarm)
    return event


def summary_for(occurrence: Occurrence) -> str:
    label = STREAM_LABELS[occurrence.stream]
    if not occurrence.shifted:
        return f"{label} pickup"
    nominal = occurrence.nominal_date
    moved = f"moved from {nominal:%A} {nominal.month}/{nominal.day}"
    if occurrence.holiday_name:
        return f"{label} pickup ({moved}, {occurrence.holiday_name})"
    return f"{label} pickup ({moved})"


def description_for(occurrence: Occurrence, options: CalendarOptions) -> str:
    label = STREAM_LABELS[occurrence.stream]
    start = options.collection_start.strftime("%I:%M %p").lstrip("0")
    end = options.collection_end.strftime("%I:%M %p").lstrip("0")
    lines = [
        f"{label} is collected {occurrence.service_date:%A, %B} {occurrence.service_date.day}.",
        f"County collection window: {start}–{end} {options.timezone}.",
        "Set carts at the curb after 6:00 PM the evening before, and by the start of the window.",
    ]
    if occurrence.shifted and occurrence.holiday_name:
        lines.append(
            f"{occurrence.holiday_name} moves this pickup from "
            f"{occurrence.nominal_date.isoformat()} to {occurrence.service_date.isoformat()}."
        )
    if occurrence.stream == "bulky":
        lines.append(
            "Curbside bulky pickup is up to 4 items on this day. Appliances, scrap tires, "
            "electronics, and scrap metal are appointment-only through PGC311 and are not "
            "scheduled here."
        )
    if occurrence.stream == "yard_waste":
        lines.append("Yard trim and food scraps are collected together.")
    lines.append("Schedules change. Confirm with Prince George's County or PGC311 before the day.")
    lines.append(HOLIDAY_SCHEDULE_URL)
    lines.append(PGC311_URL)
    return "\n".join(lines)


def _alarm_text(occurrence: Occurrence) -> str:
    label = STREAM_LABELS[occurrence.stream]
    return f"Put carts out tonight. {label} pickup is tomorrow."


def _new_york_timezone():
    parsed = Calendar.from_ical(_NEW_YORK_CALENDAR)
    for component in parsed.walk("VTIMEZONE"):
        return component
    raise RuntimeError("America/New_York VTIMEZONE template did not parse.")
