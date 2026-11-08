import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import CACHE_TTL, Settings, load_settings
from app.db import Database
from app.lists import (
    DEFAULT_LIST,
    FEET_PER_METER,
    FIELDS,
    LIST_KINDS,
    METERS_PER_MILE,
    FilterError,
    TimeRangeError,
    build_list,
    can_sort,
    choose_sort,
    CLIMB_CATEGORIES,
    filter_boxes,
    has_missing_details,
    hour_options,
    load_list_settings,
    parse_filter_form,
    parse_time_range,
    segment_list,
    tick_choices,
    save_list_settings,
)
from app.maps import Maps
from app.strava import REQUIRED_SCOPE, AuthExpired, RateLimiter, StravaClient, StravaOAuth
from app.tokens import TokenStore
from app.weather import fetch_forecasts
from app.wind import DEFAULT_POWER_20MIN_W, DEFAULT_WEIGHT_KG
from app.wind_updates import SEEN_RECENTLY, update_next_rider, wind_pending
from app.worker import Worker, enqueue_sync

# Strava's climb_category, 0 to 5, as riders know it.
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
templates.env.filters["climb_category"] = lambda category: (
    CLIMB_CATEGORIES[category] if category in range(len(CLIMB_CATEGORIES)) else "–"
)
templates.env.filters["blank"] = lambda value: "–" if value is None else value
templates.env.filters["localtime"] = lambda value: value.astimezone().strftime("%H:%M")


def _wind_percent(percent: float) -> str:
    rounded = round(percent)
    if rounded == 0:
        return "0%"
    return f"+{rounded}%" if rounded > 0 else f"\u2212{-rounded}%"


def _hour(value: datetime) -> str:
    local = value.astimezone()
    return f"{local:%a} {local.hour % 12 or 12}{'am' if local.hour < 12 else 'pm'}"


def _duration(seconds: int | None) -> str:
    """m:ss, or h:mm:ss for an hour or more."""
    if seconds is None:
        return "–"
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


templates.env.filters["duration"] = _duration
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


