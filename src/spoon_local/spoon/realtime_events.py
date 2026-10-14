"""Bounded volatile chat/heart observation; no identities or text reach SQLite."""
from collections import deque, OrderedDict
from datetime import datetime,timezone
from decimal import Decimal,localcontext
import hashlib,json,re,time,uuid
from spoon_local.spoon.event_models import utc_now

class InvalidRealtimeEvent(ValueError):
    def __init__(self):super().__init__('Invalid realtime event')


def parse(frame):
    if frame.event not in {'chat','like'}:return None
    try:
        if len(frame.data.encode('utf-8'))>32768:raise ValueError()
        def constant(value):raise ValueError()
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError()
                result[key]=value
            return result
        value=json.loads(frame.data,parse_float=Decimal,parse_int=Decimal,parse_constant=constant,object_pairs_hook=unique)
        user=value['user']
        if not isinstance(user,dict) or not isinstance(user['id'],str) or not 1<=len(user['id'])<=256:raise ValueError()
        nickname=user['nickname']
        if nickname is not None and (not isinstance(nickname,str) or len(nickname)>256):raise ValueError()
        if frame.explicit_id not in (None,'') and not re.fullmatch(r'\d{1,20}-\d{1,10}',frame.explicit_id):raise ValueError()
        field='sentTime' if frame.event=='chat' else 'time'
        moment=datetime.fromisoformat(value[field].replace('Z','+00:00'))
        if moment.tzinfo is None:raise ValueError()
        item={'kind':frame.event,'occurred_at_utc':moment.astimezone(timezone.utc).isoformat(timespec='microseconds'),'received_at_utc':utc_now()}
        if frame.event=='chat':
            if not isinstance(value['isDj'],bool) or not isinstance(value['message'],str) or len(value['message'])>4096:raise ValueError()
            item.update(message=value['message'],nickname=nickname,is_dj=value['isDj'],role='dj' if value['isDj'] else 'viewer',role_basis='official_isDj')
        else:
            def quantity(name):
                number=value[name]
                if not isinstance(number,Decimal) or not number.is_finite() or number<0 or number>2**53-1 or number!=number.to_integral_value():raise ValueError()
                return format(number.quantize(Decimal(1)),'f')
            if value['type'] not in {'FREE','PAID'}:raise ValueError()
            for name in ['amount','extraAmount']:quantity(name)
            if value['combo'] is not None:quantity('combo')
            item.update(heart_type=value['type'],total_amount=quantity('totalAmount'))
        return item
    except (KeyError,ValueError,TypeError,AttributeError,RecursionError,OverflowError):raise InvalidRealtimeEvent() from None


class RealtimeBuffer:
    def __init__(self,*,clock=time.monotonic,max_chats=100,ttl=300,max_seen=2048):
        self.clock=clock;self.max_chats=max_chats;self.ttl=ttl;self.max_seen=max_seen
        self.reset()
    def reset(self):
        self.generation=str(uuid.uuid4());self.sequence=0;self.chats=deque(maxlen=self.max_chats);self.seen=OrderedDict();self.hearts=Decimal(0);self.like_count=0;self.last_like=None;self.last_chat=None;self.invalid=0;self.conflicts=0;self.idless=0
    def clean(self):
        now=self.clock()
        while self.chats and now-self.chats[0][0]>=self.ttl:self.chats.popleft()
        while self.seen and now-next(iter(self.seen.values()))[0]>=self.ttl:self.seen.popitem(last=False)
    def permissions(self,scopes):
        if 'events.chat' not in scopes:self.chats.clear();self.last_chat=None
        if 'events.like' not in scopes:self.hearts=Decimal(0);self.like_count=0;self.last_like=None
    def accept(self,frame,scopes):
        self.clean();self.permissions(scopes)
        if frame.event not in {'chat','like'} or 'events.'+frame.event not in scopes:return 'ignored'
        try:item=parse(frame)
        except InvalidRealtimeEvent:self.invalid+=1;return 'invalid'
        if frame.explicit_id:
            key=(frame.event,hashlib.sha256(frame.explicit_id.encode()).digest())
            core={k:v for k,v in item.items() if k!='received_at_utc'}
            fingerprint=hashlib.sha256(json.dumps(core,sort_keys=True,ensure_ascii=True).encode()).digest()
            previous=self.seen.get(key)
            if previous:
                if previous[1]==fingerprint:return 'duplicate'
                self.conflicts+=1;return 'conflict'
            self.seen[key]=(self.clock(),fingerprint)
            while len(self.seen)>self.max_seen:self.seen.popitem(last=False)
        else:self.idless+=1
        self.sequence+=1;item['sequence']=self.sequence
        if frame.event=='chat':self.chats.append((self.clock(),item));self.last_chat=item['received_at_utc']
        else:
            with localcontext() as context:
                context.prec=80;self.hearts+=Decimal(item['total_amount'])
            self.like_count+=1;self.last_like=item['received_at_utc']
        return 'accepted'
    def snapshot(self,scopes):
        self.clean();self.permissions(scopes)
        return {'generation':self.generation,'sequence':self.sequence,'chats':[dict(item) for _,item in self.chats] if 'events.chat' in scopes else [],'hearts':{'observed_amount':format(self.hearts,'f'),'event_count':self.like_count,'last_received_at_utc':self.last_like},'last_chat_received_at_utc':self.last_chat,'permissions':{'chat':'events.chat' in scopes,'like':'events.like' in scopes},'coverage':'current_collection_observed_only','buffer_seconds':self.ttl,'buffer_limit':self.max_chats,'deduplication':'bounded_best_effort','invalid_count':self.invalid,'conflict_count':self.conflicts,'idless_count':self.idless}
