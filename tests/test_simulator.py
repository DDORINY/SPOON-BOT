import asyncio,json,re,socket
import httpx,pytest
from spoon_local.simulator import create_simulator_app,BroadcastSimulator
from spoon_local.main import create_app
from spoon_local.config import Settings

@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)


def test_memory_pipeline_bounds_and_duplicates():
    sim=BroadcastSimulator();sim.start();sim.action('join',5);sim.action('chat',100);sim.action('like',2);sim.action('donation',3);sim.action('duplicate');sim.action('invalid')
    result=sim.results()
    assert result['chat_buffer_count']==100 and result['hearts']['observed_amount']=='60'
    assert result['donation_count']==3 and result['donation_spoons']=='9' and result['duplicates']==1 and result['invalid']==1
    assert sim.live['live_id'].startswith('sim-live-') and not hasattr(sim.repo,'path')
    with pytest.raises(ValueError):sim.action('chat',101)
    sim.action('leave',2);assert sim.results()['listeners']==3
    sim.action('disconnect');assert not sim.connected
    sim.action('reconnect');assert sim.connected
    sim.action('change');assert sim.live['live_id']=='sim-live-0002'
    sim.action('end');assert sim.live is None and sim.last_live['ended_at_utc']
    sim.repo.close()


def test_isolated_app_no_environment_or_real_clients(tmp_path,monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('Real paths forbidden')
    monkeypatch.setattr('spoon_local.main.load_real_oauth_settings',deny)
    monkeypatch.setattr('spoon_local.main.initialize_database',deny)
    monkeypatch.setattr('spoon_local.spoon.real_oauth.RealOAuthClient.__init__',deny)
    monkeypatch.setattr('spoon_local.spoon.real_sse.RealSSESource.__init__',deny)
    original=(tmp_path/'operational.sqlite3');original.write_bytes(b'OPERATIONAL-SENTINEL')
    async def check():
        app=create_simulator_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:8000') as client:
                assert (await client.get('/api/simulator/results')).status_code==401
                page=await client.get('/broadcast')
                assert page.status_code==200 and '시뮬레이션 모드' in page.text
                csrf=re.search(r'id="sim-csrf" type="hidden" value="([^"]+)"',page.text)[1]
                async def action(name,count=1):return await client.post('/api/simulator/control',data={'csrf':csrf,'action':name,'count':count},headers={'Origin':'http://localhost:8000'})
                assert (await action('start')).status_code==200
                await action('join',5);await action('donation',3);await action('duplicate')
                assert (await client.get('/api/live')).json()['live']['live_id'].startswith('sim-live-')
                assert (await client.get('/api/live/listeners')).json()['returned_count']==5
                assert (await client.get('/api/live/fans')).json()['items'][0]['spoon_count']=='9'
                assert (await client.get('/api/donation-history')).json()['statistics']['amount_spoons']=='9'
                assert (await client.get('/api/donations')).json()['source_mode']=='simulator'
                ticket=(await client.get('/api/live/chat/prepare')).json()['request_id']
                data={'csrf':csrf,'message':'[가상] hello','request_id':ticket,'notice':'confirmed'}
                assert (await client.post('/api/live/chat',json=data,headers={'Origin':'http://localhost:8000'})).json()['simulation']
                assert (await client.post('/auth/spoon/start')).status_code==404
                assert (await client.post('/api/simulator/control',data={'action':'end','csrf':csrf},headers={'Origin':'http://evil.invalid'})).status_code==400
                assert (await client.get('/api/simulator/results',headers={'Host':'127.0.0.1:8000'})).status_code==400
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:8000') as other:
                    assert (await other.get('/broadcast')).status_code==403
                    assert (await other.get('/api/simulator/results')).status_code==401
                await action('reset');assert (await client.get('/api/simulator/results')).json()['donation_count']==0
    asyncio.run(check());assert original.read_bytes()==b'OPERATIONAL-SENTINEL'


def test_scenario_stop_and_reset():
    async def check():
        sim=BroadcastSimulator();await sim.scenario('B');await asyncio.sleep(.01);await sim.stop();before=sim.sequence
        await asyncio.sleep(.55);assert sim.sequence==before
        sim.reset();assert sim.results()['event_attempts']==0
        for name in ['A','B','C','D']:
            await sim.scenario(name);await sim.stop()
        sim.repo.close()
    asyncio.run(check())


def test_normal_app_does_not_register_simulator(tmp_path):
    app=create_app(settings=Settings(tmp_path/'test.sqlite3'))
    assert not any(route.path.startswith('/api/simulator') for route in app.routes if hasattr(route,'path'))


def test_simulator_never_accepts_db_injection(tmp_path):
    with pytest.raises(TypeError):create_simulator_app(database_path=tmp_path/'real-sse.sqlite3')


def test_network_guard_blocks_external_http_and_dns(monkeypatch):
    from spoon_local.simulator_network import install_simulator_network_guard
    with monkeypatch.context() as patch:
        for name in ['connect','connect_ex','sendto']:patch.setattr(socket.socket,name,getattr(socket.socket,name))
        patch.setattr(socket,'getaddrinfo',lambda *args,**kwargs:['loopback-only'])
        install_simulator_network_guard()
        with socket.socket() as sock:
            with pytest.raises(PermissionError):sock.connect(('203.0.113.1',443))
        with pytest.raises(PermissionError):socket.getaddrinfo('developers.spooncast.net',443)
        assert socket.getaddrinfo('localhost',8000)==['loopback-only']


def test_cli_simulator_and_conflicting_db_flags(monkeypatch):
    import spoon_local.__main__ as launcher
    calls=[]
    monkeypatch.setattr('sys.argv',['spoon_local','--simulator'])
    monkeypatch.setattr('spoon_local.simulator_network.install_simulator_network_guard',lambda:None)
    monkeypatch.setattr(launcher,'create_real_app',lambda **kwargs:(_ for _ in ()).throw(AssertionError('Real factory forbidden')))
    def run(app,**kwargs):calls.append(kwargs)
    monkeypatch.setattr(launcher.uvicorn,'run',run)
    monkeypatch.setattr('spoon_local.simulator.create_simulator_app',lambda **kwargs:create_simulator_app())
    launcher.main();assert calls==[{'host':'127.0.0.1','port':8000,'workers':1}]
    for flags in [['--simulator','--real-oauth'],['--simulator','--real-sse-db','/tmp/real-sse.sqlite3'],['--simulator','--mock-db','/tmp/mock-sse.sqlite3']]:
        monkeypatch.setattr('sys.argv',['spoon_local',*flags])
        with pytest.raises(SystemExit):launcher.main()
    assert len(calls)==1


@pytest.mark.parametrize('error,state',[('401','reauth'),('403','permission_required'),('429','rate_limited'),('500','uncertain'),('timeout','uncertain'),('uncertain','uncertain')])
def test_virtual_send_errors_never_leave_memory(error,state):
    async def check():
        app=create_simulator_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:8000') as client:
                page=await client.get('/broadcast');csrf=re.search(r'id="sim-csrf" type="hidden" value="([^"]+)"',page.text)[1]
                async def command(name):return await client.post('/api/simulator/control',data={'csrf':csrf,'action':name},headers={'Origin':'http://localhost:8000'})
                await command('start');ticket=(await client.get('/api/live/chat/prepare')).json()['request_id'];await command(error)
                response=await client.post('/api/live/chat',json={'csrf':csrf,'request_id':ticket,'message':'[가상] only','notice':'confirmed'},headers={'Origin':'http://localhost:8000'})
                assert response.json()['state']==state and response.json()['simulation'] is True
                assert (await client.get('/api/simulator/results')).json()['donation_count']==0
    asyncio.run(check())


def test_scenario_a_complete_and_processed_counts():
    async def check():
        sim=BroadcastSimulator();await sim.scenario('A');await sim.task
        data=sim.results()
        assert data['generated']==data['processed']=={'chat':10,'like':1,'donation':3}
        assert data['donation_spoons']=='9' and data['last_broadcast']['ended_at_utc']
        sim.repo.close()
    asyncio.run(check())


def test_simulator_frontend_controls_and_xss():
    from pathlib import Path
    import shutil,subprocess
    node=shutil.which('node')
    if node is None:pytest.skip('Node unavailable')
    result=subprocess.run([node,'tests/simulator_ui_check.cjs'],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr

def test_simulator_unified_states_and_end_cleanup():
    from spoon_local.simulator import BroadcastSimulator
    sim=BroadcastSimulator()
    try:
        assert sim.results()['broadcast_state']=='waiting'
        assert sim.results()['connection_state']=='disconnected'
        sim.action('start');sim.action('join');sim.action('chat');sim.action('like');sim.action('donation')
        data=sim.results()
        assert data['broadcast_state']=='live' and data['connection_state']=='connected'
        assert data['generated']==data['processed']=={'chat':1,'like':1,'donation':1}
        assert data['fan_count']==1
        sim.action('disconnect');assert sim.results()['connection_state']=='reconnecting'
        sim.action('reconnect');assert sim.results()['connection_state']=='connected'
        sim.action('403');assert sim.results()['connection_state']=='error'
        sim.action('recover');assert sim.results()['connection_state']=='connected'
        sim.action('end');data=sim.results()
        assert data['broadcast_state']=='ended' and data['connection_state']=='disconnected'
        assert data['listeners']==data['fan_count']==data['chat_buffer_count']==0
        assert data['donation_count']==1
        assert data['last_command']=='end'
        sim.reset();assert sim.results()['donation_count']==0
    finally:sim.repo.close()

@pytest.mark.parametrize('path',['/','/broadcast','/capture','/gallery','/donations','/settings'])
def test_simulator_tools_server_page_isolation(path):
    async def check():
        app=create_simulator_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:8000') as client:
                response=await client.get(path)
                assert response.status_code==200
                assert '실제 SPOON 방송과 연결되지 않습니다' in response.text
                for marker in ['id="sim-controls"','id="sim-summary"','id="automation-panel"','src="/static/simulator.js"','src="/static/automation.js"']:
                    assert (marker in response.text)==(path=='/broadcast')
                # Returning and refreshing uses newly rendered page conditions.
                other=await client.get('/broadcast' if path!='/broadcast' else '/')
                assert other.status_code==200
                again=await client.get(path)
                assert ('id="sim-controls"' in again.text)==(path=='/broadcast')
    asyncio.run(check())
