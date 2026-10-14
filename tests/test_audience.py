import asyncio,json,logging,socket
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from email.utils import format_datetime
import httpx
import pytest
from spoon_local.spoon.audience import AudienceService,normalize,retry_delay
from spoon_local.spoon.auth_service import RealAuthService
from spoon_local.spoon.live_read import LiveReadClient
from spoon_local.spoon.oauth import LOCAL_ORIGIN,REAL_EVENT_SCOPES,REQUIRED_SCOPES
from spoon_local.spoon.real_oauth import RealOAuthClient
from spoon_local.main import create_real_app
from test_real_oauth import settings,pair,authorize,begin_browser,Clock

LIVE={'liveId':100,'title':'방송','startedAt':'2026-10-14T00:00:00Z','listenerCount':30}
LISTENERS={'listeners':[{'id':'PRIVATE-USER-ID','nickname':'<img onerror=alert(1)>'}],'nextCursor':None}
FANS={'totalCount':2,'fans':[{'id':'PRIVATE-FAN-ID','nickname':'팬','rank':1,'spoonCount':1.25},{'id':'other','nickname':'<script>XSS</script>','rank':2,'spoonCount':None}]}

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)


async def service(handler,scopes=REAL_EVENT_SCOPES,oauth_handler=None):
    clock=Clock()
    oauth=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(oauth_handler or (lambda r:httpx.Response(200,json=pair(scope=' '.join(scopes))))))
    auth=RealAuthService(settings(),oauth,clock);await authorize(auth)
    live=LiveReadClient(auth,transport=httpx.MockTransport(handler),clock=clock)
    return auth,live,AudienceService(live),clock


def test_official_normalization_no_images_and_order():
    data=normalize('listeners',{**LISTENERS,'imageUrl':'javascript:alert(1)'})
    assert data['items']==[{'nickname':'<img onerror=alert(1)>'}] and data['count_basis']=='this_page_only'
    data=normalize('fans',FANS)
    assert [item['rank'] for item in data['items']]==[1,2]
    assert data['items'][1]['spoon_count'] is None and data['items'][0]['spoon_count']=='1.25'
    assert 'PRIVATE' not in json.dumps(data)

@pytest.mark.parametrize('kind,value',[('listeners',{'listeners':[]}),('listeners',{'listeners':'bad','nextCursor':None}),('listeners',{'listeners':[{'id':'x','nickname':None}],'nextCursor':None}),('listeners',{'listeners':[],'nextCursor':'\n'}),('fans',{'totalCount':1,'fans':[]}),('fans',{'totalCount':1,'fans':[{'id':'x','nickname':'y','rank':0,'spoonCount':1}]}),('fans',{'totalCount':1,'fans':[{'id':'x','nickname':'y','rank':1,'spoonCount':-1}]}),('listeners',{'listeners':[{'id':'x','nickname':'x'*257}],'nextCursor':None})])
def test_bad_payload(kind,value):
    with pytest.raises((ValueError,KeyError,TypeError)):normalize(kind,value)

@pytest.mark.parametrize('kind',['listeners','fans'])
def test_normal_queries_and_no_result_cache(kind,caplog):
    async def check():
        calls=[]
        def handler(request):
            calls.append(request)
            assert request.method=='GET' and request.headers['Authorization'].startswith('Bearer ')
            assert request.url.host=='kr-openapi.spooncast.net'
            return httpx.Response(200,json=LIVE if request.url.path=='/v1/live' else LISTENERS if kind=='listeners' else FANS)
        auth,live,audience,clock=await service(handler)
        result=await audience.get(kind,owner=auth.session)
        assert result['state']=='ready' and result['live_id']==100 and result['queried_at_utc']
        assert len(calls)==3
        assert result['returned_count']==(1 if kind=='listeners' else 2)
        clock.now+=3
        assert (await audience.get(kind,owner=auth.session))['state']=='ready' and len(calls)==6
        await live.aclose();await auth.client.aclose()
    with caplog.at_level(logging.INFO):asyncio.run(check())
    assert 'PRIVATE-USER-ID' not in caplog.text and '<script>' not in caplog.text

@pytest.mark.parametrize('status,state',[(403,'permission_required'),(404,'waiting'),(429,'rate_limited'),(500,'upstream_error'),(502,'upstream_error'),(302,'upstream_error')])
def test_http_errors(status,state):
    async def check():
        calls=[]
        def handler(request):
            calls.append(request.url.path)
            if request.url.path=='/v1/live':return httpx.Response(200,json=LIVE)
            return httpx.Response(status,json={'detailCode':'OAPI_MNGR_0301'},headers={'Retry-After':'120'})
        auth,live,audience,clock=await service(handler)
        data=await audience.get('listeners',owner=auth.session)
        assert data['state']==state and data['items']==[]
        if status==429:
            assert data['retry_after_seconds']==120
            clock.now+=5
            assert (await audience.get('listeners',owner=auth.session))['state']=='rate_limited' and len(calls)==2
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())

