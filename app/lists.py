"""The My Segments and Explore lists: which segments each holds, their fields, and sorting."""

import math
from collections.abc import Callable, Mapping
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
METERS_PER_MILE = 1609.34
FEET_PER_METER = 3.28084

Direction = Literal["asc", "desc"]


@dataclass(frozen=True)
class Field:
    """One thing shown about every segment on a list.

    `kind` says how it will be filtered: "number" (a minimum and a maximum),
    "ticks" (tick boxes) or "search". `first` is the direction of the first click
    on its heading, or None for a field that can't be sorted. `value` reads it from
    a list row, giving None for a blank, which always sorts last.

    For a "number" field, `per_shown_unit` is how many of the row's units make one unit
    as the list shows it (1609.34 metres to a mile), and `is_time` means entries are typed
    as minutes and seconds and `value` is in seconds.
    """

    label: str
    kind: Literal["number", "ticks", "search"]
    first: Direction | None
    value: Callable[[dict], object]
    per_shown_unit: float = 1.0
    is_time: bool = False


def _wind(key: str) -> Callable[[dict], float | None]:
    return lambda row: row[key]["percent"] if row[key] is not None else None


# The keys are saved in riders' list settings, so think twice before renaming one:
# a saved key that's no longer here is ignored.
FIELDS: dict[str, Field] = {
    "name": Field("Segment", "search", None, lambda row: row["name"]),
    "town": Field("Town", "ticks", None, lambda row: row["town"]),
    "distance": Field(
        "Distance", "number", "desc", lambda row: row["distance_m"], per_shown_unit=METERS_PER_MILE
    ),
    "average_grade": Field("Avg grade", "number", "desc", lambda row: row["average_grade"]),
    "steepest_grade": Field("Steepest grade", "number", "desc", lambda row: row["maximum_grade"]),
    "elevation_gain": Field(
        "Elevation gain",
        "number",
        "desc",
        lambda row: row["elevation_gain_m"],
        per_shown_unit=1 / FEET_PER_METER,
    ),
    # Strava's 0 to 5, so biggest first is hardest first: HC, 1, 2, 3, 4, then none.
    "climb_category": Field(
        "Climb category", "ticks", "desc", lambda row: row["climb_category"]
    ),
    "your_attempts": Field(
        "Your attempts, 90 days", "number", "desc", lambda row: row["recent_efforts"]
    ),
    # Seconds, fastest first. Blank for segments the rider hasn't attempted.
    "recent_best": Field(
        "Recent best", "number", "asc", lambda row: row["recent_best"], is_time=True
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


# Strava's climb category 0 to 5 as the list shows it, so a category is its position here.
CLIMB_CATEGORIES = ("none", "4", "3", "2", "1", "HC")


@dataclass
class ListSettings:
    """A rider's choices for one list. `filters` is {field key: filter}, see "Filters" below."""

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
    settings = ListSettings(
        row["sort_field"], row["sort_direction"], clean_filters(row["filters"])
    )
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


# Filters
#
# A list's filters are saved as one JSON object, {field key: filter}, with an entry only
# for fields that are filtered. What a filter looks like depends on the field's kind:
#   number: {"min": 1.5, "max": 4}  either key may be left out; in the units the list shows
#           (miles, feet, percent; seconds for a time field, shown and typed as m:ss).
#   ticks:  ["Ithaca", "Dryden"] or [0, 4, 5]  the values to keep (towns; Strava's 0 to 5
#           climb categories, where 0 "none" is a real value). A blank never passes.
#   search: "gorge"  kept when the segment's name contains it, ignoring capitals.
# Each kind has a FilterKind in FILTER_KINDS saying how to read the form, check a saved
# value, and test a row. The filter form is one form holding every box, and it replaces the
# whole set: a field with all its boxes empty is simply not filtered.


class FilterError(ValueError):
    """An entry that can't be used. The message is shown to the rider."""


@dataclass(frozen=True)
class FilterKind:
    # Reads field `key`'s boxes from the posted form: its filter, or None for unfiltered.
    parse: Callable[[str, Mapping[str, str]], object | None]
    # A saved filter if it is well formed, else None (so it is dropped).
    clean: Callable[[object], object | None]
    # Whether a row's value passes. Only called on a filtered field.
    matches: Callable[[object, object, Field], bool]
    # Text for each box when the form is shown: {box name suffix: text}.
    # Ticks show {"ticked": [...]} and search {"text": ...}.
    show: Callable[[object, Field], dict[str, object]]


def parse_time(text: str) -> float:
    """"m:ss" or "h:mm:ss" as seconds."""
    parts = text.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise ValueError(text)
    numbers = [int(part) for part in parts]
    if numbers[-1] > 59 or (len(numbers) == 3 and numbers[1] > 59):
        raise ValueError(text)
    total = 0
    for number in numbers:
        total = total * 60 + number
    return float(total)


def format_time(seconds: float) -> str:
    hours, rest = divmod(round(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def _parse_number(text: str, field_info: Field) -> float | None:
    text = text.strip().replace("\u2212", "-")  # The minus sign wind numbers are shown with.
    if not text:
        return None
    try:
        number = parse_time(text) if field_info.is_time else float(text)
    except ValueError:
        what = "a time like 1:30" if field_info.is_time else "a number"
        raise FilterError(f"{field_info.label}: \u201c{text}\u201d isn't {what}.") from None
    if not math.isfinite(number):
        raise FilterError(f"{field_info.label}: \u201c{text}\u201d isn't a usable number.")
    return number


def parse_number_filter(key: str, form: Mapping[str, str]) -> dict | None:
    field_info = FIELDS[key]
    bounds = {}
    for edge in ("min", "max"):
        number = _parse_number(form.get(f"{key}_{edge}", ""), field_info)
        if number is not None:
            bounds[edge] = number
    if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
        raise FilterError(f"{field_info.label}: the minimum is above the maximum.")
    return bounds or None


def clean_number_filter(saved: object) -> dict | None:
    if not isinstance(saved, dict):
        return None
    bounds = {}
    for edge in ("min", "max"):
        number = saved.get(edge)
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            if math.isfinite(number):
                bounds[edge] = float(number)
    return bounds or None


def number_matches(value: object, bounds: dict, field_info: Field) -> bool:
    if value is None:
        return False  # A blank can't be said to be within the limits.
    shown = round(value / field_info.per_shown_unit, 6)
    return bounds.get("min", -math.inf) <= shown <= bounds.get("max", math.inf)


def show_number_filter(bounds: object, field_info: Field) -> dict[str, str]:
    def text(edge: str) -> str:
        number = (bounds or {}).get(edge)
        if number is None:
            return ""
        return format_time(number) if field_info.is_time else f"{number:g}"

    return {"min": text("min"), "max": text("max")}


# Tick boxes are posted as one box per value, named `<key>_<value>` (climb_category_0,
# town_Ithaca), so a ticked box is any form entry starting with `<key>_`.
def _ticked_values(key: str, form: Mapping[str, str]) -> list[str]:
    prefix = f"{key}_"
    return [name[len(prefix):] for name in form if name.startswith(prefix)]


def parse_ticks_filter(key: str, form: Mapping[str, str]) -> list | None:
    values = _ticked_values(key, form)
    if key == "climb_category":
        categories = set()
        for value in values:
            if value not in {str(number) for number in range(len(CLIMB_CATEGORIES))}:
                raise FilterError(f"{FIELDS[key].label}: \u201c{value}\u201d isn't a category.")
            categories.add(int(value))
        return sorted(categories) or None
    return sorted(set(values)) or None


def clean_ticks_filter(saved: object) -> list | None:
    if not isinstance(saved, list):
        return None
    kept = [value for value in saved if isinstance(value, (str, int)) and not isinstance(value, bool)]
    return kept or None


def ticks_matches(value: object, saved: list, field_info: Field) -> bool:
    return value is not None and value in saved


def show_ticks_filter(saved: object, field_info: Field) -> dict[str, object]:
    return {"ticked": [str(value) for value in saved or []]}


def parse_search_filter(key: str, form: Mapping[str, str]) -> str | None:
    return form.get(f"{key}_search", "").strip() or None


def clean_search_filter(saved: object) -> str | None:
    return saved if isinstance(saved, str) and saved.strip() else None


def search_matches(value: object, saved: str, field_info: Field) -> bool:
    return value is not None and saved.strip().lower() in str(value).lower()


def show_search_filter(saved: object, field_info: Field) -> dict[str, object]:
    return {"text": saved or ""}


def tick_choices(key: str, rows: list[dict], saved: object) -> list[tuple[str, str]]:
    """The tick boxes to show for a field: (value, label). Towns are only those in `rows`,
    plus any still ticked from before so they can be unticked."""
    if key == "climb_category":
        return [(str(number), label) for number, label in enumerate(CLIMB_CATEGORIES)]
    towns = {row[key] for row in rows if row[key]} | {str(value) for value in saved or []}
    return [(town, town) for town in sorted(towns, key=str.lower)]


# Add a kind here to make its fields filterable.
FILTER_KINDS: dict[str, FilterKind] = {
    "ticks": FilterKind(
        parse=parse_ticks_filter,
        clean=clean_ticks_filter,
        matches=ticks_matches,
        show=show_ticks_filter,
    ),
    "search": FilterKind(
        parse=parse_search_filter,
        clean=clean_search_filter,
        matches=search_matches,
        show=show_search_filter,
    ),
    "number": FilterKind(
        parse=parse_number_filter,
        clean=clean_number_filter,
        matches=number_matches,
        show=show_number_filter,
    ),
}


def clean_filters(saved: object) -> dict:
    """Saved filters without unknown fields or malformed entries."""
    if not isinstance(saved, dict):
        return {}
    cleaned = {}
    for key, value in saved.items():
        field_info = FIELDS.get(key)
        kind = FILTER_KINDS.get(field_info.kind) if field_info else None
        if kind is not None:
            kept = kind.clean(value)
            if kept is not None:
                cleaned[key] = kept
    return cleaned


def parse_filter_form(form: Mapping[str, str]) -> dict:
    """The filters a submitted form describes. Raises FilterError on the first bad entry."""
    filters = {}
    for key, field_info in FIELDS.items():
        kind = FILTER_KINDS.get(field_info.kind)
        parsed = kind.parse(key, form) if kind is not None else None
        if parsed is not None:
            filters[key] = parsed
    return filters


def filter_boxes(filters: dict) -> dict[str, dict[str, object]]:
    """For each filterable field, what to show in its boxes: {key: {"min": "", ...}}."""
    return {
        key: FILTER_KINDS[field_info.kind].show(filters.get(key), field_info)
        for key, field_info in FIELDS.items()
        if field_info.kind in FILTER_KINDS
    }


def filter_segments(segments: list[dict], filters: dict) -> list[dict]:
    """The segments that pass every filter."""
    for key, saved in filters.items():
        field_info = FIELDS[key]
        matches = FILTER_KINDS[field_info.kind].matches
        segments = [row for row in segments if matches(field_info.value(row), saved, field_info)]
    return segments


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
    return sort_segments(filter_segments(segments, settings.filters), settings)


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
               s.elevation_gain_m, s.climb_category, s.athlete_count, s.effort_count,
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
