"""Memory-only automation. No injectable transport, HTTP client or OAuth tokens."""
import time
from collections import deque
from decimal import Decimal,InvalidOperation
from string import Formatter
from spoon_local.spoon.chat_send import validate_message


class Automation:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.offset=0;self.settings={'thanks':False,'announcement':False,'thanks_template':'소중한 {spoons}스푼 후원 감사합니다! 💛','announcement_template':'방송에 와주셔서 감사합니다! 편하게 이야기 나눠요 💛','cooldown':10,'interval':900}
        self.queue=deque();self.records=deque(maxlen=200);self.seen=set();self.live=False;self.connected=False;self.next_at=None;self.last_thanks=float('-inf');self.last_send=float('-inf');self.sequence=0
    def now(self):return self.clock()+self.offset
    def record(self,kind,state,reason=None,message=None):
        self.sequence+=1;self.records.append({'sequence':self.sequence,'kind':kind,'at':self.now(),'state':state,'mock':state=='mock_sent','reason':reason,'message':message})
    def template(self,text,kind,spoons='3'):
        if not isinstance(text,str) or len(text)>400:raise ValueError()
        for _,field,spec,conversion in Formatter().parse(text):
            if field is not None and (kind!='thanks' or field!='spoons' or spec or conversion):raise ValueError()
        return validate_message(text.format(spoons=spoons))
    def configure(self,values):
        if set(values)!=set(self.settings):raise ValueError()
        for key in ('thanks','announcement'):
            if type(values[key]) is not bool:raise ValueError()
        for key,lower,upper in [('cooldown',10,3600),('interval',60,86400)]:
            if type(values[key]) is not int or not lower<=values[key]<=upper:raise ValueError()
        self.template(values['thanks_template'],'thanks');self.template(values['announcement_template'],'announcement')
        changed=values['interval']!=self.settings['interval'] or values['announcement']!=self.settings['announcement']
        self.settings=dict(values)
        for kind in ('thanks','announcement'):
            if not values[kind]:self.cancel('disabled',kind)
        if changed:self.next_at=self.now()+values['interval'] if self.live and values['announcement'] else None
    def cancel(self,reason,kind=None):
        remaining=deque()
        for item in self.queue:
            if kind is None or item['kind']==kind:self.record(item['kind'],'cancelled',reason)
            else:remaining.append(item)
        self.queue=remaining
    def context(self,live,connected):
        if not live:self.cancel('broadcast_ended');self.next_at=None
        elif not connected:self.cancel('disconnected');self.next_at=None
        elif not self.live or not self.connected:self.next_at=self.now()+self.settings['interval'] if self.settings['announcement'] else None
        self.live=live;self.connected=connected
    def stop(self):
        self.settings['thanks']=self.settings['announcement']=False;self.cancel('stopped');self.next_at=None
    def donation(self,event_id,amount):
        try:
            value=Decimal(str(amount))
            if not value.is_finite() or value<=0:raise ValueError()
        except (InvalidOperation,ValueError):self.record('thanks','invalid','invalid_amount');return
        if event_id is not None:
            if event_id in self.seen:self.record('thanks','duplicate');return
            if len(self.seen)>=5000:self.record('thanks','excluded','dedup_capacity');return
            self.seen.add(event_id)
        if not self.settings['thanks']:self.record('thanks','disabled');return
        if self.now()-self.last_thanks<self.settings['cooldown']:self.record('thanks','cooldown');return
        if self.enqueue('thanks',str(value)):self.last_thanks=self.now()
    def enqueue(self,kind,spoons='3'):
        if not self.live or not self.connected:self.record(kind,'cancelled','not_connected');return False
        try:message=self.template(self.settings[kind+'_template'],kind,spoons)
        except (ValueError,UnicodeError):self.record(kind,'length_or_template_error');return False
        self.record(kind,'generated')
        if len(self.queue)>=20:self.record(kind,'queue_full');return False
        self.queue.append({'kind':kind,'message':message,'at':self.now()});self.record(kind,'queued');return True
    def tick(self,error=None,manual_at=None):
        if not self.live or not self.connected:return
        now=self.now()
        if self.settings['announcement'] and self.next_at is not None and now>=self.next_at:
            self.next_at=now+self.settings['interval'];self.enqueue('announcement')
        if not self.queue or now-max(self.last_send,manual_at if manual_at is not None else float('-inf'))<3:return
        item=self.queue.popleft()
        if now-item['at']>60:self.record(item['kind'],'cancelled','expired');return
        self.last_send=now
        state='mock_sent' if not error else 'uncertain' if error in {'timeout','uncertain'} else 'failed'
        self.record(item['kind'],state,'simulated_'+error if error else None,item['message'])
    def advance(self,seconds):
        if type(seconds) is not int or not 1<=seconds<=86400:raise ValueError()
        self.offset+=seconds
    def snapshot(self):
        return {'simulation':True,'settings':dict(self.settings),'queue_count':len(self.queue),'next_announcement_in':max(0,int(self.next_at-self.now())) if self.next_at is not None else None,'records':list(self.records),'virtual_offset':self.offset}