@pytest.mark.parametrize('error,state',[(httpx.ReadTimeout,'timeout'),(httpx.ConnectError,'network_error')])
def test_network_and_timeout(error,state):
    async def check():
        def handler(request):
            if request.url.path=='/v1/live':return httpx.Response(200,json=LIVE)
            raise error('PRIVATE-ERROR-DETAIL')
        auth,live,audience,_=await service(handler)
        assert (await audience.get('fans',owner=auth.session))['state']==state
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_scope_missing_no_requests_and_waiting():
    async def check():
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(404,json={'detailCode':'OAPI_MNGR_0301'})
        auth,live,audience,_=await service(handler,REQUIRED_SCOPES)
        assert (await audience.get('listeners',owner=auth.session))['state']=='permission_required'
        assert calls==[]
        auth.store.replace(replace(auth.store.pair,scopes=REAL_EVENT_SCOPES))
        assert (await audience.get('fans',owner=auth.session))['state']=='waiting' and len(calls)==1
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_listener_cursor_and_broadcast_change():
    async def check():
        queries=[];current=[100]
        def handler(request):
            if request.url.path=='/v1/live':return httpx.Response(200,json={**LIVE,'liveId':current[0]})
            queries.append(dict(request.url.params))
            return httpx.Response(200,json={'listeners':[],'nextCursor':'opaque+/=' if not request.url.params else None})
        auth,live,audience,clock=await service(handler)
        first=await audience.get('listeners',owner=auth.session)
        clock.now+=3
        second=await audience.get('listeners',owner=auth.session,cursor=first['next_cursor'],expected_live_id=100)
        assert second['state']=='ready' and queries==[{}, {'cursor':'opaque+/='}]
        clock.now+=3;current[0]=101
        assert (await audience.get('listeners',owner=auth.session,cursor=first['next_cursor'],expected_live_id=100))['state']=='broadcast_changed'
        assert len(queries)==2
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_mid_request_logout_and_mid_request_broadcast_change():
    async def check():
        current=[100];auth_holder=[]
        def handler(request):
            if request.url.path=='/v1/live':return httpx.Response(200,json={**LIVE,'liveId':current[0]})
            current[0]=101
            return httpx.Response(200,json=LISTENERS)
        auth,live,audience,clock=await service(handler)
        assert (await audience.get('listeners',owner=auth.session))['state']=='broadcast_changed'
        async def logout_handler(request):
            if request.url.path=='/v1/live':return httpx.Response(200,json=LIVE)
            auth_holder[0].close();return httpx.Response(200,json=LISTENERS)
        await live.aclose();await auth.client.aclose()
        auth,live,audience,_=await service(logout_handler);auth_holder.append(auth)
        data=await audience.get('listeners',owner=auth.session)
        assert data['state']=='reauth' and data['items']==[]
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('succeed',[True,False])
def test_401_refresh_and_reauthentication(succeed):
    async def check():
        queries=[];tokens=[]
        def oauth(request):
            tokens.append(request)
            if len(tokens)>1 and not succeed:return httpx.Response(400,json={'error':'invalid_grant'})
            return httpx.Response(200,json=pair(refresh='invented-rotated' if len(tokens)>1 else 'invented-first',scope=' '.join(REAL_EVENT_SCOPES)))
        def handler(request):
            if request.url.path=='/v1/live':return httpx.Response(200,json=LIVE)
            queries.append(request)
            return httpx.Response(401) if len(queries)==1 or not succeed else httpx.Response(200,json=FANS)
        auth,live,audience,_=await service(handler,oauth_handler=oauth)
        result=await audience.get('fans',owner=auth.session)
        assert result['state']==('ready' if succeed else 'reauth')
        assert len(tokens)==2 and len(queries)==(2 if succeed else 1)
        if succeed:assert auth.store.pair.refresh_token=='invented-rotated'
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_duplicate_concurrent_requests_and_retry_after():
    async def check():
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(200,json=LIVE if request.url.path=='/v1/live' else LISTENERS)
        auth,live,audience,_=await service(handler)
        responses=await asyncio.gather(*[audience.get('listeners',owner=auth.session) for _ in range(2)])
        assert sorted(data['state'] for data in responses)==['rate_limited','ready'] and len(calls)==3
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())
    assert retry_delay('100000')==100000
    assert retry_delay('bad')==30
    assert 5<=retry_delay(format_datetime(datetime.now(timezone.utc)+timedelta(seconds=10)))<=10


