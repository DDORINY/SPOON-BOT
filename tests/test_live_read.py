import asyncio
from dataclasses import replace
import logging
import socket

import httpx
import pytest
from test_real_oauth import get_service, authorize, pair
from spoon_local.spoon.live_read import LiveReadClient

LIVE={'liveId':123,'title':'방송 <img onerror=alert(1)>','startedAt':'2026-10-14T01:00:00Z','listenerCount':42,'welcomeMessage':'PRIVATE-NOT-STORED'}

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args,**kwargs):raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',denied)
    monkeypatch.setattr(socket.socket,'connect_ex',denied)


async def ready(handler, oauth=None):
    auth,client,clock=await get_service(oauth or (lambda r:httpx.Response(200,json=pair('test-refresh-two' if b'grant_type=refresh_token' in r.content else 'test-refresh-one'))))
    await authorize(auth)
    live=LiveReadClient(auth,transport=httpx.MockTransport(handler),clock=clock)
    return auth,client,clock,live

async def cleanup(client,live):
    await live.aclose();await client.aclose()


def test_live_shape_cache_concurrency_and_private_logs(caplog):
    calls=[]
    def handler(request):
        calls.append(request)
        assert str(request.url)=='https://kr-openapi.spooncast.net/v1/live'
        assert request.method=='GET' and not request.content
        assert request.headers['authorization'].startswith('Bearer ')
        return httpx.Response(200,json=LIVE)
    async def check():
        auth,client,clock,live=await ready(handler)
        data=await asyncio.gather(*[live.get() for _ in range(5)])
        assert len(calls)==1 and data[0]['state']=='live'
        assert data[0]['live']['listener_count']==42
        assert 'welcomeMessage' not in str(data)
        data[0]['live']['title']='changed'
        assert (await live.get())['live']['title']==LIVE['title']
        clock.now+=31
        await live.get();assert len(calls)==2
        await cleanup(client,live)
    with caplog.at_level(logging.DEBUG):asyncio.run(check())
    assert 'test-access-one' not in caplog.text and 'PRIVATE' not in caplog.text

@pytest.mark.parametrize('status,body,state',[(404,{'detailCode':'OAPI_MNGR_0301'},'waiting'),(404,{},'error'),(403,{},'permission_required'),(429,{},'rate_limited'),(500,{},'error'),(502,{},'error'),(302,{},'error')])
def test_error_states(status,body,state):
    async def check():
        auth,client,clock,live=await ready(lambda r:httpx.Response(status,json=body,headers={'location':'https://attacker.invalid','retry-after':'120'}))
        assert (await live.get())['state']==state
        await cleanup(client,live)
    asyncio.run(check())

@pytest.mark.parametrize('exception',[httpx.ConnectError,httpx.ReadTimeout])
def test_network_errors(exception):
    def handler(request):raise exception('PRIVATE-ERROR')
    async def check():
        auth,client,clock,live=await ready(handler)
        assert await live.get()=={'state':'error','live':None,'retryable':True}
        await cleanup(client,live)
    asyncio.run(check())

@pytest.mark.parametrize('change',[{'listenerCount':True},{'startedAt':'invalid'},{'title':None},{'liveId':-1},{'listenerCount':-1}])
def test_invalid_payload(change):
    async def check():
        auth,client,clock,live=await ready(lambda r:httpx.Response(200,json={**LIVE,**change}))
        assert (await live.get())['state']=='error'
        await cleanup(client,live)
    asyncio.run(check())


def test_missing_scope_no_request():
    async def check():
        auth,client,clock,live=await ready(lambda r:pytest.fail('Missing permission request'))
        auth.store.replace(replace(auth.store.pair,scopes=frozenset({'events.donation'})))
        assert (await live.get())['state']=='permission_required'
        await cleanup(client,live)
    asyncio.run(check())

@pytest.mark.parametrize('second401',[False,True])
def test_401_refresh_once_and_fail_closed(second401):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(401) if len(calls)==1 or second401 else httpx.Response(200,json=LIVE)
    async def check():
        auth,client,clock,live=await ready(handler)
        data=await live.get()
        assert len(calls)==2 and data['state']==('reauth_required' if second401 else 'live')
        if second401: assert auth.store.pair is None
        await cleanup(client,live)
    asyncio.run(check())


def test_rate_limit_and_error_backoff():
    async def check():
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(429,headers={'retry-after':'120'})
        auth,client,clock,live=await ready(handler)
        await live.get();clock.now+=31;await live.get()
        assert len(calls)==1
        clock.now+=90;await live.get();assert len(calls)==2
        await cleanup(client,live)
    asyncio.run(check())


def test_expiry_uses_shared_refresh_before_read():
    async def check():
        auth,client,clock,live=await ready(lambda r:httpx.Response(200,json=LIVE))
        old=auth.store.pair
        clock.now=old.expires_at+1
        assert (await live.get())['state']=='live'
        assert auth.store.pair is not old and auth.store.pair.refresh_token!=old.refresh_token
        await cleanup(client,live)
    asyncio.run(check())


def test_logout_in_flight_discards_result():
    async def check():
        gate=asyncio.Event();opened=asyncio.Event()
        async def handler(request):
            opened.set();await gate.wait();return httpx.Response(200,json=LIVE)
        auth,client,clock,live=await ready(handler)
        task=asyncio.create_task(live.get());await opened.wait()
        auth.close();gate.set()
        assert (await task)['state']=='reauth_required'
        await cleanup(client,live)
    asyncio.run(check())


def test_local_api_browser_binding_and_no_automatic_read():
    from spoon_local.main import create_real_app
    from test_real_oauth import settings, begin_browser
    from spoon_local.spoon.oauth import LOCAL_ORIGIN
    async def check():
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(200,json=LIVE)
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair())),live_transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as browser:
                assert (await browser.get('/api/live')).status_code==401 and calls==[]
                _,state=await begin_browser(browser)
                await browser.get('/auth/spoon/callback',params={'state':state,'code':'invented-live-code'})
                assert (await browser.get('/')).status_code==200 and calls==[]
                response=await browser.get('/api/live')
                assert response.status_code==200 and response.json()['state']=='live'
                assert response.headers['cache-control']=='no-store'
                assert (await browser.get('/api/live',headers={'Host':'127.0.0.1:8000'})).status_code==400
                assert len(calls)==1
                browser.cookies.clear()
                assert (await browser.get('/api/live')).status_code==401
    asyncio.run(check())


def test_mock_token_is_never_sent_to_real_transport():
    async def check():
        auth,client,clock,live=await ready(lambda r:pytest.fail('Mock token sent'))
        auth.store.replace(replace(auth.store.pair,access_token='mock-access-offline'))
        assert (await live.get())['state']=='reauth_required' and auth.store.pair is None
        await cleanup(client,live)
    asyncio.run(check())


def test_server_errors_back_off_without_immediate_repeat():
    async def check():
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(502)
        auth,client,clock,live=await ready(handler)
        await live.get();clock.now+=31;await live.get()
        assert len(calls)==2
        clock.now+=31;await live.get();assert len(calls)==2
        clock.now+=30;await live.get();assert len(calls)==3
        await cleanup(client,live)
    asyncio.run(check())
