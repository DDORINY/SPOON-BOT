import asyncio

import httpx
import pytest

from spoon_local.config import Settings
from spoon_local.main import create_app


def test_status_dashboard_and_static_files(tmp_path, monkeypatch):
    def reject_user_configuration():
        raise AssertionError("Injected app must not load user's .env")

    monkeypatch.setattr("spoon_local.main.load_settings", reject_user_configuration)
    path = tmp_path / "test.sqlite3"
    app = create_app(settings=Settings(path))

    async def check():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/status")
                assert response.status_code == 200
                assert response.json() == {
                    "server": "online",
                    "spoon_auth": "not_configured",
                    "broadcast": "disconnected",
                    "donation_events": "not_configured",
                    "capture_agent": "not_configured",
                }
                html = await client.get("/")
                assert html.status_code == 200
                assert "text/html" in html.headers["content-type"]
                for label in ["DOHA-BOT", "최근 후원", "내 방송 관리", "오늘 받은 후원", "후원한 청취자 수"]:
                    assert label in html.text
                for asset, content_type in [("dashboard.css", "text/css"), ("dashboard.js", "javascript")]:
                    response = await client.get(f"/static/{asset}")
                    assert response.status_code == 200
                    assert content_type in response.headers["content-type"]
                assert (await client.get("/.env")).status_code == 404
                assert (await client.get("/static/%2e%2e/%2e%2e/config.py")).status_code == 404
                assert str(path) not in (await client.get("/api/status")).text
                assert app.state.database_path == path

    asyncio.run(check())


def test_app_loads_settings_at_startup_only(tmp_path, monkeypatch):
    calls = []
    path = tmp_path / "startup.sqlite3"

    def isolated_settings():
        calls.append(True)
        return Settings(path)

    monkeypatch.setattr("spoon_local.main.load_settings", isolated_settings)
    app = create_app()
    assert not calls
    assert not path.exists()

    async def check():
        async with app.router.lifespan_context(app):
            assert path.exists()
            assert calls == [True]

    asyncio.run(check())


def test_reject_ambiguous_settings(tmp_path):
    with pytest.raises(ValueError):
        create_app(tmp_path / "a.sqlite3", settings=Settings(tmp_path / "b.sqlite3"))