def saved_time_range(request: Request, list: str):
    """The list's time range kept in the rider's session, or None for all 48 hours."""
    try:
        first, last = request.session.get("time_ranges", {})[list]
        return datetime.fromisoformat(first), datetime.fromisoformat(last)
    except (KeyError, TypeError, ValueError):
        return None


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
        update_wind=lambda conn, now: update_next_rider(
            conn, Maps(settings.map_dir), fetch_forecasts, now, settings.pooled_explore
        ),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        db.init_schema()
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
    def home(request: Request, list: str = DEFAULT_LIST):
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
        if list not in LIST_KINDS:
            list = DEFAULT_LIST

        now = datetime.now(timezone.utc)
        with db.connect() as conn:
            # Read before it's updated: the time range resets after more than an hour away.
            before = conn.execute(
                "SELECT last_seen_at FROM athletes WHERE id = %s", (athlete_id,)
            ).fetchone()
            if before is None or before["last_seen_at"] is None or (
                now - before["last_seen_at"] >= SEEN_RECENTLY
            ):
                request.session.pop("time_ranges", None)
            athlete = conn.execute(
                """
                UPDATE athletes SET last_seen_at = %s WHERE id = %s
                RETURNING last_synced_at, contributes, weight_kg, power_20min_w,
                          wind_stale, wind_started_at, wind_updated_at
                """,
                (now, athlete_id),
            ).fetchone()
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
            list_settings = load_list_settings(conn, athlete_id, list)
            time_range = saved_time_range(request, list)
            segments = build_list(
                conn,
                athlete_id,
                list,
                list_settings,
                now,
                pooled=settings.pooled_explore,
                time_range=time_range,
            )
            # Tick boxes list what is on the list before filtering, so a ticked town can be unticked.
            whole_list = segment_list(conn, athlete_id, list, now, settings.pooled_explore)

        active = job is not None and job["status"] in ACTIVE_STATUSES
        # Without a worker running, nothing is going to change.
        pending = start_worker and wind_pending(athlete, now)
        # The routine hourly update stays quiet, because reloading closes open hour views.
        has_numbers = any(segment["wind_hours"] for segment in segments)
        reload_for_wind = pending and bool(segments) and (athlete["wind_stale"] or not has_numbers)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "job": job,
                "active": active,
                "show_lists": lists_ready or (job is not None and job["status"] == "done"),
                "segments": segments,
                "list": list,
                "fields": FIELDS,
                "list_settings": list_settings,
                "filter_boxes": filter_boxes(list_settings.filters),
                "tick_choices": {
                    key: tick_choices(key, whole_list, list_settings.filters.get(key))
                    for key, field_info in FIELDS.items()
                    if field_info.kind == "ticks"
                },
                "filter_error": request.session.pop("filter_error", None),
                "time_range": time_range,
                "hour_options": hour_options(now),
                "range_error": request.session.pop("range_error", None),
                "wind_pending": pending,
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

    @app.post("/sort")
    def sort(request: Request, list: str = Form(DEFAULT_LIST), field: str = Form("")):
        """A click on a field's heading. Anything unknown changes nothing."""
        athlete_id = request.session.get("athlete_id")
        if list not in LIST_KINDS:
            list = DEFAULT_LIST
        elif athlete_id is not None and can_sort(field):
            with db.connect() as conn:
                list_settings = load_list_settings(conn, athlete_id, list)
                choose_sort(list_settings, field)
                save_list_settings(conn, athlete_id, list, list_settings)
        return RedirectResponse(f"/?list={list}", status_code=303)

    @app.post("/filters")
    async def set_filters(request: Request):
        """The filter form: replaces the list's filters, or changes nothing if any entry is bad."""
        form = await request.form()
        list = form.get("list")
        athlete_id = request.session.get("athlete_id")
        if list not in LIST_KINDS:
            return RedirectResponse(f"/?list={DEFAULT_LIST}", status_code=303)
        if athlete_id is not None:
            try:
                filters = parse_filter_form({key: str(value) for key, value in form.items()})
            except FilterError as error:
                request.session["filter_error"] = f"{error} Nothing was changed."
            else:
                with db.connect() as conn:
                    list_settings = load_list_settings(conn, athlete_id, list)
                    list_settings.filters = filters
                    save_list_settings(conn, athlete_id, list, list_settings)
        return RedirectResponse(f"/?list={list}", status_code=303)

    @app.post("/filters/clear")
    def clear_filters(request: Request, list: str = Form(DEFAULT_LIST)):
        """Clear-all: removes the list's filters and leaves its sort alone."""
        athlete_id = request.session.get("athlete_id")
        if list not in LIST_KINDS:
            list = DEFAULT_LIST
        elif athlete_id is not None:
            with db.connect() as conn:
                list_settings = load_list_settings(conn, athlete_id, list)
                list_settings.filters = {}
                save_list_settings(conn, athlete_id, list, list_settings)
        return RedirectResponse(f"/?list={list}", status_code=303)

    @app.post("/timerange")
    def set_time_range(
        request: Request, list: str = Form(DEFAULT_LIST), start: str = Form(""), end: str = Form("")
    ):
        """The time range form: keeps the range for this visit, or changes nothing if it's bad."""
        athlete_id = request.session.get("athlete_id")
        if list not in LIST_KINDS:
            list = DEFAULT_LIST
        elif athlete_id is not None:
            try:
                first, last = parse_time_range(start, end, datetime.now(timezone.utc))
            except TimeRangeError as error:
                request.session["range_error"] = f"{error} Nothing was changed."
            else:
                ranges = dict(request.session.get("time_ranges", {}))
                ranges[list] = [first.isoformat(), last.isoformat()]
                request.session["time_ranges"] = ranges
        return RedirectResponse(f"/?list={list}", status_code=303)

    @app.post("/timerange/clear")
    def clear_time_range(request: Request, list: str = Form(DEFAULT_LIST)):
        """Back to all 48 hours for this list."""
        if list not in LIST_KINDS:
            list = DEFAULT_LIST
        else:
            ranges = dict(request.session.get("time_ranges", {}))
            ranges.pop(list, None)
            request.session["time_ranges"] = ranges
        return RedirectResponse(f"/?list={list}", status_code=303)

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


def _latest_job(conn, athlete_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM sync_jobs WHERE athlete_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (athlete_id,),
    ).fetchone()


def _login_failed(request: Request, message: str) -> RedirectResponse:
    request.session["login_error"] = message
    return RedirectResponse("/", status_code=303)

