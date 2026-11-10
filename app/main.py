import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import Refusal, api_router, refused
from app.config import Settings, load_settings
from app.db import Database, note_fastest_time_setting
from app.maps import Maps
from app.strava import REQUIRED_SCOPE, AuthExpired, RateLimiter, StravaClient, StravaOAuth
from app.tokens import TokenStore
from app.weather import fetch_forecasts
from app.wind_updates import update_next_rider
from app.worker import Worker, enqueue_sync

# The React app, built by `npm run build` in web/ (see README.md).
WEB_BUILD = Path(__file__).resolve().parent.parent / "web" / "dist"

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Gorges Gusts</title>
<p>The app's screens haven't been built yet. Run <code>./start.sh</code>, or see README.md.</p>"""


def create_app(
    settings: Settings | None = None,
    start_worker: bool = True,
    web_build: Path = WEB_BUILD,
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
    app.add_exception_handler(Refusal, refused)
    app.include_router(api_router(db, settings, lambda: worker.running()))

    @app.get("/", response_class=HTMLResponse)
    def home():
        """The React app. It asks /api/state whether the visitor is logged in."""
        index = web_build / "index.html"
        if not index.exists():
            return HTMLResponse(NOT_BUILT, status_code=503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    class BuiltAssets(StaticFiles):
        """The build's scripts and styles. Their folder may not exist until the first build,
        and building while the server runs needs no restart."""

        async def __call__(self, scope, receive, send):
            if not (web_build / "assets").is_dir():
                await HTMLResponse("Not found", status_code=404)(scope, receive, send)
                return
            await super().__call__(scope, receive, send)

    # File names carry a hash of their contents, so a new build never reuses one.
    app.mount(
        "/assets", BuiltAssets(directory=web_build / "assets", check_dir=False), name="assets"
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

    return app


def _login_failed(request: Request, message: str) -> RedirectResponse:
    request.session["login_error"] = message
    return RedirectResponse("/", status_code=303)
