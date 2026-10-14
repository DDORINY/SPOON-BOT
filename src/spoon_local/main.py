"""Local dashboard and API; configuration is loaded only on app startup."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
import sqlite3
from collections.abc import Callable
from fastapi.staticfiles import StaticFiles

from spoon_local.config import Settings, load_settings
from spoon_local.database import initialize_database
from spoon_local.logging_config import configure_safe_access_logs
from spoon_local.spoon.auth_service import AuthService, RealAuthService
from spoon_local.web.auth_routes import auth_router
from spoon_local.web.auth_security import AuthSecurityMiddleware
from spoon_local.spoon.donation_repository import DonationRepository
from spoon_local.spoon.stream_service import MockStreamService, RealStreamService
from spoon_local.spoon.donation_repository import RealDonationRepository
from spoon_local.spoon.real_sse import RealSSESource
from spoon_local.spoon.sse_source import MockSSESource, demo_source
from spoon_local.spoon.oauth import OAuthSettings, MockOAuthClient
from spoon_local.spoon.real_oauth import RealOAuthClient, load_real_oauth_settings
from spoon_local.web.real_auth_routes import real_auth_router

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def create_app(
    database_path: Path | None = None, *, settings: Settings | None = None,
    auth_service: AuthService | RealAuthService | None = None,
    stream_service: MockStreamService | RealStreamService | None = None
) -> FastAPI:
    if database_path is not None and settings is not None:
        raise ValueError("Provide either database_path or settings")

    if isinstance(auth_service,RealAuthService) and (settings is not None or database_path is not None or (stream_service is not None and not isinstance(stream_service, RealStreamService))):
        raise ValueError("Real auth-only mode cannot use a database or mock stream")

    if stream_service and (stream_service.auth is not auth_service or settings is not None or database_path is not None):
        raise ValueError("Mock SSE app must use only its dedicated repository")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if isinstance(auth_service,RealAuthService):
            app.state.database_path = None
        elif stream_service:
            stream_service.repository.initialize()
            app.state.database_path = stream_service.repository.path
        else:
            configuration = Settings(database_path) if database_path is not None else settings
            if configuration is None:
                configuration = load_settings()
            initialize_database(configuration.database_path)
            app.state.database_path = configuration.database_path
        configure_safe_access_logs()
        try:
            yield
        finally:
            if stream_service:
                await stream_service.close()
            if auth_service:
                auth_service.close()
                if isinstance(auth_service,RealAuthService):
                    await auth_service.client.aclose()

    app = FastAPI(title="SPOON-BOT", lifespan=lifespan)
    app.add_middleware(AuthSecurityMiddleware)
    app.include_router(real_auth_router(auth_service, stream_service) if isinstance(auth_service,RealAuthService) else auth_router(auth_service, stream_service))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    async def status() -> dict[str, str]:
        auth_state = await auth_service.status() if auth_service else "not_configured"
        if isinstance(stream_service, RealStreamService) and auth_state != "authenticated" and stream_service.task and not stream_service.task.done():
            await stream_service.stop("auth_disconnected")
        return {
            "server": "online",
            "spoon_auth": auth_state,
            "broadcast": ("real_live" if stream_service.mode == "real" else "mock_live") if stream_service and stream_service.state == "LIVE" else "disconnected",
            "donation_events": stream_service.state.lower() if stream_service else "not_configured",
            "capture_agent": "not_configured",
        }

    @app.get("/api/mode")
    async def mode():
        return {"auth_mode": auth_service.mode if auth_service else "disabled"}

    @app.get("/api/collection-mode")
    async def collection_mode():
        return {"collection_mode": stream_service.mode if stream_service else "disabled"}

    @app.get("/api/donations")
    async def donations(limit: int = Query(20, ge=1, le=100), before_sequence: int | None = Query(None, ge=1)):
        if not stream_service:
            return {"source_mode": "disabled", "items": [], "coverage": "observed_only", "observed_total_spoons": "0", "issues": {"conflicts": 0, "gaps": 0, "idless": 0}}
        try:
            if stream_service.mode == "real" and not stream_service.repository.path.exists():
                return {"source_mode": "real", "items": [], "coverage": "observed_only", "observed_total_spoons": "0", "issues": {"conflicts": 0, "gaps": 0, "idless": 0}, "collection_state": stream_service.state.lower()}
            result = stream_service.repository.recent(limit, before_sequence)
            result["collection_state"] = stream_service.state.lower()
            result["gap_persistence_failed"] = stream_service.gap_persistence_failed
            return result
        except (sqlite3.Error, ValueError, OSError):
            raise HTTPException(status_code=503, detail="real_storage_unavailable" if stream_service.mode == "real" else "mock_storage_unavailable") from None

    return app


def create_mock_app(mock_db: Path, source_factory: Callable[[], MockSSESource] = demo_source) -> FastAPI:
    """No dotenv loading, operational DB initialization, or real credentials."""
    repository = DonationRepository(mock_db)
    auth = AuthService(OAuthSettings.mock(), MockOAuthClient())
    stream = MockStreamService(auth, repository, source_factory)
    return create_app(auth_service=auth, stream_service=stream)


def create_real_app(oauth_settings: OAuthSettings | None = None, *, project_root: Path | None = None, transport=None, real_sse_db: Path | None = None, sse_transport=None) -> FastAPI:
    """Explicit Real factory: loads credentials, but never starts login or HTTP."""
    configuration = oauth_settings if oauth_settings is not None else (load_real_oauth_settings(project_root) if project_root is not None else load_real_oauth_settings())
    client = RealOAuthClient(configuration,enabled=True,transport=transport)
    auth = RealAuthService(configuration,client)
    stream = None
    if real_sse_db is not None:
        repository = RealDonationRepository(real_sse_db)
        stream = RealStreamService(auth, repository, lambda: RealSSESource(auth, transport=sse_transport))
    return create_app(auth_service=auth, stream_service=stream)


app = create_app()
