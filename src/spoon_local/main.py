"""Local dashboard and API; configuration is loaded only on app startup."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from spoon_local.config import Settings, load_settings
from spoon_local.database import initialize_database
from spoon_local.logging_config import configure_safe_access_logs
from spoon_local.spoon.auth_service import AuthService
from spoon_local.web.auth_routes import auth_router
from spoon_local.web.auth_security import AuthSecurityMiddleware

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def create_app(
    database_path: Path | None = None, *, settings: Settings | None = None,
    auth_service: AuthService | None = None
) -> FastAPI:
    if database_path is not None and settings is not None:
        raise ValueError("Provide either database_path or settings")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configuration = (
            Settings(database_path) if database_path is not None else settings
        )
        if configuration is None:
            configuration = load_settings()
        initialize_database(configuration.database_path)
        app.state.database_path = configuration.database_path
        configure_safe_access_logs()
        try:
            yield
        finally:
            if auth_service:
                auth_service.close()

    app = FastAPI(title="SPOON-BOT", lifespan=lifespan)
    app.add_middleware(AuthSecurityMiddleware)
    app.include_router(auth_router(auth_service))
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
            "broadcast": "disconnected",
            "donation_events": "not_configured",
            "capture_agent": "not_configured",
        }

    return app


app = create_app()
