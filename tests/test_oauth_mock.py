import asyncio
from contextlib import asynccontextmanager
import logging
import re
import socket
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from spoon_local.main import create_app
from spoon_local.logging_config import SafeAccessFilter
from spoon_local.spoon.auth_service import AuthService, SESSION_TTL
from spoon_local.spoon.oauth import (
    CALLBACK_URI, LOCAL_ORIGIN, DisabledHttpOAuthClient,
    MockOAuthClient, OAuthFailure, OAuthSettings,
)
from spoon_local.web.auth_routes import COOKIE
from spoon_local.web.auth_security import AuthSecurityMiddleware


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network access forbidden in mock tests")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now


@asynccontextmanager
async def environment(tmp_path, mock=None):
    clock = Clock()
    mock = mock or MockOAuthClient(clock)
    service = AuthService(OAuthSettings.mock(), mock, clock)
    app = create_app(tmp_path / "test.sqlite3", auth_service=service)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=LOCAL_ORIGIN) as client:
            yield client, service, mock, clock


def hidden(html, name):
    return re.search(r'name="' + name + r'" value="([^"]+)"', html).group(1)


async def begin(client):
    page = await client.get("/auth/spoon/prepare")
    assert page.status_code == 200
    cookie = page.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    csrf = hidden(page.text, "csrf")
    response = await client.post("/auth/spoon/start", data={"csrf": csrf}, headers={"Origin": LOCAL_ORIGIN})
    assert response.status_code == 303
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    return csrf, state


async def authenticate(client, mock):
    csrf, state = await begin(client)
    code = mock.issue_code()
    response = await client.get("/auth/spoon/callback", params={"state": state, "code": code})
    assert response.status_code == 303
    assert response.headers["location"] == LOCAL_ORIGIN + "/"
    return csrf, state, code


def test_normal_browser_mock_flow(tmp_path):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            csrf, state = await begin(client)
            consent = await client.get("/auth/spoon/mock-consent", params={"state": state})
            assert consent.status_code == 200
            decision = await client.post("/auth/spoon/mock-consent", data={"csrf": csrf, "state": state, "decision": "allow"}, headers={"Origin": LOCAL_ORIGIN})
            assert decision.status_code == 303
            callback = await client.get(decision.headers["location"])
            assert callback.status_code == 303
            assert mock.exchange_calls == 1
            status = (await client.get("/api/status")).json()
            assert status["spoon_auth"] == "mock_authenticated"
            assert status["broadcast"] == "disconnected"
            assert "mock-access-" not in str(status)
            assert service.store.pair is not None
        assert service.store.pair is None  # Shutdown clears memory.
    asyncio.run(check())


@pytest.mark.parametrize("attack", ["missing", "changed", "unicode", "expired", "session", "no_session", "duplicate", "code_and_error"])
def test_state_and_callback_attacks(tmp_path, attack):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            csrf, state = await begin(client)
            code = mock.issue_code()
            params = {"state": state, "code": code}
            if attack == "missing": params.pop("state")
            if attack == "changed": params["state"] = "forged"
            if attack == "unicode": params["state"] = "잘못된값"
            if attack == "expired": clock.now += SESSION_TTL + 1
            if attack == "no_session": client.cookies.clear()
            if attack == "duplicate": params = [("state", state), ("state", state), ("code", code)]
            if attack == "code_and_error": params["error"] = "access_denied"
            response = await client.get("/auth/spoon/callback", params=params, headers={"Cookie": f"{COOKIE}=wrong-session"} if attack == "session" else {})
            assert response.status_code == 400
            assert mock.exchange_calls == 0
            assert service.store.pair is None
    asyncio.run(check())


def test_state_and_code_reuse_with_new_transaction(tmp_path):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            csrf, state, code = await authenticate(client, mock)
            assert (await client.get("/auth/spoon/callback", params={"state": state, "code": code})).status_code == 400
            assert mock.exchange_calls == 1
            assert (await client.post("/auth/spoon/disconnect", data={"csrf": csrf}, headers={"Origin": LOCAL_ORIGIN})).status_code == 303
            csrf, state2 = await begin(client)
            assert (await client.get("/auth/spoon/callback", params={"state": state2, "code": code})).status_code == 400
            assert mock.exchange_calls == 1
    asyncio.run(check())


