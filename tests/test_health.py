import asyncio
import sqlite3

import httpx

from spoon_local.main import create_app


def test_health_and_startup_database(tmp_path):
    path = tmp_path / "data" / "test.sqlite3"
    app = create_app(path)
    assert not path.exists()

    async def check():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/health")
                assert response.status_code == 200
                assert response.json() == {"status": "ok"}
                assert app.state.database_path == path
                with sqlite3.connect(path) as connection:
                    assert connection.execute("SELECT version FROM schema_version").fetchall() == [(1,)]

    asyncio.run(check())
