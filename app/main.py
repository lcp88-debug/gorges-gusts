import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import CACHE_TTL, Settings, load_settings
from app.db import Database, note_fastest_time_setting
from app.lists import (
    CLIMB_CATEGORIES,
    DEFAULT_LIST,
    FEET_PER_METER,
    LIST_KINDS,
    METERS_PER_MILE,
    FilterError,
    ListSettings,
    TimeRangeError,
    active_fields,
    boxless_ticks,
    build_list,
    can_sort,
    choose_sort,
    filter_boxes,
    has_missing_details,
    hidden_filters,
    hour_options,
    load_list_settings,
    merge_ticks,
    parse_filter_form,
    parse_time_range,
    save_list_filters,
    save_list_settings,
    this_hour,
    tick_choices,
)
from app.maps import Maps
from app.strava import REQUIRED_SCOPE, AuthExpired, RateLimiter, StravaClient, StravaOAuth
from app.times import format_time
from app.tokens import TokenStore
from app.weather import fetch_forecasts
from app.wind import DEFAULT_POWER_20MIN_W, DEFAULT_WEIGHT_KG
from app.wind_updates import SEEN_RECENTLY, update_next_rider, wind_pending
from app.worker import Worker, enqueue_sync

MPH_PER_MS = 2.23694
WEIGHT_RANGE_KG = (30, 200)
POWER_RANGE_W = (50, 600)
COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
ACTIVE_STATUSES = ("pending", "running", "paused")

templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))
templates.env.filters["miles"] = lambda meters: (
    "–" if meters is None else f"{meters / METERS_PER_MILE:.1f} mi"
)
templates.env.filters["percent"] = lambda grade: "–" if grade is None else f"{grade:.1f}%"
templates.env.filters["count"] = lambda value: "–" if value is None else f"{value:,}"
templates.env.filters["feet"] = lambda meters: (
    "–" if meters is None else f"{meters * FEET_PER_METER:,.0f} ft"
)
# Strava's climb_category, 0 to 5, as riders know it.
templates.env.filters["climb_category"] = lambda category: (
    CLIMB_CATEGORIES[category] if category in range(len(CLIMB_CATEGORIES)) else "–"
)
templates.env.filters["blank"] = lambda value: "–" if value is None else value
templates.env.filters["localtime"] = lambda value: value.astimezone().strftime("%H:%M")


def _wind_percent(percent: float) -> str:
    rounded = round(percent)
    if rounded == 0:
        return "0%"
    return f"+{rounded}%" if rounded > 0 else f"−{-rounded}%"


def _hour(value: datetime) -> str:
    local = value.astimezone()
    return f"{local:%a} {local.hour % 12 or 12}{'am' if local.hour < 12 else 'pm'}"


templates.env.filters["duration"] = lambda seconds: "–" if seconds is None else format_time(seconds)
templates.env.filters["wind_percent"] = _wind_percent
templates.env.filters["hour"] = _hour
templates.env.filters["mph"] = lambda speed: f"{speed * MPH_PER_MS:.0f} mph"
templates.env.filters["compass"] = lambda degrees: COMPASS[round(degrees / 22.5) % 16]


def _in_range(text: str, limits: tuple[int, int]) -> float | None:
    """A number within `limits`, or None for a blank entry. Raises ValueError otherwise."""
    if not text.strip():
        return None
    value = float(text)
    if not limits[0] <= value <= limits[1]:  # Also rejects "nan".
        raise ValueError(text)
    return value


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


