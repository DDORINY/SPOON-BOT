import asyncio
import json
import socket
import sqlite3
from dataclasses import replace

import pytest

from spoon_local.spoon.auth_service import AuthService
from spoon_local.spoon.donation_repository import DonationRepository
from spoon_local.spoon.oauth import OAuthSettings, MockOAuthClient
from spoon_local.spoon.sse_source import MockSSESource, MockConnection
from spoon_local.spoon.stream_service import MockStreamService


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs): raise AssertionError('Network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)


def donation_wire(event_id='1000-0', amount=1.25):
    payload = {'user': {'id': 'PRIVATE-USER', 'nickname': '<img onerror=alert(1)>'}, 'amount': amount, 'message': 'PRIVATE-MESSAGE', 'time': '2026-10-14T00:00:00Z'}
    return ((f'id:{event_id}\n' if event_id else '') + 'event:donation\ndata:' + json.dumps(payload) + '\n\n').encode()


def end(reason):
    return f'event:end\ndata:{{"reason":"{reason}"}}\n\n'.encode()


async def ready(tmp_path, connections, **kwargs):
    repo = DonationRepository(tmp_path / 'mock-sse.sqlite3')
    repo.initialize()
    auth = AuthService(OAuthSettings.mock(), MockOAuthClient())
    session = auth.prepare(None)
    state = await auth.begin(session.identifier)
    assert await auth.complete(session.identifier, state, auth.client.issue_code(), None)
    source = MockSSESource(connections)
    delays = []
    async def sleep(delay):
        delays.append(delay)
        await asyncio.sleep(0.005)
    service = MockStreamService(auth, repo, lambda:source, sleep=sleep, jitter=lambda:0, **kwargs)
    return service, repo, auth, source, delays


async def wait_for(predicate):
    for _ in range(200):
        if predicate(): return
        await asyncio.sleep(0.005)
    raise AssertionError('Collector condition not reached')


def test_collect_invalid_duplicate_conflict_and_idless(tmp_path):
    async def check():
        chunks = (donation_wire(), donation_wire(), donation_wire(amount=2), donation_wire(None), donation_wire(None), b'event:donation\ndata:{\n\n', end('LIVE_ENDED'))
        service, repo, auth, source, delays = await ready(tmp_path, [MockConnection(chunks, live_id='mock-1')])
        assert await service.start()
        assert not await service.start()
        await wait_for(lambda: service.counts['inserted']==3 and service.counts['invalid']==1)
        await service.stop()
        assert service.counts == {'inserted':3,'duplicate':1,'conflict':1,'invalid':1}
        data = repo.recent()
        assert data['issues']['idless'] == 2 and data['issues']['conflicts'] == 1
        assert 'PRIVATE' not in json.dumps(data)
        assert source.connections[0].closed
        assert data['coverage']=='observed_only'
    asyncio.run(check())


def test_reconnect_and_gap_do_not_duplicate_or_claim_completeness(tmp_path):
    async def check():
        connections = [MockConnection((donation_wire(),),live_id='same'), MockConnection((donation_wire(),donation_wire('1001-0'),end('LIVE_ENDED')),live_id='same')]
        service, repo, auth, source, delays = await ready(tmp_path, connections)
        await service.start()
        await wait_for(lambda: service.counts['inserted']==2)
        await service.stop()
        assert service.counts['duplicate']==1
        assert delays[0]==1
        with repo.connection() as db:
            assert db.execute('SELECT COUNT(*) FROM broadcast_sessions').fetchone()[0]==1
            gap=db.execute("SELECT completeness,recovered_at_utc FROM collection_gaps WHERE reason_code='unexpected_eof'").fetchone()
            assert gap[0]=='unknown' and gap[1]
    asyncio.run(check())


@pytest.mark.parametrize('status,detail,state', [(403,None,'PERMISSION_REQUIRED'),(403,'OAPI_MNGR_0209','BLOCKED'),(400,None,'STOPPED')])
def test_terminal_connection_states(tmp_path,status,detail,state):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection(status=status,detail_code=detail)])
        await service.start()
        await wait_for(lambda: service.task.done())
        assert service.state==state and source.open_calls==1
    asyncio.run(check())


