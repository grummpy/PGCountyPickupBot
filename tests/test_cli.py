"""CLI commands against the example config."""

from __future__ import annotations

from pathlib import Path

from icalendar import Calendar

from pgpickup.cli import main
from pgpickup.models import DayField, LookupResult


def test_schedule_next_and_ics(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["--config", str(config), "schedule"]) == 0
    assert main(["--config", str(config), "next"]) == 0
    destination = tmp_path / "pickups.ics"
    assert main(["--config", str(config), "ics", "--out", str(destination)]) == 0
    raw = destination.read_bytes()
    calendar = Calendar.from_ical(raw)
    events = calendar.walk("VEVENT")
    assert events
    text = raw.decode("utf-8")
    assert "McCormick" not in text
    assert "123 Secret" not in text
    assert main(["--config", str(config), "push"]) == 1


def test_schedule_text_mentions_the_pattern_and_the_table_limit(capsys):
    config = Path("config.example.yaml")
    assert main(["--config", str(config), "schedule", "--limit", "4"]) == 0
    output = capsys.readouterr().out
    assert "Thursday" in output
    assert "Monday" in output
    assert "not shifted" in output


def test_reconcile_is_an_offline_no_mutation_preview(tmp_path: Path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(
        Path("config.example.yaml").read_text(encoding="utf-8")
        + "\noverrides:\n"
        + "  - stream: trash\n"
        + "    nominal_date: '2026-03-05'\n"
        + "    service_date: '2026-03-06'\n"
        + "    expires_on: '2026-03-06'\n"
        + "    state: proposed\n",
        encoding="utf-8",
    )
    before = config.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("reconcile must not call external or calendar code")

    monkeypatch.setattr("pgpickup.cli.lookup_address", forbidden)
    monkeypatch.setattr("pgpickup.cli.push_calendar", forbidden)

    assert (
        main(
            [
                "--config",
                str(config),
                "reconcile",
                "--as-of",
                "2026-03-04",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Offline reconciliation preview" in output
    assert "No county lookup, ICS export, cache write, or calendar mutation" in output
    assert "[proposed; proposed]" in output
    assert config.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.yaml"]


def test_incomplete_lookup_is_not_saved_as_a_partial_schedule(tmp_path: Path, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text(Path("config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    result = LookupResult(
        query_address="Example",
        matched_address="EXAMPLE",
        score=100,
        longitude=0,
        latitude=0,
        trash=DayField(weekdays=(3,)),
        recycling=DayField(),
        yard_waste=DayField(),
        bulky=DayField(),
        contractor="",
        tier="",
        polygon_count=1,
        missing_streams=("recycling", "yard_waste", "bulky"),
    )
    monkeypatch.setattr("pgpickup.cli.lookup_address", lambda *args, **kwargs: result)
    destination = tmp_path / "saved.yaml"
    assert (
        main(
            [
                "--config",
                str(config),
                "lookup",
                "--address",
                "Example",
                "--save",
                str(destination),
            ]
        )
        == 1
    )
    assert not destination.exists()
    assert "Not writing a partial schedule" in capsys.readouterr().err
