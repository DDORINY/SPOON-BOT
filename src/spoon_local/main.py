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
from spoon_local.spoon.auth_service import AuthService
from spoon_local.web.auth_routes import auth_router
from spoon_local.web.auth_security import AuthSecurityMiddleware
from spoon_local.spoon.donation_repository import DonationRepository
from spoon_local.spoon.stream_service import MockStreamService
from spoon_local.spoon.sse_source import MockSSESource, demo_source
from spoon_local.spoon.oauth import OAuthSettings, MockOAuthClient

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def create_app(
    database_path: Path | None = None, *, settings: Settings | None = None,
    auth_service: AuthService | None = None,
    stream_service: MockStreamService | None = None
) -> FastAPI:
    if database_path is not None and settings is not None:
        raise ValueError("Provide either database_path or settings")

    if stream_service and (stream_service.auth is not auth_service or settings is not None or database_path is not None):
        raise ValueError("Mock SSE app must use only its dedicated repository")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if stream_service:
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

    app = FastAPI(title="SPOON-BOT", lifespan=lifespan)
    app.add_middleware(AuthSecurityMiddleware)
    app.include_router(auth_router(auth_service, stream_service))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    async def status() -> dict[str, str]:
        return {
            "server": "online",
            "spoon_auth": await auth_service.status() if auth_service else "not_configured",
            "broadcast": "mock_live" if stream_service and stream_service.state == "LIVE" else "disconnected",
            "donation_events": stream_service.state.lower() if stream_service else "not_configured",
            "capture_agent": "not_configured",
        }

    @app.get("/api/donations")
    async def donations(limit: int = Query(20, ge=1, le=100), before_sequence: int | None = Query(None, ge=1)):
        if not stream_service:
            return {"source_mode": "disabled", "items": [], "coverage": "observed_only", "observed_total_spoons": "0", "issues": {"conflicts": 0, "gaps": 0, "idless": 0}}
        try:
            result = stream_service.repository.recent(limit, before_sequence)
            result["collection_state"] = stream_service.state.lower()
            result["gap_persistence_failed"] = stream_service.gap_persistence_failed
            return result
        except (sqlite3.Error, ValueError, OSError):
            raise HTTPException(status_code=503, detail="mock_storage_unavailable") from None

    return app


def create_mock_app(mock_db: Path, source_factory: Callable[[], MockSSESource] = demo_source) -> FastAPI:
    """No dotenv loading, operational DB initialization, or real credentials."""
    repository = DonationRepository(mock_db)
    auth = AuthService(OAuthSettings.mock(), MockOAuthClient())
    stream = MockStreamService(auth, repository, source_factory)
    return create_app(auth_service=auth, stream_service=stream)


app = create_app()
