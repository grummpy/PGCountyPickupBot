"""Google Calendar planning stays idempotent and never deletes."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from pgpickup.errors import ConfigError
from pgpickup.gcal import (
    CALENDAR_EVENTS_SCOPE,
    SCOPES,
    RemoteEvent,
    fingerprint,
    google_body,
    plan_sync,
    push_calendar,
)
from pgpickup.ics import CalendarOptions, reminder_minutes
from pgpickup.models import Occurrence

OPTIONS = CalendarOptions(
    reminder_time=time(19, 0),
    collection_start=time(6, 0),
    now=datetime(2026, 10, 6, tzinfo=UTC),
)


def _body(nominal: date, service: date | None = None, stream: str = "trash") -> dict:
    return google_body(
        Occurrence(stream, nominal, service or nominal),
        OPTIONS,
    )


def _remote(body: dict, *, marker: str | None = "1", google_id: str = "g1") -> RemoteEvent:
    private = body["extendedProperties"]["private"]
    if marker is None:
        private = dict(private)
        private.pop("pgpickup", None)
        body = {
            **body,
            "extendedProperties": {"private": private},
        }
    elif marker != "1":
        body = {
            **body,
            "extendedProperties": {
                "private": {**body["extendedProperties"]["private"], "pgpickup": marker}
            },
        }
    return RemoteEvent(google_id, body["iCalUID"], marker, fingerprint(body))


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeEvents:
    def __init__(self):
        self.remote: list[dict] = []
        self.inserted: list[dict] = []
        self.patched: list[dict] = []

    def list(self, **kwargs):
        assert kwargs["privateExtendedProperty"] == "pgpickup=1"
        return FakeRequest({"items": list(self.remote)})

    def list_next(self, request, payload):
        return None

    def insert(self, **kwargs):
        self.inserted.append(kwargs["body"])
        return FakeRequest({"id": "new", "iCalUID": kwargs["body"]["iCalUID"]})

    def patch(self, **kwargs):
        self.patched.append(kwargs)
        return FakeRequest({"id": kwargs["eventId"]})

    def delete(self, **kwargs):
        raise AssertionError("delete was called")


class FakeService:
    def __init__(self):
        self._events = FakeEvents()

    def events(self):
        return self._events


def test_scope_is_calendar_events_only():
    assert CALENDAR_EVENTS_SCOPE == "https://www.googleapis.com/auth/calendar.events"
    assert SCOPES == [CALENDAR_EVENTS_SCOPE]
    assert "calendar.readonly" not in CALENDAR_EVENTS_SCOPE
    source = Path("src/pgpickup/gcal.py").read_text(encoding="utf-8")
    assert ".delete(" not in source


def test_plan_is_idempotent_and_does_not_touch_foreign_events():
    local = _body(date(2026, 11, 5))
    first = plan_sync([local], [])
    assert [action.op for action in first] == ["create"]

    remote = _remote(local)
    second = plan_sync([local], [remote])
    assert [action.op for action in second] == ["noop"]

    changed = _body(date(2026, 11, 5))
    changed["summary"] = "Trash pickup (edited)"
    third = plan_sync([changed], [remote])
    assert [action.op for action in third] == ["update"]

    foreign = RemoteEvent("other", "school@example.com", None, "x")
    unmarked_body = _body(date(2026, 11, 12))
    unmarked = _remote(unmarked_body, marker=None, google_id="g2")
    actions = plan_sync([local, unmarked_body], [remote, foreign, unmarked])
    assert [action.op for action in actions] == ["noop", "skip_foreign"]
    assert all(action.op != "delete" for action in actions)


def test_dry_run_makes_no_api_call_and_apply_respects_the_flag():
    occurrence = Occurrence("trash", date(2026, 11, 5), date(2026, 11, 5))
    service = FakeService()

    def push(*, apply: bool, dry_run: bool):
        return push_calendar(
            enabled=True,
            dry_run=dry_run,
            calendar_id="primary",
            timezone_name="America/New_York",
            occurrences=[occurrence],
            options=OPTIONS,
            apply=apply,
            service=service,
            range_start=date(2026, 11, 1),
            range_end=date(2026, 11, 30),
        )

    report = push(apply=False, dry_run=True)
    assert report.mutated is False
    assert service.events().inserted == []
    with pytest.raises(ConfigError, match="dry_run"):
        push(apply=True, dry_run=True)
    assert service.events().inserted == []

    written = push(apply=True, dry_run=False)
    assert written.mutated is True
    assert written.counts["create"] == 1
    assert len(service.events().inserted) == 1
    assert service.events().inserted[0]["iCalUID"].endswith("@pgcounty-pickup-bot.local")

    service.events().remote = [
        {
            "id": "existing",
            "iCalUID": service.events().inserted[0]["iCalUID"],
            "summary": service.events().inserted[0]["summary"],
            "description": service.events().inserted[0]["description"],
            "start": service.events().inserted[0]["start"],
            "end": service.events().inserted[0]["end"],
            "reminders": service.events().inserted[0]["reminders"],
            "extendedProperties": service.events().inserted[0]["extendedProperties"],
        }
    ]
    again = push(apply=True, dry_run=False)
    assert again.counts["noop"] == 1
    assert service.events().patched == []
    assert len(service.events().inserted) == 1


def test_disabled_push_refuses():
    with pytest.raises(ConfigError, match="disabled"):
        push_calendar(
            enabled=False,
            dry_run=True,
            calendar_id="primary",
            timezone_name="America/New_York",
            occurrences=[],
            options=OPTIONS,
            apply=False,
        )


def test_reminder_minutes_follow_the_clock_across_dst():
    assert reminder_minutes(date(2026, 10, 29), OPTIONS) == 11 * 60
    assert reminder_minutes(date(2026, 11, 5), OPTIONS) == 11 * 60
    assert reminder_minutes(date(2026, 11, 1), OPTIONS) == 12 * 60
    assert reminder_minutes(date(2026, 3, 8), OPTIONS) == 10 * 60
    late = CalendarOptions(reminder_time=time(20, 30), collection_start=time(6, 0))
    assert reminder_minutes(date(2026, 11, 5), late) == 9 * 60 + 30
