"""Fixed-endpoint, bounded read-only live metadata; never opens SSE."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import hashlib
import math
import time

import httpx
from spoon_local.logging_config import configure_safe_http_logs
from spoon_local.spoon.auth_service import RealAuthService
from spoon_local.spoon.real_oauth import REGIONS
from spoon_local.spoon.retry_after import retry_delay
from spoon_local.spoon.event_models import utc_now


def result(state, retryable=False, live=None):
    return {'state': state, 'live': live, 'retryable': retryable}


class LiveReadClient:
    def __init__(self, auth, *, transport=None, clock=time.monotonic):
        if not isinstance(auth, RealAuthService):
            raise ValueError('Real authentication required')
        self.auth, self.clock = auth, clock
        self.lock = asyncio.Lock()
        self.cached = None
        self.cache_key = None
        self.cache_context = None
        self.until = 0
        self.failures = 0
        self.observation_sequence = 0
        self.observed_at = None
        configure_safe_http_logs()
        self.http = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False, timeout=httpx.Timeout(10, connect=5), limits=httpx.Limits(max_connections=1))

    async def _request(self, pair, path="/v1/live", params=None):
        if path not in {"/v1/live", "/v1/live/listeners", "/v1/live/fans"}:raise ValueError("Invalid API path")
        try:
            async with asyncio.timeout(15):
                async with self.http.stream('GET', REGIONS[self.auth.settings.region][1]+path, params=params, headers={'Authorization':'Bearer '+pair.access_token, 'Accept':'application/json'}) as response:
                    body=bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        if len(body)+len(chunk)>65536:
                            return 502, None, None
                        body.extend(chunk)
                    value=None
                    try: value=json.loads(body,parse_float=Decimal) if path=="/v1/live/fans" else json.loads(body)
                    except (ValueError, RecursionError): pass
                    if response.status_code==200 and response.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
                        return 502,None,None
                    return response.status_code,value,response.headers.get('retry-after')
        except (httpx.TimeoutException, TimeoutError):
            return 504,None,None
        except httpx.HTTPError:
            return 503,{"local_error":"network"},None
        finally:
            self.http.cookies.clear()

    @staticmethod
    def normalize(value):
        if not isinstance(value,dict): raise ValueError()
        def number(name):
            number=value[name]
            if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or number<0 or number>2**53-1 or int(number)!=number:
                raise ValueError()
            return int(number)
        title=value['title']
        if not isinstance(title,str) or len(title)>1000: raise ValueError()
        started=datetime.fromisoformat(value['startedAt'].replace('Z','+00:00'))
        if started.tzinfo is None: raise ValueError()
        if 'isChatFrozen' in value and not isinstance(value['isChatFrozen'],bool):raise ValueError()
        extra={'is_chat_frozen':value['isChatFrozen']} if 'isChatFrozen' in value else {}
        return {**extra,'live_id':number('liveId'),'title':title,'started_at_utc':started.astimezone(timezone.utc).isoformat().replace('+00:00','Z'),'listener_count':number('listenerCount')}

    async def get(self, *, force=False):
        async with self.lock:
            owner=self.auth.session
            context=(owner,getattr(self.auth,'account_context',None))
            pair=await self.auth.access_lease({'live.read'})
            if pair is None:
                self.cached=None
                return result('permission_required' if await self.auth.ensure_access() else 'reauth_required')
            if pair.access_token.lower().startswith('mock-'):
                await self.auth.invalidate_access()
                self.cached=None
                return result('reauth_required')
            if self.cached is not None and self.cache_context == context and self.cache_key == hashlib.sha256(pair.access_token.encode()).digest() and self.clock()<self.until and (not force or self.cached['state'] in {'rate_limited','permission_required','error'}):
                return {**self.cached, 'live':dict(self.cached['live']) if self.cached['live'] else None}
            status,value,retry=await self._request(pair)
            if status==401:
                if self.auth.session is not owner or self.auth.state!='authenticated':return result('reauth_required')
                await self.auth.refresh_if_needed(force=True,expected_pair=pair)
                if self.auth.session is not owner:return result('reauth_required')
                pair=await self.auth.access_lease({'live.read'})
                if pair is not None:
                    status,value,retry=await self._request(pair)
                if pair is None or status==401:
                    await self.auth.invalidate_access()
                    self.cached=None
                    return result('reauth_required')
            # Never disclose a result after logout while the request was pending.
            if self.auth.session is not owner or self.auth.store.pair is not pair or self.auth.state!='authenticated':
                return result('reauth_required')
            delay=30
            if status==200:
                try: data=result('live',live=self.normalize(value))
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError): data=result('error',True)
            elif status==404 and isinstance(value,dict) and value.get('detailCode')=='OAPI_MNGR_0301': data=result('waiting')
            elif status==403: data=result('permission_required')
            elif status==429:
                data=result('rate_limited',True)
                delay=max(30,retry_delay(retry))
            else: data=result('error',status>=500)
            if data['state']=='permission_required':
                delay=float('inf')  # Wait for a different, re-consented token.
            elif data['state']=='error' and data['retryable']:
                self.failures+=1
                delay=min(300,30*2**min(self.failures-1,4))
            else:
                self.failures=0
            self.observation_sequence += 1
            self.observed_at = utc_now()
            self.cache_context = context
            self.cached,self.cache_key,self.until=data,hashlib.sha256(pair.access_token.encode()).digest(),self.clock()+delay
            return {**data,'live':dict(data['live']) if data['live'] else None}

    async def aclose(self):
        self.cached=None
        self.cache_key=None
        await self.http.aclose()