@pytest.mark.parametrize('reason', ['TOKEN_EXPIRED','RECONNECT'])
def test_end_refresh_or_immediate_reconnect(tmp_path,reason):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection((end(reason),)),MockConnection((donation_wire(),),hold_open=True)])
        await service.start()
        await wait_for(lambda: service.counts['inserted']==1)
        assert auth.client.refresh_calls==(1 if reason=='TOKEN_EXPIRED' else 0)
        assert delays==[]
        await service.stop()
    asyncio.run(check())


def test_refresh_failure_and_scope_loss_stop_collection(tmp_path):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection((end('TOKEN_EXPIRED'),))])
        auth.client.fail_refresh=True
        await service.start()
        await wait_for(lambda: service.task.done())
        assert service.state=='AUTH_REQUIRED' and auth.state=='reauth_required'
    asyncio.run(check())


def test_explicit_start_requires_mock_auth_and_scope(tmp_path):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[])
        auth.store.replace(replace(auth.store.pair, scopes=frozenset({'live.read'})))
        assert not await service.start() and service.state=='PERMISSION_REQUIRED'
        auth.close()
        assert not await service.start() and service.state=='AUTH_REQUIRED'
        assert source.open_calls==0
    asyncio.run(check())


@pytest.mark.parametrize('status,retry,delay', [(404,None,5),(429,'7',7),(429,'invalid',1),(502,None,1)])
def test_backoff_statuses(tmp_path,status,retry,delay):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection(status=status,retry_after=retry),MockConnection((donation_wire(),),hold_open=True)])
        await service.start()
        await wait_for(lambda:service.counts['inserted']==1)
        assert delays[0]==delay
        await service.stop()
    asyncio.run(check())


def test_401_refresh_once_then_stop(tmp_path):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection(status=401),MockConnection(status=401)])
        await service.start()
        await wait_for(lambda:service.task.done())
        assert auth.client.refresh_calls==1 and source.open_calls==2
        assert service.state=='AUTH_REQUIRED'
    asyncio.run(check())


def test_storage_failure_then_explicit_restart(tmp_path,monkeypatch):
    async def check():
        connections=[MockConnection((donation_wire(),),hold_open=True),MockConnection((donation_wire(),),hold_open=True)]
        service,repo,auth,source,delays=await ready(tmp_path,connections)
        original=repo.append
        def broken(*args):raise sqlite3.OperationalError('PRIVATE-STORAGE-DETAIL')
        monkeypatch.setattr(repo,'append',broken)
        await service.start()
        await wait_for(lambda:service.task.done())
        assert service.state=='STORAGE_ERROR' and not repo.recent()['items']
        monkeypatch.setattr(repo,'append',original)
        await service.start()
        await wait_for(lambda:service.counts['inserted']==1)
        await service.stop()
        assert repo.recent()['issues']['gaps']>=1
    asyncio.run(check())


def test_queue_overflow_and_uncommitted_gap(tmp_path,monkeypatch):
    async def check():
        bulk=b''.join(donation_wire(f'1000-{index}') for index in range(10))
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection((bulk,))],queue_size=1)
        await service.start()
        await wait_for(lambda:service.task.done())
        assert service.state=='STOPPED'
        with repo.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM collection_gaps WHERE reason_code='queue_overflow'").fetchone()[0]==1
        assert len(repo.recent()['items'])==1
    asyncio.run(check())


def test_process_restart_recovers_marked_db_without_duplicate(tmp_path):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection((donation_wire(),),hold_open=True)])
        await service.start()
        await wait_for(lambda:service.counts['inserted']==1)
        await service.close()
        second,repo2,auth2,source2,delays2=await ready(tmp_path,[MockConnection((donation_wire(),),hold_open=True)])
        await second.start()
        await wait_for(lambda:second.counts['duplicate']==1)
        await second.close()
        assert len(repo2.recent()['items'])==1
    asyncio.run(check())


