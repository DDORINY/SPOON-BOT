"""Explicit-start fixed HTTPS SSE transport; no redirects, proxies or replay."""

import json
import httpx

from spoon_local.logging_config import configure_safe_http_logs
from spoon_local.spoon.auth_service import RealAuthService
from spoon_local.spoon.real_oauth import REGIONS


class RealConnection:
    live_id = None  # SSE does not specify liveId; local sessions are observations.

    def __init__(self, response):
        self.response = response
        self.status = response.status_code
        self.detail_code = None
        self.retry_after = response.headers.get('retry-after')

    async def read(self):
        try:
            async for chunk in self.response.aiter_bytes():
                yield chunk
        except httpx.HTTPError:
            raise ConnectionError('SSE transport interrupted') from None

    async def close(self):
        await self.response.aclose()


class RealSSESource:
    def __init__(self, auth: RealAuthService, *, transport=None):
        if not isinstance(auth, RealAuthService):
            raise ValueError('Real authentication required')
        self.auth = auth
        self.lease = None
        configure_safe_http_logs()
        self.client = httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(45, connect=5), limits=httpx.Limits(max_connections=1),
        )

    async def open(self):
        self.lease = await self.auth.access_lease({'events.donation'})
        if self.lease is None or self.lease.access_token.lower().startswith('mock-'):
            raise ConnectionError('Authentication unavailable')
        request = self.client.build_request(
            'GET', REGIONS[self.auth.settings.region][1] + '/v1/live/events',
            headers={'Authorization': 'Bearer ' + self.lease.access_token, 'Accept': 'text/event-stream'},
        )
        response = None
        try:
            response = await self.client.send(request, stream=True)
            connection = RealConnection(response)
            if response.status_code == 200:
                if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'text/event-stream':
                    await response.aclose()
                    raise ConnectionError('Invalid SSE content type')
            else:
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    remaining = 65536 - len(body)
                    body.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        break
                text = body.decode('utf-8', errors='replace')
                if text.startswith('data:'):
                    text = text[5:].strip()
                try:
                    value = json.loads(text)
                    code = value.get('detailCode') if isinstance(value, dict) else None
                    if code in {'OAPI_MNGR_0209', 'OAPI_MNGR_0301'}:
                        connection.detail_code = code
                except ValueError:
                    pass
                await response.aclose()
            return connection
        except BaseException as error:
            if response is not None:
                await response.aclose()
            if isinstance(error, httpx.HTTPError):
                raise ConnectionError('SSE transport unavailable') from None
            raise
        finally:
            self.client.cookies.clear()

    async def aclose(self):
        self.lease = None
        await self.client.aclose()
