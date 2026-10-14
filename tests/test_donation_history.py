import asyncio
from datetime import datetime,timezone
import socket
import httpx
import pytest
from spoon_local.spoon.donation_history import boundaries,search
from spoon_local.spoon.donation_repository import RealDonationRepository
from spoon_local.spoon.event_models import Donation
from spoon_local.main import create_real_app
from spoon_local.spoon.oauth import LOCAL_ORIGIN
from test_real_oauth import settings,pair,begin_browser

@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('No external network')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket.socket,'connect_ex',deny)

@pytest.fixture
def ledger(tmp_path):
    repo=RealDonationRepository(tmp_path/'real-sse.sqlite3');repo.initialize();return repo

NOW=datetime(2026,10,14,15,10,tzinfo=timezone.utc) # Seoul Oct 15

def add(repo,identifier,time,amount='0.1'):
    session=repo.session(None)
    return repo.append(Donation(amount,time,time,identifier),session)

def test_seoul_inclusive_dates_and_periods(ledger):
    lower,upper=boundaries('today',now=NOW)
    assert lower=='2026-10-14T15:00:00.000000+00:00' and upper=='2026-10-15T15:00:00.000000+00:00'
    for i,time in enumerate(['2026-10-14T14:59:59.000000+00:00',lower,'2026-10-15T14:59:59.000000+00:00',upper]):add(ledger,str(i),time)
    result=search(ledger,period='today',now=NOW)
    assert result['total']==2 and result['statistics']['amount_spoons']=='0.2'
    assert result['statistics']['today_event_count']==2
    assert search(ledger,period='custom',start='2026-10-15',end='2026-10-15',now=NOW)['total']==2
    assert boundaries('7d',now=NOW)[0]=='2026-10-08T15:00:00.000000+00:00'
    assert boundaries('30d',now=NOW)[0]=='2026-09-15T15:00:00.000000+00:00'

def test_pages_snapshot_decimal_and_duplicates(ledger):
    time='2026-10-14T15:00:00.000000+00:00'
    for i in range(25):add(ledger,str(i),time)
    assert add(ledger,'0',time)=='duplicate'
    first=search(ledger,now=NOW);second=search(ledger,page=2,snapshot=first['snapshot'],now=NOW)
    assert first['total']==25 and first['statistics']['amount_spoons']=='2.5'
    assert len(first['items'])==12 and first['pages']==3
    assert not {row['record_id'] for row in first['items']} & {row['record_id'] for row in second['items']}
    add(ledger,'new',time)
    assert search(ledger,page=2,snapshot=first['snapshot'],now=NOW)['items']==second['items']
    assert len(search(ledger,page_size=24)['items'])==24
    assert search(ledger,page=10000)['items']==[]
    assert first['unit']=='spoon' and first['coverage']=='observed_stored_only'
    assert all('user_id' not in row and 'nickname' not in row for row in first['items'])

@pytest.mark.parametrize('kwargs',[{'page':0},{'page':10001},{'page_size':1000},{'period':'invalid'},{'period':'custom','start':'2026-10-16','end':'2026-10-15'},{'period':'custom','start':"' OR 1=1",'end':'2026-10-15'},{'period':'all','start':'2026-10-15'},{'snapshot':-1}])
def test_invalid_filters(ledger,kwargs):
    with pytest.raises(ValueError):search(ledger,**kwargs)

def test_empty_and_no_creation(tmp_path,ledger):
    assert search(ledger)['total']==0
    missing=RealDonationRepository(tmp_path/'missing'/'real-sse.sqlite3')
    assert search(missing)['items']==[] and not missing.path.exists()

def test_api_local_history_access_and_security(ledger):
    async def check():
        add(ledger,'one','2026-10-14T15:00:00.000000+00:00')
        before=ledger.path.read_bytes()
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair())),real_sse_db=ledger.path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                assert (await client.get('/api/donation-history')).status_code==401
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-history-code'})
                assert (await client.get('/api/donation-history')).status_code==403
                assert (await client.get('/api/donations')).status_code==403
                endpoint='/auth/spoon/history/allow'
                values={'csrf':csrf,'local_owner':'confirmed'}
                assert (await client.post(endpoint,data=values)).status_code==400
                assert (await client.post(endpoint,data={**values,'csrf':'wrong'},headers={'Origin':LOCAL_ORIGIN})).status_code==400
                assert (await client.post(endpoint,data=values,headers={'Origin':LOCAL_ORIGIN})).status_code==200
                result=await client.get('/api/donation-history')
                assert result.json()['total']==1 and result.headers['cache-control']=='no-store'
                for query in ['page=10001','page_size=13','start=bad','period=custom&start=bad&end=bad','page=1&page=2']:
                    assert (await client.get('/api/donation-history?'+query)).status_code==400
                assert (await client.get('/api/donation-history',headers={'Host':'127.0.0.1:8000'})).status_code==400
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as stranger:
                    assert (await stranger.get('/api/donation-history')).status_code==401
        assert ledger.path.read_bytes()==before
    asyncio.run(check())


def test_received_time_fallback(ledger):
    session=ledger.session(None)
    ledger.append(Donation('1.25','','2026-10-14T15:00:00.000000+00:00','fallback'),session)
    data=search(ledger,period='today',now=NOW)
    assert data['total']==1 and data['items'][0]['time_basis']=='received'
    assert data['statistics']['amount_spoons']=='1.25'


def test_access_expires_and_relogin_does_not_inherit(ledger):
    from spoon_local.main import create_app
    from spoon_local.spoon.real_oauth import RealOAuthClient
    from spoon_local.spoon.auth_service import RealAuthService
    from spoon_local.spoon.stream_service import RealStreamService
    from test_real_oauth import Clock
    async def check():
        clock=Clock();auth=RealAuthService(settings(),RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair()))),clock)
        stream=RealStreamService(auth,ledger,lambda:None)
        app=create_app(auth_service=auth,stream_service=stream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'history-first-code'})
                values={'csrf':csrf,'local_owner':'confirmed'}
                assert (await client.post('/auth/spoon/history/allow',data=values,headers={'Origin':LOCAL_ORIGIN})).status_code==200
                assert (await client.get('/api/donation-history')).status_code==200
                clock.now+=901
                assert (await client.get('/api/donation-history')).status_code==403
                auth.close()
                _,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'history-second-code'})
                assert (await client.get('/api/donation-history')).status_code==403
                assert stream.task is None
    asyncio.run(check())


def test_frontend_history_state_and_xss():
    from pathlib import Path
    import shutil,subprocess
    node=shutil.which('node')
    if node is None:pytest.skip('Node unavailable')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([node,'tests/history_ui_check.cjs'],cwd=root,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
