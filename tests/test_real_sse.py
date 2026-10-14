"""Offline real-mode coverage: invented tokens, temp ledgers, blocked sockets."""
import asyncio
from dataclasses import replace
import json
import logging
import re
import socket
import sqlite3
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from spoon_local.main import create_real_app
from spoon_local.spoon.auth_service import RealAuthService, AuthService
from spoon_local.spoon.donation_repository import DonationRepository, RealDonationRepository
from spoon_local.spoon.oauth import OAuthSettings, MockOAuthClient, LOCAL_ORIGIN
from spoon_local.spoon.real_oauth import RealOAuthClient
from spoon_local.spoon.real_sse import RealSSESource
from spoon_local.spoon.stream_service import RealStreamService, MockStreamService
from spoon_local.spoon.token_store import TokenPair

SETTINGS = OAuthSettings('11111111-1111-4111-8111-111111111111', 'invented-test-secret')
TOKEN = {'access_token': 'invented-access-SENTINEL', 'refresh_token': 'invented-refresh-SENTINEL', 'token_type': 'Bearer', 'expires_in': 3600, 'scope': 'events.donation live.read'}


@pytest.fixture(autouse=True)
def blocked(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('External network or real settings forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr('spoon_local.main.load_settings', deny)
    monkeypatch.setattr('spoon_local.main.initialize_database', deny)
    monkeypatch.setattr('spoon_local.main.load_real_oauth_settings', deny)


def wire(identifier='1000-0', amount=1.25):
    payload = {'user': {'id': 'PRIVATE-USER', 'nickname': '<img onerror=alert(1)>'}, 'amount': amount, 'message': 'PRIVATE-MESSAGE 한글', 'time': '2026-10-14T00:00:00Z'}
    return ((f'id:{identifier}\r\n' if identifier else '') + 'event:donation\r\ndata:' + json.dumps(payload, ensure_ascii=False) + '\r\n\r\n').encode()


def end(reason):
    return ('event:end\ndata:' + json.dumps({'reason': reason}) + '\n\n').encode()


class Bytes(httpx.AsyncByteStream):
    def __init__(self, data=b'', hold=False):
        self.data, self.hold, self.closed = data, hold, False

    async def __aiter__(self):
        for index in range(0, len(self.data), 7):
            yield self.data[index:index + 7]
            await asyncio.sleep(0)
        while self.hold:
            await asyncio.sleep(10)
            yield b':heartbeat\n\n'

    async def aclose(self):
        self.closed = True


def response(data=b'', hold=False):
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Bytes(data, hold))


def authorized(auth):
    auth.store.replace(TokenPair.from_response(TOKEN, auth.clock()))
    auth.state = 'authenticated'


def service(tmp_path, handler, oauth=None):
    auth = RealAuthService(SETTINGS, RealOAuthClient(SETTINGS, enabled=True, transport=httpx.MockTransport(oauth or (lambda r: httpx.Response(200, json={**TOKEN, 'refresh_token': 'invented-refresh-rotated'})))))
    authorized(auth)
    repo = RealDonationRepository(tmp_path / 'real-sse.sqlite3')
    delays = []
    async def sleep(delay):
        delays.append(delay)
        await asyncio.sleep(.002)
    collector = RealStreamService(auth, repo, lambda: RealSSESource(auth, transport=httpx.MockTransport(handler)), sleep=sleep, jitter=lambda: 0)
    return collector, auth, repo, delays


async def until(predicate):
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(.002)
    raise AssertionError('Collector did not reach expected state')


async def close(collector, auth):
    await collector.close()
    auth.close()
    await auth.client.aclose()


def test_real_get_bearer_fragmented_storage_privacy_and_explicit_start(tmp_path, caplog):
    calls, streams = [], []
    def handler(request):
        calls.append(request)
        result = response(wire() + wire() + wire(amount=2) + wire(None) + wire(None), True)
        streams.append(result.stream)
        return result
    async def check():
        collector, auth, repo, delays = service(tmp_path, handler)
        assert not repo.path.exists() and calls == []
        assert await collector.start()
        assert not await collector.start()
        await until(lambda: collector.counts['inserted'] == 3)
        await collector.stop()
        assert streams[0].closed
        assert collector.counts['duplicate'] == 1 and collector.counts['conflict'] == 1
        request = calls[0]
        assert str(request.url) == 'https://kr-openapi.spooncast.net/v1/live/events'
        assert request.method == 'GET' and not request.content
        assert request.headers['authorization'] == 'Bearer ' + TOKEN['access_token']
        assert request.headers['accept'] == 'text/event-stream'
        assert 'last-event-id' not in request.headers
        data = repo.recent()
        assert data['source_mode'] == 'real' and data['coverage'] == 'observed_only'
        assert data['observed_total_spoons'] == '3.75'
        assert data['issues']['idless'] == 2 and data['issues']['gaps'] >= 1
        assert 'PRIVATE' not in json.dumps(data)
        assert b'PRIVATE' not in repo.path.read_bytes()
        with repo.connection() as db:
            assert db.execute('SELECT DISTINCT source_mode,integration_key FROM donations').fetchall() == [('real', 'real-single-dj')]
        await close(collector, auth)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(check())
    assert 'PRIVATE' not in caplog.text and 'SENTINEL' not in caplog.text


@pytest.mark.parametrize('status,code,state', [(403, None, 'PERMISSION_REQUIRED'), (403, 'OAPI_MNGR_0209', 'BLOCKED'), (400, None, 'STOPPED'), (404, None, 'STOPPED'), (302, None, 'STOPPED')])
def test_terminal_errors_no_redirect(tmp_path, status, code, state):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, content=('data:' + json.dumps({'detailCode': code, 'message': 'PRIVATE-MESSAGE 한글'})).encode(), headers={'location': 'https://attacker.invalid'})
    async def check():
        collector, auth, repo, _ = service(tmp_path, handler)
        await collector.start()
        await until(lambda: collector.task.done())
        assert collector.state == state and len(calls) == 1
        await close(collector, auth)
    asyncio.run(check())


