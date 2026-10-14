"""Studio skeleton: no capture/source access, placeholder controls stay disabled."""
import asyncio
from html.parser import HTMLParser
import socket

import httpx
import pytest
from spoon_local.main import create_app
from spoon_local.config import Settings
from test_real_oauth import browser, begin_browser, pair

PAGES=['/','/broadcast','/capture','/gallery','/donations','/settings']


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args,**kwargs): raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket.socket,'connect_ex',deny)


class Tags(HTMLParser):
    def __init__(self): super().__init__();self.tags=[]
    def handle_starttag(self,tag,attrs): self.tags.append((tag,dict(attrs)))


def test_pages_navigation_and_disabled_capture_controls(tmp_path):
    async def check():
        app=create_app(settings=Settings(tmp_path/'test.sqlite3'))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                for path in PAGES:
                    response=await client.get(path)
                    assert response.status_code==200
                    parsed=Tags();parsed.feed(response.text)
                    assert all(f'href="{url}"' in response.text for url in PAGES)
                    assert 'DOHA-BOT' in response.text and '미리보기 준비 중' in response.text
                    view={'/':'home','/broadcast':'broadcast','/capture':'capture','/gallery':'gallery','/donations':'donations','/settings':'settings'}[path]
                    for tag,attrs in parsed.tags:
                        if 'data-view' in attrs:
                            assert ('hidden' not in attrs)==(view in attrs['data-view'].split())
                        if tag in {'button','select','input'} and attrs.get('id') != 'live-retry':
                            assert 'disabled' in attrs
                    assert not any(tag=='img' for tag,attrs in parsed.tags)
                for path in ['/api/captures','/api/captures/download','/data/real-sse.sqlite3','/static/%2e%2e/data/capture.png']:
                    assert (await client.get(path)).status_code==404
                for asset in ['dj.css','dj.js']:
                    assert (await client.get('/static/'+asset)).status_code==200
    asyncio.run(check())


def test_real_studio_requires_bound_login_no_source_requests():
    calls=[]
    def handler(request): calls.append(request);return httpx.Response(200,json=pair())
    async def check():
        async with browser(handler) as (client,app):
            for path in PAGES:
                assert (await client.get(path)).status_code==303
            assert (await client.get('/broadcast',headers={'Host':'127.0.0.1:8000'})).status_code==400
            assert calls==[]
            _,state=await begin_browser(client)
            await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-studio-code'})
            for path in PAGES:
                response=await client.get(path)
                assert response.status_code==200
                assert response.headers['cache-control']=='no-store'
                assert "default-src 'none'" in response.headers['content-security-policy']
            assert len(calls)==1  # Fake exchange only; UI cannot connect/capture.
            client.cookies.clear()
            assert (await client.get('/gallery')).status_code==303
            assert (await client.get('/api/captures/image/1')).status_code==404
    asyncio.run(check())


def test_frontend_has_no_capture_permission_or_upload_code():
    from spoon_local.main import STATIC_DIR
    script=(STATIC_DIR/'dj.js').read_text()
    for forbidden in ['getDisplayMedia','getUserMedia','innerHTML','captureStream','FormData','XMLHttpRequest']:
        assert forbidden not in script
    assert '/api/captures' not in script
    assert 'textContent' in script


def test_stylesheet_responses_do_not_contain_html(tmp_path):
    async def check():
        app=create_app(settings=Settings(tmp_path/'test.sqlite3'))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                for asset in ['dj.css','oauth.css','dashboard.css']:
                    response=await client.get('/static/'+asset)
                    assert response.status_code==200
                    assert response.headers['content-type'].startswith('text/css')
                    assert not response.text.lstrip().startswith('<')
                    assert '<!doctype' not in response.text.lower() and '<html' not in response.text.lower()
    asyncio.run(check())
