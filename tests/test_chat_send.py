import asyncio,json,socket
import httpx,pytest
from spoon_local.spoon.chat_send import ChatSendService,validate_message
from spoon_local.spoon.oauth import LOCAL_ORIGIN,REQUIRED_SCOPES
from spoon_local.main import create_real_app
from test_audience import service,LIVE
from test_real_oauth import settings,pair,begin_browser

@pytest.fixture(autouse=True)
def blocked(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)

@pytest.mark.parametrize('message',['','   ','x'*201,'😀'*101,None])
def test_bad_message(message):
    with pytest.raises(ValueError):validate_message(message)

def test_utf16_limit():assert validate_message('😀'*100)=='😀'*100

@pytest.mark.parametrize('status,expected',[(204,'sent'),(401,'reauth_or_retry_manually'),(403,'permission_required'),(404,'waiting'),(429,'rate_limited'),(500,'uncertain'),(502,'uncertain'),(200,'uncertain')])
def test_send_once_and_duplicate_protection(status,expected):
    async def check():
        posts=[]
        def handler(request):
            if request.method=='GET':return httpx.Response(200,json={**LIVE,'isChatFrozen':False})
            posts.append(request)
            assert request.url.path=='/v1/live/chat' and json.loads(request.content)=={'message':'<img onerror=alert(1)>'}
            return httpx.Response(status,json={'detailCode':'OAPI_MNGR_0301'} if status==404 else {},headers={'Retry-After':'90'}) if status!=204 else httpx.Response(204)
        auth,live,_,clock=await service(handler)
        sender=ChatSendService(live);prepared=await sender.prepare(auth.session)
        result=await sender.send(auth.session,'<img onerror=alert(1)>',prepared['request_id'])
        assert result['state']==expected and len(posts)==1
        if status!=401:
            assert (await sender.send(auth.session,'<img onerror=alert(1)>',prepared['request_id']))['state']=='request_expired_or_used'
        if status==429:assert result['retry_after_seconds']==90
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())

@pytest.mark.parametrize('scenario',['scope','waiting','frozen','missing','changed','timeout'])
def test_send_rejection_and_uncertain(scenario):
    async def check():
        posts=[];live_id=[100]
        def handler(request):
            if request.method=='POST':posts.append(request);raise httpx.ReadTimeout('PRIVATE-MESSAGE')
            if scenario=='waiting':return httpx.Response(404,json={'detailCode':'OAPI_MNGR_0301'})
            return httpx.Response(200,json={**LIVE,'liveId':live_id[0],**({} if scenario=='missing' else {'isChatFrozen':scenario=='frozen'})})
        auth,live,_,clock=await service(handler)
        if scenario=='scope':
            from dataclasses import replace
            auth.store.replace(replace(auth.store.pair,scopes=REQUIRED_SCOPES))
        sender=ChatSendService(live);prepared=await sender.prepare(auth.session)
        if scenario in {'scope','waiting','frozen','missing'}:
            assert prepared['state']=={'scope':'permission_required','waiting':'waiting','frozen':'chat_blocked','missing':'broadcast_unverified'}[scenario]
        else:
            if scenario=='changed':live_id[0]=101
            result=await sender.send(auth.session,'hello',prepared['request_id'])
            assert result['state']==('broadcast_changed' if scenario=='changed' else 'uncertain')
        assert len(posts)==(1 if scenario=='timeout' else 0)
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_route_csrf_origin_host_json_and_no_auto_post(tmp_path):
    async def check():
        posts=[]
        def handler(request):
            if request.method=='POST':posts.append(request);return httpx.Response(204)
            return httpx.Response(200,json={**LIVE,'isChatFrozen':False})
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair(scope='live.read events.donation chat.send'))),live_transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                assert (await client.post('/api/live/chat',json={})).status_code==401
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-send-code'})
                assert (await client.get('/api/live/chat')).status_code==405
                assert posts==[]
                prepared=(await client.get('/api/live/chat/prepare')).json()
                values={'csrf':csrf,'message':'안녕하세요','request_id':prepared['request_id'],'notice':'confirmed'}
                for origin,change in [('http://evil.invalid',{}),(LOCAL_ORIGIN,{'csrf':'bad'}),(LOCAL_ORIGIN,{'message':' '}),(LOCAL_ORIGIN,{'notice':'no'})]:
                    assert (await client.post('/api/live/chat',json={**values,**change},headers={'Origin':origin})).status_code==400
                assert posts==[]
                assert (await client.post('/api/live/chat',content='bad',headers={'Origin':LOCAL_ORIGIN,'Content-Type':'application/json'})).status_code==400
                assert (await client.post('/api/live/chat',content='x'*4097,headers={'Origin':LOCAL_ORIGIN,'Content-Type':'application/json'})).status_code==400
                assert (await client.post('/api/live/chat',json=values,headers={'Origin':LOCAL_ORIGIN,'Host':'127.0.0.1:8000'})).status_code==400
                prepared=(await client.get('/api/live/chat/prepare')).json();values['request_id']=prepared['request_id']
                result=await client.post('/api/live/chat',json=values,headers={'Origin':LOCAL_ORIGIN})
                assert result.json()['state']=='sent' and len(posts)==1
                assert (await client.post('/api/live/chat',json=values,headers={'Origin':LOCAL_ORIGIN})).json()['state']=='request_expired_or_used'
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as other:
                    assert (await other.post('/api/live/chat',json=values,headers={'Origin':LOCAL_ORIGIN})).status_code==401
        assert not list(tmp_path.iterdir())
    asyncio.run(check())


def test_duplicate_message_and_ticket_session_binding():
    async def check():
        posts=[]
        def handler(request):
            if request.method=='POST':posts.append(request);return httpx.Response(204)
            return httpx.Response(200,json={**LIVE,'isChatFrozen':False})
        auth,live,_,clock=await service(handler);sender=ChatSendService(live)
        one=await sender.prepare(auth.session)
        assert (await sender.send(auth.session,'hello',one['request_id']))['state']=='sent'
        clock.now+=4;two=await sender.prepare(auth.session)
        assert (await sender.send(auth.session,'hello',two['request_id']))['state']=='duplicate_message'
        three=await sender.prepare(auth.session);auth.prepare(None)
        assert (await sender.send(auth.session,'different',three['request_id']))['state']=='request_expired_or_used'
        assert len(posts)==1
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())


def test_frontend_manual_send_and_uncertainty():
    from pathlib import Path
    import shutil,subprocess
    node=shutil.which('node')
    if node is None:pytest.skip('Node unavailable')
    result=subprocess.run([node,'tests/chat_send_ui_check.cjs'],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_rate_limit_prepare_never_calls_provider_and_expired_ticket():
    async def check():
        calls=[]
        def handler(request):calls.append(request);return httpx.Response(200,json={**LIVE,'isChatFrozen':False})
        auth,live,_,clock=await service(handler);sender=ChatSendService(live)
        prepared=await sender.prepare(auth.session);count=len(calls)
        sender.until=clock.now+90
        assert (await sender.prepare(auth.session))['state']=='rate_limited' and len(calls)==count
        clock.now+=301
        assert (await sender.send(auth.session,'hello',prepared['request_id']))['state']=='request_expired_or_used'
        assert len(calls)==count
        await live.aclose();await auth.client.aclose()
    asyncio.run(check())