@pytest.mark.parametrize('first,expected', [('404', 5), ('429', 2), ('502', 1), ('eof', 1), ('timeout', 1), ('RECONNECT', None), ('LIVE_ENDED', 5)])
def test_reconnect_gap_backoff(tmp_path, first, expected):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) > 1:
            return response(wire(), True)
        if first == 'timeout':
            raise httpx.ReadTimeout('PRIVATE-TRANSPORT', request=request)
        if first == '404':
            return httpx.Response(404, json={'detailCode': 'OAPI_MNGR_0301'})
        if first in {'429', '502'}:
            return httpx.Response(int(first), headers={'retry-after': '2'})
        return response(end(first) if first != 'eof' else b'')
    async def check():
        collector, auth, repo, delays = service(tmp_path, handler)
        await collector.start()
        await until(lambda: collector.counts['inserted'] == 1)
        assert len(calls) == 2
        if expected is not None:
            assert delays[0] == expected
        else:
            assert delays == []
        await collector.stop()
        assert repo.recent()['issues']['gaps'] >= 1
        await close(collector, auth)
    asyncio.run(check())


@pytest.mark.parametrize('expired', [False, True])
def test_401_or_token_expiry_rotates_once(tmp_path, expired):
    calls, refreshes = [], []
    def handler(request):
        calls.append(request.headers['authorization'])
        if len(calls) == 1:
            return response(end('TOKEN_EXPIRED')) if expired else httpx.Response(401)
        return response(wire(), True)
    def oauth(request):
        refreshes.append(request)
        return httpx.Response(200, json={**TOKEN, 'access_token': 'new-invented-access', 'refresh_token': 'new-invented-refresh'})
    async def check():
        collector, auth, repo, _ = service(tmp_path, handler, oauth)
        await collector.start()
        await until(lambda: collector.counts['inserted'] == 1)
        assert len(refreshes) == 1 and calls[-1] == 'Bearer new-invented-access'
        assert auth.store.pair.refresh_token == 'new-invented-refresh'
        await close(collector, auth)
    asyncio.run(check())


def test_rotation_identity_avoids_double_refresh(tmp_path):
    calls = []
    def oauth(request):
        calls.append(request)
        return httpx.Response(200, json={**TOKEN, 'refresh_token': 'new-invented-refresh'})
    async def check():
        collector, auth, _, _ = service(tmp_path, lambda r: response(), oauth)
        source = RealSSESource(auth, transport=httpx.MockTransport(lambda r: response()))
        connection = await source.open()
        await auth.ensure_access(force_refresh=True)
        assert await collector._refresh(source)
        assert len(calls) == 1
        await connection.close()
        await source.aclose()
        await close(collector, auth)
    asyncio.run(check())


@pytest.mark.parametrize('failure', ['refresh', 'second401'])
def test_failed_auth_stops_without_loop(tmp_path, failure):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401)
    def oauth(request):
        return httpx.Response(400) if failure == 'refresh' else httpx.Response(200, json={**TOKEN, 'refresh_token': 'new-invented-refresh'})
    async def check():
        collector, auth, _, _ = service(tmp_path, handler, oauth)
        await collector.start()
        await until(lambda: collector.task.done())
        assert collector.state == 'AUTH_REQUIRED'
        assert len(calls) == (1 if failure == 'refresh' else 2)
        await close(collector, auth)
    asyncio.run(check())


