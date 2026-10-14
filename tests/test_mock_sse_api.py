import asyncio
from contextlib import asynccontextmanager
import json
import logging
import re
import socket
from urllib.parse import urlsplit, parse_qs

import httpx
import pytest

from spoon_local.main import create_mock_app, create_app
from spoon_local.spoon.sse_source import MockConnection, MockSSESource
from spoon_local.spoon.oauth import LOCAL_ORIGIN


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs): raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)


def fixture_source():
    data = json.dumps({'user':{'id':'PRIVATE-USER-SENTINEL','nickname':'<script>alert(1)</script>'},'amount':0.1,'message':'PRIVATE-MESSAGE-SENTINEL','time':'2026-10-14T00:00:00Z'})
    return MockSSESource([MockConnection((('id:1000-0\nevent:donation\ndata:'+data+'\n\n').encode(),),live_id='mock-live',hold_open=True)])


@asynccontextmanager
async def client_app(tmp_path):
    app = create_mock_app(tmp_path / 'mock' / 'mock-sse.sqlite3', fixture_source)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
            yield client, app


def csrf_from(page):
    return re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)


async def login(client):
    page=await client.get('/auth/spoon/prepare')
    csrf=csrf_from(page)
    response=await client.post('/auth/spoon/start',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
    state=parse_qs(urlsplit(response.headers['location']).query)['state'][0]
    response=await client.post('/auth/spoon/mock-consent',data={'csrf':csrf,'state':state,'decision':'allow'},headers={'Origin':LOCAL_ORIGIN})
    assert (await client.get(response.headers['location'])).status_code==303
    return csrf


async def wait_items(client):
    for _ in range(100):
        data=(await client.get('/api/donations')).json()
        if data['items']:return data
        await asyncio.sleep(.005)
    raise AssertionError('No stored mock item')


def test_mock_path_never_loads_dotenv_or_initializes_operational_db(tmp_path,monkeypatch):
    def denied(*args,**kwargs):raise AssertionError('Operational settings/DB accessed')
    monkeypatch.setattr('spoon_local.main.load_settings',denied)
    monkeypatch.setattr('spoon_local.config.dotenv_values',denied)
    monkeypatch.setattr('spoon_local.main.initialize_database',denied)
    operational=tmp_path/'operational.sqlite3'
    operational.write_bytes(b'OPERATIONAL-SENTINEL')
    monkeypatch.setenv('SPOON_LOCAL_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('SPOON_CLIENT_SECRET','LOCAL-PRIVATE-SENTINEL')
    async def check():
        async with client_app(tmp_path) as (client,app):
            for _ in range(3):
                assert (await client.get('/api/health')).json()=={'status':'ok'}
                assert (await client.get('/api/donations')).json()['items']==[]
                assert (await client.get('/api/status')).json()['donation_events']=='stopped'
            assert app.state.database_path==tmp_path/'mock'/'mock-sse.sqlite3'
    asyncio.run(check())
    assert operational.read_bytes()==b'OPERATIONAL-SENTINEL'


def test_browser_start_stop_and_disconnect_with_private_fields_discarded(tmp_path,caplog):
    async def check():
        async with client_app(tmp_path) as (client,app):
            csrf=await login(client)
            page=await client.get('/auth/spoon/collection')
            assert page.status_code==200 and 'Mock 수집 시작' in page.text
            response=await client.post('/auth/spoon/collection/start',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
            assert response.status_code==303
            data=await wait_items(client)
            assert data['source_mode']=='mock' and data['observed_total_spoons']=='0.1'
            assert data['coverage']=='observed_only'
            text=json.dumps(data)+(await client.get('/api/status')).text
            for value in ['PRIVATE-USER-SENTINEL','PRIVATE-MESSAGE-SENTINEL','<script>','mock-access-','mock-refresh-']:
                assert value not in text+caplog.text
                assert value.encode() not in app.state.database_path.read_bytes()
            assert not set(data['items'][0])&{'nickname','message','user_id','stream_event_id','fingerprint'}
            status=(await client.get('/api/status')).json()
            assert status['donation_events']=='live' and status['broadcast']=='mock_live'
            response=await client.post('/auth/spoon/collection/stop',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
            assert response.status_code==303
            assert (await client.get('/api/status')).json()['donation_events']=='stopped'
            assert len((await client.get('/api/donations')).json()['items'])==1
            assert (await client.post('/auth/spoon/collection/start',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})).status_code==303
            response=await client.post('/auth/spoon/disconnect',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
            assert response.status_code==303
            assert (await client.get('/api/status')).json()['donation_events']=='auth_required'
            assert len((await client.get('/api/donations')).json()['items'])==1
    asyncio.run(check())


@pytest.mark.parametrize('attack',['csrf','origin','host','method'])
def test_collection_control_security(tmp_path,attack):
    async def check():
        async with client_app(tmp_path) as (client,app):
            csrf=await login(client)
            headers={'Origin':LOCAL_ORIGIN}
            if attack=='csrf':csrf='wrong'
            if attack=='origin':headers['Origin']='https://evil.example'
            if attack=='host':headers['Host']='127.0.0.1:8000'
            if attack=='method':response=await client.get('/auth/spoon/collection/start')
            else:response=await client.post('/auth/spoon/collection/start',data={'csrf':csrf},headers=headers)
            assert response.status_code==(405 if attack=='method' else 400)
            assert (await client.get('/api/status')).json()['donation_events']=='stopped'
    asyncio.run(check())


def test_auth_required_and_query_bounds(tmp_path):
    async def check():
        async with client_app(tmp_path) as (client,app):
            csrf=csrf_from(await client.get('/auth/spoon/prepare'))
            assert (await client.post('/auth/spoon/collection/start',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})).status_code==409
            for query in ['limit=0','limit=101','before_sequence=-1']:
                assert (await client.get('/api/donations?'+query)).status_code==422
    asyncio.run(check())


def test_default_api_never_reads_mock_records(tmp_path):
    async def check():
        async with client_app(tmp_path) as (client,app):
            csrf=await login(client)
            await client.post('/auth/spoon/collection/start',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
            await wait_items(client)
        default=create_app(tmp_path/'normal-test.sqlite3')
        async with default.router.lifespan_context(default):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=default),base_url=LOCAL_ORIGIN) as client:
                data=(await client.get('/api/donations')).json()
                assert data['source_mode']=='disabled' and data['items']==[]
    asyncio.run(check())


def test_database_query_failure_api_is_generic(tmp_path,monkeypatch):
    async def check():
        async with client_app(tmp_path) as (client,app):
            import sqlite3
            def denied(*args,**kwargs):raise sqlite3.OperationalError('PRIVATE-DB-DETAIL')
            monkeypatch.setattr('spoon_local.spoon.donation_repository.DonationRepository.recent',denied)
            response=await client.get('/api/donations')
            assert response.status_code==503 and 'PRIVATE' not in response.text
    asyncio.run(check())


def test_mock_launcher_requires_explicit_db_before_config_loading(monkeypatch):
    from spoon_local.__main__ import main
    def denied(*args,**kwargs):raise AssertionError('App launched without explicit DB')
    monkeypatch.setattr('sys.argv',['spoon_local','--mock-auth'])
    monkeypatch.setattr('spoon_local.__main__.uvicorn.run',denied)
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2
