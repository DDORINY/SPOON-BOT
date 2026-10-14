import asyncio,json,logging,socket
from urllib.parse import parse_qs,urlsplit
import httpx
import pytest
from spoon_local.spoon.realtime_events import parse,RealtimeBuffer,InvalidRealtimeEvent
from spoon_local.spoon.sse_parser import SSEFrame
from spoon_local.spoon.oauth import REAL_EVENT_SCOPES,REQUIRED_SCOPES,LOCAL_ORIGIN
from spoon_local.spoon.real_oauth import load_real_oauth_settings,RealOAuthClient
from spoon_local.main import create_real_app
from test_real_oauth import settings,pair,begin_browser
from test_real_sse import response,wire,end

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)


def payload(kind='chat'):
    common={'user':{'id':'PRIVATE-CHAT-USER','nickname':'청취자'},'sentTime':'2026-10-14T00:00:00Z'}
    if kind=='chat':return {**common,'isDj':False,'message':'안녕하세요 <img onerror=alert(1)>'}
    return {'user':common['user'],'type':'PAID','totalAmount':30,'amount':10,'extraAmount':5,'combo':2,'time':common['sentTime']}


def frame(kind='chat',identifier='1000-1',**changes):
    return SSEFrame(kind,json.dumps({**payload(kind),**changes},ensure_ascii=False),identifier)


def bytes_frame(kind='chat',identifier='1000-1',**changes):
    item=frame(kind,identifier,**changes)
    return ((f'id:{identifier}\n' if identifier else '')+f'event:{kind}\ndata:{item.data}\n\n').encode()


def test_chat_official_fields_and_no_user_id():
    item=parse(frame(isDj=True))
    assert item['message'].startswith('안녕하세요') and item['nickname']=='청취자' and item['is_dj']
    assert item['received_at_utc'] and item['occurred_at_utc'].endswith('+00:00')
    assert 'PRIVATE-CHAT-USER' not in json.dumps(item)
    assert parse(frame(user={'id':'fake','nickname':None}))['nickname'] is None

@pytest.mark.parametrize('kind,changes',[('chat',{'message':'x'*4097}),('chat',{'isDj':'true'}),('chat',{'sentTime':'bad'}),('chat',{'sentTime':'2026-10-14'}),('chat',{'user':{'id':'','nickname':None}}),('chat',{'user':{'id':'fake','nickname':1}}),('like',{'totalAmount':-1}),('like',{'totalAmount':1.5}),('like',{'combo':False}),('like',{'type':'UNKNOWN'}),('like',{'totalAmount':10**20})])
def test_invalid_payload(kind,changes):
    with pytest.raises(InvalidRealtimeEvent):parse(frame(kind,**changes))

@pytest.mark.parametrize('raw',['not json','[]','{"user":null}','{"user":{},"user":{}}'])
def test_invalid_json(raw):
    with pytest.raises(InvalidRealtimeEvent):parse(SSEFrame('chat',raw,'1000-1'))


def test_hearts_total_amount_free_and_paid():
    buffer=RealtimeBuffer()
    buffer.accept(frame('like'),REAL_EVENT_SCOPES)
    buffer.accept(frame('like','1001-1',type='FREE',totalAmount=1,amount=1,extraAmount=0,combo=None),REAL_EVENT_SCOPES)
    data=buffer.snapshot(REAL_EVENT_SCOPES)
    assert data['hearts']['observed_amount']=='31' and data['hearts']['event_count']==2
    assert 'nickname' not in json.dumps(data['hearts'])


def test_order_duplicates_conflicts_unknown_and_idless():
    buffer=RealtimeBuffer()
    assert buffer.accept(SSEFrame('future','{}'),REAL_EVENT_SCOPES)=='ignored'
    assert buffer.accept(frame(),REAL_EVENT_SCOPES)=='accepted'
    assert buffer.accept(frame(),REAL_EVENT_SCOPES)=='duplicate'
    assert buffer.accept(frame(message='changed'),REAL_EVENT_SCOPES)=='conflict'
    buffer.accept(frame(identifier='1001-1'),REAL_EVENT_SCOPES)
    for _ in range(2):assert buffer.accept(frame(identifier=None),REAL_EVENT_SCOPES)=='accepted'
    items=buffer.snapshot(REAL_EVENT_SCOPES)['chats']
    assert [item['sequence'] for item in items]==[1,2,3,4]
    assert buffer.idless==2 and buffer.conflicts==1
    assert buffer.accept(frame(identifier=''),REAL_EVENT_SCOPES)=='accepted'
    assert buffer.idless==3