def test_missing_scope_no_connection_or_database(tmp_path):
    async def check():
        collector, auth, repo, _ = service(tmp_path, lambda r: pytest.fail('Connected without scope'))
        auth.store.replace(replace(auth.store.pair, scopes=frozenset({'live.read'})))
        assert not await collector.start() and collector.state == 'PERMISSION_REQUIRED'
        assert not repo.path.exists()
        await close(collector, auth)
    asyncio.run(check())


def test_real_mock_marker_and_unmarked_db_protection(tmp_path):
    real = RealDonationRepository(tmp_path / 'real-sse.sqlite3')
    mock = DonationRepository(tmp_path / 'mock-sse.sqlite3')
    mock.initialize()
    real.path.write_bytes(mock.path.read_bytes())
    before = real.path.read_bytes()
    with pytest.raises(ValueError):
        real.initialize()
    assert real.path.read_bytes() == before
    other = tmp_path / 'other' / 'real-sse.sqlite3'
    other.parent.mkdir()
    with sqlite3.connect(other) as db:
        db.execute('CREATE TABLE operational (id INTEGER)')
    before = other.read_bytes()
    with pytest.raises(ValueError):
        RealDonationRepository(other).initialize()
    assert other.read_bytes() == before
    with pytest.raises(ValueError):
        RealDonationRepository(mock.path)


def test_sqlite_writer_failure_is_terminal(tmp_path, monkeypatch):
    async def check():
        collector, auth, repo, _ = service(tmp_path, lambda r: response(wire(), True))
        def fail(*args):
            raise sqlite3.OperationalError('PRIVATE-DATABASE')
        monkeypatch.setattr(repo, 'append', fail)
        await collector.start()
        await until(lambda: collector.task.done())
        assert collector.state == 'STORAGE_ERROR'
        assert repo.recent()['items'] == [] and repo.recent()['issues']['gaps'] >= 1
        await close(collector, auth)
    asyncio.run(check())


def test_dashboard_query_no_autoconnect_and_confirmed_post_logout(tmp_path):
    calls, streams = [], []
    def handler(request):
        calls.append(request)
        result = response(wire(), True)
        streams.append(result.stream)
        return result
    async def check():
        path = tmp_path / 'real-sse.sqlite3'
        app = create_real_app(SETTINGS, transport=httpx.MockTransport(lambda r: httpx.Response(200) if r.url.path.endswith('revoke') else httpx.Response(200, json=TOKEN)), real_sse_db=path, sse_transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=LOCAL_ORIGIN) as browser:
                prepare = await browser.get('/auth/spoon/prepare')
                csrf = re.search(r'name="csrf" value="([^"]+)"', prepare.text)[1]
                login = await browser.post('/auth/spoon/start', data={'csrf': csrf}, headers={'Origin': LOCAL_ORIGIN})
                handoff = await browser.get(login.headers['location'])
                from html import unescape
                destination = unescape(re.search(r'id="official-oauth-link" href="([^"]+)"', handoff.text)[1])
                state = parse_qs(urlsplit(destination).query)['state'][0]
                assert (await browser.get('/auth/spoon/callback', params={'state': state, 'code': 'invented-code'})).status_code == 303
                for url in ['/', '/api/status', '/api/donations', '/api/collection-mode', '/auth/spoon/collection']:
                    assert (await browser.get(url)).status_code == 200
                assert calls == [] and not path.exists()
                page = await browser.get('/auth/spoon/collection')
                csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
                action = '/auth/spoon/collection/start'
                assert (await browser.get(action)).status_code == 405
                for values, origin in [({'csrf': csrf}, LOCAL_ORIGIN), ({'csrf': csrf, 'dj_notice': 'confirmed'}, 'http://evil.invalid'), ({'csrf': 'bad', 'dj_notice': 'confirmed'}, LOCAL_ORIGIN)]:
                    assert (await browser.post(action, data=values, headers={'Origin': origin})).status_code == 400
                assert not calls and not path.exists()
                assert (await browser.post(action, data={'csrf': csrf, 'dj_notice': 'confirmed'}, headers={'Origin': LOCAL_ORIGIN})).status_code == 303
                await until(lambda: path.exists())
                assert (await browser.post('/auth/spoon/history/allow',data={'csrf':csrf,'local_owner':'confirmed'},headers={'Origin':LOCAL_ORIGIN})).status_code==200
                for _ in range(100):
                    data = (await browser.get('/api/donations')).json()
                    if data['items']:
                        break
                    await asyncio.sleep(.002)
                assert data['source_mode'] == 'real' and data['items']
                assert 'PRIVATE' not in json.dumps(data)
                assert (await browser.get('/api/status')).json()['broadcast'] == 'real_live'
                assert (await browser.post('/auth/spoon/disconnect', data={'csrf': csrf}, headers={'Origin': LOCAL_ORIGIN})).status_code == 303
                assert streams[0].closed
                assert (await browser.get('/api/status')).json()['spoon_auth'] == 'real_ready'
                assert (await browser.get('/api/status')).json()['donation_events'] == 'auth_required'
    asyncio.run(check())


