"""Read-only audience queries via the existing authenticated live HTTP client."""
import asyncio, math
from decimal import Decimal
from spoon_local.spoon.retry_after import retry_delay
from spoon_local.spoon.event_models import utc_now


def number(value,*,minimum=0):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<minimum or value>2**53-1 or value!=int(value):raise ValueError()
    return int(value)


def normalize(kind,value):
    if not isinstance(value,dict):raise ValueError()
    rows=value[kind]
    if not isinstance(rows,list) or len(rows)>(1000 if kind=='listeners' else 30):raise ValueError()
    output=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row['id'],str) or not 1<=len(row['id'])<=256 or not isinstance(row['nickname'],str) or len(row['nickname'])>256:raise ValueError()
        # The official user ID is validated but never needed by this read-only UI.
        item={'nickname':row['nickname']}
        if kind=='fans':
            item['rank']=number(row['rank'],minimum=1)
            spoons=row['spoonCount']
            if spoons is not None:
                if isinstance(spoons,bool) or not isinstance(spoons,(int,float,Decimal)) or not math.isfinite(spoons) or spoons<0 or spoons>2**53-1:raise ValueError()
                spoons=format(Decimal(str(spoons)),'f')
            item['spoon_count']=spoons
        output.append(item)
    if kind=='listeners':
        cursor=value['nextCursor']
        if cursor is not None and (not isinstance(cursor,str) or not cursor.isprintable() or not 1<=len(cursor)<=2048):raise ValueError()
        return {'items':output,'next_cursor':cursor,'returned_count':len(output),'count_basis':'this_page_only'}
    count=number(value['totalCount'])
    if count>30 or count!=len(output):raise ValueError()
    return {'items':output,'returned_count':count,'next_cursor':None,'ranking_basis':'current_broadcast_current_listeners_top_30','unit':'spoon'}



class AudienceService:
    def __init__(self,live):
        self.live=live;self.lock=asyncio.Lock();self.owner=None;self.blocked_until={};self.last_request={}
        self.cursor_context=None;self.allowed_cursor=None
    def reset(self,owner):
        self.owner=owner;self.blocked_until.clear();self.last_request.clear();self.cursor_context=None;self.allowed_cursor=None
    async def get(self,kind,*,owner,cursor=None,expected_live_id=None):
        if kind not in {'listeners','fans'}:raise ValueError()
        if cursor is not None and (kind!='listeners' or not isinstance(cursor,str) or not cursor.isprintable() or not 1<=len(cursor)<=2048):raise ValueError()
        if self.lock.locked():return {'state':'busy','items':[],'next_cursor':None,'queried_at_utc':None}
        async with self.lock:
            auth=self.live.auth
            if self.owner is not auth.session:self.reset(auth.session)
            def result(state,**extra):return {'state':state,'items':[],'next_cursor':None,'queried_at_utc':None,**extra}
            if owner is not auth.session or auth.state!='authenticated':return result('reauth')
            pair=await auth.access_lease({kind+'.read'})
            if pair is None:return result('permission_required' if await auth.ensure_access() else 'reauth')
            delay=max(self.blocked_until.get(kind,0),self.last_request.get(kind,-100)+2)-self.live.clock()
            if delay>0:return result('rate_limited',retry_after_seconds=math.ceil(delay))
            self.last_request[kind]=self.live.clock()
            live=await self.live.get(force=True)
            if owner is not auth.session:return result('reauth')
            if live['state']!='live':
                self.cursor_context=None;self.allowed_cursor=None
                return result({'reauth_required':'reauth','error':'upstream_error'}.get(live['state'],live['state']),**({'retry_after_seconds':math.ceil(max(1,self.live.until-self.live.clock()))} if live['state']=='rate_limited' else {}))
            live_id=live['live']['live_id']
            if expected_live_id is not None and expected_live_id!=live_id:
                self.cursor_context=None;self.allowed_cursor=None
                return result('broadcast_changed',live_id=live_id)
            if cursor is not None and (self.cursor_context!=(owner,live_id) or cursor!=self.allowed_cursor):return result('invalid_cursor',live_id=live_id)
            # Re-read the lease after live metadata may have refreshed it.
            pair=await auth.access_lease({kind+'.read'})
            if pair is None:return result('permission_required' if await auth.ensure_access() else 'reauth')
            path='/v1/live/'+kind
            status,value,retry=await self.live._request(pair,path,{'cursor':cursor} if cursor else None)
            if owner is not auth.session or auth.state!='authenticated':return result('reauth')
            if status==401:
                await auth.refresh_if_needed(force=True,expected_pair=pair)
                if owner is not auth.session:return result('reauth')
                pair=await auth.access_lease({kind+'.read'})
                if pair is None:return result('permission_required' if await auth.ensure_access() else 'reauth')
                status,value,retry=await self.live._request(pair,path,{'cursor':cursor} if cursor else None)
            if owner is not auth.session or auth.store.pair is not pair or auth.state!='authenticated':return result('reauth')
            if status==401:
                await auth.invalidate_access();return result('reauth')
            if status==403:return result('permission_required')
            if status==404 and isinstance(value,dict) and value.get('detailCode')=='OAPI_MNGR_0301':
                self.cursor_context=None;self.allowed_cursor=None;return result('waiting')
            if status==429:
                seconds=retry_delay(retry);self.blocked_until[kind]=self.live.clock()+seconds
                return result('rate_limited',retry_after_seconds=seconds)
            if status==400 and kind=='listeners':return result('invalid_cursor')
            if status==504:return result('timeout')
            if status==503 and isinstance(value,dict) and value.get('local_error')=='network':return result('network_error')
            if status!=200:return result('upstream_error')
            try:normalized=normalize(kind,value)
            except (ValueError,TypeError,KeyError,OverflowError):return result('upstream_error')
            confirmed=await self.live.get(force=True)
            if owner is not auth.session or auth.state!='authenticated':return result('reauth')
            if confirmed['state']!='live':
                self.cursor_context=None;self.allowed_cursor=None
                return result({'reauth_required':'reauth','error':'upstream_error'}.get(confirmed['state'],confirmed['state']),**({'retry_after_seconds':math.ceil(max(1,self.live.until-self.live.clock()))} if confirmed['state']=='rate_limited' else {}))
            if confirmed['live']['live_id']!=live_id:
                self.cursor_context=None;self.allowed_cursor=None
                return result('broadcast_changed',live_id=confirmed['live']['live_id'])
            if kind+'.read' not in auth.store.pair.scopes:return result('permission_required')
            if kind=='listeners':
                self.cursor_context=(owner,live_id);self.allowed_cursor=normalized['next_cursor']
            return result('ready',**normalized,live_id=live_id,queried_at_utc=utc_now())
