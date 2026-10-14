"""Offline Chrome form-header regressions; no browser/provider navigation."""
import asyncio
from contextlib import asynccontextmanager
import logging
import re
import socket

import httpx
import pytest

from spoon_local.main import create_app
from spoon_local.spoon.auth_service import RealAuthService
from spoon_local.spoon.oauth import OAuthSettings, LOCAL_ORIGIN
from spoon_local.spoon.real_oauth import RealOAuthClient


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Provider network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)


@asynccontextmanager
async def browser():
    settings = OAuthSettings('11111111-1111-4111-8111-111111111111', 'invented-only-secret')
    def denied(request):
        raise AssertionError('Even mock provider requests forbidden for start tests')
    auth = RealAuthService(settings, RealOAuthClient(settings, enabled=True, transport=httpx.MockTransport(denied)))
    app = create_app(auth_service=auth)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=LOCAL_ORIGIN, follow_redirects=False) as client:
            page = await client.get('/auth/spoon/prepare')
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
            yield client, page, csrf, auth


@pytest.mark.parametrize('referer', [None, LOCAL_ORIGIN + '/auth/spoon/prepare'])
def test_chrome_same_origin_form_post_passes(referer):
    async def check():
        async with browser() as (client, page, csrf, auth):
            assert page.headers['referrer-policy'] == 'same-origin'
            assert 'HttpOnly' in page.headers['set-cookie'] and 'SameSite=lax' in page.headers['set-cookie']
            headers = {'Origin': LOCAL_ORIGIN, 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Dest': 'document', 'User-Agent': 'Mozilla/5.0 Chrome/130.0.0.0'}
            if referer:
                headers['Referer'] = referer
            result = await client.post('/auth/spoon/start', data={'csrf': csrf}, headers=headers)
            assert result.status_code == 303
            assert result.headers['location'] == LOCAL_ORIGIN + '/auth/spoon/continue'
            assert result.headers['referrer-policy'] == 'no-referrer'
            assert 'x-auth-failure-code' not in result.headers
    asyncio.run(check())


@pytest.mark.parametrize('case,code', [
    ('null', 'origin_null'), ('no_origin', 'origin_missing'),
    ('ip_origin', 'origin_mismatch'), ('double_origin', 'origin_duplicate'),
    ('ip_host', 'host_mismatch'), ('double_host', 'host_duplicate'),
    ('double_cookie', 'cookie_duplicate'), ('no_cookie', 'session_cookie_missing'),
    ('wrong_cookie', 'session_mismatch'), ('no_session', 'session_unavailable'),
    ('expired', 'session_expired'), ('wrong_csrf', 'csrf_mismatch'),
    ('no_csrf', 'csrf_missing_or_duplicate'), ('double_csrf', 'csrf_missing_or_duplicate'),
    ('wrong_type', 'content_type_invalid'), ('large', 'body_too_large'),
    ('invalid_form', 'form_invalid'),
])
def test_precise_rejection_codes_without_values(case, code, caplog):
    async def check():
        async with browser() as (client, page, csrf, auth):
            headers = [('Origin', LOCAL_ORIGIN), ('Referer', LOCAL_ORIGIN + '/SECRET-REFERER?token=SECRET-TOKEN')]
            data = {'csrf': csrf}
            content = None
            if case == 'null': headers[0] = ('Origin', 'null')
            if case == 'no_origin': headers = headers[1:]
            if case == 'ip_origin': headers[0] = ('Origin', 'http://127.0.0.1:8000')
            if case == 'double_origin': headers.append(('Origin', LOCAL_ORIGIN))
            if case == 'ip_host': headers.append(('Host', '127.0.0.1:8000'))
            if case == 'double_host': headers.extend([('Host', 'localhost:8000')] * 2)
            if case == 'double_cookie': headers.append(('Cookie', 'spoon_real_session=SECRET-COOKIE; spoon_real_session=SECOND-COOKIE'))
            if case == 'no_cookie': client.cookies.clear()
            if case == 'wrong_cookie': headers.append(('Cookie', 'spoon_real_session=SECRET-COOKIE'))
            if case == 'no_session': auth.session = None
            if case == 'expired': auth.session.deadline = auth.clock() - 1
            if case == 'wrong_csrf': data = {'csrf': 'SECRET-CSRF'}
            if case == 'no_csrf': data = {'other': 'SECRET-CSRF'}
            if case == 'double_csrf': data = {'csrf': ['SECRET-CSRF', 'OTHER-CSRF']}
            if case == 'wrong_type': headers.append(('Content-Type', 'text/plain'))
            if case == 'large': data = {'csrf': csrf, 'other': 'x' * 4096}
            if case == 'invalid_form':
                content = b'\xff'
                headers.append(('Content-Type', 'application/x-www-form-urlencoded'))
            result = await client.post('/auth/spoon/start', data=data if content is None else None, content=content, headers=headers)
            assert result.status_code == 400
            assert result.headers['x-auth-failure-code'] == code
            assert auth.pending is None
            assert csrf not in caplog.text
    with caplog.at_level(logging.WARNING, logger='spoon_local.auth_diagnostics'):
        asyncio.run(check())
    records = [record for record in caplog.records if record.name == 'spoon_local.auth_diagnostics']
    assert [record.getMessage() for record in records] == ['auth_rejected:' + code]
    assert all(word not in caplog.text for word in ['SECRET-', 'invented-only-secret', 'SECOND-COOKIE'])


def test_referer_cannot_replace_origin():
    async def check():
        async with browser() as (client, page, csrf, auth):
            result = await client.post('/auth/spoon/start', data={'csrf': csrf}, headers={'Referer': LOCAL_ORIGIN + '/auth/spoon/prepare'})
            assert result.status_code == 400 and result.headers['x-auth-failure-code'] == 'origin_missing'
    asyncio.run(check())


def test_stale_start_form_when_already_authenticated():
    from spoon_local.spoon.token_store import TokenPair
    async def check():
        async with browser() as (client, page, csrf, auth):
            auth.store.replace(TokenPair.from_response({'access_token': 'invented-access', 'refresh_token': 'invented-refresh', 'token_type': 'Bearer', 'expires_in': 3600, 'scope': 'events.donation live.read'}, auth.clock()))
            result = await client.post('/auth/spoon/start', data={'csrf': csrf}, headers={'Origin': LOCAL_ORIGIN})
            assert result.status_code == 400 and result.headers['x-auth-failure-code'] == 'auth_already_active'
            assert auth.pending is None
    asyncio.run(check())


def test_handoff_refresh_back_and_duplicate_start_keep_one_transaction(caplog):
    from html import unescape
    from urllib.parse import parse_qs, urlsplit
    async def check():
        async with browser() as (client, page, csrf, auth):
            assert (await client.get('/auth/spoon/continue')).status_code == 400
            response = await client.post('/auth/spoon/start', data={'csrf': csrf}, headers={'Origin': LOCAL_ORIGIN})
            assert response.headers['location'] == LOCAL_ORIGIN + '/auth/spoon/continue'
            pending = auth.pending
            for _ in range(3):
                handoff = await client.get('/auth/spoon/continue')
                assert handoff.status_code == 200
                assert handoff.headers['cache-control'] == 'no-store'
                assert handoff.headers['referrer-policy'] == 'no-referrer'
                assert handoff.headers.get_list('content-security-policy') == [page.headers['content-security-policy']]
                assert '<form' not in handoff.text and '<script' not in handoff.text and 'http-equiv' not in handoff.text
                destination = unescape(re.search(r'id="official-oauth-link" href="([^"]+)"', handoff.text)[1])
                url = urlsplit(destination)
                assert (url.scheme, url.netloc, url.path) == ('https', 'developers.spooncast.net', '/kr/oauth/authorize')
                assert parse_qs(url.query)['state'] == [pending.state]
                assert auth.pending is pending
                assert pending.state not in caplog.text and destination not in caplog.text
            # Simulate back to the prepare page and resubmit its form.
            await client.get('/auth/spoon/prepare')
            await asyncio.gather(*[client.post('/auth/spoon/start', data={'csrf': csrf}, headers={'Origin': LOCAL_ORIGIN}) for _ in range(2)])
            assert auth.pending is pending  # No new nonce or extended deadline.
    asyncio.run(check())


@pytest.mark.parametrize('attack', ['ip_host', 'wrong_cookie', 'query_destination', 'expired', 'consumed'])
def test_handoff_rejects_invalid_transaction(attack):
    async def check():
        async with browser() as (client, page, csrf, auth):
            await client.post('/auth/spoon/start', data={'csrf': csrf}, headers={'Origin': LOCAL_ORIGIN})
            headers = {}
            path = '/auth/spoon/continue'
            if attack == 'ip_host': headers['Host'] = '127.0.0.1:8000'
            if attack == 'wrong_cookie': headers['Cookie'] = 'spoon_real_session=invented-wrong'
            if attack == 'query_destination': path += '?next=https%3A%2F%2Fattacker.invalid'
            if attack == 'expired': auth.pending.deadline = auth.clock() - 1
            if attack == 'consumed': auth.pending = None
            result = await client.get(path, headers=headers)
            assert result.status_code == 400 and 'official-oauth-link' not in result.text
    asyncio.run(check())