def test_buffer_bounds_ttl_and_permission_revocation():
    clock=[0.0];buffer=RealtimeBuffer(clock=lambda:clock[0],max_chats=2,max_seen=3,ttl=5)
    for i in range(10):buffer.accept(frame(identifier=f'{i}-1'),REAL_EVENT_SCOPES)
    assert len(buffer.chats)==2 and len(buffer.seen)==3
    clock[0]=5
    assert buffer.snapshot(REAL_EVENT_SCOPES)['chats']==[] and len(buffer.seen)==0
    buffer.accept(frame('like'),REAL_EVENT_SCOPES)
    assert buffer.snapshot(REQUIRED_SCOPES)['hearts']['observed_amount']=='0'
    assert buffer.accept(frame(),REQUIRED_SCOPES)=='ignored'


def test_extended_authorization_and_old_token_compatibility(tmp_path):
    from spoon_local.spoon.token_store import TokenPair
    (tmp_path/'.env').write_text('SPOON_CLIENT_ID=11111111-1111-4111-8111-111111111111\nSPOON_CLIENT_SECRET=invented-test-only\nSPOON_REGION=kr\nSPOON_REDIRECT_URI=http://localhost:8000/auth/spoon/callback\n')
    configuration=load_real_oauth_settings(tmp_path,environ={})
    assert configuration.scopes==REAL_EVENT_SCOPES
    client=RealOAuthClient(configuration,enabled=True,transport=httpx.MockTransport(lambda r:httpx.Response(500)))
    query=parse_qs(urlsplit(client.authorization_url('invented-state')).query)
    assert set(query['scope'][0].split())==REAL_EVENT_SCOPES
    assert TokenPair.from_response(pair(),0).scopes==REQUIRED_SCOPES
    asyncio.run(client.aclose())

