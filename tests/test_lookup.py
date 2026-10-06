"""County lookup parsing, cache, and the one-request budget."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pgpickup.errors import LookupError
from pgpickup.lookup import (
    MAX_HTTP_REQUESTS,
    RequestBudget,
    UrllibTransport,
    choose_feature,
    lookup_address,
    parse_day_value,
)
from pgpickup.models import DayField

FIXTURES = Path("tests/fixtures")
OFFICE = "1301 McCormick Drive, Largo, MD 20774"


class FakeTransport:
    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_json(self, url: str, params: dict[str, str]) -> dict:
        self.calls.append(url)
        if "findAddressCandidates" in url:
            return self.responses["geocode"]
        if url.endswith("/query"):
            return self.responses["query"]
        raise AssertionError(url)


def _responses(service_name: str = "service_office.json") -> dict:
    return {
        "geocode": json.loads((FIXTURES / "geocode_office.json").read_text(encoding="utf-8")),
        "query": json.loads((FIXTURES / service_name).read_text(encoding="utf-8")),
    }


def test_office_fixture_is_no_service_and_cached(tmp_path: Path):
    transport = FakeTransport(_responses())
    moment = datetime(2026, 10, 6, tzinfo=UTC)
    first = lookup_address(
        OFFICE,
        cache_dir=tmp_path,
        transport=transport,
        now=moment,
    )
    assert len(transport.calls) == MAX_HTTP_REQUESTS
    assert first.in_layer
    assert first.trash == DayField()
    assert first.contractor == "JEDA"
    assert first.tier == "903-1"
    assert first.cached is False
    assert "McCormick" not in str(tmp_path.iterdir())
    cached_name = next(tmp_path.iterdir()).name
    assert "McCormick" not in cached_name
    assert cached_name.endswith(".json")

    second = lookup_address(OFFICE, cache_dir=tmp_path, transport=transport, now=moment)
    assert len(transport.calls) == MAX_HTTP_REQUESTS
    assert second.cached is True
    assert second.matched_address.startswith("1301 MCCORMICK")


def test_refresh_and_corrupt_cache_each_make_two_calls(tmp_path: Path):
    transport = FakeTransport(_responses("service_thursday.json"))
    moment = datetime(2026, 10, 6, tzinfo=UTC)
    found = lookup_address("1 Example Rd", cache_dir=tmp_path, transport=transport, now=moment)
    assert found.trash.weekdays == (3,)
    assert found.yard_waste.weekdays == (0,)
    assert found.bulky.weekdays == (3,)
    lookup_address(
        "1 Example Rd",
        cache_dir=tmp_path,
        transport=transport,
        refresh=True,
        now=moment,
    )
    assert len(transport.calls) == 4
    next(tmp_path.iterdir()).write_text("{", encoding="utf-8")
    lookup_address("1 Example Rd", cache_dir=tmp_path, transport=transport, now=moment)
    assert len(transport.calls) == 6


def test_no_match_does_not_query(tmp_path: Path):
    transport = FakeTransport(
        {"geocode": {"spatialReference": {"wkid": 4326}, "candidates": []}, "query": {}}
    )
    with pytest.raises(LookupError, match="No county address"):
        lookup_address("nowhere", cache_dir=tmp_path, transport=transport)
    assert len(transport.calls) == 1


def test_low_score_does_not_query(tmp_path: Path):
    payload = _responses()
    payload["geocode"]["candidates"][0]["score"] = 10
    transport = FakeTransport(payload)
    with pytest.raises(LookupError, match="score"):
        lookup_address(OFFICE, cache_dir=tmp_path, transport=transport)
    assert len(transport.calls) == 1


def test_budget_refuses_a_third_call():
    class AnyTransport:
        def get_json(self, url: str, params: dict[str, str]) -> dict:
            return {"url": url}

    budget = RequestBudget(AnyTransport())
    budget.get_json("https://example.test/a", {})
    budget.get_json("https://example.test/b", {})
    with pytest.raises(LookupError, match="extra HTTP"):
        budget.get_json("https://example.test/c", {})
    assert budget.calls == MAX_HTTP_REQUESTS


def test_paging_flag_is_refused(tmp_path: Path):
    payload = _responses()
    payload["query"]["exceededTransferLimit"] = True
    transport = FakeTransport(payload)
    with pytest.raises(LookupError, match="another page"):
        lookup_address(OFFICE, cache_dir=tmp_path, transport=transport)
    assert len(transport.calls) == 2


def test_choose_feature_prefers_the_served_polygon_and_rejects_two():
    empty = {
        "attributes": {
            "Trash_Day_of_Service1": "No Service",
            "Recycle_Day_of_Service": "No Service",
            "Yard_Day_of_Service": "No Service",
            "Bulky_Day_of_Service": "No Service",
        }
    }
    thursday = {
        "attributes": {
            "Trash_Day_of_Service1": "Thursday",
            "Recycle_Day_of_Service": "Thursday",
            "Yard_Day_of_Service": "Monday",
            "Bulky_Day_of_Service": "Call 311 to Schedule",
        }
    }
    chosen = choose_feature([empty, thursday])
    assert chosen is not None
    assert chosen["Trash_Day_of_Service1"] == "Thursday"
    with pytest.raises(LookupError, match="more than one"):
        choose_feature([thursday, thursday])


def test_day_values():
    assert parse_day_value("No Service") == DayField()
    assert parse_day_value("Call 311 to Schedule").appointment is True
    assert parse_day_value("Monday").weekdays == (0,)
    with pytest.raises(LookupError, match="Unrecognized"):
        parse_day_value("Funday")


def test_http_error_is_one_request_and_hides_the_query():
    hits = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits["n"] += 1
            self.send_response(500)
            self.end_headers()

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/query"
        with pytest.raises(LookupError) as caught:
            UrllibTransport().get_json(url, {"SingleLine": "SECRET-ADDRESS"})
        assert "SECRET-ADDRESS" not in str(caught.value)
        assert hits["n"] == 1
    finally:
        server.shutdown()
