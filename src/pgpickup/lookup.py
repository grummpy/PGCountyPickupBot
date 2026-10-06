"""Address lookup against the county's public ArcGIS collection layer.

A lookup makes at most one geocode request and one spatial query. Repeat
lookups of the same address are served from a local cache. The county website
itself is not fetched.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pgpickup.errors import LookupError
from pgpickup.models import DayField, LookupResult, parse_weekday

GEOCODE_URL = (
    "https://gisent.princegeorgescountymd.gov/gisonline/rest/services/"
    "DATA311/PGMD_Address_Locator/GeocodeServer/findAddressCandidates"
)
QUERY_URL = (
    "https://gisent.princegeorgescountymd.gov/gisonline/rest/services/"
    "DoE/Trash_Services/MapServer/2/query"
)
USER_AGENT = (
    "pgpickup/0.1 (family collection calendar; caches responses; one request per county service)"
)
OUT_FIELDS = (
    "Recycle_Day_of_Service,Trash_Day_of_Service1,Bulky_Day_of_Service,"
    "Yard_Day_of_Service,CONTRACTOR,Tier_Service_Area"
)
MIN_SCORE = 80.0
MAX_HTTP_REQUESTS = 2
MAX_BYTES = 1_000_000
WGS84 = frozenset({4326, 4269})

_FIELD_TO_STREAM = (
    ("Trash_Day_of_Service1", "trash"),
    ("Recycle_Day_of_Service", "recycling"),
    ("Yard_Day_of_Service", "yard_waste"),
    ("Bulky_Day_of_Service", "bulky"),
)


class RequestBudget:
    """Stop a lookup from issuing a third HTTP call."""

    def __init__(self, transport: Transport, limit: int = MAX_HTTP_REQUESTS) -> None:
        self.transport = transport
        self.limit = limit
        self.calls = 0

    def get_json(self, url: str, params: dict[str, str]) -> dict:
        if self.calls >= self.limit:
            raise LookupError("Refusing an extra HTTP request for this lookup.")
        self.calls += 1
        return self.transport.get_json(url, params)


class Transport:
    def get_json(self, url: str, params: dict[str, str]) -> dict:
        raise NotImplementedError


class UrllibTransport(Transport):
    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def get_json(self, url: str, params: dict[str, str]) -> dict:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{url}?{query}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                raw = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise LookupError(
                f"County service returned HTTP {exc.code} for {_public_url(url)}."
            ) from exc
        except urllib.error.URLError as exc:
            raise LookupError(f"County service request failed for {_public_url(url)}.") from exc
        if status != 200:
            raise LookupError(f"County service returned HTTP {status} for {_public_url(url)}.")
        if len(raw) > MAX_BYTES:
            raise LookupError("County response was larger than 1 MB; not reading the rest.")
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise LookupError(
                f"County service did not return JSON from {_public_url(url)}."
            ) from exc
        if isinstance(data, dict) and "error" in data:
            message = data["error"]
            if isinstance(message, dict):
                message = message.get("message") or "request failed"
            raise LookupError(f"County service error from {_public_url(url)}: {message}")
        if not isinstance(data, dict):
            raise LookupError(
                f"County service returned an unexpected payload from {_public_url(url)}."
            )
        return data


def normalize_address(address: str) -> str:
    return " ".join(address.split()).casefold()


def parse_day_value(value: object) -> DayField:
    if value is None:
        return DayField()
    text = str(value).strip()
    if not text or text.casefold() == "no service":
        return DayField()
    if text.casefold() == "call 311 to schedule":
        return DayField(appointment=True)
    try:
        return DayField(weekdays=(parse_weekday(text),))
    except ValueError as exc:
        raise LookupError(f"Unrecognized collection day {value!r} from the county layer.") from exc


def attributes_to_days(attributes: dict) -> dict[str, DayField]:
    return {stream: parse_day_value(attributes.get(field)) for field, stream in _FIELD_TO_STREAM}


def choose_feature(features: list[dict]) -> dict | None:
    if not features:
        return None
    parsed: list[tuple[dict, dict[str, DayField]]] = []
    for feature in features:
        attributes = feature.get("attributes") or {}
        if not isinstance(attributes, dict):
            raise LookupError("County layer returned a feature without attributes.")
        parsed.append((attributes, attributes_to_days(attributes)))
    served = [
        item for item in parsed if any(day.weekdays or day.appointment for day in item[1].values())
    ]
    if len(served) > 1:
        raise LookupError(
            "This point intersects more than one collection area. Confirm the day with the county."
        )
    if len(served) == 1:
        return served[0][0]
    return parsed[0][0]


def lookup_address(
    address: str,
    *,
    cache_dir: Path,
    cache_days: int = 7,
    transport: Transport | None = None,
    refresh: bool = False,
    now: datetime | None = None,
) -> LookupResult:
    cleaned = " ".join(address.split())
    if not cleaned:
        raise LookupError("Address is empty.")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    cache_file = _cache_path(cache_dir, cleaned)
    if not refresh:
        cached = _read_cache(cache_file, moment, cache_days)
        if cached is not None:
            return cached
    budget = RequestBudget(transport or UrllibTransport())
    geocode = budget.get_json(
        GEOCODE_URL,
        {
            "SingleLine": cleaned,
            "f": "json",
            "outSR": "4326",
            "maxLocations": "1",
            "outFields": "Match_addr,Addr_type,Score,ShortLabel",
        },
    )
    result = _lookup_from_geocode(cleaned, geocode, budget)
    _write_cache(cache_file, result, moment)
    return result


def _lookup_from_geocode(address: str, geocode: dict, budget: RequestBudget) -> LookupResult:
    spatial = geocode.get("spatialReference") or {}
    wkid = spatial.get("latestWkid") or spatial.get("wkid")
    if wkid not in WGS84:
        raise LookupError(f"Geocoder spatial reference {wkid} is not WGS84.")
    candidates = geocode.get("candidates") or []
    if not candidates:
        raise LookupError("No county address match. Check the street, city, and ZIP.")
    best = candidates[0]
    score = float(best.get("score") or 0)
    if score < MIN_SCORE:
        raise LookupError(f"Address match score {score:.0f} is below {MIN_SCORE:.0f}.")
    location = best.get("location") or {}
    if "x" not in location or "y" not in location:
        raise LookupError("Geocoder did not return a point.")
    longitude = float(location["x"])
    latitude = float(location["y"])
    geometry = json.dumps(
        {"x": longitude, "y": latitude, "spatialReference": {"wkid": 4326}},
        separators=(",", ":"),
    )
    payload = budget.get_json(
        QUERY_URL,
        {
            "geometry": geometry,
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": OUT_FIELDS,
            "returnGeometry": "false",
            "f": "json",
        },
    )
    if payload.get("exceededTransferLimit"):
        raise LookupError("County service asked for another page; refusing a second query.")
    features = payload.get("features") or []
    if not isinstance(features, list):
        raise LookupError("County layer returned an unexpected feature list.")
    chosen = choose_feature(features)
    days = attributes_to_days(chosen or {})
    return LookupResult(
        query_address=address,
        matched_address=str(best.get("address") or address),
        score=score,
        longitude=longitude,
        latitude=latitude,
        trash=days.get("trash", DayField()),
        recycling=days.get("recycling", DayField()),
        yard_waste=days.get("yard_waste", DayField()),
        bulky=days.get("bulky", DayField()),
        contractor=str((chosen or {}).get("CONTRACTOR") or ""),
        tier=str((chosen or {}).get("Tier_Service_Area") or ""),
        polygon_count=len(features),
        cached=False,
    )


def _cache_path(cache_dir: Path, address: str) -> Path:
    digest = hashlib.sha256(normalize_address(address).encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


def _read_cache(path: Path, now: datetime, cache_days: int) -> LookupResult | None:
    if cache_days == 0 or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(payload["cached_at"])
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=UTC)
        if now - cached_at > timedelta(days=cache_days):
            return None
        return _result_from_payload(payload, cached=True)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _write_cache(path: Path, result: LookupResult, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_payload(result, now), indent=2) + "\n",
        encoding="utf-8",
    )


def _payload(result: LookupResult, now: datetime) -> dict:
    def field(day: DayField) -> dict:
        return {"weekdays": list(day.weekdays), "appointment": day.appointment}

    return {
        "cached_at": now.astimezone(UTC).isoformat(),
        "query_address": result.query_address,
        "matched_address": result.matched_address,
        "score": result.score,
        "longitude": result.longitude,
        "latitude": result.latitude,
        "trash": field(result.trash),
        "recycling": field(result.recycling),
        "yard_waste": field(result.yard_waste),
        "bulky": field(result.bulky),
        "contractor": result.contractor,
        "tier": result.tier,
        "polygon_count": result.polygon_count,
    }


def _result_from_payload(payload: dict, *, cached: bool) -> LookupResult:
    def field(raw: dict) -> DayField:
        return DayField(
            weekdays=tuple(int(day) for day in raw.get("weekdays") or ()),
            appointment=bool(raw.get("appointment")),
        )

    return LookupResult(
        query_address=str(payload["query_address"]),
        matched_address=str(payload["matched_address"]),
        score=float(payload["score"]),
        longitude=float(payload["longitude"]),
        latitude=float(payload["latitude"]),
        trash=field(payload["trash"]),
        recycling=field(payload["recycling"]),
        yard_waste=field(payload["yard_waste"]),
        bulky=field(payload["bulky"]),
        contractor=str(payload.get("contractor") or ""),
        tier=str(payload.get("tier") or ""),
        polygon_count=int(payload["polygon_count"]),
        cached=cached,
    )


def _public_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
