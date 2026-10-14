"""DJ UI and server authorization, fake OAuth only."""
import asyncio
import socket
import httpx
import pytest

from test_real_oauth import browser, begin_browser, pair
from spoon_local.spoon.oauth import LOCAL_ORIGIN


@pytest.fixture(autouse=True)
def blocked(monkeypatch):
    def deny(*args, **kwargs): raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket.socket,'connect_ex',deny)


def test_dashboard_login_gate_success_and_browser_binding():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=pair())
    async def check():
        async with browser(handler) as (client,app):
            for path in ['/', '/donations', '/settings']:
                response=await client.get(path)
                assert response.status_code==303 and response.headers['location']=='/auth/spoon/prepare'
            assert (await client.get('/api/donations')).status_code==401
            csrf,state=await begin_browser(client)
            assert calls==[]
            callback=await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-dj-code'})
            assert callback.status_code==303 and callback.headers['location']==LOCAL_ORIGIN+'/'
            assert 'HttpOnly' in callback.headers['set-cookie'] and 'SameSite=lax' in callback.headers['set-cookie']
            page=await client.get('/')
            assert page.status_code==200
            for text in ['내 방송 관리','오늘 받은 후원','후원한 청취자 수','최근 후원','대시보드','설정']:
                assert text in page.text
            for text in ['OAuth','SSE','SQLite','Mock','API 상태']:
                assert text not in page.text
            assert '—' in page.text
            assert (await client.get('/auth/spoon/prepare')).headers['location']==LOCAL_ORIGIN+'/'
            assert (await client.get('/api/donations')).status_code==200
            # A second browser cannot piggyback on the process's access token.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as stranger:
                assert (await stranger.get('/api/donations')).status_code==401
                assert (await stranger.get('/')).status_code==303
                assert (await stranger.get('/auth/spoon/prepare')).status_code==401
                assert (await stranger.get('/auth/spoon/manage')).status_code==303
                assert (await stranger.get('/api/donations',headers={'Cookie':'spoon_real_session=invented-wrong'})).status_code==401
            assert len(calls)==1  # Read-only UI never connects or exchanges again.
            management=await client.get('/auth/spoon/manage')
            assert management.status_code==200 and '로그아웃' in management.text
            assert '/auth/spoon/collection/start' not in management.text  # Auth-only mode.
            assert (await client.get('/api/donations',headers={'Host':'127.0.0.1:8000'})).status_code==400
            assert (await client.get('/api/donations',headers={'Cookie':'spoon_real_session=a; spoon_real_session=b'})).status_code==400
    asyncio.run(check())


def test_failed_callback_does_not_open_dashboard():
    async def check():
        async with browser(lambda r:httpx.Response(400,json={'error':'invalid_grant'})) as (client,app):
            csrf,state=await begin_browser(client)
            callback=await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-failed-code'})
            assert callback.headers['location']==LOCAL_ORIGIN+'/auth/spoon/result'
            result=await client.get(callback.headers['location'])
            assert '계정 연결을 완료하지 못했습니다' in result.text
            assert (await client.get('/')).status_code==303
            assert (await client.get('/api/donations')).status_code==401
    asyncio.run(check())


def test_dashboard_real_controls_require_notice_and_csrf(tmp_path):
    import re
    from spoon_local.main import create_real_app
    from test_real_oauth import settings
    from test_real_sse import response, until
    calls=[]
    def sse(request):
        calls.append(request)
        return response(b':heartbeat\n\n',True)
    async def check():
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair())),real_sse_db=tmp_path/'real-sse.sqlite3',sse_transport=httpx.MockTransport(sse))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-controls-code'})
                page=await client.get('/')
                assert 'action="/auth/spoon/collection/start"' in page.text
                assert 'name="dj_notice" value="confirmed" required' in page.text
                assert calls==[] and not (tmp_path/'real-sse.sqlite3').exists()
                for values in [{'csrf':csrf},{'csrf':'bad','dj_notice':'confirmed'}]:
                    assert (await client.post('/auth/spoon/collection/start',data=values,headers={'Origin':LOCAL_ORIGIN})).status_code==400
                assert calls==[]
                assert (await client.post('/auth/spoon/collection/start',data={'csrf':csrf,'dj_notice':'confirmed'},headers={'Origin':LOCAL_ORIGIN})).status_code==303
                await until(lambda:bool(calls))
                page=await client.get('/')
                assert '/auth/spoon/collection/stop' in page.text and '방송 연결 해제하기' in page.text
                csrf=re.search(r'name="csrf" value="([^"]+)"',page.text)[1]
                assert (await client.post('/auth/spoon/collection/stop',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})).status_code==303
                assert (await client.get('/api/status')).json()['donation_events']=='stopped'
    asyncio.run(check())
