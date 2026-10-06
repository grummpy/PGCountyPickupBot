"""Shared schedule builders."""

from __future__ import annotations

from datetime import date

from pgpickup.models import Schedule, StreamSchedule


def weekly(
    *,
    trash: tuple[int, ...] = (3,),
    recycling: tuple[int, ...] = (3,),
    yard: tuple[int, ...] = (0,),
    bulky_mode: str = "with_trash",
    bulky: tuple[int, ...] = (),
    frequency: str = "weekly",
    anchor: date | None = None,
    season: tuple[tuple[int, int], tuple[int, int]] | None = None,
) -> Schedule:
    season_start = season[0] if season else None
    season_end = season[1] if season else None
    return Schedule(
        trash=StreamSchedule(weekdays=trash),
        recycling=StreamSchedule(weekdays=recycling, frequency=frequency, anchor=anchor),
        yard_waste=StreamSchedule(
            weekdays=yard,
            season_start=season_start,
            season_end=season_end,
        ),
        bulky=StreamSchedule(weekdays=bulky),
        bulky_mode=bulky_mode,
    )
