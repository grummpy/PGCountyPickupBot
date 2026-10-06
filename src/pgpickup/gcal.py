"""Optional Google Calendar push. Dry-run unless the config and CLI both opt in.

Events are matched by the same UID used in the ICS file. This module never
deletes a calendar event. It only inserts or updates events that carry this
program's UID domain and the private marker ``pgpickup=1``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pgpickup.errors import ConfigError
from pgpickup.ics import (
    CalendarOptions,
    collection_end_at,
    collection_start_at,
    description_for,
    reminder_at,
    reminder_minutes,
    summary_for,
)
from pgpickup.models import UID_DOMAIN, Occurrence, occurrence_uid

CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SCOPES = [CALENDAR_EVENTS_SCOPE]


@dataclass(frozen=True)
class RemoteEvent:
    google_id: str
    ical_uid: str
    pgpickup: str | None
    fingerprint: str


@dataclass(frozen=True)
class SyncAction:
    op: str
    ical_uid: str
    google_id: str | None = None


@dataclass(frozen=True)
class PushReport:
    actions: tuple[SyncAction, ...]
    mutated: bool

    @property
    def counts(self) -> dict[str, int]:
        counts = {"create": 0, "update": 0, "noop": 0, "skip_foreign": 0}
        for action in self.actions:
            counts[action.op] = counts.get(action.op, 0) + 1
        return counts


def google_body(occurrence: Occurrence, options: CalendarOptions) -> dict:
    start = collection_start_at(occurrence.service_date, options)
    end = collection_end_at(occurrence.service_date, options)
    reminder = reminder_at(occurrence.service_date, options)
    body: dict = {
        "summary": summary_for(occurrence),
        "description": description_for(occurrence, options),
        "iCalUID": occurrence_uid(occurrence),
        "start": {"dateTime": start.isoformat(), "timeZone": options.timezone},
        "end": {"dateTime": end.isoformat(), "timeZone": options.timezone},
        "transparency": "transparent",
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": reminder_minutes(occurrence.service_date, options)}
            ],
        },
        "extendedProperties": {
            "private": {
                "pgpickup": "1",
                "stream": occurrence.stream,
                "nominalDate": occurrence.nominal_date.isoformat(),
            }
        },
    }
    if options.include_location and options.location:
        body["location"] = options.location
    # Touch reminder so a DST-sensitive clock time is part of the fingerprint.
    body["extendedProperties"]["private"]["reminderLocal"] = reminder.isoformat()
    return body


def fingerprint(body: dict) -> str:
    import json

    keep = {
        key: body.get(key)
        for key in ("summary", "description", "start", "end", "location", "reminders")
    }
    private = (body.get("extendedProperties") or {}).get("private") or {}
    keep["nominalDate"] = private.get("nominalDate")
    keep["reminderLocal"] = private.get("reminderLocal")
    return json.dumps(keep, sort_keys=True, default=str)


def plan_sync(local_bodies: list[dict], remote: list[RemoteEvent]) -> list[SyncAction]:
    """Decide creates and updates. Never emits a delete."""
    by_uid = {item.ical_uid: item for item in remote}
    actions: list[SyncAction] = []
    for body in local_bodies:
        uid = str(body["iCalUID"])
        found = by_uid.get(uid)
        if found is None:
            actions.append(SyncAction("create", uid))
            continue
        if not _is_ours(found):
            actions.append(SyncAction("skip_foreign", uid, found.google_id))
            continue
        if found.fingerprint == fingerprint(body):
            actions.append(SyncAction("noop", uid, found.google_id))
        else:
            actions.append(SyncAction("update", uid, found.google_id))
    return actions


def push_calendar(
    *,
    enabled: bool,
    dry_run: bool,
    calendar_id: str,
    timezone_name: str,
    occurrences: list[Occurrence],
    options: CalendarOptions,
    apply: bool,
    service=None,
    oauth_client_file=None,
    token_file=None,
    range_start=None,
    range_end=None,
) -> PushReport:
    if not enabled:
        raise ConfigError("Google Calendar push is disabled in the config.")
    bodies = [google_body(occurrence, options) for occurrence in occurrences]
    if not apply:
        actions = tuple(SyncAction("create", str(body["iCalUID"])) for body in bodies)
        return PushReport(actions=actions, mutated=False)
    if dry_run:
        raise ConfigError(
            "Refusing to write: google_calendar.dry_run is true. Set it to false and pass --apply."
        )
    if service is None:
        service = build_google_service(oauth_client_file, token_file)
    remote = list_our_events(
        service,
        calendar_id=calendar_id,
        timezone_name=timezone_name,
        start=range_start,
        end=range_end,
    )
    actions = plan_sync(bodies, remote)
    _apply_actions(service, calendar_id, bodies, actions)
    return PushReport(actions=tuple(actions), mutated=True)


def list_our_events(
    service,
    *,
    calendar_id: str,
    timezone_name: str,
    start,
    end,
) -> list[RemoteEvent]:
    if start is None or end is None:
        raise ConfigError("A date range is required to list Google Calendar events.")
    zone = ZoneInfo(timezone_name)
    time_min = datetime.combine(start, time.min, tzinfo=zone).astimezone(UTC).isoformat()
    time_max = (
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC).isoformat()
    )
    request = service.events().list(
        calendarId=calendar_id,
        privateExtendedProperty="pgpickup=1",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=False,
        showDeleted=False,
        maxResults=250,
    )
    found: list[RemoteEvent] = []
    pages = 0
    while request is not None:
        pages += 1
        if pages > 10:
            raise ConfigError("Google Calendar returned too many pages of pgpickup events.")
        payload = request.execute()
        for item in payload.get("items", []):
            remote = remote_from_google(item)
            if remote is not None:
                found.append(remote)
        request = service.events().list_next(request, payload)
    return found


def remote_from_google(item: dict) -> RemoteEvent | None:
    uid = item.get("iCalUID")
    google_id = item.get("id")
    if not uid or not google_id:
        return None
    private = (item.get("extendedProperties") or {}).get("private") or {}
    marker = private.get("pgpickup")
    body = {
        "summary": item.get("summary"),
        "description": item.get("description"),
        "start": item.get("start"),
        "end": item.get("end"),
        "location": item.get("location"),
        "reminders": item.get("reminders"),
        "extendedProperties": {
            "private": {
                "nominalDate": private.get("nominalDate"),
                "reminderLocal": private.get("reminderLocal"),
            }
        },
    }
    return RemoteEvent(
        google_id=str(google_id),
        ical_uid=str(uid),
        pgpickup=str(marker) if marker is not None else None,
        fingerprint=fingerprint(body),
    )


def build_google_service(oauth_client_file, token_file):
    if oauth_client_file is None or token_file is None:
        raise ConfigError("Google Calendar needs oauth_client_file and token_file.")
    if not oauth_client_file.is_file():
        raise ConfigError("OAuth client file was not found. It belongs in a gitignored path.")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ConfigError(
            "Google Calendar support is not installed. Run: pip install 'pgpickup[google]'"
        ) from exc
    creds = None
    if token_file.is_file():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds is None or not creds.valid:
        if creds is not None and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(oauth_client_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds)


def _apply_actions(
    service, calendar_id: str, bodies: list[dict], actions: list[SyncAction]
) -> None:
    by_uid = {str(body["iCalUID"]): body for body in bodies}
    for action in actions:
        body = by_uid[action.ical_uid]
        if action.op == "create":
            service.events().insert(calendarId=calendar_id, body=body).execute()
        elif action.op == "update":
            patch = dict(body)
            patch.pop("iCalUID", None)
            service.events().patch(
                calendarId=calendar_id,
                eventId=action.google_id,
                body=patch,
            ).execute()
        elif action.op in {"noop", "skip_foreign"}:
            continue
        else:
            raise ConfigError(f"Unknown sync action {action.op}.")


def _is_ours(item: RemoteEvent) -> bool:
    return item.pgpickup == "1" and item.ical_uid.endswith(f"@{UID_DOMAIN}")
