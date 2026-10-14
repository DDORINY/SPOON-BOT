import sqlite3
import pytest
from spoon_local.spoon.broadcast_rounds import RoundRepository,migrate_rounds,version
from spoon_local.spoon.donation_repository import RealDonationRepository,DonationRepository
from spoon_local.spoon.event_models import normalize_donation
from spoon_local.spoon.sse_parser import SSEParser
from test_mock_stream import donation_wire


@pytest.fixture(autouse=True)
def block_external_sockets(monkeypatch):
    import socket
    def deny(*args, **kwargs):
        raise AssertionError('External socket forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)


def event(identifier='1000-0'):
    return normalize_donation(SSEParser().feed(donation_wire(identifier))[0])


def ready(tmp_path):
    repo=RealDonationRepository(tmp_path/'real-sse.sqlite3');repo.initialize();migrate_rounds(repo)
    return repo,RoundRepository(repo)


def test_explicit_migration_preserves_legacy_and_idempotency(tmp_path):
    repo=RealDonationRepository(tmp_path/'real-sse.sqlite3');repo.initialize()
    key=repo.session(None);repo.append(event(),key)
    with repo.connection() as db:
        before=db.execute('SELECT * FROM donations').fetchall()
        salt=db.execute('SELECT * FROM real_metadata').fetchall()
        indexes=db.execute("SELECT sql FROM sqlite_master WHERE name='donation_id'").fetchall()
    migrate_rounds(repo);migrate_rounds(repo);repo.initialize()
    assert version(repo)==2
    with repo.connection() as db:
        rows=db.execute('SELECT * FROM donations').fetchall()
        assert tuple(rows[0][:-3])==before[0] and rows[0][-3:]==(None,None,'unconfirmed')
        assert db.execute('SELECT * FROM real_metadata').fetchall()==salt
        assert db.execute("SELECT sql FROM sqlite_master WHERE name='donation_id'").fetchall()==indexes
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_rounds_identity_unknown_end_and_restart(tmp_path):
    repo,rounds=ready(tmp_path)
    one=rounds.observe('account-a',100,'2026-10-14T00:00:00Z')
    assert rounds.observe('account-a',100,'2026-10-14T00:00:01Z')==one
    two=rounds.observe('account-a',101,'2026-10-14T02:00:00Z')
    other=rounds.observe('account-b',100)
    unknown=rounds.observe('account-a')
    assert len({one,two,other,unknown})==4
    assert rounds.detail('account-a',other) is None
    assert rounds.detail('account-a',unknown)['provider_live_id'] is None
    rounds.end_observed('account-a',one)
    info=rounds.detail('account-a',one)
    assert info['state']=='ended' and info['ended_at_utc'] is None and info['end_observed_at_utc']
    again=RoundRepository(RealDonationRepository(repo.path))
    assert again.detail('account-a',one)==info


def test_attribution_unknown_duplicates_and_account_isolation(tmp_path):
    repo,rounds=ready(tmp_path);session=repo.session(None)
    one=rounds.observe('account-a',100)
    assert repo.append(event(),session,account_key='account-a',round_id=one)=='inserted'
    two=rounds.observe('account-a',101)
    other=rounds.observe('account-b',100)
    assert repo.append(event(),session,account_key='account-a',round_id=two)=='duplicate'
    repo.append(event('1001-0'),session,account_key='account-a')
    assert len(rounds.donations('account-a',one)['items'])==1
    assert rounds.donations('account-a',two)['items']==[]
    assert len(rounds.donations('account-a',unconfirmed=True)['items'])==1
    assert rounds.donations('account-b')['items']==[]
    with pytest.raises(ValueError):repo.append(event('1002-0'),session,account_key='account-a',round_id=other)
    unknown=rounds.observe('account-a')
    with pytest.raises(ValueError):repo.append(event('1002-0'),session,account_key='account-a',round_id=unknown)


def test_date_cursor_and_injection(tmp_path):
    repo,rounds=ready(tmp_path);session=repo.session(None)
    for identifier in ['1000-0','1001-0','1002-0']:repo.append(event(identifier),session,account_key='account-a')
    data=rounds.donations('account-a',limit=2,date_from='2026-10-14T00:00:00Z',date_to='2026-10-15T00:00:00Z')
    assert len(data['items'])==2
    assert len(rounds.donations('account-a',before=data['next_before'])['items'])==1
    assert rounds.donations("account-a' OR 1=1 --")['items']==[]
    assert rounds.donations('account-a',round_id="' OR 1=1 --")['items']==[]
    for kwargs in [{'limit':101},{'before':0},{'date_from':'invalid'},{'date_from':'2026-10-15T00:00:00Z','date_to':'2026-10-14T00:00:00Z'},{'round_id':'x','unconfirmed':True}]:
        with pytest.raises(ValueError):rounds.donations('account-a',**kwargs)


def test_migration_rollback_and_wrong_database(tmp_path,monkeypatch):
    from spoon_local.spoon import broadcast_rounds
    repo=RealDonationRepository(tmp_path/'real-sse.sqlite3');repo.initialize()
    monkeypatch.setattr(broadcast_rounds,'MIGRATION',broadcast_rounds.MIGRATION+['BROKEN SQL'])
    with pytest.raises(sqlite3.Error):migrate_rounds(repo)
    assert version(repo)==1
    with repo.connection() as db:
        assert 'account_key' not in {row[1] for row in db.execute('PRAGMA table_info(donations)')}
    mock=DonationRepository(tmp_path/'mock-sse.sqlite3');mock.initialize()
    with pytest.raises(ValueError):migrate_rounds(mock)


def test_round_api_authentication_and_migration_gate(tmp_path, verified_identity):
    import asyncio
    import httpx
    from spoon_local.main import create_real_app
    from spoon_local.spoon.oauth import LOCAL_ORIGIN
    from test_real_oauth import settings,pair,begin_browser
    async def check():
        path=tmp_path/'real-sse.sqlite3'
        repo=RealDonationRepository(path);repo.initialize();migrate_rounds(repo)
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair())),real_sse_db=path,live_transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'liveId':100,'title':'<script>safe text</script>','startedAt':'2026-10-14T00:00:00Z','listenerCount':2})))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                assert (await client.get('/api/broadcast-sessions')).status_code==401
                _,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-rounds-code'})
                assert (await client.get('/api/broadcast-sessions')).json()['items']==[]
                await client.get('/api/live')
                listed=(await client.get('/api/broadcast-sessions')).json()['items']
                assert len(listed)==1 and listed[0]['provider_live_id']=='100'
                assert 'account_key' not in listed[0]
                await client.get('/api/live')
                assert (await client.get('/api/broadcast-sessions')).json()['items']==listed
                assert (await client.get('/api/broadcast-sessions/'+listed[0]['round_id'])).status_code==200
                assert (await client.get('/api/session-donations?unconfirmed=true')).json()['items']==[]
                assert (await client.get('/api/session-donations?date_from=invalid')).status_code==422
                assert (await client.get('/api/session-donations?limit=101')).status_code==422
                assert (await client.get('/api/broadcast-sessions',headers={'Host':'127.0.0.1:8000'})).status_code==400
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as stranger:
                    assert (await stranger.get('/api/broadcast-sessions')).status_code==401
                # No source is constructed or started by these read-only calls.
                assert (await client.get('/api/helper')).json()['active'] is False
    asyncio.run(check())


