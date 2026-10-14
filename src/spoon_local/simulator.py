"""Standalone simulator: no environment, HTTP clients, OAuth or file DB paths."""
import asyncio,json,secrets,sqlite3,time,hashlib
from contextlib import asynccontextmanager,contextmanager
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from spoon_local.spoon.donation_repository import DonationRepository,SCHEMA
from spoon_local.spoon.event_models import normalize_donation,utc_now,InvalidDonation
from spoon_local.spoon.sse_parser import SSEParser
from spoon_local.spoon.realtime_events import RealtimeBuffer
from spoon_local.spoon.audience import normalize
from spoon_local.spoon.chat_send import validate_message
from spoon_local.web.auth_routes import post_values
from spoon_local.spoon.auth_service import same
from spoon_local.simulator_automation import Automation
from spoon_local.bot_settings import BotSettingsStore,SettingsConflict

STATIC=Path(__file__).resolve().parent/'web'/'static'
COOKIE='doha_simulator_session'
SCOPES=frozenset({'events.chat','events.like'})
HEADERS={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}

class MemoryLedger(DonationRepository):
    def __init__(self):
        self.db=sqlite3.connect(':memory:');self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO mock_metadata VALUES ('fingerprint_salt',?)",(secrets.token_hex(32),));self.db.commit()
    @contextmanager
    def connection(self):
        with self.db:yield self.db
    def close(self):self.db.close()

class SessionGuard:
    def __init__(self):self.session=None;self.clock=time.monotonic
    def valid_session(self,sid):return bool(self.session and sid and same(sid,self.session.identifier) and self.clock()<self.session.deadline)
    def valid_csrf(self,sid,csrf):return bool(self.valid_session(sid) and csrf and same(csrf,self.session.csrf))
    def claim(self,sid):
        if self.session and (not sid or not same(sid,self.session.identifier)):return False
        if not self.valid_session(sid):self.session=SimpleNamespace(identifier=secrets.token_urlsafe(32),csrf=secrets.token_urlsafe(32),deadline=self.clock()+3600)
        return True