def test_api_browser_authorization_and_input(tmp_path):
    async def check():
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(200,json=LIVE if request.url.path=='/v1/live' else LISTENERS if request.url.path.endswith('listeners') else FANS)
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair(scope=' '.join(REAL_EVENT_SCOPES)))),live_transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                assert (await client.get('/api/live/listeners')).status_code==401
                _,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-audience-code'})
                result=await client.get('/api/live/listeners')
                assert result.json()['state']=='ready' and result.headers['cache-control']=='no-store'
                assert 'PRIVATE-USER-ID' not in result.text and 'test-access-one' not in result.text
                for query in ['live_id=invalid','cursor=bad&cursor=again','unexpected=1','live_id=-1','cursor=%0A']:
                    assert (await client.get('/api/live/listeners?'+query)).status_code==400
                assert (await client.get('/api/live/fans',headers={'Host':'127.0.0.1:8000'})).status_code==400
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as stranger:
                    assert (await stranger.get('/api/live/listeners')).status_code==401
                assert len(calls)==3
                assert not list(tmp_path.iterdir())
    asyncio.run(check())


@pytest.mark.parametrize('body',[{}, {'listeners':[],'nextCursor':None}])
def test_bad_and_empty_http_response(body):
    async def check():
        auth,live,audience,_=await service(lambda r:httpx.Response(200,json=LIVE if r.url.path=='/v1/live' else body))
        result=await audience.get('listeners',owner=auth.session)
        assert result['state']==('ready' if body else 'upstream_error') and result['items']==[]
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_expired_token_refresh_and_old_session_cursor_rejected():
    async def check():
        tokens=[];calls=[]
        def oauth(request):tokens.append(request);return httpx.Response(200,json=pair(refresh='invented-rotated' if len(tokens)>1 else 'invented-first',scope=' '.join(REAL_EVENT_SCOPES)))
        def handler(request):calls.append(request);return httpx.Response(200,json=LIVE if request.url.path=='/v1/live' else {**LISTENERS,'nextCursor':'opaque'})
        auth,live,audience,_=await service(handler,oauth_handler=oauth)
        auth.store.replace(replace(auth.store.pair,expires_at=0,refresh_at=0))
        owner=auth.session
        assert (await audience.get('listeners',owner=owner))['state']=='ready' and len(tokens)==2
        auth.prepare(None)
        assert (await audience.get('listeners',owner=owner))['state']=='reauth'
        assert audience.allowed_cursor is None # old session paging metadata was cleared
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_audience_frontend_late_responses_and_xss():
    from pathlib import Path
    import shutil,subprocess
    node=shutil.which('node')
    if node is None:pytest.skip('Node unavailable')
    result=subprocess.run([node,'tests/audience_ui_check.cjs'],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_inflight_lookup_returns_busy_without_second_http_request():
    async def check():
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def handler(request):
            calls.append(request)
            if request.url.path=='/v1/live/listeners':entered.set();await release.wait()
            return httpx.Response(200,json=LIVE if request.url.path=='/v1/live' else LISTENERS)
        auth,live,audience,_=await service(handler)
        first=asyncio.create_task(audience.get('listeners',owner=auth.session))
        await entered.wait()
        assert (await audience.get('fans',owner=auth.session))['state']=='busy'
        assert len(calls)==2
        release.set();assert (await first)['state']=='ready'
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_pending_metadata_401_never_refreshes_rebound_session():
    async def check():
        holder=[];tokens=[];queries=[]
        def oauth(request):tokens.append(request);return httpx.Response(200,json=pair(scope=' '.join(REAL_EVENT_SCOPES)))
        def handler(request):
            queries.append(request)
            holder[0].prepare(None)
            return httpx.Response(401)
        auth,live,audience,_=await service(handler,oauth_handler=oauth);holder.append(auth)
        assert (await audience.get('listeners',owner=auth.session))['state']=='reauth'
        assert len(tokens)==1 and len(queries)==1
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_metadata_retry_after_protects_entire_lookup():
    async def check():
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(429,headers={'Retry-After':'100000'})
        auth,live,audience,clock=await service(handler)
        assert (await audience.get('fans',owner=auth.session))['retry_after_seconds']==100000
        auth.store.replace(replace(auth.store.pair,expires_at=clock.now+200000,refresh_at=clock.now+200000))
        clock.now+=90000
        result=await audience.get('fans',owner=auth.session)
        assert result['state']=='rate_limited' and result['retry_after_seconds']==10000 and len(calls)==1
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())