def test_v2_writer_records_current_context_without_guessing_round(tmp_path, verified_identity):
    import asyncio,httpx
    from spoon_local.main import create_real_app
    from spoon_local.spoon.oauth import LOCAL_ORIGIN
    from test_real_oauth import settings,pair,begin_browser
    from test_real_sse import response,wire
    async def check():
        path=tmp_path/'real-sse.sqlite3';repo=RealDonationRepository(path);repo.initialize();migrate_rounds(repo)
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair())),real_sse_db=path,sse_transport=httpx.MockTransport(lambda r:response(wire()+wire(),True)),live_transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'liveId':100,'title':'title','startedAt':'2026-10-14T00:00:00Z','listenerCount':1})))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                csrf,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-v2-code'})
                await client.get('/api/live')
                assert len((await client.get('/api/broadcast-sessions')).json()['items'])==1
                await client.post('/auth/spoon/collection/start',data={'csrf':csrf,'dj_notice':'confirmed'},headers={'Origin':LOCAL_ORIGIN,'Accept':'application/json'})
                for _ in range(100):
                    items=(await client.get('/api/session-donations?unconfirmed=true')).json()['items']
                    if items:break
                    await asyncio.sleep(.005)
                assert len(items)==1 and items[0]['broadcast_round_id'] is None
                data=(await client.get('/api/donations')).json()
                assert data['observed_total_spoons']=='1.25' and isinstance(data['issues']['gaps'],int)
                await client.post('/auth/spoon/collection/stop',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN,'Accept':'application/json'})
    asyncio.run(check())