class BroadcastSimulator:
    def __init__(self):self.task=None;self.reset()
    def reset(self):
        if getattr(self,'task',None) and not self.task.done():raise ValueError('Stop scenario before reset')
        if hasattr(self,'repo'):self.repo.close()
        self.repo=MemoryLedger();self.session=self.repo.session(None);self.buffer=RealtimeBuffer();self.sequence=0;self.round=0;self.live=None;self.last_live=None;self.listeners={};self.spoons={};self.connected=False;self.reconnecting=False;self.error=None;self.last_wire=None;self.duplicates=0;self.invalid=0;self.generated={'chat':0,'like':0,'donation':0};self.processed={'chat':0,'like':0,'donation':0};self.next_user=0;self.last_event=None;self.scenario_error=None;self.tickets={};self.last_send=0;self.sent_digest={};self.last_command=None;self.last_result='준비 완료';self.ended=False;self.automation=Automation();self.confirmed_round=None;self.confirmation_context=secrets.token_urlsafe(24);self.roles={}
    def start(self):
        if self.round>=1000:raise ValueError('Round cap reached; reset simulator')
        self.halt_automation()
        self.automation.context(False,False)
        self.roles={}
        if self.live:self.last_live={**self.live,'ended_at_utc':utc_now()}
        self.listeners={};self.spoons={};self.round+=1;self.live={'live_id':f'sim-live-{self.round:04d}','title':'[가상] DOHA 테스트 방송','started_at_utc':utc_now(),'listener_count':len(self.listeners),'is_chat_frozen':False};self.session=self.repo.session(self.live['live_id']);self.buffer.reset();self.connected=True;self.reconnecting=False;self.error=None;self.ended=False;self.automation.context(True,True)
    def halt_automation(self):
        self.confirmed_round=None;self.confirmation_context=secrets.token_urlsafe(24);self.automation.stop()
    def start_automation(self,settings,round_id):
        if self.confirmed_round or not self.live or self.live['live_id']!=round_id or not self.connected or self.error:raise ValueError()
        if not settings['thanks'] and not settings['announcement']:raise ValueError()
        self.halt_automation();self.automation.configure(settings);self.automation.context(True,True)
        self.confirmed_round=round_id;self.automation.next_at=self.automation.now()+settings['interval'] if settings['announcement'] else None
    def emit(self,kind,duplicate=False,role='viewer'):

        if not self.live or not self.connected or self.error in {'401','403'}:return
        if self.sequence>=5000:raise ValueError('Event cap reached; reset simulator')
        self.sequence+=1;moment=utc_now();key=next(iter(self.listeners),'sim-user-1');user={'id':key,'nickname':self.listeners.get(key,'[가상] 테스트 청취자')}
        value={'user':user,'message':'[가상] 채팅 이벤트','isDj':role=='dj','sentTime':moment} if kind=='chat' else {'user':user,'type':'PAID','totalAmount':30,'amount':10,'extraAmount':5,'combo':2,'time':moment} if kind=='like' else {'user':user,'amount':3,'message':None,'time':moment}
        wire=(f'id:{self.sequence}-0\r\nevent:{kind}\r\ndata:'+json.dumps(value,ensure_ascii=False)+'\r\n\r\n').encode()
        if duplicate:
            if self.last_wire is None:return
            wire=self.last_wire
        else:self.last_wire=wire;self.generated[kind]+=1
        parser=SSEParser();frames=[]
        for i in range(0,len(wire),7):frames.extend(parser.feed(wire[i:i+7]))
        for frame in frames:
            if frame.event=='donation':
                try:
                    event=normalize_donation(frame);result=self.repo.append(event,self.session)
                    if result=='inserted':self.processed['donation']+=1
                    if result in {'inserted','duplicate'}:self.automation.donation(frame.explicit_id,event.amount_spoons)
                    if result=='inserted':self.spoons[value['user']['id']]=self.spoons.get(value['user']['id'],Decimal(0))+Decimal(event.amount_spoons)
                    if result=='duplicate':self.duplicates+=1
                except InvalidDonation:self.invalid+=1
            else:
                result=self.buffer.accept(frame,SCOPES)
                if result=='accepted':self.processed[frame.event]+=1
                if result=='accepted' and frame.event=='chat':self.roles[self.buffer.sequence]=role;self.roles={key:value for key,value in self.roles.items() if key>self.buffer.sequence-100}
                if result=='duplicate':self.duplicates+=1
                if result=='invalid':self.invalid+=1
        self.last_event=moment
    def action(self,action,count=1):
        if not 1<=count<=100:raise ValueError()
        if action in {'start','change'}:self.start()
        elif action=='end':
            if self.live:self.last_live={**self.live,'ended_at_utc':utc_now()}
            self.live=None;self.connected=False;self.reconnecting=False;self.error=None;self.ended=True;self.listeners={};self.spoons={};self.buffer.reset();self.tickets={}
        elif action=='join':
            for _ in range(count):
                if len(self.listeners)>=100:raise ValueError()
                self.next_user+=1;key=f'sim-user-{self.next_user}';self.listeners[key]='[가상] 청취자 '+str(len(self.listeners)+1)
        elif action=='leave':
            for _ in range(min(count,len(self.listeners))):self.listeners.pop(next(iter(self.listeners)))
        elif action in {'dj_chat','bot_chat','unknown_chat'}:self.emit('chat',role={'dj_chat':'dj','bot_chat':'bot','unknown_chat':'unknown'}[action])
        elif action in {'chat','like','donation'}:
            for _ in range(count):self.emit(action)
        elif action=='duplicate':self.emit('donation',True)
        elif action=='invalid':
            if self.buffer.accept(next(iter(SSEParser().feed(b'event:chat\ndata:bad\n\n'))),SCOPES)=='invalid':self.invalid+=1
        elif action=='disconnect':self.connected=False;self.reconnecting=True;self.repo.gap('simulated_disconnect')
        elif action=='reconnect':self.connected=bool(self.live);self.reconnecting=False;self.repo.recover_gaps()
        elif action in {'401','403','429','500','timeout','uncertain'}:self.error=action
        elif action=='recover':self.error=None
        else:raise ValueError()
        if self.live:self.live['listener_count']=len(self.listeners)
        self.last_command=action;self.last_result='처리 완료'
        if action in {'end','disconnect','401','403','429','500','timeout','uncertain'}:self.halt_automation()
        self.automation.context(bool(self.live),self.connected and self.error not in {'401','403'})
        self.automation.tick(self.error)
    def results(self):
        stored=self.repo.recent(100)
        return {'simulation':True,'broadcast_state':'live' if self.live else 'ended' if self.ended else 'waiting','connection_state':'error' if self.error else 'reconnecting' if self.reconnecting else 'connected' if self.connected else 'disconnected','simulated_error':self.error,'last_command':self.last_command,'last_result':self.last_result,'fan_count':sum(key in self.listeners for key in self.spoons),'generated':dict(self.generated),'processed':dict(self.processed),'chat_buffer_count':len(self.buffer.snapshot(SCOPES)['chats']),'hearts':self.buffer.snapshot(SCOPES)['hearts'],'donation_count':self.repo.db.execute('SELECT COUNT(*) FROM donations').fetchone()[0],'donation_spoons':stored['observed_total_spoons'],'listeners':len(self.listeners),'duplicates':self.duplicates,'invalid':self.invalid,'last_event_at':self.last_event,'scenario_running':bool(self.task and not self.task.done()),'last_broadcast':self.last_live,'event_attempts':self.sequence,'event_cap':5000,'scenario_error':self.scenario_error}
    async def stop(self):
        if self.task and not self.task.done():self.task.cancel()
        if self.task:
            try:await self.task
            except asyncio.CancelledError:pass
        self.task=None
    async def scenario(self,name):
        plans={'A':[('start',1),('join',5),('chat',10),('like',1),('donation',3),('end',1)],'B':[('start',1),('join',30),('chat',100),('like',30),('donation',30)],'C':[('start',1),('chat',1),('disconnect',1),('reconnect',1),('duplicate',1),('chat',1)],'D':[('start',1),('403',1),('401',1),('429',1),('500',1),('end',1)]}
        if name not in plans:raise ValueError()
        async def run():
            try:
                for action,count in plans[name]:self.action(action,count);await asyncio.sleep(.5)
            except ValueError:self.scenario_error='simulation_limit_reached'
        self.scenario_error=None
        await self.stop();self.task=asyncio.create_task(run())


