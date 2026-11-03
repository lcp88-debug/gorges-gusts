import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import CACHE_TTL, Settings, load_settings
from app.db import Database
from app.lists import DEFAULT_SORT, SORT_ORDERS, has_missing_details, segment_list
from app.strava import REQUIRED_SCOPE, AuthExpired, RateLimiter, StravaClient, StravaOAuth
from app.tokens import TokenStore
from app.worker import Worker, enqueue_sync

METERS_PER_MILE = 1609.34
ACTIVE_STATUSES = ("pending", "running", "paused")

templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))
templates.env.filters["miles"] = lambda meters: (
    "–" if meters is None else f"{meters / METERS_PER_MILE:.1f} mi"
)
templates.env.filters["percent"] = lambda grade: "–" if grade is None else f"{grade:.1f}%"
templates.env.filters["count"] = lambda value: "–" if value is None else f"{value:,}"
templates.env.filters["localtime"] = lambda value: value.astimezone().strftime("%H:%M")


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

    worker = Worker(db, client_for)

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
    def home(request: Request, sort: str = DEFAULT_SORT):
        athlete_id = request.session.get("athlete_id")
        if athlete_id is None:
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "error": request.session.pop("login_error", None),
                    "pooled": settings.pooled_most_popular,
                },
            )
        if sort not in SORT_ORDERS:
            sort = DEFAULT_SORT

        now = datetime.now(timezone.utc)
        with db.connect() as conn:
            athlete = conn.execute(
                "SELECT last_synced_at, contributes FROM athletes WHERE id = %s", (athlete_id,)
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
            segments = segment_list(
                conn, athlete_id, sort, now, pooled=settings.pooled_most_popular
            )

        active = job is not None and job["status"] in ACTIVE_STATUSES
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "job": job,
                "active": active,
                "show_lists": lists_ready or (job is not None and job["status"] == "done"),
                "segments": segments,
                "sort": sort,
                "pooled": settings.pooled_most_popular,
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
            if settings.pooled_most_popular:
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

