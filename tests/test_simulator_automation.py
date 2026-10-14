import asyncio,re
import httpx,pytest
from spoon_local.simulator_automation import Automation
from spoon_local.simulator import BroadcastSimulator,create_simulator_app


def enabled(**overrides):
    engine=Automation(clock=lambda:0)
    engine.configure({**engine.settings,'thanks':True,'announcement':True,**overrides})
    engine.context(True,True)
    return engine


def test_default_off_and_anonymous_valid_donation():
    engine=Automation(clock=lambda:0);engine.context(True,True);engine.donation('1','3')
    assert engine.records[-1]['state']=='disabled' and not engine.queue
    engine.configure({**engine.settings,'thanks':True});engine.donation('2','3');engine.tick()
    assert engine.records[-1]['state']=='mock_sent'
    assert engine.records[-1]['message']=='소중한 3스푼 후원 감사합니다! 💛'
    engine.donation('2','3');assert engine.records[-1]['state']=='duplicate'


@pytest.mark.parametrize('value',['0','-1','NaN','Infinity','oops',None])
def test_invalid_amount(value):
    engine=enabled();engine.donation(None,value)
    assert engine.records[-1]['state']=='invalid' and not engine.queue


@pytest.mark.parametrize('text',['{nickname}','{spoons.x}','{spoons!r}','{spoons:20}','{','{}',' ','💛'*101])
def test_template_rejection(text):
    engine=enabled()
    with pytest.raises(ValueError):engine.configure({**engine.settings,'thanks_template':text})


def test_utf16_preview_and_plain_text():
    engine=enabled(thanks_template='💛'*100)
    assert engine.template('💛'*100,'thanks')=='💛'*100
    engine.configure({**engine.settings,'thanks_template':'<script>{spoons}</script>'})
    engine.donation('x','3');engine.tick()
    assert engine.records[-1]['message']=='<script>3</script>'


def test_cooldown_spacing_queue_limit_and_expiry():
    engine=enabled();engine.donation('a','3');engine.tick();engine.donation('b','3')
    assert engine.records[-1]['state']=='cooldown'
    engine.advance(10);engine.donation('c','3');engine.tick()
    assert engine.records[-1]['state']=='mock_sent'
    for _ in range(21):engine.enqueue('thanks')
    assert len(engine.queue)==20 and engine.records[-1]['state']=='queue_full'
    engine.tick();assert len(engine.queue)==20
    engine.advance(61);engine.tick();assert engine.records[-1]['reason']=='expired'


def test_announcement_delayed_and_large_advance_no_burst():
    engine=enabled();engine.tick();assert not engine.records
    engine.advance(899);engine.tick();assert not engine.records
    engine.advance(1);engine.tick();assert engine.records[-1]['state']=='mock_sent'
    engine.advance(86400);engine.tick()
    assert sum(row['state']=='mock_sent' for row in engine.records)==2
    assert engine.snapshot()['next_announcement_in']==900


@pytest.mark.parametrize('error,state',[('500','failed'),('401','failed'),('403','failed'),('429','failed'),('timeout','uncertain'),('uncertain','uncertain')])
def test_mock_failures_no_retries(error,state):
    engine=enabled();engine.donation('a','3');engine.tick(error)
    assert engine.records[-1]['state']==state and not engine.queue
    count=len(engine.records);engine.advance(10);engine.tick()
    assert len(engine.records)==count


def test_off_stop_end_disconnect_and_no_reconnect_backlog():
    engine=enabled();engine.enqueue('thanks');engine.configure({**engine.settings,'thanks':False})
    assert not engine.queue and engine.records[-1]['reason']=='disabled'
    engine.enqueue('announcement');engine.context(True,False)
    assert not engine.queue and engine.records[-1]['reason']=='disconnected'
    engine.advance(3600);engine.context(True,True);engine.tick()
    assert engine.records[-1]['state']=='cancelled'
    engine.enqueue('announcement');engine.context(False,False)
    assert not engine.queue and engine.next_at is None
    engine.stop();assert not engine.settings['announcement']


def test_pipeline_duplicate_end_change_reset():
    sim=BroadcastSimulator()
    try:
        sim.start();sim.automation.configure({**sim.automation.settings,'thanks':True})
        sim.action('donation');sim.action('duplicate')
        assert sum(row['state']=='mock_sent' for row in sim.automation.records)==1
        assert sim.automation.records[-1]['state']=='duplicate'
        sim.action('change');assert not sim.automation.queue
        sim.action('end');assert not sim.automation.live
        sim.reset();assert not sim.automation.settings['thanks'] and not sim.automation.records
    finally:sim.repo.close()


def test_automation_route_csrf_session_and_sim_only():
    async def check():
        app=create_simulator_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:8000') as client:
                assert (await client.get('/api/simulator/automation')).status_code==401
                page=await client.get('/broadcast');assert 'automation-panel' in page.text
                csrf=re.search(r'id="sim-csrf" type="hidden" value="([^"]+)"',page.text)[1]
                settings=(await client.get('/api/simulator/automation')).json()['settings']
                values={**settings,'thanks':'true','announcement':'true','command':'configure','csrf':csrf}
                assert (await client.post('/api/simulator/automation',data=values,headers={'Origin':'http://evil.invalid'})).status_code==400
                assert (await client.post('/api/simulator/automation',data=values,headers={'Origin':'http://localhost:8000'})).status_code==200
                assert (await client.post('/api/simulator/automation',data={'csrf':csrf,'command':'advance','seconds':'999999'},headers={'Origin':'http://localhost:8000'})).status_code==400
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost:8000') as stranger:
                    assert (await stranger.get('/api/simulator/automation')).status_code==401
    asyncio.run(check())


def test_automation_javascript():
    import subprocess
    result=subprocess.run(['node','tests/automation_ui_check.cjs'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
