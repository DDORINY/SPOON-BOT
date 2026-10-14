"""Presentation regressions with fake sessions; no actual provider requests."""
import asyncio
from html import escape
import re

import pytest

from test_auth_diagnostics import browser, deny_network
from spoon_local.spoon.oauth import LOCAL_ORIGIN
from spoon_local.web.oauth_ui import render_auth_page


def test_prepare_design_form_css_and_csp():
    async def check():
        async with browser() as (client, page, csrf, auth):
            assert page.status_code == 200
            for text in ['방송을 더 즐겁게', 'SPOON으로 로그인', 'DJ STREAM ASSISTANT', 'Local Edition', '계정 미연결']:
                assert text in page.text
            assert '<form method="post" action="/auth/spoon/start">' in page.text
            assert f'name="csrf" value="{csrf}"' in page.text
            assert 'name="viewport"' in page.text
            assert 'href="/static/oauth.css"' in page.text
            csp = page.headers['content-security-policy']
            for directive in ["default-src 'none'", "style-src 'self'", "form-action 'self' https://developers.spooncast.net", "frame-ancestors 'none'", "base-uri 'none'"]:
                assert directive in csp
            assert 'unsafe-inline' not in csp and 'unsafe-eval' not in csp
            css = await client.get('/static/oauth.css')
            assert css.status_code == 200 and css.headers['content-type'].startswith('text/css')
            assert '@import' not in css.text and 'url(' not in css.text
            assert ':focus-visible' in css.text and '@media' in css.text
            assert '<script' not in page.text and ' style=' not in page.text
    asyncio.run(check())


def test_continue_design_expired_ui_and_no_new_state():
    async def check():
        async with browser() as (client, page, csrf, auth):
            response = await client.post('/auth/spoon/start', data={'csrf': csrf}, headers={'Origin': LOCAL_ORIGIN})
            pending = auth.pending
            page = await client.get(response.headers['location'])
            assert page.status_code == 200 and 'SPOON 인증 준비 완료' in page.text
            assert '공식 SPOON 인증으로 이동' in page.text and '처음으로 돌아가기' in page.text
            assert page.headers['referrer-policy'] == 'no-referrer' and page.headers['cache-control'] == 'no-store'
            assert '<script' not in page.text and '<form' not in page.text and 'http-equiv' not in page.text
            assert auth.pending is pending
            pending.deadline = auth.clock() - 1
            expired = await client.get('/auth/spoon/continue')
            assert expired.status_code == 400 and '인증 요청이 만료되었습니다' in expired.text
            assert 'official-oauth-link' not in expired.text
            assert expired.headers['referrer-policy'] == 'no-referrer'
            assert auth.pending is pending  # UI does not mutate or renew it.
    asyncio.run(check())


@pytest.mark.parametrize('state,title', [('authenticated','SPOON 계정 연결 완료'), ('reauth_required','계정 연결을 완료하지 못했습니다'), ('configuration_error','계정 연결을 완료하지 못했습니다'), ('real_ready','SPOON 계정 연결을 시작하세요')])
def test_result_uses_server_state_only(state, title):
    async def check():
        async with browser() as (client, page, csrf, auth):
            auth.state = state
            result = await client.get('/auth/spoon/result')
            assert title in result.text
            assert ('계정 연결됨' in result.text) == (state == 'authenticated')
            assert auth.session.identifier not in result.text and auth.session.csrf not in result.text
    asyncio.run(check())


def test_template_escapes_text():
    attack = '<img src=x onerror=alert(1)>'
    rendered = render_auth_page('<p>Server owned</p>', title=attack, description=attack, eyebrow=attack)
    assert attack not in rendered and escape(attack) in rendered