def create_app(
    settings: Settings | None = None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or load_settings()
    db = Database(settings.database_url)
    oauth = StravaOAuth(settings.strava_client_id, settings.strava_client_secret)
    token_store = TokenStore(db, oauth, settings.token_encryption_key)
    limiter = RateLimiter()

    def client_for(athlete_id: int) -> StravaClient:
        return StravaClient(lambda: token_store.access_token(athlete_id), limiter)

    worker = Worker(
        db,
        client_for,
        fastest_time=settings.fastest_time,
        update_wind=lambda conn, now: update_next_rider(
            conn, Maps(settings.map_dir), fetch_forecasts, now, settings.pooled_explore
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        db.init_schema()
        with db.connect() as conn:
            note_fastest_time_setting(conn, settings.fastest_time)
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware, secret_key=settings.session_secret, same_site="lax"
    )
    app.state.worker = worker

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, kind: str = Query(DEFAULT_LIST, alias="list")):
        athlete_id = request.session.get("athlete_id")
        if athlete_id is None:
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "error": request.session.pop("login_error", None),
                    "pooled": settings.pooled_explore,
                },
            )
        if kind not in LIST_KINDS:
            kind = DEFAULT_LIST

        now = datetime.now(timezone.utc)
        with db.connect() as conn:
            athlete = _record_visit(conn, request, athlete_id, now)
            if athlete is None:
                request.session.clear()
                return RedirectResponse("/", status_code=303)
            synced = athlete["last_synced_at"]
            lists_ready = synced is not None and now - synced < CACHE_TTL
            job = _latest_job(conn, athlete_id)
            # A returning visitor's data may have expired, and shared details can expire
            # before their own rides do. A failed job stays put until they press Refresh,
            # so a broken login can't retry on every page load.
            needs_sync = not lists_ready or has_missing_details(conn, athlete_id)
            if needs_sync and job is not None and job["status"] == "done":
                enqueue_sync(conn, athlete_id)
                job = _latest_job(conn, athlete_id)
            list_settings = load_list_settings(conn, athlete_id, kind, settings.fastest_time)
            time_range = saved_time_range(request, kind, now)
            # Tick boxes list the towns on the list before filtering.
            segments, whole_list, filters = build_list(
                conn,
                athlete_id,
                kind,
                list_settings,
                now,
                pooled=settings.pooled_explore,
                fastest_time=settings.fastest_time,
                time_range=time_range,
            )

        active = job is not None and job["status"] in ACTIVE_STATUSES
        # Without a worker running, nothing is going to change.
        pending = start_worker and wind_pending(athlete, now)
        # The routine hourly update stays quiet, because reloading closes open hour views.
        # Judged on the whole list, so filters that hide every segment can't stop the reload.
        has_numbers = any(segment["wind_hours"] for segment in whole_list)
        reload_for_wind = pending and bool(whole_list) and (athlete["wind_stale"] or not has_numbers)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "job": job,
                "active": active,
                "show_lists": lists_ready or (job is not None and job["status"] == "done"),
                "segments": segments,
                "list": kind,
                "fields": active_fields(settings.fastest_time),
                "fastest_time": settings.fastest_time,
                "list_settings": list_settings,
                "filters": filters,
                "filter_boxes": filter_boxes(filters, settings.fastest_time),
                "tick_choices": {
                    key: tick_choices(key, whole_list)
                    for key, field_info in active_fields(settings.fastest_time).items()
                    if field_info.kind == "ticks"
                },
                # Menus open only on the page right after the rider used them.
                "menu_used": request.session.pop("menu_used", None),
                "filter_error": request.session.pop("filter_error", None),
                "time_range": time_range,
                "hour_options": hour_options(now),
                "range_error": request.session.pop("range_error", None),
                "wind_pending": pending and bool(whole_list),
                "reload_for_wind": reload_for_wind,
                "athlete": athlete,
                "default_weight": DEFAULT_WEIGHT_KG,
                "default_power": DEFAULT_POWER_20MIN_W,
                "weight_range": WEIGHT_RANGE_KG,
                "power_range": POWER_RANGE_W,
                "profile_error": request.session.pop("profile_error", None),
                "pooled": settings.pooled_explore,
                "contributes": athlete["contributes"],
            },
        )

    @app.get("/login")
    def login(request: Request):
        state = secrets.token_urlsafe(24)
        request.session["oauth_state"] = state
        return RedirectResponse(oauth.authorize_url(settings.redirect_uri, state), status_code=303)

    @app.get("/auth/callback")
    def auth_callback(
        request: Request,
        state: str = "",
        code: str = "",
        scope: str = "",
        error: str = "",
    ):
        expected = request.session.pop("oauth_state", None)
        if not expected or not secrets.compare_digest(expected, state):
            return _login_failed(request, "That login link expired. Please try again.")
        if error or not code:
            return _login_failed(request, "Strava login was cancelled.")
        if REQUIRED_SCOPE not in scope.split(","):
            return _login_failed(
                request, "Please leave the private activities permission ticked on Strava's page."
            )
        try:
            tokens = oauth.exchange_code(code)
        except AuthExpired:
            return _login_failed(request, "Strava didn't accept that login. Please try again.")

        athlete_id = tokens["athlete"]["id"]
        token_store.save_new_athlete(athlete_id, tokens, scope)
        with db.connect() as conn:
            if settings.pooled_explore:
                # They've just seen the notice. An earlier opt-out is left alone.
                conn.execute(
                    "UPDATE athletes SET contributes = TRUE WHERE id = %s AND contributes IS NULL",
                    (athlete_id,),
                )
            enqueue_sync(conn, athlete_id)
        # Start clean, so nothing from an earlier rider on this browser carries over.
        request.session.clear()
        request.session["athlete_id"] = athlete_id
        return RedirectResponse("/", status_code=303)

    @app.post("/refresh")
    def refresh(request: Request):
        athlete_id = request.session.get("athlete_id")
        if athlete_id is not None:
            with db.connect() as conn:
                enqueue_sync(conn, athlete_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/contributing/{choice}")
    def set_contributing(request: Request, choice: Literal["on", "off"]):
        athlete_id = request.session.get("athlete_id")
        if athlete_id is not None:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE athletes SET contributes = %s WHERE id = %s",
                    (choice == "on", athlete_id),
                )
        return RedirectResponse("/", status_code=303)

    def change_list_settings(
        request: Request,
        kind: str,
        change: Callable[[ListSettings], None] | None,
        save: Callable[..., None] = save_list_settings,
    ) -> RedirectResponse:
        """Makes `change` to the rider's saved settings for list `kind`, saves them with
        `save`, then shows that list.

        An unknown list, a logged-out visitor or no `change` changes nothing.
        """
        if kind not in LIST_KINDS:
            return _show_list(DEFAULT_LIST)
        athlete_id = request.session.get("athlete_id")
        if athlete_id is not None and change is not None:
            with db.connect() as conn:
                list_settings = load_list_settings(conn, athlete_id, kind, settings.fastest_time)
                change(list_settings)
                save(conn, athlete_id, kind, list_settings)
        return _show_list(kind)

    @app.post("/sort")
    def sort(request: Request, kind: str = Form(DEFAULT_LIST, alias="list"), field: str = Form("")):
        """A click on a field's heading. Anything unknown changes nothing."""
        if not can_sort(field, settings.fastest_time):
            return change_list_settings(request, kind, None)
        return change_list_settings(request, kind, lambda chosen: choose_sort(chosen, field))

    @app.post("/filters")
    async def set_filters(request: Request):
        """The filter form: replaces the list's filters, or changes nothing if any entry is bad.

        Filters the form has no boxes for (their setting is off) stay as they were, and so
        do saved ticks it had no box for (a town that's off the list for now).
        """
        form = await request.form()
        kind = str(form.get("list"))
        request.session["menu_used"] = "filters"
        try:
            filters = parse_filter_form(
                {key: str(value) for key, value in form.items()}, settings.fastest_time
            )
        except FilterError as error:
            if kind in LIST_KINDS:
                request.session["filter_error"] = f"{error} Nothing was changed."
            return change_list_settings(request, kind, None)

        def change(chosen: ListSettings) -> None:
            boxless = boxless_ticks(chosen.filters, form, settings.fastest_time)
            chosen.filters = merge_ticks(
                {**hidden_filters(chosen.filters, settings.fastest_time), **filters}, boxless
            )

        return change_list_settings(request, kind, change, save_list_filters)

    @app.post("/filters/clear")
    def clear_filters(request: Request, kind: str = Form(DEFAULT_LIST, alias="list")):
        """Clear-all: removes the list's filters (all the rider can see) and leaves its sort."""
        request.session["menu_used"] = "filters"

        def change(chosen: ListSettings) -> None:
            chosen.filters = hidden_filters(chosen.filters, settings.fastest_time)

        return change_list_settings(request, kind, change, save_list_filters)

    @app.post("/timerange")
    def set_time_range(
        request: Request,
        kind: str = Form(DEFAULT_LIST, alias="list"),
        start: str = Form(""),
        end: str = Form(""),
    ):
        """The time range form: keeps the range for this visit, or changes nothing if it's bad."""
        request.session["menu_used"] = "time_range"
        if kind not in LIST_KINDS:
            return _show_list(DEFAULT_LIST)
        athlete_id = request.session.get("athlete_id")
        if athlete_id is not None:
            now = datetime.now(timezone.utc)
            # Setting a range is part of a visit, so the page it leads to mustn't reset it.
            with db.connect() as conn:
                _record_visit(conn, request, athlete_id, now)
            try:
                first, last = parse_time_range(start, end, now)
            except TimeRangeError as error:
                request.session["range_error"] = f"{error} Nothing was changed."
            else:
                ranges = dict(request.session.get("time_ranges", {}))
                ranges[kind] = [first.isoformat(), last.isoformat()]
                request.session["time_ranges"] = ranges
        return _show_list(kind)

    @app.post("/timerange/clear")
    def clear_time_range(request: Request, kind: str = Form(DEFAULT_LIST, alias="list")):
        """Back to all 48 hours for this list."""
        request.session["menu_used"] = "time_range"
        if kind not in LIST_KINDS:
            return _show_list(DEFAULT_LIST)
        ranges = dict(request.session.get("time_ranges", {}))
        ranges.pop(kind, None)
        request.session["time_ranges"] = ranges
        return _show_list(kind)

    @app.post("/profile")
    def save_profile(request: Request, weight_kg: str = Form(""), power_20min_w: str = Form("")):
        athlete_id = request.session.get("athlete_id")
        if athlete_id is not None:
            try:
                weight = _in_range(weight_kg, WEIGHT_RANGE_KG)
                power = _in_range(power_20min_w, POWER_RANGE_W)
            except ValueError:
                request.session["profile_error"] = (
                    f"Weight must be {WEIGHT_RANGE_KG[0]}\u2013{WEIGHT_RANGE_KG[1]} kg and power "
                    f"{POWER_RANGE_W[0]}\u2013{POWER_RANGE_W[1]} W. Nothing was changed."
                )
            else:
                with db.connect() as conn:
                    conn.execute(
                        """
                        UPDATE athletes SET weight_kg = %s, power_20min_w = %s, wind_stale = TRUE
                        WHERE id = %s
                        """,
                        (weight, power, athlete_id),
                    )
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    return app


def _show_list(kind: str) -> RedirectResponse:
    return RedirectResponse(f"/?list={kind}", status_code=303)


def _record_visit(conn, request: Request, athlete_id: int, now: datetime) -> dict | None:
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


def _latest_job(conn, athlete_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM sync_jobs WHERE athlete_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (athlete_id,),
    ).fetchone()


def _login_failed(request: Request, message: str) -> RedirectResponse:
    request.session["login_error"] = message
    return RedirectResponse("/", status_code=303)

