"""Config validation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from pgpickup.config import (
    apply_address_env,
    assert_safe_save_path,
    load_config,
    parse_config,
    render_saved_config,
)
from pgpickup.errors import ConfigError
from pgpickup.holidays import load_holiday_table


def base() -> dict:
    return {
        "timezone": "America/New_York",
        "reminder_time": "19:00",
        "source": "manual",
        "range": {"start": "2026-01-01", "end": "2026-12-31"},
        "schedule": {
            "trash": {"weekdays": ["thursday"]},
            "recycling": {"weekdays": ["thursday"], "frequency": "weekly"},
            "yard_waste": {"weekdays": ["monday"], "season": None},
            "bulky": {"mode": "with_trash"},
        },
        "google_calendar": {"enabled": False},
    }


def test_example_config_loads_and_google_defaults_to_dry_run():
    config = load_config(Path("config.example.yaml"))
    assert config.address is None
    assert config.include_location is False
    assert config.source == "manual"
    assert config.reminder_time.hour == 19
    assert config.schedule.trash.weekdays == (3,)
    assert config.schedule.yard_waste.season_start is None
    assert config.google_enabled is False
    assert config.google_dry_run is True
    assert config.google_oauth_client_file is not None
    assert config.google_oauth_client_file.name == "google-oauth-client.json"


def test_valid_manual_config():
    config = parse_config(base())
    assert config.schedule.recycling.frequency == "weekly"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data["schedule"]["trash"].update(weekdays=["funday"]), "Unknown weekday"),
        (lambda data: data.update(timezone="Not/AZone"), "Unknown timezone"),
        (lambda data: data.update(reminder_time="25:00"), "HH:MM"),
        (lambda data: data.update(reminder_time="7 PM"), "HH:MM"),
        (lambda data: data["range"].update(end="2025-01-01"), "before"),
        (lambda data: data["schedule"]["recycling"].update(frequency="alternate"), "anchor"),
        (
            lambda data: data["schedule"]["recycling"].update(
                frequency="alternate",
                anchor="2026-01-07",
            ),
            "anchor",
        ),
        (
            lambda data: data["schedule"]["recycling"].update(
                weekdays=["thursday", "friday"],
                frequency="alternate",
                anchor="2026-01-08",
            ),
            "exactly one",
        ),
        (lambda data: data["schedule"]["yard_waste"].update(season={"start": "03-01"}), "MM-DD"),
        (
            lambda data: data["schedule"]["yard_waste"].update(
                season={"start": "02-31", "end": "03-01"}
            ),
            "MM-DD",
        ),
        (
            lambda data: data["schedule"]["trash"].update(weekdays=["thursday", "thursday"]),
            "duplicate",
        ),
        (lambda data: data.update(source="lookup"), "address"),
        (
            lambda data: data["google_calendar"].update(enabled=True),
            "oauth_client_file",
        ),
        (lambda data: data["schedule"]["bulky"].update(mode="sometimes"), "bulky.mode"),
        (lambda data: data.update(include_location="maybe"), "true or false"),
    ],
)
def test_invalid_config(mutate, message):
    data = deepcopy(base())
    mutate(data)
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_alternate_anchor_on_the_right_weekday_loads():
    data = deepcopy(base())
    data["schedule"]["recycling"].update(frequency="alternate", anchor="2026-01-08")
    config = parse_config(data)
    assert config.schedule.recycling.anchor is not None
    assert config.schedule.recycling.anchor.isoformat() == "2026-01-08"


def test_season_parses():
    data = deepcopy(base())
    data["schedule"]["yard_waste"]["season"] = {"start": "03-01", "end": "12-15"}
    config = parse_config(data)
    assert config.schedule.yard_waste.season_start == (3, 1)
    assert config.schedule.yard_waste.season_end == (12, 15)


def test_lookup_source_allows_empty_weekdays():
    data = deepcopy(base())
    data["source"] = "lookup"
    data["address"] = "1301 McCormick Drive, Largo, MD 20774"
    data["schedule"] = {"recycling": {"frequency": "weekly"}, "yard_waste": {"season": None}}
    config = parse_config(data)
    assert config.schedule.bulky_mode == "derive"
    assert config.schedule.trash.weekdays == ()


def test_address_env_override(monkeypatch):
    config = parse_config(base())
    assert config.address is None
    monkeypatch.setenv("PGPICKUP_ADDRESS", "1 Public Plaza")
    updated = apply_address_env(config)
    assert updated.address == "1 Public Plaza"
    assert config.address is None


def test_refuse_to_save_over_the_example_or_tests():
    with pytest.raises(ConfigError):
        assert_safe_save_path(Path("config.example.yaml"))
    with pytest.raises(ConfigError):
        assert_safe_save_path(Path("tests/config.yaml"))


def test_saved_render_quotes_the_address():
    data = deepcopy(base())
    data["address"] = "1 Public Plaza"
    config = parse_config(data)
    text = render_saved_config(config, config.schedule)
    assert 'address: "1 Public Plaza"' in text
    assert "source: manual" in text
    assert "dry_run: true" in text


def test_holiday_file_rejects_duplicates_and_bad_observe(tmp_path: Path):
    path = tmp_path / "holidays.yaml"
    path.write_text(
        "source_url: https://example.test/holidays\n"
        "checked: 2026-10-06\n"
        "holidays:\n"
        "  - name: One\n"
        "    date: 2026-01-01\n"
        "    observe: slide\n"
        "  - name: Two\n"
        "    date: 2026-01-01\n"
        "    observe: none\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="Duplicate"):
        load_holiday_table(path)
    path.write_text(
        "source_url: https://example.test/holidays\n"
        "checked: 2026-10-06\n"
        "holidays:\n"
        "  - name: One\n"
        "    date: 2026-01-01\n"
        "    observe: maybe\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="observe"):
        load_holiday_table(path)
