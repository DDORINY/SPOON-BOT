"""Fixed-endpoint OAuth HTTP client, explicitly enabled and never auto-retried."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from collections.abc import Mapping
from urllib.parse import quote, urlencode

import httpx
from dotenv import dotenv_values

from spoon_local.config import PROJECT_ROOT
from spoon_local.logging_config import configure_safe_http_logs
from spoon_local.spoon.oauth import (
    REAL_EVENT_SCOPES, CALLBACK_URI, MOCK_CLIENT_ID, MOCK_CLIENT_SECRET, OAuthFailure, OAuthSettings,
)
from spoon_local.spoon.token_store import TokenPair

REGIONS = {
    'kr': ('https://developers.spooncast.net/kr/oauth/authorize', 'https://kr-openapi.spooncast.net'),
    'jp': ('https://developers.spooncast.net/jp/oauth/authorize', 'https://jp-openapi.spooncast.net'),
}


class RealConfigurationError(ValueError):
    def __init__(self):
        super().__init__('Real OAuth configuration is missing or invalid')


def validate_real_settings(settings: OAuthSettings):
    if (
        not isinstance(settings, OAuthSettings)
        or settings.client_id == MOCK_CLIENT_ID or settings.client_secret == MOCK_CLIENT_SECRET
        or settings.client_secret.lower().startswith('mock-')
    ):
        raise RealConfigurationError()


def load_real_oauth_settings(project_root: Path = PROJECT_ROOT, environ: Mapping[str,str] | None = None) -> OAuthSettings:
    """Only the explicit Real launcher calls this; no values are logged."""
    try:
        values = dotenv_values(project_root.resolve() / '.env', interpolate=False)
        environment = os.environ if environ is None else environ
        names = ('SPOON_CLIENT_ID','SPOON_CLIENT_SECRET','SPOON_REGION','SPOON_REDIRECT_URI')
        selected = {name: environment.get(name, values.get(name)) for name in names}
        if any(not isinstance(value,str) or not value.strip() for value in selected.values()):
            raise RealConfigurationError()
        settings = OAuthSettings(selected[names[0]],selected[names[1]],selected[names[2]],selected[names[3]],scopes=REAL_EVENT_SCOPES)
        validate_real_settings(settings)
        return settings
    except (ValueError, TypeError, OSError):
        raise RealConfigurationError() from None


class OAuthHttpFailure(OAuthFailure):
    def __init__(self, category: str = 'oauth_error'):
        super().__init__()
        self.category = category


class RealOAuthClient:
    def __init__(self, settings: OAuthSettings, *, enabled: bool = False, transport: httpx.AsyncBaseTransport | None = None):
        if enabled is not True:
            raise RealConfigurationError()
        validate_real_settings(settings)
        self.settings = settings
        self.used_codes: set[bytes] = set()
        self.code_lock = asyncio.Lock()
        configure_safe_http_logs()
        self.http = httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(10, connect=5),
            follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )

    def authorization_url(self, state: str) -> str:
        if not isinstance(state,str) or not state or len(state)>512:
            raise OAuthHttpFailure('invalid_request')
        return REGIONS[self.settings.region][0] + '?' + urlencode({
            'response_type':'code','client_id':self.settings.client_id,
            'redirect_uri':self.settings.redirect_uri,
            'scope':' '.join(sorted(self.settings.scopes)),'state':state,
        },quote_via=quote)

    def _guard(self, settings: OAuthSettings, value: str):
        if settings != self.settings or not isinstance(value,str) or not value or value.startswith('mock-'):
            raise OAuthHttpFailure('invalid_request')

    async def _post(self, operation: str, fields: dict, *, token_response: bool):
        if operation not in {'token','revoke'}:
            raise OAuthHttpFailure('invalid_request')
        endpoint = REGIONS[self.settings.region][1] + '/v1/oauth/' + operation
        try:
            async with asyncio.timeout(15):
                async with self.http.stream(
                    'POST',endpoint,data=fields,
                    auth=httpx.BasicAuth(self.settings.client_id,self.settings.client_secret),
                    headers={'Accept':'application/json'},
                ) as response:
                    body=bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body)>65536:
                            raise OAuthHttpFailure('invalid_response')
                    if response.status_code != 200:
                        category = {401:'invalid_client',403:'forbidden',429:'rate_limited'}.get(response.status_code, 'provider_unavailable' if response.status_code>=500 else 'oauth_error')
                        try:
                            error=json.loads(body).get('error')
                            if error in {'invalid_request','invalid_client','invalid_grant','unsupported_grant_type'}:
                                category=error
                        except (ValueError, TypeError, AttributeError):
                            pass
                        raise OAuthHttpFailure(category)
                    if not token_response:
                        if body:
                            raise OAuthHttpFailure('invalid_response')
                        return None
                    if response.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':
                        raise OAuthHttpFailure('invalid_response')
                    result=json.loads(body)
                    if not isinstance(result,dict):
                        raise OAuthHttpFailure('invalid_response')
                    TokenPair.from_response(result,0)
                    if any(result[name].startswith('mock-') for name in ('access_token','refresh_token')):
                        raise OAuthHttpFailure('invalid_response')
                    # Retain only schema fields, never provider error/extra values.
                    return {key:result[key] for key in ('access_token','refresh_token','expires_in','token_type','scope')}
        except OAuthHttpFailure:
            raise
        except (httpx.HTTPError, TimeoutError):
            raise OAuthHttpFailure('network_error') from None
        except (ValueError, TypeError, KeyError, OAuthFailure, RecursionError):
            raise OAuthHttpFailure('invalid_response') from None
        finally:
            self.http.cookies.clear()

    async def exchange(self, code: str, settings: OAuthSettings) -> dict:
        self._guard(settings,code)
        async with self.code_lock:
            fingerprint=hashlib.sha256(code.encode()).digest()
            if fingerprint in self.used_codes or len(self.used_codes)>=4096:
                raise OAuthHttpFailure('invalid_grant')
            self.used_codes.add(fingerprint)  # Mark before the single request, even on timeout.
        return await self._post('token',{'grant_type':'authorization_code','code':code,'redirect_uri':CALLBACK_URI},token_response=True)

    async def refresh(self, refresh_token: str, settings: OAuthSettings) -> dict:
        self._guard(settings,refresh_token)
        result=await self._post('token',{'grant_type':'refresh_token','refresh_token':refresh_token},token_response=True)
        if result['refresh_token']==refresh_token:
            raise OAuthHttpFailure('invalid_response')
        return result

    async def revoke(self, token: str, settings: OAuthSettings) -> None:
        self._guard(settings,token)
        await self._post('revoke',{'token':token},token_response=False)

    async def aclose(self):
        await self.http.aclose()
