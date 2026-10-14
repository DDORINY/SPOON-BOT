"""FastAPI entry point; initialize SQLite during application startup."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from spoon_local.config import get_database_path
from spoon_local.database import initialize_database


def create_app(database_path: Path | None = None) -> FastAPI:
    path = database_path if database_path is not None else get_database_path()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize_database(path)
        app.state.database_path = path
        yield

    app = FastAPI(title="SPOON Local", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
