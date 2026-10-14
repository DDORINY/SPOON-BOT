import asyncio
import re
import socket
import httpx
import pytest
from dataclasses import replace
from spoon_local.main import create_real_app
from spoon_local.spoon.oauth import LOCAL_ORIGIN
from test_real_oauth import settings, pair, begin_browser
from test_real_sse import response, wire, until

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket.socket,'connect_ex',deny)


def test_helper_ui_json_start_stop_waiting_and_stored_events(tmp_path):
    calls=[]
    def sse(request):
        calls.append(request)
        return response(wire()+wire(),True)
    async def check():
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair())),real_sse_db=tmp_path/'real-sse.sqlite3',sse_transport=httpx.MockTransport(sse))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                assert (await client.get('/api/helper')).status_code==401
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-helper-code'})
                assert (await client.post('/auth/spoon/history/allow',data={'csrf':csrf,'local_owner':'confirmed'},headers={'Origin':LOCAL_ORIGIN})).status_code==200
                before=(await client.get('/api/helper')).json()
                assert before['state']=='disconnected' and before['available'] and calls==[]
                page=await client.get('/')
                assert 'id="helper-start"' in page.text and 'id="helper-stop"' in page.text
                headers={'Origin':LOCAL_ORIGIN,'Accept':'application/json'}
                assert (await client.post('/auth/spoon/collection/start',data={'csrf':csrf},headers=headers)).status_code==400
                started=await client.post('/auth/spoon/collection/start',data={'csrf':csrf,'dj_notice':'confirmed'},headers=headers)
                assert started.status_code==200 and started.json()['state']=='connecting'
                for _ in range(150):
                    data=(await client.get('/api/donations')).json()
                    if data['items']:break
                    await asyncio.sleep(.005)
                assert len(data['items'])==1
                info=(await client.get('/api/helper')).json()
                assert info['state']=='connected' and info['last_donation_at']
                assert (await client.post('/auth/spoon/collection/start',data={'csrf':csrf,'dj_notice':'confirmed'},headers=headers)).status_code==409
                assert len(calls)==1
                assert 'PRIVATE' not in str(info)+str(data)
                assert (await client.get('/')).status_code==200
                assert (await client.get('/api/helper')).json()['active']
                stopped=await client.post('/auth/spoon/collection/stop',data={'csrf':csrf},headers=headers)
                assert stopped.json()['state']=='stopped'
                assert (await client.get('/api/helper')).json()['state']=='disconnected'
                assert len((await client.get('/api/donations')).json()['items'])==1
    asyncio.run(check())


def test_no_sse_configuration_is_explicit_without_initializing_database():
    async def check():
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair())))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                _,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-config-code'})
                info=(await client.get('/api/helper')).json()
                assert info['reason']=='not_enabled' and not info['available']
                assert app.state.database_path is None
                assert (await client.post('/auth/spoon/collection/start')).status_code==404
    asyncio.run(check())


def test_waiting_never_reports_connected(tmp_path):
    async def check():
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair())),real_sse_db=tmp_path/'real-sse.sqlite3',sse_transport=httpx.MockTransport(lambda r:httpx.Response(404,json={'detailCode':'OAPI_MNGR_0301'})))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-wait-code'})
                await client.post('/auth/spoon/collection/start',data={'csrf':csrf,'dj_notice':'confirmed'},headers={'Origin':LOCAL_ORIGIN,'Accept':'application/json'})
                for _ in range(100):
                    info=(await client.get('/api/helper')).json()
                    if info['reason']=='waiting':break
                    await asyncio.sleep(.005)
                assert info['state']=='waiting_for_live' and info['active']
                assert info['last_donation_at'] is None
                assert (await client.post('/auth/spoon/collection/stop',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN,'Accept':'application/json'})).status_code==200
    asyncio.run(check())