@pytest.mark.parametrize("attack", ["csrf_missing", "csrf_wrong", "csrf_duplicate", "origin_missing", "origin_wrong", "host", "ip_host", "duplicate_host", "body_large"])
def test_post_security(tmp_path, attack):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            page = await client.get("/auth/spoon/prepare")
            csrf = hidden(page.text, "csrf")
            data = {"csrf": csrf}
            headers = {"Origin": LOCAL_ORIGIN}
            if attack == "csrf_missing": data = {}
            if attack == "csrf_wrong": data["csrf"] = "forged"
            if attack == "origin_missing": headers = {}
            if attack == "origin_wrong": headers["Origin"] = "https://evil.example"
            if attack == "host": headers["Host"] = "evil.example"
            if attack == "ip_host": headers["Host"] = "127.0.0.1:8000"
            if attack == "duplicate_host": headers = [("origin", LOCAL_ORIGIN), ("host", "localhost:8000"), ("host", "evil.example")]
            if attack == "body_large": data["padding"] = "a" * 5000
            if attack == "csrf_duplicate":
                response = await client.post("/auth/spoon/start", content=f"csrf={csrf}&csrf={csrf}", headers={**headers, "Content-Type": "application/x-www-form-urlencoded"})
            else:
                response = await client.post("/auth/spoon/start", data=data, headers=headers)
            assert response.status_code == 400
            assert service.pending is None
            assert mock.exchange_calls == 0
            assert (await client.get("/auth/spoon/start")).status_code == 405
            assert (await client.get("/auth/spoon/disconnect")).status_code == 405
    asyncio.run(check())


@pytest.mark.parametrize("override", [
    {"access_token": ""}, {"token_type": "Other"}, {"expires_in": 0},
    {"expires_in": True}, {"expires_in": float("nan")},
    {"expires_in": "3600"}, {"refresh_token": None}, {"scope": "live.read"},
])
def test_invalid_token_responses(tmp_path, override):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            csrf, state = await begin(client)
            mock.token_overrides = override
            response = await client.get("/auth/spoon/callback", params={"state": state, "code": mock.issue_code()})
            assert response.status_code == 400
            assert service.store.pair is None
            assert await service.status() == "reauth_required"
    asyncio.run(check())


def test_exchange_failure_not_retried(tmp_path):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            csrf, state = await begin(client)
            code = mock.issue_code()
            mock.fail_exchange = True
            for _ in range(2):
                assert (await client.get("/auth/spoon/callback", params={"state": state, "code": code})).status_code == 400
            assert mock.exchange_calls == 1
            assert service.state == "reauth_required"
    asyncio.run(check())


def test_refresh_expiration_rotation_and_single_flight(tmp_path):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            await authenticate(client, mock)
            old = service.store.pair
            assert old.expires_at == clock.now + 3600
            assert old.refresh_at == clock.now + 3300
            clock.now = old.expires_at + 1
            await asyncio.gather(*(service.status() for _ in range(10)))
            assert mock.refresh_calls == 1
            assert service.store.pair.refresh_token != old.refresh_token
            assert old.refresh_token not in mock.refresh_tokens
            assert service.store.pair.expires_at > clock.now
    asyncio.run(check())


@pytest.mark.parametrize("failure", ["provider", "scope", "same_refresh", "storage"])
def test_refresh_failure_clears_tokens_and_requires_reauth(tmp_path, failure, monkeypatch):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            await authenticate(client, mock)
            if failure == "provider": mock.fail_refresh = True
            if failure == "scope": mock.token_overrides = {"scope": "live.read"}
            if failure == "same_refresh": mock.token_overrides = {"refresh_token": service.store.pair.refresh_token}
            if failure == "storage":
                def broken(pair): raise RuntimeError("private storage details")
                monkeypatch.setattr(service.store, "replace", broken)
            clock.now = service.store.pair.refresh_at
            assert await service.status() == "reauth_required"
            assert service.store.pair is None
            await service.status()
            assert mock.refresh_calls == 1
    asyncio.run(check())


