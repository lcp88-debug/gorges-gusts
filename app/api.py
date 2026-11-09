"""The JSON routes the React app calls (see ADR 0003).

The server hands the app each list's segments and stores the rider's choices. Sorting,
filtering and choosing the best hour happen in the browser, in web/src/rules.
"""

from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import CACHE_TTL, Settings
from app.db import Database
from app.lists import (
    LIST_KINDS,
    TimeRangeError,
    active_fields,
    can_sort,
    clean_filters,
    has_missing_details,
    hidden_filters,
    hour_options,
    load_list_settings,
    parse_time_range,
    save_list_filters,
    save_list_sort,
    segment_list,
    this_hour,
    wind_hours,
)
from app.wind import DEFAULT_POWER_20MIN_W, DEFAULT_WEIGHT_KG
from app.wind_updates import SEEN_RECENTLY, wind_pending
from app.worker import enqueue_sync

ACTIVE_STATUSES = ("pending", "running", "paused")
WEIGHT_RANGE_KG = (30, 200)
POWER_RANGE_W = (50, 600)


def _error(message: str, status: int = 422) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


NOT_LOGGED_IN = {"error": "Not logged in."}
UNKNOWN_LIST = {"error": "No such list."}


def _iso(value: datetime | None) -> str | None:
    """A moment as the app reads it, always in UTC."""
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def in_range(value: object, limits: tuple[int, int]) -> float | None:
    """A number within `limits`, or None for a blank entry. Raises ValueError otherwise."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(value)
    number = float(value)
    if not limits[0] <= number <= limits[1]:  # Also rejects "nan".
        raise ValueError(value)
    return number


def saved_time_range(request: Request, kind: str, now: datetime):
    """List `kind`'s time range kept in the rider's session, or None for all 48 hours.

    A visit can outlast its range. Hours already gone are left out, and a range that has
    ended entirely goes back to all 48 hours rather than leaving best hour blank.
    """
    try:
        first, last = request.session.get("time_ranges", {})[kind]
        first, last = datetime.fromisoformat(first), datetime.fromisoformat(last)
    except (KeyError, TypeError, ValueError):
        return None
    start = this_hour(now)
    if last < start:
        return None
    return max(first, start), last


def latest_job(conn, athlete_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM sync_jobs WHERE athlete_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (athlete_id,),
    ).fetchone()


def record_visit(conn, request: Request, athlete_id: int, now: datetime) -> dict | None:
    """Notes that the rider is using the app, and returns their row (None if they're gone).

    A rider whose previous visit was more than an hour ago starts a new visit, which resets
    their time ranges. So does a rider never seen before.
    """
    before = conn.execute(
        "SELECT last_seen_at FROM athletes WHERE id = %s", (athlete_id,)
    ).fetchone()
    if before is None or before["last_seen_at"] is None or (
        now - before["last_seen_at"] > SEEN_RECENTLY
    ):
        request.session.pop("time_ranges", None)
    return conn.execute(
        """
        UPDATE athletes SET last_seen_at = %s WHERE id = %s
        RETURNING last_synced_at, contributes, weight_kg, power_20min_w,
                  wind_stale, wind_started_at, wind_updated_at
        """,
        (now, athlete_id),
    ).fetchone()


def _job(job: dict | None) -> dict | None:
    if job is None:
        return None
    return {
        "status": job["status"],
        "phase": job["phase"],
        "done": job["done"],
        "total": job["total"],
        "resume_at": _iso(job["resume_at"]),
        "error": job["error"],
    }


class SortChoice(BaseModel):
    field: str
    direction: str


class FilterChoice(BaseModel):
    filters: dict


class TimeRangeChoice(BaseModel):
    start: str
    end: str


class ProfileChoice(BaseModel):
    weight_kg: float | str | None = None
    power_20min_w: float | str | None = None


class ContributingChoice(BaseModel):
    on: bool


def api_router(db: Database, settings: Settings, worker_running: Callable[[], bool]) -> APIRouter:
    router = APIRouter(prefix="/api")

    def rider(request: Request) -> int | None:
        return request.session.get("athlete_id")

    @router.get("/state")
    def state(request: Request):
        """Who's logged in, how their data is doing, and their profile.

        The app asks again every few seconds while `busy`, and reloads its lists when
        `lists_version` changes.
        """
        athlete_id = rider(request)
        if athlete_id is None:
            return {
                "logged_in": False,
                "login_error": request.session.pop("login_error", None),
                "pooled": settings.pooled_explore,
            }
        now = datetime.now(timezone.utc)
        with db.connect() as conn:
            athlete = record_visit(conn, request, athlete_id, now)
            if athlete is None:
                request.session.clear()
                return {"logged_in": False, "login_error": None, "pooled": settings.pooled_explore}
            synced = athlete["last_synced_at"]
            lists_ready = synced is not None and now - synced < CACHE_TTL
            job = latest_job(conn, athlete_id)
            # A returning visitor's data may have expired, and shared details can expire
            # before their own rides do. A failed job stays put until they press Refresh,
            # so a broken login can't retry on every visit.
            needs_sync = not lists_ready or has_missing_details(conn, athlete_id)
            if needs_sync and job is not None and job["status"] == "done":
                enqueue_sync(conn, athlete_id)
                job = latest_job(conn, athlete_id)

        active = job is not None and job["status"] in ACTIVE_STATUSES
        # Without a worker running, nothing is going to change.
        pending = worker_running() and wind_pending(athlete, now)
        return {
            "logged_in": True,
            "pooled": settings.pooled_explore,
            "fastest_time": settings.fastest_time,
            "job": _job(job),
            "syncing": active,
            "show_lists": lists_ready or (job is not None and job["status"] == "done"),
            "last_synced_at": _iso(synced),
            "wind_pending": pending,
            "busy": active or pending,
            "lists_version": f"{_iso(synced)}|{_iso(athlete['wind_updated_at'])}",
            "this_hour": _iso(this_hour(now)),
            "profile": {
                "weight_kg": athlete["weight_kg"],
                "power_20min_w": athlete["power_20min_w"],
                "default_weight_kg": DEFAULT_WEIGHT_KG,
                "default_power_20min_w": DEFAULT_POWER_20MIN_W,
                "weight_range": WEIGHT_RANGE_KG,
                "power_range": POWER_RANGE_W,
            },
            "contributes": athlete["contributes"],
        }

    @router.get("/lists/{kind}")
    def get_list(request: Request, kind: str):
        """Every segment on list `kind` with its wind hours, before any filtering, and the
        rider's saved choices for it."""
        athlete_id = rider(request)
        if athlete_id is None:
            return JSONResponse(NOT_LOGGED_IN, status_code=401)
        if kind not in LIST_KINDS:
            return JSONResponse(UNKNOWN_LIST, status_code=404)
        now = datetime.now(timezone.utc)
        with db.connect() as conn:
            list_settings = load_list_settings(conn, athlete_id, kind, settings.fastest_time)
            segments = segment_list(conn, athlete_id, kind, now, settings.pooled_explore)
            hours, less_certain = wind_hours(conn, athlete_id, segments, now)
        time_range = saved_time_range(request, kind, now)
        shown = active_fields(settings.fastest_time)
        return {
            "segments": [
                _segment(
                    segment,
                    hours.get(segment["id"], []),
                    segment["id"] in less_certain,
                    settings.fastest_time,
                )
                for segment in segments
            ],
            "sort": {"field": list_settings.sort_field, "direction": list_settings.sort_direction},
            # Filters on a field whose setting is off stay saved but aren't the app's business.
            "filters": {key: value for key, value in list_settings.filters.items() if key in shown},
            "time_range": [_iso(t) for t in time_range] if time_range else None,
            "hours": [_iso(hour) for hour in hour_options(now)],
            "this_hour": _iso(this_hour(now)),
        }

    @router.put("/lists/{kind}/sort")
    def put_sort(request: Request, kind: str, choice: SortChoice):
        athlete_id = rider(request)
        if athlete_id is None:
            return JSONResponse(NOT_LOGGED_IN, status_code=401)
        if kind not in LIST_KINDS:
            return JSONResponse(UNKNOWN_LIST, status_code=404)
        if not can_sort(choice.field, settings.fastest_time) or choice.direction not in ("asc", "desc"):
            return _error("That field can't be sorted.")
        with db.connect() as conn:
            save_list_sort(conn, athlete_id, kind, choice.field, choice.direction)
        return {"sort": {"field": choice.field, "direction": choice.direction}}

    @router.put("/lists/{kind}/filters")
    def put_filters(request: Request, kind: str, choice: FilterChoice):
        """Replaces the list's filters with the ones the app sends.

        Malformed entries are dropped. Filters on a field whose setting is off aren't sent
        by the app, and stay as they were.
        """
        athlete_id = rider(request)
        if athlete_id is None:
            return JSONResponse(NOT_LOGGED_IN, status_code=401)
        if kind not in LIST_KINDS:
            return JSONResponse(UNKNOWN_LIST, status_code=404)
        shown = active_fields(settings.fastest_time)
        sent = {key: value for key, value in clean_filters(choice.filters).items() if key in shown}
        with db.connect() as conn:
            list_settings = load_list_settings(conn, athlete_id, kind, settings.fastest_time)
            list_settings.filters = {**hidden_filters(list_settings.filters, settings.fastest_time), **sent}
            save_list_filters(conn, athlete_id, kind, list_settings)
        return {"filters": sent}

    @router.put("/lists/{kind}/time-range")
    def put_time_range(request: Request, kind: str, choice: TimeRangeChoice):
        """Keeps the range for this visit, or changes nothing if it's bad."""
        athlete_id = rider(request)
        if athlete_id is None:
            return JSONResponse(NOT_LOGGED_IN, status_code=401)
        if kind not in LIST_KINDS:
            return JSONResponse(UNKNOWN_LIST, status_code=404)
        now = datetime.now(timezone.utc)
        # Setting a range is part of a visit, so the next look at the list mustn't reset it.
        with db.connect() as conn:
            record_visit(conn, request, athlete_id, now)
        try:
            first, last = parse_time_range(choice.start, choice.end, now)
        except TimeRangeError as error:
            return _error(f"{error} Nothing was changed.")
        ranges = dict(request.session.get("time_ranges", {}))
        ranges[kind] = [_iso(first), _iso(last)]
        request.session["time_ranges"] = ranges
        return {"time_range": ranges[kind]}

    @router.delete("/lists/{kind}/time-range")
    def delete_time_range(request: Request, kind: str):
        """Back to all 48 hours for this list."""
        if rider(request) is None:
            return JSONResponse(NOT_LOGGED_IN, status_code=401)
        if kind not in LIST_KINDS:
            return JSONResponse(UNKNOWN_LIST, status_code=404)
        ranges = dict(request.session.get("time_ranges", {}))
        ranges.pop(kind, None)
        request.session["time_ranges"] = ranges
        return {"time_range": None}

    @router.post("/refresh")
    def refresh(request: Request):
        athlete_id = rider(request)
        if athlete_id is None:
            return JSONResponse(NOT_LOGGED_IN, status_code=401)
        with db.connect() as conn:
            enqueue_sync(conn, athlete_id)
        return {"ok": True}

    @router.put("/contributing")
    def put_contributing(request: Request, choice: ContributingChoice):
        athlete_id = rider(request)
        if athlete_id is None:
            return JSONResponse(NOT_LOGGED_IN, status_code=401)
        with db.connect() as conn:
            conn.execute(
                "UPDATE athletes SET contributes = %s WHERE id = %s", (choice.on, athlete_id)
            )
        return {"contributes": choice.on}

    @router.put("/profile")
    def put_profile(request: Request, choice: ProfileChoice):
        athlete_id = rider(request)
        if athlete_id is None:
            return JSONResponse(NOT_LOGGED_IN, status_code=401)
        try:
            weight = in_range(choice.weight_kg, WEIGHT_RANGE_KG)
            power = in_range(choice.power_20min_w, POWER_RANGE_W)
        except ValueError:
            return _error(
                f"Weight must be {WEIGHT_RANGE_KG[0]}–{WEIGHT_RANGE_KG[1]} kg and power "
                f"{POWER_RANGE_W[0]}–{POWER_RANGE_W[1]} W. Nothing was changed."
            )
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE athletes SET weight_kg = %s, power_20min_w = %s, wind_stale = TRUE
                WHERE id = %s
                """,
                (weight, power, athlete_id),
            )
        return {"weight_kg": weight, "power_20min_w": power}

    @router.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    return router


def _segment(row: dict, hours: list[dict], less_certain: bool, fastest_time: bool) -> dict:
    segment = {
        "id": row["id"],
        "name": row["name"],
        "town": row["town"],
        "distance_m": row["distance_m"],
        "average_grade": row["average_grade"],
        "maximum_grade": row["maximum_grade"],
        "elevation_gain_m": row["elevation_gain_m"],
        "climb_category": row["climb_category"],
        "recent_efforts": row["recent_efforts"],
        "recent_best": row["recent_best"],
        "athlete_count": row["athlete_count"],
        "effort_count": row["effort_count"],
        "less_certain": less_certain,
        "wind_hours": [
            {
                "hour": _iso(hour["hour"]),
                "percent": hour["percent"],
                "speed_ms": hour["speed_ms"],
                "from_degrees": hour["from_degrees"],
            }
            for hour in hours
        ],
    }
    if fastest_time:
        segment["fastest_time_s"] = row["fastest_time_s"]
    return segment