def test_stop_discards_uncommitted_queue_with_gap_and_restart(tmp_path):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection((donation_wire(),),hold_open=True)])
        await service.start()
        await wait_for(lambda:service.counts['inserted']==1)
        await service.stop()
        assert service.queue.empty() and service.task.done() and source.connections[0].closed
        with repo.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM collection_gaps WHERE reason_code='manual_stop'").fetchone()[0]==1
    asyncio.run(check())


def test_storage_gap_is_persisted_after_recovery(tmp_path,monkeypatch):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection((donation_wire(),),hold_open=True),MockConnection((donation_wire(),),hold_open=True)])
        original_append,original_gap=repo.append,repo.gap
        def broken(*args):raise sqlite3.OperationalError('PRIVATE-STORAGE')
        monkeypatch.setattr(repo,'append',broken)
        monkeypatch.setattr(repo,'gap',broken)
        await service.start()
        await wait_for(lambda:service.task.done())
        assert service.gap_persistence_failed
        monkeypatch.setattr(repo,'append',original_append)
        monkeypatch.setattr(repo,'gap',original_gap)
        await service.start()
        await wait_for(lambda:service.counts['inserted']==1)
        assert not service.gap_persistence_failed
        with repo.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM collection_gaps WHERE reason_code='storage_error'").fetchone()[0]==1
        await service.stop()
    asyncio.run(check())


def test_runtime_writes_share_one_writer_task(tmp_path,monkeypatch):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection((donation_wire(),)),MockConnection((donation_wire('1001-0'),),hold_open=True)])
        tasks=[]
        for name in ['session','append','gap','recover_gaps']:
            original=getattr(repo,name)
            def wrapped(*args,original=original):
                tasks.append(asyncio.current_task())
                return original(*args)
            monkeypatch.setattr(repo,name,wrapped)
        await service.start()
        await wait_for(lambda:service.counts['inserted']==2)
        assert tasks and len(set(tasks))==1
        await service.stop()
    asyncio.run(check())


def test_untrusted_source_error_text_never_logged(tmp_path,monkeypatch,caplog):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[])
        def broken():raise RuntimeError('PRIVATE-SOURCE-SENTINEL')
        service.source_factory=broken
        assert not await service.start()
        assert 'PRIVATE-SOURCE-SENTINEL' not in caplog.text
        assert service.state=='STOPPED'
    asyncio.run(check())


def test_runtime_scope_loss_stops_without_further_ingestion(tmp_path):
    async def check():
        released=asyncio.Event()
        class ControlledConnection(MockConnection):
            async def read(self):
                yield donation_wire()
                await released.wait()
                yield donation_wire('1001-0')
        service,repo,auth,source,delays=await ready(tmp_path,[ControlledConnection()])
        await service.start()
        await wait_for(lambda:service.counts['inserted']==1)
        auth.store.replace(replace(auth.store.pair,scopes=frozenset({'live.read'})))
        released.set()
        await wait_for(lambda:service.task.done())
        assert service.state=='PERMISSION_REQUIRED'
        assert len(repo.recent()['items'])==1
        assert source.connections[0].closed
    asyncio.run(check())


def test_network_interruption_reconnects_and_closes_source(tmp_path):
    class BrokenConnection(MockConnection):
        async def read(self):
            raise ConnectionError('PRIVATE-NETWORK-DETAIL')
            yield b''
    async def check():
        broken=BrokenConnection()
        service,repo,auth,source,delays=await ready(tmp_path,[broken,MockConnection((donation_wire(),),hold_open=True)])
        await service.start()
        await wait_for(lambda:service.counts['inserted']==1)
        assert delays[0]==1 and broken.closed
        await service.stop()
        with repo.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM collection_gaps WHERE reason_code='network_interruption'").fetchone()[0]==1
    asyncio.run(check())


def test_incomplete_frame_gap_does_not_create_donation(tmp_path):
    async def check():
        service,repo,auth,source,delays=await ready(tmp_path,[MockConnection((b'event:donation\ndata:{"amount":1}',)),MockConnection(hold_open=True)])
        await service.start()
        await wait_for(lambda:source.open_calls>=2)
        await service.stop()
        assert repo.recent()['items']==[]
        with repo.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM collection_gaps WHERE reason_code='incomplete_frame'").fetchone()[0]==1
    asyncio.run(check())
