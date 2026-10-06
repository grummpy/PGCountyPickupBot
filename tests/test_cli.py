"""CLI commands against the example config."""

from __future__ import annotations

from pathlib import Path

from icalendar import Calendar

from pgpickup.cli import main


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
