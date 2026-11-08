"""The My Segments and Explore lists: which segments each holds, their fields, and sorting."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb

from app.config import RIDE_WINDOW
from app.region import route_in_region

LIST_KINDS = ("my_segments", "explore")
DEFAULT_LIST = "my_segments"
FORECAST_HOURS = 48

Direction = Literal["asc", "desc"]


@dataclass(frozen=True)
class Field:
    """One thing shown about every segment on a list.

    `kind` says how it will be filtered: "number" (a minimum and a maximum),
    "ticks" (tick boxes) or "search". `first` is the direction of the first click
    on its heading, or None for a field that can't be sorted. `value` reads it from
    a list row, giving None for a blank, which always sorts last.
    """

    label: str
    kind: Literal["number", "ticks", "search"]
    first: Direction | None
    value: Callable[[dict], object]


def _wind(key: str) -> Callable[[dict], float | None]:
    return lambda row: row[key]["percent"] if row[key] is not None else None


# The keys are saved in riders' list settings, so think twice before renaming one:
# a saved key that's no longer here is ignored.
FIELDS: dict[str, Field] = {
    "name": Field("Segment", "search", None, lambda row: row["name"]),
    "distance": Field("Distance", "number", "desc", lambda row: row["distance_m"]),
    "average_grade": Field("Avg grade", "number", "desc", lambda row: row["average_grade"]),
    "your_attempts": Field(
        "Your attempts, 90 days", "number", "desc", lambda row: row["recent_efforts"]
    ),
    # Most negative first: the biggest help from the wind.
    "wind_now": Field("Wind now", "number", "asc", _wind("wind_now")),
    "best_hour": Field("Best hour", "number", "asc", _wind("wind_best")),
    "people_on_strava": Field(
        "People on Strava", "number", "desc", lambda row: row["athlete_count"]
    ),
    "total_attempts": Field("Total attempts", "number", "desc", lambda row: row["effort_count"]),
}

DEFAULT_SORTS = {"my_segments": "your_attempts", "explore": "people_on_strava"}


@dataclass
class ListSettings:
    """A rider's choices for one list. `filters` is stored now and used by the filter tickets."""

    sort_field: str
    sort_direction: Direction
    filters: dict = field(default_factory=dict)


def default_settings(kind: str) -> ListSettings:
    sort_field = DEFAULT_SORTS[kind]
    return ListSettings(sort_field, FIELDS[sort_field].first)


def load_list_settings(conn: psycopg.Connection, athlete_id: int, kind: str) -> ListSettings:
    """The rider's saved settings for list `kind`, or the defaults if nothing is saved.

    A saved sort on a field that's unknown or can't be sorted falls back to the default sort.
    """
    row = conn.execute(
        """
        SELECT sort_field, sort_direction, filters FROM list_settings
        WHERE athlete_id = %s AND list_kind = %s
        """,
        (athlete_id, kind),
    ).fetchone()
    if row is None:
        return default_settings(kind)
    settings = ListSettings(row["sort_field"], row["sort_direction"], row["filters"])
    sort = FIELDS.get(settings.sort_field)
    if sort is None or sort.first is None or settings.sort_direction not in ("asc", "desc"):
        default = default_settings(kind)
        settings.sort_field, settings.sort_direction = default.sort_field, default.sort_direction
    return settings


def save_list_settings(
    conn: psycopg.Connection, athlete_id: int, kind: str, settings: ListSettings
) -> None:
    conn.execute(
        """
        INSERT INTO list_settings (athlete_id, list_kind, sort_field, sort_direction, filters)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (athlete_id, list_kind) DO UPDATE SET
            sort_field = EXCLUDED.sort_field,
            sort_direction = EXCLUDED.sort_direction,
            filters = EXCLUDED.filters
        """,
        (athlete_id, kind, settings.sort_field, settings.sort_direction, Jsonb(settings.filters)),
    )


def can_sort(field_key: str) -> bool:
    return field_key in FIELDS and FIELDS[field_key].first is not None


def choose_sort(settings: ListSettings, field_key: str) -> None:
    """A click on a sortable field's heading: reverses the current sort, or starts a new one."""
    if field_key == settings.sort_field:
        settings.sort_direction = "asc" if settings.sort_direction == "desc" else "desc"
    else:
        settings.sort_field, settings.sort_direction = field_key, FIELDS[field_key].first