def create_simulator_app(*,settings_path=None):
    guard=SessionGuard();sim=BroadcastSimulator();settings_store=BotSettingsStore(settings_path)
    @asynccontextmanager
    async def lifespan(app):
        from spoon_local.logging_config import configure_safe_access_logs
        configure_safe_access_logs()
        async def automation_loop():
            while True:
                if guard.session and not guard.valid_session(guard.session.identifier):sim.halt_automation()
                sim.automation.tick(sim.error)
                await asyncio.sleep(.25)
        worker=asyncio.create_task(automation_loop())
        try:yield
        finally:
            worker.cancel()
            try:await worker
            except asyncio.CancelledError:pass
            sim.halt_automation()
            await sim.stop();sim.repo.close();settings_store.close()
    app=FastAPI(title='DOHA-BOT Simulator',lifespan=lifespan)
    @app.middleware('http')
    async def security(request,call_next):
        if request.url.path.startswith('/auth/spoon'):request.scope['query_string']=b''
        hosts=request.headers.getlist('host');cookies=request.headers.getlist('cookie')
        duplicate=len(cookies)>1 or sum(part.strip().startswith(COOKIE+'=') for value in cookies for part in value.split(';'))>1
        if hosts!=['localhost:8000'] or duplicate:return JSONResponse({'error':'invalid_simulator_host'},400,headers=HEADERS)
        if request.url.path.startswith('/api/') and not guard.valid_session(request.cookies.get(COOKIE)):return JSONResponse({'state':'reauth'},401,headers=HEADERS)
        response=await call_next(request)
        for key,value in HEADERS.items():response.headers[key]=value
        return response
    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    @app.get('/')
    @app.get('/broadcast')
    @app.get('/settings')
    @app.get('/capture')
    @app.get('/gallery')
    @app.get('/donations')
    async def page(request:Request):
        if not guard.claim(request.cookies.get(COOKIE)):return JSONResponse({'error':'simulator_owned_by_another_browser'},403)
        content=(STATIC/'index.html').read_text().replace('<body>','<body data-simulator="true">')
        banner='<aside class="dj-card simulation-banner" role="status">시뮬레이션 모드 — 실제 SPOON 방송과 연결되지 않습니다</aside>'
        content=content.replace('<main>',banner+'<main>').replace('<!--chat-csrf-->','<input type="hidden" name="csrf" value="'+guard.session.csrf+'">')
        panel='<section class="dj-card" id="sim-controls"><h2>가상 방송 스튜디오</h2><p id="sim-state" role="status" aria-live="polite">시뮬레이터 준비 중</p><input id="sim-csrf" type="hidden" value="'+guard.session.csrf+'"><div id="sim-buttons" class="sim-groups"></div><fieldset><legend>시나리오</legend><label>시나리오 선택 <select id="sim-scenario"><option>A</option><option>B</option><option>C</option><option>D</option></select></label><button id="sim-run">시나리오 실행</button><button id="sim-stop">중지</button><button id="sim-reset">초기화</button></fieldset><p id="sim-error" role="alert"></p><p id="sim-command" role="status" aria-live="polite"></p></section><section class="dj-card" id="sim-summary"><h2>가상 이벤트 처리 결과</h2><div id="sim-metrics" class="sim-metrics"></div><p id="sim-extra"></p><details><summary>개발자 상세 정보</summary><pre id="sim-results"></pre></details></section>'
        if request.url.path=='/broadcast':content=content.replace('</main>',panel+'</main>')
        content=content.replace('<!--history-access-->','<form id="history-access" action="/api/simulator/history/allow" method="post"><input name="csrf" type="hidden" value="'+guard.session.csrf+'"><button type="submit">가상 후원 조회 시작</button></form>')
        if request.url.path=='/broadcast':
            content=content.replace('</main>',(STATIC/'automation.html').read_text()+'</main>')
            content=content.replace('</head>','<script src="/static/automation.js" defer></script></head>')
            content=content.replace('</head>','<link rel="stylesheet" href="/static/simulator.css"><script src="/static/simulator.js" defer></script></head>')
        response=HTMLResponse(content);response.set_cookie(COOKIE,guard.session.identifier,httponly=True,samesite='lax',path='/');return response
    async def post(request):
        values=await post_values(request,guard,COOKIE)
        if values is None:raise ValueError()
        return {key:value[0] for key,value in values.items() if len(value)==1}
    @app.post('/api/simulator/control')
    async def control(request:Request):
        try:
            values=await post(request);action=values.get('action')
            if action=='run':await sim.scenario(values['scenario'])
            elif action=='stop':await sim.stop();sim.halt_automation()
            elif action=='reset':await sim.stop();sim.reset()
            else:sim.action(action,int(values.get('count','1')))
            sim.last_command=action;sim.last_result='처리 완료'
        except (ValueError,KeyError):return JSONResponse({'error':'invalid_simulation_action'},400)
        return sim.results()
    @app.get('/api/simulator/automation')
    async def automation_status():
        saved=settings_store.read()
        return {**sim.automation.snapshot(),**saved,'simulation':True,'run_state':'error' if sim.error else 'running' if sim.confirmed_round else 'ready' if sim.live and sim.connected and not sim.error else 'stopped','broadcast_id':sim.live['live_id'] if sim.live else None,'confirmation_context':sim.confirmation_context}
    @app.post('/api/simulator/automation')
    async def automation_control(request:Request):
        try:
            values=await post(request);command=values.get('command')
            if command=='configure':
                if values.get('thanks') not in {'true','false'} or values.get('announcement') not in {'true','false'}:raise ValueError()
                Automation().configure({'thanks':values['thanks']=='true','announcement':values['announcement']=='true','thanks_template':values['thanks_template'],'announcement_template':values['announcement_template'],'cooldown':int(values['cooldown']),'interval':int(values['interval'])});sim.halt_automation()
            elif command=='start':
                if values.get('confirmation')!='confirmed' or not same(values.get('context',''),sim.confirmation_context) or int(values.get('version','0'))!=settings_store.read()['version']:raise ValueError()
                sim.start_automation(settings_store.read()['settings'],values.get('broadcast_id'))
            elif command=='stop':sim.halt_automation()
            elif command=='clear':sim.automation.cancel('cleared')
            elif command=='reset':settings_store.save(Automation().settings,settings_store.read()['version']);sim.halt_automation();sim.automation=Automation();sim.automation.context(bool(sim.live),sim.connected)
            elif command=='advance':sim.automation.advance(int(values['seconds']))
            else:raise ValueError()
            sim.automation.tick(sim.error)
        except (ValueError,KeyError,UnicodeError):return JSONResponse({'error':'invalid_automation_settings'},400)
        except sqlite3.Error:return JSONResponse({'error':'settings_storage_failed'},503)
        return await automation_status()
    @app.get('/api/simulator/bot-settings')
    async def bot_settings():return settings_store.read()
    @app.post('/api/simulator/bot-settings')
    async def save_bot_settings(request:Request):
        try:
            values=await post(request)
            if set(values)!={'csrf','version','thanks','announcement','thanks_template','announcement_template','cooldown','interval'}:raise ValueError()
            if values['thanks'] not in {'true','false'} or values['announcement'] not in {'true','false'}:raise ValueError()
            result=settings_store.save({'thanks':values['thanks']=='true','announcement':values['announcement']=='true','thanks_template':values['thanks_template'],'announcement_template':values['announcement_template'],'cooldown':int(values['cooldown']),'interval':int(values['interval'])},int(values['version']))
            sim.halt_automation();return result
        except SettingsConflict:return JSONResponse({'error':'settings_version_conflict'},409)
        except (ValueError,KeyError,UnicodeError):return JSONResponse({'error':'invalid_settings'},400)
        except sqlite3.Error:return JSONResponse({'error':'settings_save_failed'},503)
    @app.post('/api/simulator/history/allow')
    async def allow_history(request:Request):
        try:await post(request)
        except ValueError:return JSONResponse({'error':'invalid_request'},400)
        return {'access':'allowed','simulation':True}
    @app.get('/api/simulator/results')
    async def results():return sim.results()
    @app.get('/api/mode')
    async def mode():return {'auth_mode':'simulator'}
    @app.get('/api/helper')
    async def helper():
        if sim.error in {'401','403'}:return {'state':'error','reason':'auth_required' if sim.error=='401' else 'permission_required','available':False,'active':False}
        return {'state':'connected' if sim.connected else 'reconnecting' if sim.reconnecting else 'disconnected','active':sim.connected,'available':False,'reason':'simulator','last_donation_at':sim.last_event}
    @app.get('/api/live')
    async def live():return {'state':{'401':'reauth_required','403':'permission_required','429':'rate_limited','500':'error','timeout':'error'}.get(sim.error,'live' if sim.live else 'waiting'),'live':sim.live if not sim.error else None,'retryable':False,'simulation':True}
    @app.get('/api/realtime-events')
    async def realtime():
        snapshot=sim.buffer.snapshot(frozenset() if sim.error in {'401','403'} else SCOPES)
        for item in snapshot['chats']:item.update(role=sim.roles.get(item['sequence'],'unknown'),simulation=True,role_basis='simulator_metadata')
        return {**snapshot,'state':'connected' if sim.connected and not sim.error else 'reconnecting' if sim.reconnecting else 'error' if sim.error else 'disconnected','active':sim.connected and not sim.error,'simulation':True}
    @app.get('/api/donation-history')
    async def history(request:Request):
        from spoon_local.spoon.donation_history import boundaries,DecimalSum
        try:
            q=request.query_params;page=int(q.get('page','1'));size=int(q.get('page_size','12'))
            if not 1<=page<=10000 or size not in {12,24}:raise ValueError()
            lower,upper=boundaries(q.get('period','all'),q.get('start'),q.get('end'))
            today_start,today_end=boundaries('today')
            db=sim.repo.db;db.create_aggregate('decimal_sum',1,DecimalSum)
            maximum=db.execute('SELECT COALESCE(MAX(received_sequence),0) FROM donations').fetchone()[0]
            anchor=int(q.get('snapshot',maximum))
            if not 0<=anchor<=maximum:raise ValueError()
            condition='received_sequence<=? AND (? IS NULL OR occurred_at_utc>=?) AND (? IS NULL OR occurred_at_utc<?)'
            params=(anchor,lower,lower,upper,upper)
            total,amount,latest=db.execute('SELECT COUNT(*),decimal_sum(amount_spoons_text),MAX(occurred_at_utc) FROM donations WHERE '+condition,params).fetchone()
            rows=db.execute('SELECT record_id,amount_spoons_text,occurred_at_utc,received_at_utc FROM donations WHERE '+condition+' ORDER BY occurred_at_utc DESC,received_sequence DESC LIMIT ? OFFSET ?',(*params,size,(page-1)*size)).fetchall()
            today=db.execute('SELECT COUNT(*) FROM donations WHERE received_sequence<=? AND occurred_at_utc>=? AND occurred_at_utc<?',(anchor,today_start,today_end)).fetchone()[0]
        except (ValueError,OverflowError):return JSONResponse({'error':'invalid_filter'},400)
        return {'simulation':True,'items':[{'record_id':row[0],'amount_spoons':row[1],'occurred_at_utc':row[2],'received_at_utc':row[3],'display_at_utc':row[2],'time_basis':'occurred'} for row in rows],'total':total,'page':page,'page_size':size,'pages':(total+size-1)//size,'snapshot':anchor,'has_previous':page>1,'has_next':page*size<total,'statistics':{'event_count':total,'amount_spoons':amount or '0','today_event_count':today,'latest_at_utc':latest}}
    @app.get('/api/donations')
    async def donations():return {**sim.repo.recent(20),'source_mode':'simulator','simulation':True}
    @app.get('/api/live/listeners')
    async def listeners(cursor:str|None=None):
        if sim.error:return {'state':{'401':'reauth','403':'permission_required','429':'rate_limited','500':'upstream_error','timeout':'timeout','uncertain':'upstream_error'}[sim.error],'items':[],'simulation':True,'retry_after_seconds':30}
        rows=[{'id':key,'nickname':nick} for key,nick in sim.listeners.items()]
        try:offset=int(cursor or 0)
        except ValueError:return JSONResponse({'state':'invalid_cursor'},400)
        if offset<0 or offset>100:return JSONResponse({'state':'invalid_cursor'},400)
        data={'listeners':rows[offset:offset+12],'nextCursor':str(offset+12) if offset+12<len(rows) else None}
        return {**normalize('listeners',data),'state':'ready' if sim.live else 'waiting','live_id':sim.live['live_id'] if sim.live else None,'queried_at_utc':utc_now(),'simulation':True} if sim.live else {'state':'waiting','items':[]}
    @app.get('/api/live/fans')
    async def fans():
        if sim.error:return {'state':{'401':'reauth','403':'permission_required','429':'rate_limited','500':'upstream_error','timeout':'timeout','uncertain':'upstream_error'}[sim.error],'items':[],'simulation':True}
        if not sim.live:return {'state':'waiting','items':[],'simulation':True}
        ranked=sorted(((key,amount) for key,amount in sim.spoons.items() if key in sim.listeners),key=lambda item:item[1],reverse=True)[:30]
        data={'fans':[{'id':key,'nickname':'[가상] 후원 청취자','rank':i+1,'spoonCount':int(amount)} for i,(key,amount) in enumerate(ranked)],'totalCount':len(ranked)}
        return {**normalize('fans',data),'state':'ready' if sim.live else 'waiting','live_id':sim.live['live_id'] if sim.live else None,'queried_at_utc':utc_now(),'simulation':True}
    @app.get('/api/live/chat/prepare')
    async def prepare():
        if sim.error and sim.error not in {'500','timeout','uncertain'}:return {'state':{'401':'reauth','403':'permission_required','429':'rate_limited'}[sim.error]}
        if not sim.live:return {'state':'waiting'}
        key=secrets.token_urlsafe(32);sim.tickets={key:time.monotonic()+300}
        return {'state':'ready','request_id':key,'simulation':True}
    @app.post('/api/live/chat')
    async def send(request:Request):
        try:
            body=bytearray()
            async for chunk in request.stream():
                if len(body)+len(chunk)>4096:raise ValueError()
                body.extend(chunk)
            if request.headers.get('content-type','').split(';')[0]!='application/json':raise ValueError()
            value=json.loads(body)
            if not isinstance(value,dict) or set(value)!={'csrf','message','request_id','notice'}:raise ValueError()
            validate_message(value['message'])
            if request.headers.getlist('origin')!=['http://localhost:8000'] or not guard.valid_csrf(request.cookies.get(COOKIE),value['csrf']) or value.get('notice')!='confirmed':raise ValueError()
            if sim.tickets.pop(value['request_id'],0)<=time.monotonic():return {'state':'request_expired_or_used'}
        except (ValueError,KeyError,TypeError):return JSONResponse({'state':'invalid_request'},400)
        now=time.monotonic();sim.sent_digest={key:stamp for key,stamp in sim.sent_digest.items() if now-stamp<60}
        digest=hashlib.sha256(value['message'].encode()).digest()
        if digest in sim.sent_digest:return {'state':'duplicate_message','simulation':True}
        if now-sim.last_send<3 or sim.automation.now()-sim.automation.last_send<3:return {'state':'rate_limited','retry_after_seconds':3,'simulation':True}
        if len(sim.sent_digest)>=128:return {'state':'busy','simulation':True}
        sim.sent_digest[digest]=now;sim.last_send=now
        state={'401':'reauth','403':'permission_required','429':'rate_limited','500':'uncertain','timeout':'uncertain','uncertain':'uncertain'}.get(sim.error,'sent' if sim.live else 'waiting')
        sim.automation.last_send=sim.automation.now()
        return {'state':state,'simulation':True,'mock_http_status':204 if state=='sent' else None}
    return app
