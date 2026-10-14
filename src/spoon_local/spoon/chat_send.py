"""Explicit one-shot bot chat; never retry an external POST."""
import asyncio,hashlib,json,secrets
import httpx
from spoon_local.spoon.real_oauth import REGIONS
from spoon_local.spoon.retry_after import retry_delay


def validate_message(message):
    if not isinstance(message,str) or not message.strip() or len(message.encode('utf-16-le'))//2>200:raise ValueError('Invalid message')
    return message


class ChatSendService:
    def __init__(self,live):
        self.live=live;self.lock=asyncio.Lock();self.owner=None;self.tickets={};self.recent={};self.until=0
    def reset(self,owner):self.owner=owner;self.tickets.clear();self.recent.clear();self.until=0
    def context(self,owner):
        if self.owner is not self.live.auth.session:self.reset(self.live.auth.session)
        return owner is self.live.auth.session and self.live.auth.state=='authenticated'
    async def check(self,owner):
        if not self.context(owner):return {'state':'reauth'}
        pair=await self.live.auth.access_lease({'chat.send'})
        if pair is None:return {'state':'permission_required'}
        data=await self.live.get(force=True)
        if not self.context(owner):return {'state':'reauth'}
        if data['state']!='live':return {'state':{'reauth_required':'reauth','error':'upstream_error'}.get(data['state'],data['state'])}
        frozen=data['live'].get('is_chat_frozen')
        if frozen is None:return {'state':'broadcast_unverified'}
        if frozen:return {'state':'chat_blocked'}
        return {'state':'ready','live_id':data['live']['live_id']}
    async def prepare(self,owner):
        if not self.context(owner):return {'state':'reauth'}
        if self.lock.locked():return {'state':'busy'}
        if self.live.clock()<self.until:return {'state':'rate_limited','retry_after_seconds':int(self.until-self.live.clock())+1}
        data=await self.check(owner)
        now=self.live.clock();self.tickets={key:value for key,value in self.tickets.items() if value[0]>now}
        if data['state']!='ready':return data
        if now<self.until:return {'state':'rate_limited','retry_after_seconds':int(self.until-now)+1}
        if len(self.tickets)>=32:return {'state':'busy'}
        key=secrets.token_urlsafe(32);self.tickets[key]=(now+300,data['live_id'])
        return {**data,'request_id':key,'max_utf16_units':200}
    async def send(self,owner,message,request_id):
        validate_message(message)
        if not self.context(owner):return {'state':'reauth'}
        if self.lock.locked():return {'state':'busy'}
        async with self.lock:
            ticket=self.tickets.pop(request_id,None)
            now=self.live.clock()
            if ticket is None or ticket[0]<=now:return {'state':'request_expired_or_used'}
            self.recent={key:time for key,time in self.recent.items() if now-time<60}
            digest=hashlib.sha256(message.encode()).digest()
            if digest in self.recent:return {'state':'duplicate_message'}
            if now<self.until:return {'state':'rate_limited','retry_after_seconds':int(self.until-now)+1}
            data=await self.check(owner)
            if data['state']!='ready':return data
            if data['live_id']!=ticket[1]:return {'state':'broadcast_changed'}
            auth=self.live.auth;pair=await auth.access_lease({'chat.send'})
            if pair is None or not self.context(owner):return {'state':'reauth'}
            if len(self.recent)>=128:return {'state':'busy'}
            self.recent[digest]=self.live.clock();self.until=self.live.clock()+3
            # Mark consumed before touching transport. No retry, including 401.
            try:
                async with asyncio.timeout(15):
                    async with self.live.http.stream('POST',REGIONS[auth.settings.region][1]+'/v1/live/chat',headers={'Authorization':'Bearer '+pair.access_token,'Content-Type':'application/json'},json={'message':message}) as response:
                        status=response.status_code;retry=response.headers.get('retry-after');value={}
                        if status!=204:
                            body=bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=4096):
                                if len(body)+len(chunk)>65536:return {'state':'uncertain'}
                                body.extend(chunk)
                            try:value=json.loads(body)
                            except (ValueError,RecursionError):pass
            except asyncio.CancelledError:
                raise # consumed ticket/message digest remain; no automatic retry
            except (httpx.HTTPError,TimeoutError):return {'state':'uncertain'}
            finally:self.live.http.cookies.clear()
            if not self.context(owner):return {'state':'uncertain'}
            if status==429:
                seconds=retry_delay(retry);self.until=max(self.until,self.live.clock()+seconds)
                return {'state':'rate_limited','retry_after_seconds':seconds}
            if status==401:
                await auth.refresh_if_needed(force=True,expected_pair=pair)
                return {'state':'reauth_or_retry_manually'}
            code=value.get('detailCode') if isinstance(value,dict) else None
            if status==403:return {'state':'chat_blocked' if code=='OAPI_MNGR_0208' else 'entry_blocked' if code=='OAPI_MNGR_0209' else 'permission_required'}
            if status==404 and code=='OAPI_MNGR_0301':return {'state':'waiting'}
            if status==400:return {'state':'invalid_message'}
            if status!=204:return {'state':'uncertain'}
            # Success is known, but report whether the broadcast context changed.
            confirmed=await self.live.get(force=True)
            if not self.context(owner):return {'state':'sent_context_unverified'}
            if confirmed['state']!='live':return {'state':'sent_context_unverified'}
            return {'state':'sent' if confirmed['live']['live_id']==ticket[1] else 'sent_broadcast_changed'}