@pytest.mark.parametrize("fail", [False, True])
def test_revoke_and_local_disconnect_preserve_db(tmp_path, fail):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            csrf, state, code = await authenticate(client, mock)
            before = (tmp_path / "test.sqlite3").read_bytes()
            mock.fail_revoke = fail
            response = await client.post("/auth/spoon/disconnect", data={"csrf": csrf}, headers={"Origin": LOCAL_ORIGIN})
            assert response.status_code == (503 if fail else 303)
            assert service.state == ("revoke_unconfirmed" if fail else "mock_ready")
            assert (service.store.pair is not None) == fail
            assert mock.revoke_calls == 1
            assert (tmp_path / "test.sqlite3").read_bytes() == before
            if fail:
                await service.status()
                assert mock.refresh_calls == 0
                mock.fail_revoke = False
                assert (await client.post("/auth/spoon/disconnect", data={"csrf": csrf}, headers={"Origin": LOCAL_ORIGIN})).status_code == 303
                assert service.store.pair is None
    asyncio.run(check())


def test_sensitive_values_not_in_api_errors_or_service_logs(tmp_path, caplog):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            csrf, state = await begin(client)
            code = mock.issue_code()
            sid = client.cookies.get(COOKIE)
            sentinel = "PRIVATE-ERROR-SENTINEL"
            async def broken(*args): raise RuntimeError(sentinel)
            mock.exchange = broken
            # HTTPX is the test browser and logs outgoing query URLs at INFO.
            # Application/provider logs are captured; test-browser logs are not.
            caplog.set_level(logging.DEBUG)
            caplog.set_level(logging.WARNING, logger="httpx")
            response = await client.get("/auth/spoon/callback", params={"state": state, "code": code})
            status = await client.get("/api/status")
            for value in [state, code, sid, service.settings.client_secret, sentinel]:
                assert value not in response.text + status.text + caplog.text
            assert state not in repr(service.pending)
            assert service.settings.client_secret not in repr(service.settings)
    asyncio.run(check())


def test_access_log_filter_redacts_query():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1", "GET", "/auth/spoon/callback?code=PRIVATE&state=PRIVATE", "1.1", 303), None)
    assert SafeAccessFilter().filter(record)
    assert "PRIVATE" not in record.getMessage()
    assert "/auth/spoon/callback" in record.getMessage()


def test_middleware_redacts_original_scope_before_logging():
    scope = {"type": "http", "path": "/auth/spoon/callback", "query_string": b"code=PRIVATE&state=PRIVATE", "headers": [(b"host", b"localhost:8000")]}
    async def downstream(scope, receive, send):
        assert scope["query_string"] == b""
        assert scope["oauth_query"]["code"] == ["PRIVATE"]
    asyncio.run(AuthSecurityMiddleware(downstream)(scope, None, None))
    assert scope["query_string"] == b""


@pytest.mark.parametrize("change", [
    {"client_id": "bad"}, {"client_secret": ""}, {"client_secret": 123},
    {"client_secret": "   "}, {"region": "xx"}, {"region": []},
    {"redirect_uri": "http://127.0.0.1:8000/auth/spoon/callback"},
    {"redirect_uri": "https://evil.example"}, {"scopes": frozenset({"chat.send"})},
])
def test_oauth_configuration_validation(change):
    values = {"client_id": OAuthSettings.mock().client_id, "client_secret": OAuthSettings.mock().client_secret, **change}
    with pytest.raises(ValueError): OAuthSettings(**values)


def test_real_credentials_and_network_client_cannot_be_enabled():
    with pytest.raises(ValueError):
        AuthService(OAuthSettings(OAuthSettings.mock().client_id, "different-secret"), MockOAuthClient())
    with pytest.raises(ValueError):
        AuthService(OAuthSettings.mock(), DisabledHttpOAuthClient())
    async def check():
        client = DisabledHttpOAuthClient()
        for method, value in [(client.exchange, "code"), (client.refresh, "token"), (client.revoke, "token")]:
            with pytest.raises(OAuthFailure): await method(value, OAuthSettings.mock())
    asyncio.run(check())


