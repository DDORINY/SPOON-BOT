import asyncio,json,re,socket,sqlite3
import httpx,pytest
from spoon_local.bot_settings import BotSettingsStore,SettingsConflict
from spoon_local.simulator import create_simulator_app,BroadcastSimulator
from spoon_local.spoon.realtime_events import RealtimeBuffer,parse
from spoon_local.spoon.sse_parser import SSEFrame

@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def deny(*args,**kwargs):raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket.socket,'connect_ex',deny)


def test_settings_reopen_and_optimistic_version(tmp_path):
    path=tmp_path/'bot-settings.sqlite3';first=BotSettingsStore(path);second=BotSettingsStore(path)
    try:
        original=first.read();assert not original['settings']['thanks']
        data=first.save({**original['settings'],'thanks':True},original['version'])
        with pytest.raises(SettingsConflict):second.save(original['settings'],original['version'])
        assert data['version']==2 and data['schema_version']==1
    finally:first.close();second.close()
    reopened=BotSettingsStore(path)
    try:assert reopened.read()['settings']['thanks'] and reopened.read()['version']==2
    finally:reopened.close()
    assert path.stat().st_mode&0o777==0o600


@pytest.mark.parametrize('key,value',[('thanks','true'),('cooldown',9),('interval',59),('interval',86401),('thanks_template','{nickname}'),('thanks_template','{spoons.__class__}'),('thanks_template','💛'*101),('announcement_template',' '),('announcement_template','{spoons}')])
def test_invalid_settings_leave_saved_version_unchanged(key,value):
    store=BotSettingsStore()
    try:
        original=store.read()
        with pytest.raises(ValueError):store.save({**original['settings'],key:value},original['version'])
        assert store.read()==original
    finally:store.close()


def test_settings_store_rejects_existing_foreign_schema(tmp_path):
    path=tmp_path/'bot-settings.sqlite3'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE sentinel(value TEXT)');db.execute("INSERT INTO sentinel VALUES ('keep')")
    with pytest.raises(ValueError):BotSettingsStore(path)
    with sqlite3.connect(path) as db:assert db.execute('SELECT value FROM sentinel').fetchone()[0]=='keep'


def test_unified_official_roles_never_guess_bot_from_name():
    for is_dj,expected in [(True,'dj'),(False,'viewer')]:
        payload={'user':{'id':'official-event-test-id','nickname':'BOT 관리자'},'message':'자동화 템플릿 같아도 분류 근거 아님','isDj':is_dj,'sentTime':'2026-10-15T01:00:00Z'}
        frame=SSEFrame('chat',json.dumps(payload), '1-0')
        result=parse(frame);assert result['role']==expected and 'id' not in result
        buffer=RealtimeBuffer();assert buffer.accept(frame,{'events.chat'})=='accepted'
        assert buffer.accept(frame,{'events.chat'})=='duplicate'
        assert buffer.snapshot({'events.chat'})['chats'][0]['role']==expected


def test_simulated_roles_and_no_manual_echo():
    sim=BroadcastSimulator()
    try:
        sim.start()
        for command in ['chat','dj_chat','bot_chat','unknown_chat']:sim.action(command)
        assert list(sim.roles.values())==['viewer','dj','bot','unknown']
        assert [item['role'] for item in sim.buffer.snapshot({'events.chat'})['chats']]==['viewer','dj','viewer','viewer']
        sim.action('end');assert sim.buffer.snapshot({'events.chat'})['chats']==[]
    finally:sim.repo.close()


def test_persistent_settings_confirm_each_round_and_session(tmp_path):
    path=tmp_path/'bot-settings.sqlite3'
    async def check():
        app=create_simulator_app(settings_path=path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:8000') as client:
                page=await client.get('/broadcast');csrf=re.search(r'id="sim-csrf" type="hidden" value="([^"]+)"',page.text)[1]
                async def post(url,values):return await client.post(url,data={'csrf':csrf,**values},headers={'Origin':'http://localhost:8000'})
                async def control(action):return await post('/api/simulator/control',{'action':action})
                saved=(await client.get('/api/simulator/bot-settings')).json()
                values={**saved['settings'],'thanks':'true','announcement':'true','version':saved['version']}
                assert (await post('/api/simulator/bot-settings',values)).status_code==200
                assert (await post('/api/simulator/bot-settings',values)).status_code==409
                assert (await client.post('/api/simulator/bot-settings',data={'csrf':csrf,**values},headers={'Origin':'http://bad.invalid'})).status_code==400
                await control('start');status=(await client.get('/api/simulator/automation')).json()
                assert status['settings']['thanks'] and status['run_state']=='ready'
                await control('donation');assert not any(row['mock'] for row in (await client.get('/api/simulator/automation')).json()['records'])
                command={'command':'start','confirmation':'confirmed','broadcast_id':status['broadcast_id'],'context':status['confirmation_context'],'version':status['version']}
                assert (await post('/api/simulator/automation',{**command,'confirmation':'no'})).status_code==400
                assert (await post('/api/simulator/automation',command)).json()['run_state']=='running'
                assert (await post('/api/simulator/automation',command)).status_code==400
                await control('donation');status=(await client.get('/api/simulator/automation')).json()
                assert any(row['mock'] for row in status['records'])
                # Mock ACK never fabricates an official received chat.
                assert (await client.get('/api/realtime-events')).json()['chats']==[]
                current=(await client.get('/api/simulator/bot-settings')).json();new_values={**current['settings'],'thanks':'true','announcement':'true','version':current['version']}
                assert (await post('/api/simulator/bot-settings',new_values)).status_code==200
                assert (await client.get('/api/simulator/automation')).json()['run_state']=='ready'
                await control('disconnect');await control('reconnect')
                assert (await client.get('/api/simulator/automation')).json()['run_state']=='ready'
                assert (await post('/api/simulator/automation',command)).status_code==400
                await control('change');assert (await post('/api/simulator/automation',command)).status_code==400
                for action in ['dj_chat','bot_chat','unknown_chat']:await control(action)
                chats=(await client.get('/api/realtime-events')).json()['chats']
                assert [row['role'] for row in chats]==['dj','bot','unknown']
                assert all(row['simulation'] and row['role_basis']=='simulator_metadata' for row in chats)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:8000') as stranger:
                    assert (await stranger.get('/api/simulator/bot-settings')).status_code==401
                await control('end');assert (await client.get('/api/simulator/automation')).json()['run_state']=='stopped'
        # New app instance/session preserves settings, never confirmation/run state.
        restarted=create_simulator_app(settings_path=path)
        async with restarted.router.lifespan_context(restarted):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted),base_url='http://localhost:8000') as client:
                await client.get('/broadcast');status=(await client.get('/api/simulator/automation')).json()
                assert status['settings']['thanks'] and status['run_state']=='stopped' and not status['records']
    asyncio.run(check())


def test_settings_survive_new_python_process(tmp_path):
    import subprocess,sys
    path=tmp_path/'bot-settings.sqlite3';store=BotSettingsStore(path)
    store.save({**store.read()['settings'],'thanks':True},1);store.close()
    code='from spoon_local.bot_settings import BotSettingsStore; import sys; store=BotSettingsStore(sys.argv[1]); value=store.read(); assert value["settings"]["thanks"] is True and value["version"]==2; store.close(); print("settings retained")'
    result=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='settings retained'