def sort_segments(segments: list[dict], settings: ListSettings) -> list[dict]:
    """Sorted by the chosen field, with blanks last either way and ties in name order."""
    value = FIELDS[settings.sort_field].value
    by_name = sorted(segments, key=lambda row: (row["name"].lower(), row["id"]))
    known = [row for row in by_name if value(row) is not None]
    blank = [row for row in by_name if value(row) is None]
    # Python's sort keeps the name order of ties, even when reversed.
    return sorted(known, key=value, reverse=settings.sort_direction == "desc") + blank


def build_list(
    conn: psycopg.Connection,
    athlete_id: int,
    kind: str,
    settings: ListSettings,
    now: datetime,
    pooled: bool = False,
) -> list[dict]:
    """A rider's list `kind` with every field filled in and their settings applied."""
    segments = segment_list(conn, athlete_id, kind, now, pooled)
    add_wind(conn, athlete_id, segments, now)
    return sort_segments(segments, settings)


def has_missing_details(conn: psycopg.Connection, athlete_id: int) -> bool:
    """Whether any of the athlete's cached rides include a segment with no details."""
    return conn.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM efforts e
            LEFT JOIN segments s ON s.id = e.segment_id
            WHERE e.athlete_id = %s AND s.id IS NULL
        ) AS missing
        """,
        (athlete_id,),
    ).fetchone()["missing"]


def segment_list(
    conn: psycopg.Connection, athlete_id: int, kind: str, now: datetime, pooled: bool = False
) -> list[dict]:
    """The segments on an athlete's list `kind`, in name order, without wind numbers.

    My Segments holds segments from the athlete's last 90 days of rides. With `pooled`,
    Explore also includes segments from contributors' rides; without it, Explore holds
    the same segments as My Segments. Segments with any part of their route outside
    the region are left out of both.
    """
    rows = conn.execute(
        """
        SELECT s.polyline, s.id, s.name, s.distance_m, s.average_grade, s.athlete_count, s.effort_count,
               COUNT(e.id) FILTER (WHERE e.athlete_id = %(athlete_id)s) AS recent_efforts
        FROM segments s
        JOIN efforts e ON e.segment_id = s.id
        JOIN athletes a ON a.id = e.athlete_id
        WHERE e.start_date >= %(cutoff)s
          AND (e.athlete_id = %(athlete_id)s OR (%(pooled)s AND a.contributes IS TRUE AND e.shareable))
        GROUP BY s.id
        ORDER BY lower(s.name), s.id
        """,
        {
            "athlete_id": athlete_id,
            "cutoff": now - RIDE_WINDOW,
            "pooled": pooled and kind == "explore",
        },
    ).fetchall()
    return [
        {key: value for key, value in row.items() if key != "polyline"}
        for row in rows
        if route_in_region(row["polyline"])
    ]


def add_wind(
    conn: psycopg.Connection, athlete_id: int, segments: list[dict], now: datetime
) -> None:
    """Adds each segment's `wind_now`, `wind_best`, `wind_hours` and `less_certain`."""
    this_hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    ids = [segment["id"] for segment in segments]
    hours: dict[int, list[dict]] = {}
    for row in conn.execute(
        """
        SELECT segment_id, hour, percent, speed_ms, from_degrees FROM wind_effects
        WHERE athlete_id = %s AND segment_id = ANY(%s) AND hour >= %s AND hour <= %s
        ORDER BY hour
        """,
        (athlete_id, ids, this_hour, this_hour + timedelta(hours=FORECAST_HOURS)),
    ):
        hours.setdefault(row["segment_id"], []).append(row)
    less_certain = {
        row["segment_id"]
        for row in conn.execute(
            "SELECT segment_id FROM segment_wind_models WHERE segment_id = ANY(%s) AND less_certain",
            (ids,),
        )
    }
    for segment in segments:
        rows = hours.get(segment["id"], [])
        segment["wind_hours"] = rows
        segment["wind_now"] = next((row for row in rows if row["hour"] == this_hour), None)
        # min() keeps the earliest of equal hours.
        segment["wind_best"] = min(rows, key=lambda row: row["percent"], default=None)
        segment["less_certain"] = segment["id"] in less_certain