def test_default_app_disabled_and_env_not_used_for_oauth(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOON_CLIENT_ID", "not-a-real-id")
    monkeypatch.setenv("SPOON_CLIENT_SECRET", "not-a-real-secret")
    app = create_app(tmp_path / "test.sqlite3")
    async def check():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=LOCAL_ORIGIN) as client:
                page = await client.get("/auth/spoon/prepare")
                assert page.status_code == 200
                assert "<form" not in page.text
                assert "set-cookie" not in page.headers
                assert (await client.get("/api/status")).json()["spoon_auth"] == "not_configured"
    asyncio.run(check())


def test_code_expiry_with_valid_state(tmp_path):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            csrf, state = await begin(client)
            code = mock.issue_code()
            clock.now += 61
            assert (await client.get("/auth/spoon/callback", params={"state": state, "code": code})).status_code == 400
            assert mock.exchange_calls == 1
            assert service.state == "reauth_required"
    asyncio.run(check())


def test_deny_consent_consumes_state(tmp_path):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            csrf, state = await begin(client)
            response = await client.post("/auth/spoon/mock-consent", data={"csrf": csrf, "state": state, "decision": "deny"}, headers={"Origin": LOCAL_ORIGIN})
            assert response.status_code == 303
            assert (await client.get(response.headers["location"])).status_code == 400
            assert service.pending is None
            assert service.state == "mock_ready"
            assert mock.exchange_calls == 0
    asyncio.run(check())


def test_duplicate_cookies_and_origin_rejected(tmp_path):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            page = await client.get("/auth/spoon/prepare")
            csrf = hidden(page.text, "csrf")
            sid = client.cookies.get(COOKIE)
            response = await client.post("/auth/spoon/start", data={"csrf": csrf}, headers={"Origin": LOCAL_ORIGIN, "Cookie": f"{COOKIE}={sid}; {COOKIE}=forged"})
            assert response.status_code == 400
            response = await client.post("/auth/spoon/start", data={"csrf": csrf}, headers=[("Origin", LOCAL_ORIGIN), ("Origin", "https://evil.example")])
            assert response.status_code == 400
            assert service.pending is None
    asyncio.run(check())


def test_authenticated_tokens_not_exposed_in_api_or_repr(tmp_path):
    async def check():
        async with environment(tmp_path) as (client, service, mock, clock):
            await authenticate(client, mock)
            pair = service.store.pair
            text = (await client.get("/api/status")).text + (await client.get("/")).text
            for secret in [pair.access_token, pair.refresh_token, service.settings.client_secret]:
                assert secret not in text
                assert secret not in repr(pair)
            assert (await client.get("/.env")).status_code == 404
    asyncio.run(check())


def test_concurrent_callbacks_exchange_only_once(tmp_path):
    class SlowMock(MockOAuthClient):
        async def exchange(self, code, settings):
            await asyncio.sleep(0.01)
            return await super().exchange(code, settings)
    async def check():
        async with environment(tmp_path, SlowMock()) as (client, service, mock, clock):
            csrf, state = await begin(client)
            code = mock.issue_code()
            results = await asyncio.gather(*(client.get("/auth/spoon/callback", params={"state": state, "code": code}) for _ in range(5)))
            assert sorted(result.status_code for result in results) == [303, 400, 400, 400, 400]
            assert mock.exchange_calls == 1
    asyncio.run(check())


def test_concurrent_refresh_waiters_share_one_result(tmp_path):
    class SlowMock(MockOAuthClient):
        async def refresh(self, token, settings):
            await asyncio.sleep(0.01)
            return await super().refresh(token, settings)
    async def check():
        async with environment(tmp_path, SlowMock()) as (client, service, mock, clock):
            await authenticate(client, mock)
            clock.now = service.store.pair.refresh_at
            results = await asyncio.gather(*(service.status() for _ in range(5)))
            assert results == ["mock_authenticated"] * 5
            assert mock.refresh_calls == 1
    asyncio.run(check())