@pytest.mark.parametrize('scopes',[REQUIRED_SCOPES,REAL_EVENT_SCOPES])
def test_shared_stream_feed_owner_and_donation_regression(tmp_path,scopes,caplog):
    async def check():
        calls=[]
        def sse(request):
            calls.append(request.url.path)
            data=bytes_frame()+bytes_frame()+b'event:chat\ndata:bad\n\n'+bytes_frame('like')+wire()
            return response(data,True)
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair(scope=' '.join(scopes)))),real_sse_db=tmp_path/'real-sse.sqlite3',sse_transport=httpx.MockTransport(sse))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                assert (await client.get('/api/realtime-events')).status_code==401
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-monitor-code'})
                initial=(await client.get('/api/realtime-events')).json()
                assert initial['state']=='disconnected' and calls==[]
                await client.post('/auth/spoon/collection/start',data={'csrf':csrf,'dj_notice':'confirmed'},headers={'Origin':LOCAL_ORIGIN,'Accept':'application/json'})
                for _ in range(200):
                    data=(await client.get('/api/realtime-events')).json()
                    if data.get('sequence',0)>=2 or (scopes==REQUIRED_SCOPES and data['state']=='connected'):break
                    await asyncio.sleep(.005)
                assert calls==['/v1/live/events']
                encoded=json.dumps(data,ensure_ascii=False)
                for sensitive in ['test-access-one','test-refresh-one','test-only-not-issued-PRIVATE-SENTINEL','PRIVATE-CHAT-USER']:
                    assert sensitive not in encoded
                assert data['permissions']['chat']==('events.chat' in scopes)
                if scopes==REAL_EVENT_SCOPES:
                    assert len(data['chats'])==1 and data['hearts']['observed_amount']=='30'
                    assert data['invalid_count']==1
                else:assert data['chats']==[] and data['hearts']['event_count']==0
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as stranger:
                    assert (await stranger.get('/api/realtime-events')).status_code==401
                assert (await client.get('/api/realtime-events')).json()['chats']==data['chats']
                page=await client.get('/broadcast')
                assert page.status_code==200 and '최근 채팅' in page.text and '미리보기 준비 중' in page.text
                assert (await client.get('/api/realtime-events',headers={'Host':'127.0.0.1:8000'})).status_code==400
                assert (await client.get('/api/realtime-events',headers={'Cookie':'spoon_real_session=a; spoon_real_session=b'})).status_code==400
                await client.post('/auth/spoon/history/allow',data={'csrf':csrf,'local_owner':'confirmed'},headers={'Origin':LOCAL_ORIGIN})
                for _ in range(100):
                    stored=(await client.get('/api/donations')).json()
                    if stored['items']:break
                    await asyncio.sleep(.005)
                assert len(stored['items'])==1
                db_bytes=(tmp_path/'real-sse.sqlite3').read_bytes()
                assert b'PRIVATE-CHAT-USER' not in db_bytes and '안녕하세요'.encode() not in db_bytes
                await client.post('/auth/spoon/collection/stop',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
                stopped=(await client.get('/api/realtime-events')).json()
                assert stopped['state']=='disconnected' and stopped['chats']==[]
                assert stopped['hearts']['event_count']==0
    with caplog.at_level(logging.INFO):asyncio.run(check())
    assert 'PRIVATE-CHAT-USER' not in caplog.text and '안녕하세요' not in caplog.text


@pytest.mark.parametrize('mode',['waiting','reconnect'])
def test_waiting_and_reconnection_without_duplicate_feed(tmp_path,mode):
    async def check():
        calls=[]
        def handler(request):
            calls.append(request.url.path)
            if mode=='waiting':return httpx.Response(404,json={'detailCode':'OAPI_MNGR_0301'})
            return response(bytes_frame()+end('RECONNECT'),False) if len(calls)==1 else response(bytes_frame()+bytes_frame(identifier='1001-1',message='새 메시지'),True)
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair(scope=' '.join(REAL_EVENT_SCOPES)))),real_sse_db=tmp_path/'real-sse.sqlite3',sse_transport=httpx.MockTransport(handler))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-reconnect-code'})
                await client.post('/auth/spoon/collection/start',data={'csrf':csrf,'dj_notice':'confirmed'},headers={'Origin':LOCAL_ORIGIN})
                for _ in range(200):
                    data=(await client.get('/api/realtime-events')).json()
                    if (mode=='waiting' and data['state']=='waiting_for_live') or (mode=='reconnect' and len(data['chats'])==2):break
                    await asyncio.sleep(.005)
                if mode=='waiting':assert data['chats']==[] and data['hearts']['event_count']==0
                else:assert len(calls)==2 and len(data['chats'])==2
                await client.post('/auth/spoon/collection/stop',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
    asyncio.run(check())


def test_scope_loss_and_session_rebinding_clear_feed(tmp_path,monkeypatch):
    from dataclasses import replace
    from spoon_local.spoon.auth_service import RealAuthService
    from spoon_local.web.real_auth_routes import REAL_COOKIE
    captured=[];original=RealAuthService.complete
    async def complete(self,*args):
        success=await original(self,*args)
        captured.append(self)
        return success
    monkeypatch.setattr(RealAuthService,'complete',complete)
    async def check():
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair(scope=' '.join(REAL_EVENT_SCOPES)))),real_sse_db=tmp_path/'real-sse.sqlite3',sse_transport=httpx.MockTransport(lambda r:response(bytes_frame()+bytes_frame('like'),True)))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-owner-code'})
                await client.post('/auth/spoon/collection/start',data={'csrf':csrf,'dj_notice':'confirmed'},headers={'Origin':LOCAL_ORIGIN})
                for _ in range(100):
                    result=(await client.get('/api/realtime-events')).json()
                    if result['chats']:break
                    await asyncio.sleep(.005)
                assert result['chats']
                auth=captured[0];auth.store.replace(replace(auth.store.pair,scopes=REQUIRED_SCOPES))
                result=(await client.get('/api/realtime-events')).json()
                assert result['chats']==[] and result['hearts']['event_count']==0
                assert result['state']=='connected'
                new=auth.prepare(None)
                client.cookies.clear();client.cookies.set(REAL_COOKIE,new.identifier)
                assert (await client.get('/api/realtime-events')).status_code==403
                result=(await client.get('/api/realtime-events')).json()
                assert result['state']=='disconnected' and result['chats']==[]
                auth.close()
                assert (await client.get('/api/realtime-events')).status_code==401
    asyncio.run(check())


def test_frontend_realtime_xss_and_reading_position():
    from pathlib import Path
    import shutil,subprocess
    node=shutil.which('node')
    if node is None:pytest.skip('Node unavailable')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([node,'tests/realtime_ui_check.cjs'],cwd=root,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
