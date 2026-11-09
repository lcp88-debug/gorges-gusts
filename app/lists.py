"""The My Segments and Explore lists: which segments each holds, and the rider's saved
choices for each.

Sorting, filtering and choosing the best hour happen in the browser (web/src/rules, see
ADR 0003). The server checks and stores what the rider chooses.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb

from app.config import RIDE_WINDOW
from app.region import route_in_region

LIST_KINDS = ("my_segments", "explore")
FORECAST_HOURS = 48

Direction = Literal["asc", "desc"]
FilterKind = Literal["number", "ticks", "search"]


# The field table is shared with the app, so each rule exists once (ADR 0003).
FIELD_TABLE = Path(__file__).resolve().parent.parent / "web" / "src" / "rules" / "fields.json"


@dataclass(frozen=True)
class Field:
    """One thing shown about every segment on a list: how it's filtered, and the direction
    of the first sort on it (None if it can't be sorted). Read from FIELD_TABLE, where the
    app also finds them; labels and units are in web/src/rules/fields.ts."""

    kind: FilterKind
    first_click: Direction | None
    setting: str | None = None


def _read_fields() -> dict[str, Field]:
    table = json.loads(FIELD_TABLE.read_text())["fields"]
    return {
        key: Field(entry["kind"], entry["firstClick"], entry.get("setting"))
        for key, entry in table.items()
    }


# The keys are saved in riders' list settings: a saved key that's no longer here is ignored.
FIELDS: dict[str, Field] = _read_fields()

# Fields that only exist while their app setting is on.
SETTING_FIELDS = {key for key, info in FIELDS.items() if info.setting}


def active_fields(fastest_time: bool = False) -> dict[str, Field]:
    """The fields the list has. Without the fastest-time setting, that field is absent from
    sorting and filters, and saved settings on it are ignored (but kept)."""
    if fastest_time:
        return FIELDS
    return {key: info for key, info in FIELDS.items() if key not in SETTING_FIELDS}


def can_sort(field_key: str, fastest_time: bool = False) -> bool:
    fields = active_fields(fastest_time)
    return field_key in fields and fields[field_key].first_click is not None


DEFAULT_SORTS = {"my_segments": "your_attempts", "explore": "people_on_strava"}


@dataclass
class ListSettings:
    """A rider's choices for one list. `filters` is {field key: filter}, see "Filters" below."""

    sort_field: str
    sort_direction: Direction
    filters: dict = field(default_factory=dict)


def default_settings(kind: str) -> ListSettings:
    sort_field = DEFAULT_SORTS[kind]
    return ListSettings(sort_field, FIELDS[sort_field].first_click)


def load_list_settings(
    conn: psycopg.Connection, athlete_id: int, kind: str, fastest_time: bool = False
) -> ListSettings:
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
    settings = ListSettings(
        row["sort_field"], row["sort_direction"], clean_filters(row["filters"])
    )
    sort = active_fields(fastest_time).get(settings.sort_field)
    if sort is None or sort.first_click is None or settings.sort_direction not in ("asc", "desc"):
        default = default_settings(kind)
        settings.sort_field, settings.sort_direction = default.sort_field, default.sort_direction
    return settings


def save_list_sort(
    conn: psycopg.Connection, athlete_id: int, kind: str, sort_field: str, direction: Direction
) -> None:
    """Saves only the sort, leaving saved filters as they were."""
    conn.execute(
        """
        INSERT INTO list_settings (athlete_id, list_kind, sort_field, sort_direction, filters)
        VALUES (%s, %s, %s, %s, '{}')
        ON CONFLICT (athlete_id, list_kind) DO UPDATE SET
            sort_field = EXCLUDED.sort_field,
            sort_direction = EXCLUDED.sort_direction
        """,
        (athlete_id, kind, sort_field, direction),
    )


def save_list_filters(
    conn: psycopg.Connection, athlete_id: int, kind: str, settings: ListSettings
) -> None:
    """Saves only the filters, so a saved sort the list can't use right now (on a field
    whose setting is off) is still there when the setting is turned back on."""
    conn.execute(
        """
        INSERT INTO list_settings (athlete_id, list_kind, sort_field, sort_direction, filters)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (athlete_id, list_kind) DO UPDATE SET filters = EXCLUDED.filters
        """,
        (athlete_id, kind, settings.sort_field, settings.sort_direction, Jsonb(settings.filters)),
    )


# Filters
#
# A list's filters are saved as one JSON object, {field key: filter}, with an entry only
# for fields that are filtered. What a filter looks like depends on the field's kind:
#   number: {"min": 1.5, "max": 4}  either key may be left out; in the units the list shows
#           (miles, feet, percent; seconds for a time field).
#   ticks:  ["Ithaca", "Dryden"] or [0, 4, 5]  the values to keep (towns; Strava's 0 to 5
#           climb categories, where 0 "none" is a real value).
#   search: "gorge"  kept when the segment's name contains it, ignoring capitals.


def _clean_number(saved: object) -> dict | None:
    if not isinstance(saved, dict):
        return None
    bounds = {}
    for edge in ("min", "max"):
        number = saved.get(edge)
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            if math.isfinite(number):
                bounds[edge] = float(number)
    return bounds or None


def _clean_ticks(saved: object) -> list | None:
    if not isinstance(saved, list):
        return None
    kept = [value for value in saved if isinstance(value, (str, int)) and not isinstance(value, bool)]
    return kept or None


def _clean_search(saved: object) -> str | None:
    return saved if isinstance(saved, str) and saved.strip() else None


CLEANERS = {"number": _clean_number, "ticks": _clean_ticks, "search": _clean_search}


def clean_filters(saved: object) -> dict:
    """Filters without unknown fields or malformed entries.

    Filters on a field whose setting is off are kept, so they're back when it's turned on.
    """
    if not isinstance(saved, dict):
        return {}
    cleaned = {}
    for key, value in saved.items():
        field_info = FIELDS.get(key)
        if field_info is not None:
            kept = CLEANERS[field_info.kind](value)
            if kept is not None:
                cleaned[key] = kept
    return cleaned


def hidden_filters(filters: dict, fastest_time: bool = False) -> dict:
    """The saved filters on fields whose setting is off. The app doesn't see them, so
    saving or clearing its filters keeps these as they were."""
    fields = active_fields(fastest_time)
    return {key: saved for key, saved in filters.items() if key not in fields}


# Time range
#
# A rider's time range for a list is the stretch of the next 48 hours that best hour is
# chosen from, as a (start, end) pair of hours, both included. None means all 48 hours.
# It's kept in the rider's session (see api.py), not with their saved list settings.

TimeRange = tuple[datetime, datetime]


class TimeRangeError(ValueError):
    """A range that can't be used. The message is shown to the rider."""


def this_hour(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def hour_options(now: datetime) -> list[datetime]:
    """Every hour a time range can start or end at: this hour and the 48 after it."""
    start = this_hour(now)
    return [start + timedelta(hours=offset) for offset in range(FORECAST_HOURS + 1)]


def _parse_hour(text: str, what: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text.strip())
    except ValueError:
        raise TimeRangeError(f"The {what} of the time range isn't a time.") from None
    # A time with no zone is read as the server's local time, which the page shows hours in.
    return this_hour(moment)


def parse_time_range(start: str, end: str, now: datetime) -> TimeRange:
    """The range the form describes. Raises TimeRangeError if it can't be used."""
    first, last = _parse_hour(start, "start"), _parse_hour(end, "end")
    options = hour_options(now)
    if not (options[0] <= first <= options[-1] and options[0] <= last <= options[-1]):
        raise TimeRangeError("The time range has to be within the next 48 hours.")
    if last < first:
        raise TimeRangeError("The time range ends before it starts.")
    return first, last


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
        SELECT s.polyline, s.id, s.name, s.town, s.distance_m, s.average_grade, s.maximum_grade,
               s.elevation_gain_m, s.climb_category, s.athlete_count, s.effort_count, s.fastest_time_s,
               COUNT(e.id) FILTER (WHERE e.athlete_id = %(athlete_id)s) AS recent_efforts,
               MIN(e.elapsed_s) FILTER (WHERE e.athlete_id = %(athlete_id)s) AS recent_best
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


def wind_hours(
    conn: psycopg.Connection, athlete_id: int, segments: list[dict], now: datetime
) -> tuple[dict[int, list[dict]], set[int]]:
    """Each segment's stored wind hours from this hour on, {segment id: rows in hour order},
    and the ids of segments whose numbers are less certain."""
    start_hour = this_hour(now)
    ids = [segment["id"] for segment in segments]
    hours: dict[int, list[dict]] = {}
    for row in conn.execute(
        """
        SELECT segment_id, hour, percent, speed_ms, from_degrees FROM wind_effects
        WHERE athlete_id = %s AND segment_id = ANY(%s) AND hour >= %s AND hour <= %s
        ORDER BY hour
        """,
        (athlete_id, ids, start_hour, start_hour + timedelta(hours=FORECAST_HOURS)),
    ):
        hours.setdefault(row["segment_id"], []).append(row)
    less_certain = {
        row["segment_id"]
        for row in conn.execute(
            "SELECT segment_id FROM segment_wind_models WHERE segment_id = ANY(%s) AND less_certain",
            (ids,),
        )
    }
    return hours, less_certain