def test_legacy_owner_not_reassigned_and_cross_context_id_conflict(tmp_path):
    repo=RealDonationRepository(tmp_path/'real-sse.sqlite3');repo.initialize()
    session=repo.session(None);repo.append(event(),session)
    migrate_rounds(repo);rounds=RoundRepository(repo)
    assert rounds.donations('new-login')['items']==[]
    assert repo.append(event(),session,account_key='new-login')=='conflict'
    with repo.connection() as db:
        assert db.execute('SELECT account_key,broadcast_round_id FROM donations').fetchall()==[(None,None)]
        assert db.execute('SELECT account_key FROM event_conflicts').fetchone()[0]=='new-login'


def test_v1_round_api_explicit_migration_required(tmp_path, verified_identity):
    import asyncio,httpx
    from spoon_local.main import create_real_app
    from spoon_local.spoon.oauth import LOCAL_ORIGIN
    from test_real_oauth import settings,pair,begin_browser
    async def check():
        path=tmp_path/'real-sse.sqlite3';repo=RealDonationRepository(path);repo.initialize()
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=pair())),real_sse_db=path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                _,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-v1-code'})
                assert (await client.get('/api/broadcast-sessions')).json()['migration_required'] is True
                assert (await client.get('/api/session-donations')).status_code==409
                assert version(repo)==1
    asyncio.run(check())


@pytest.fixture
def verified_identity(monkeypatch):
    """Only a test injection; production has no verified identity resolver."""
    from spoon_local.spoon.auth_service import RealAuthService
    original=RealAuthService.complete
    async def complete(self,*args,**kwargs):
        success=await original(self,*args,**kwargs)
        if success:self.account_context='verified-test-dj'
        return success
    monkeypatch.setattr(RealAuthService,'complete',complete)


def test_oauth_does_not_infer_identity_from_undocumented_fields(tmp_path):
    import asyncio,httpx
    from spoon_local.main import create_real_app
    from spoon_local.spoon.oauth import LOCAL_ORIGIN
    from test_real_oauth import settings,pair,begin_browser
    async def check():
        path=tmp_path/'real-sse.sqlite3';repo=RealDonationRepository(path);repo.initialize();migrate_rounds(repo)
        session=repo.session(None);repo.append(event(),session)
        response={**pair(),'user_id':'untrusted','dj_id':'untrusted','sub':'untrusted','id_token':'unverified'}
        app=create_real_app(settings(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json=response)),real_sse_db=path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                _,state=await begin_browser(client)
                await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-unverified-code'})
                result=await client.get('/api/broadcast-sessions')
                assert result.status_code==409 and result.json()['detail']=='dj_identity_unverified'
                data=(await client.get('/api/donations')).json()
                assert data['items']==[] and data['identity_status']=='unverified'
                assert (await client.get('/api/session-donations')).status_code==409
                with repo.connection() as db:
                    assert db.execute('SELECT account_key,broadcast_round_id FROM donations').fetchall()==[(None,None)]
                    assert db.execute('SELECT COUNT(*) FROM broadcast_rounds').fetchone()[0]==0
    asyncio.run(check())