def test_restart_preserves_records_and_dedup_salt(tmp_path):
    async def check():
        for cycle in range(2):
            collector, auth, repo, _ = service(tmp_path, lambda r: response(wire(), True))
            await collector.start()
            await until(lambda: collector.counts['inserted'] + collector.counts['duplicate'] == 1)
            assert collector.counts['inserted'] == (1 if cycle == 0 else 0)
            await close(collector, auth)
        assert len(repo.recent()['items']) == 1
        with repo.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM collection_gaps WHERE reason_code='collection_started'").fetchone()[0] == 2
    asyncio.run(check())


def test_real_mock_controller_cannot_mix(tmp_path):
    auth = AuthService(OAuthSettings.mock(), MockOAuthClient())
    with pytest.raises(ValueError):
        MockStreamService(auth, RealDonationRepository(tmp_path / 'real-sse.sqlite3'), lambda: None)
    with pytest.raises(ValueError):
        RealSSESource(auth)


def test_invalid_payload_frame_and_content_type(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={'content-type': 'text/html'}, content='PRIVATE-MESSAGE')
        if len(calls) == 2:
            return response(b'event:donation\ndata:{\n\n' + b'\xff')
        return response(wire('1001-0'), True)
    async def check():
        collector, auth, repo, _ = service(tmp_path, handler)
        await collector.start()
        await until(lambda: collector.counts['inserted'] == 1)
        assert len(calls) == 3
        assert repo.recent()['issues']['gaps'] >= 2
        await close(collector, auth)
    asyncio.run(check())


def test_auth_cleared_while_receiving_stops_collection(tmp_path):
    stream = Bytes(wire(), True)
    async def check():
        collector, auth, repo, _ = service(tmp_path, lambda r: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream))
        await collector.start()
        await until(lambda: collector.counts['inserted'] == 1)
        # Same ordering as the logout route: close the SSE before revoking tokens.
        await collector.stop('auth_disconnected')
        await auth.disconnect()
        assert stream.closed and collector.task.done() and auth.store.pair is None
        assert collector.state == 'AUTH_REQUIRED'
        await close(collector, auth)
    asyncio.run(check())


@pytest.mark.parametrize('argument', ['--mock-auth', '--real-sse-db'])
def test_real_db_flag_needs_real_oauth(monkeypatch, tmp_path, argument):
    from spoon_local.__main__ import main
    monkeypatch.setattr('sys.argv', ['spoon_local', argument, '--real-sse-db', str(tmp_path / 'real-sse.sqlite3')] if argument == '--mock-auth' else ['spoon_local', '--real-sse-db', str(tmp_path / 'real-sse.sqlite3')])
    with pytest.raises(SystemExit) as failure:
        main()
    assert failure.value.code == 2


def test_real_transport_error_body_bound_and_cookie_drop():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(403, content=b'data:' + b'x' * 70000, headers={'set-cookie': 'provider_cookie=PRIVATE; Path=/'})
        return response()
    async def check():
        auth = RealAuthService(SETTINGS, RealOAuthClient(SETTINGS, enabled=True, transport=httpx.MockTransport(lambda r: httpx.Response(200))))
        authorized(auth)
        source = RealSSESource(auth, transport=httpx.MockTransport(handler))
        first = await source.open()
        assert first.status == 403 and first.detail_code is None
        second = await source.open()
        assert 'cookie' not in calls[-1].headers
        await first.close()
        await second.close()
        await source.aclose()
        await auth.client.aclose()
    asyncio.run(check())


def test_real_http_read_error_is_safe(tmp_path, caplog):
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b':heartbeat\n\n'
            raise httpx.ReadError('PRIVATE-ERROR-SENTINEL')
        async def aclose(self):
            pass
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Broken()) if len(calls) == 1 else response(wire(), True)
    async def check():
        collector, auth, repo, delays = service(tmp_path, handler)
        await collector.start()
        await until(lambda: collector.counts['inserted'] == 1)
        assert delays[0] == 1 and len(calls) == 2
        await close(collector, auth)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(check())
    assert 'PRIVATE-ERROR-SENTINEL' not in caplog.text
