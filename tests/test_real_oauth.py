import asyncio
import base64
from contextlib import asynccontextmanager
import json
import logging
import re
import socket
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from spoon_local.main import create_real_app, create_mock_app, create_app
from spoon_local.spoon.auth_service import AuthService, RealAuthService
from spoon_local.spoon.oauth import OAuthSettings, MockOAuthClient, LOCAL_ORIGIN, CALLBACK_URI
from spoon_local.spoon.real_oauth import RealOAuthClient, RealConfigurationError, OAuthHttpFailure, load_real_oauth_settings
from spoon_local.web.real_auth_routes import REAL_COOKIE

# Deliberately invented values. Never load the user's project .env in this file.
TEST_ID='11111111-1111-4111-8111-111111111111'
TEST_SECRET='test-only-not-issued-PRIVATE-SENTINEL'


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args,**kwargs):raise AssertionError('Real network forbidden in automatic tests')
    monkeypatch.setattr(socket.socket,'connect',denied)
    monkeypatch.setattr(socket.socket,'connect_ex',denied)
    monkeypatch.setattr(socket,'create_connection',denied)


def settings():
    return OAuthSettings(TEST_ID,TEST_SECRET)


def pair(refresh='test-refresh-one', **changes):
    return {'access_token':'test-access-one','refresh_token':refresh,'token_type':'Bearer','expires_in':3600,'scope':'events.donation live.read',**changes}


class Clock:
    def __init__(self):self.now=1000.0
    def __call__(self):return self.now


async def get_service(handler):
    client=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(handler))
    clock=Clock()
    service=RealAuthService(settings(),client,clock)
    return service,client,clock


@asynccontextmanager
async def browser(handler):
    app=create_real_app(settings(),transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
            yield client,app


async def begin_browser(client):
    page=await client.get('/auth/spoon/prepare')
    assert 'SPOON으로 로그인' in page.text
    assert "form-action 'self' https://developers.spooncast.net" in page.headers['content-security-policy']
    cookie=page.headers['set-cookie']
    assert REAL_COOKIE in cookie and 'HttpOnly' in cookie and 'SameSite=lax' in cookie
    csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
    response=await client.post('/auth/spoon/start',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
    assert response.status_code==303
    assert response.headers['location']==LOCAL_ORIGIN+'/auth/spoon/continue'
    handoff=await client.get(response.headers['location'])
    from html import unescape
    destination=unescape(re.search(r'id="official-oauth-link" href="([^"]+)"',handoff.text)[1])
    url=urlsplit(destination)
    assert (url.scheme,url.netloc,url.path)==('https','developers.spooncast.net','/kr/oauth/authorize')
    return csrf,parse_qs(url.query)['state'][0]


async def authorize(service,code='test-code-one'):
    session=service.prepare(None)
    state=await service.begin(session.identifier)
    assert await service.complete(session.identifier,state,code,None)
    return session,state


def test_fixed_authorization_url_and_scope():
    async def check():
        client=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(lambda request:httpx.Response(500)))
        url=client.authorization_url('test-state')
        values=parse_qs(urlsplit(url).query)
        assert values=={'response_type':['code'],'client_id':[TEST_ID],'redirect_uri':[CALLBACK_URI],'scope':['events.donation live.read'],'state':['test-state']}
        assert '%20' in url and 'code_challenge' not in url and TEST_SECRET not in url
        await client.aclose()
    asyncio.run(check())


def test_exchange_refresh_revoke_request_shapes():
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.headers['Authorization']=='Basic '+base64.b64encode((TEST_ID+':'+TEST_SECRET).encode()).decode()
        assert request.headers['content-type']=='application/x-www-form-urlencoded'
        if request.url.path.endswith('revoke'):return httpx.Response(200)
        return httpx.Response(200,json=pair('test-refresh-two' if len(requests)==2 else 'test-refresh-one'))
    async def check():
        client=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(handler))
        assert (await client.exchange('test-code',settings()))['token_type']=='Bearer'
        assert (await client.refresh('test-refresh-one',settings()))['refresh_token']=='test-refresh-two'
        await client.revoke('test-refresh-two',settings())
        assert [str(request.url) for request in requests]==['https://kr-openapi.spooncast.net/v1/oauth/token','https://kr-openapi.spooncast.net/v1/oauth/token','https://kr-openapi.spooncast.net/v1/oauth/revoke']
        assert parse_qs(requests[0].content.decode())=={'grant_type':['authorization_code'],'code':['test-code'],'redirect_uri':[CALLBACK_URI]}
        assert 'client_secret' not in requests[0].content.decode()
        await client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('status,error,category',[(400,'invalid_grant','invalid_grant'),(401,'invalid_client','invalid_client'),(403,None,'forbidden'),(429,None,'rate_limited'),(500,None,'provider_unavailable'),(503,None,'provider_unavailable'),(302,None,'oauth_error')])
def test_safe_http_failures_no_retry(status,error,category):
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(status,json={'error':error,'error_description':TEST_SECRET},headers={'Location':'https://evil.example/steal'})
    async def check():
        client=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(handler))
        with pytest.raises(OAuthHttpFailure) as raised:await client.exchange('code',settings())
        assert raised.value.category==category and TEST_SECRET not in str(raised.value)
        with pytest.raises(OAuthHttpFailure):await client.exchange('code',settings())
        assert len(requests)==1
        await client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('changes',[{'access_token':''},{'refresh_token':None},{'token_type':'Other'},{'expires_in':0},{'expires_in':True},{'expires_in':float('inf')},{'scope':'live.read'},{'access_token':'mock-access-fake'}])
def test_response_validation(changes):
    async def check():
        client=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(lambda request:httpx.Response(200,json=pair(**changes))))
        with pytest.raises(OAuthHttpFailure):await client.exchange('code',settings())
        await client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('body,content_type',[(b'not JSON','application/json'),(b'[]','application/json'),(b'{}','text/html'),(b'x'*65537,'application/json')])
def test_malformed_or_large_response(body,content_type):
    async def check():
        client=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(lambda request:httpx.Response(200,content=body,headers={'Content-Type':content_type})))
        with pytest.raises(OAuthHttpFailure):await client.exchange('code',settings())
        await client.aclose()
    asyncio.run(check())


def test_timeout_never_reuses_code():
    calls=[]
    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout('PRIVATE-NETWORK-'+TEST_SECRET,request=request)
    async def check():
        client=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(handler))
        with pytest.raises(OAuthHttpFailure) as error:await client.exchange('code',settings())
        assert error.value.category=='network_error' and TEST_SECRET not in str(error.value)
        with pytest.raises(OAuthHttpFailure):await client.exchange('code',settings())
        assert len(calls)==1
        await client.aclose()
    asyncio.run(check())


def test_manual_browser_flow_and_logout_without_sse():
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200) if request.url.path.endswith('revoke') else httpx.Response(200,json=pair())
    async def check():
        async with browser(handler) as (client,app):
            assert app.state.database_path is None
            assert (await client.get('/api/health')).status_code==200
            assert (await client.get('/api/mode')).json()=={'auth_mode':'real'}
            assert (await client.get('/api/status')).json()['spoon_auth']=='real_ready'
            assert requests==[]
            csrf,state=await begin_browser(client)
            assert requests==[]  # URL generation did not fetch authorization endpoint.
            callback=await client.get('/auth/spoon/callback',params={'code':'test-code','state':state})
            assert callback.status_code==303 and callback.headers['location']==LOCAL_ORIGIN+'/'
            assert '?' not in callback.headers['location']
            assert (await client.get('/api/status')).json()['spoon_auth']=='authenticated'
            assert 'SPOON 계정 연결 완료' in (await client.get('/auth/spoon/result')).text
            assert (await client.get('/api/donations')).json()['source_mode']=='disabled'
            assert (await client.get('/auth/spoon/mock-consent')).status_code==404
            assert (await client.post('/auth/spoon/collection/start',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})).status_code==404
            logout=await client.post('/auth/spoon/disconnect',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
            assert logout.status_code==303
            assert (await client.get('/api/status')).json()['spoon_auth']=='real_ready'
            assert len(requests)==2
    asyncio.run(check())


@pytest.mark.parametrize('attack',['missing','wrong','expired','session','duplicate','both'])
def test_callback_security_prevents_http(attack):
    calls=[]
    async def check():
        service,http,clock=await get_service(lambda request:(calls.append(1) or httpx.Response(200,json=pair())))
        session=service.prepare(None)
        state=await service.begin(session.identifier)
        if attack=='expired':clock.now+=301
        if attack in {'duplicate','both'}:
            async with browser(lambda request:(calls.append(1) or httpx.Response(200,json=pair()))) as (client,app):
                csrf,s=await begin_browser(client)
                params=[('state',s),('state',s),('code','code')] if attack=='duplicate' else {'state':s,'code':'code','error':'access_denied'}
                assert (await client.get('/auth/spoon/callback',params=params)).status_code==400
        else:
            assert not await service.complete('bad-session' if attack=='session' else session.identifier,None if attack=='missing' else ('wrong' if attack=='wrong' else state),'code',None)
        assert calls==[]
        await http.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('attack',['csrf','origin','host','cookie','get'])
def test_start_csrf_host_origin(attack):
    calls=[]
    async def check():
        async with browser(lambda request:(calls.append(1) or httpx.Response(500))) as (client,app):
            page=await client.get('/auth/spoon/prepare')
            csrf=re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)
            headers={'Origin':LOCAL_ORIGIN}
            if attack=='csrf':csrf='forged'
            if attack=='origin':headers['Origin']='https://evil.example'
            if attack=='host':headers['Host']='127.0.0.1:8000'
            if attack=='cookie':headers['Cookie']=f'{REAL_COOKIE}=one; {REAL_COOKIE}=two'
            response=await client.get('/auth/spoon/start') if attack=='get' else await client.post('/auth/spoon/start',data={'csrf':csrf},headers=headers)
            assert response.status_code==(405 if attack=='get' else 400)
            assert calls==[]
    asyncio.run(check())


@pytest.mark.parametrize('deny',[False,True])
def test_validated_failure_clears_tokens_session(deny):
    async def check():
        service,http,clock=await get_service(lambda request:httpx.Response(400,json={'error':'invalid_grant','error_description':TEST_SECRET}))
        session=service.prepare(None)
        state=await service.begin(session.identifier)
        assert not await service.complete(session.identifier,state,None if deny else 'expired-code','access_denied' if deny else None)
        assert service.store.pair is None and service.session is None and service.pending is None
        assert service.state=='reauth_required'
        await http.aclose()
    asyncio.run(check())


def test_concurrent_callback_is_consumed_once_without_clearing_success():
    calls=[]
    async def handler(request):
        calls.append(1)
        await asyncio.sleep(.01)
        return httpx.Response(200,json=pair())
    async def check():
        service,http,clock=await get_service(handler)
        session=service.prepare(None)
        state=await service.begin(session.identifier)
        result=await asyncio.gather(*(service.complete(session.identifier,state,'one-code',None) for _ in range(5)))
        assert result.count(True)==1 and len(calls)==1
        assert service.store.pair and service.state=='authenticated'
        await http.aclose()
    asyncio.run(check())


def test_concurrent_refresh_rotation_and_scope_recheck():
    calls=[]
    async def handler(request):
        fields=parse_qs(request.content.decode())
        calls.append(fields['grant_type'][0])
        await asyncio.sleep(.01)
        return httpx.Response(200,json=pair('test-refresh-two' if fields['grant_type']==['refresh_token'] else 'test-refresh-one'))
    async def check():
        service,http,clock=await get_service(handler)
        await authorize(service)
        previous=service.store.pair
        clock.now=previous.refresh_at
        assert (await asyncio.gather(*(service.status() for _ in range(10))))==['authenticated']*10
        assert calls==['authorization_code','refresh_token']
        assert service.store.pair.refresh_token!='test-refresh-one'
        await http.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('failure',['invalid_grant','scope','unchanged'])
def test_refresh_failure_requires_reauth_and_cleans_session(failure):
    def handler(request):
        if parse_qs(request.content.decode())['grant_type']==['authorization_code']:return httpx.Response(200,json=pair())
        if failure=='invalid_grant':return httpx.Response(400,json={'error':'invalid_grant'})
        return httpx.Response(200,json=pair('test-refresh-two',scope='live.read') if failure=='scope' else pair())
    async def check():
        service,http,clock=await get_service(handler)
        await authorize(service)
        clock.now=service.store.pair.refresh_at
        assert await service.status()=='reauth_required'
        assert service.store.pair is None and service.session is None
        await http.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('success',[True,False])
def test_revoke_cleans_local_memory_even_on_failure(success):
    def handler(request):
        if request.url.path.endswith('revoke'):return httpx.Response(200 if success else 503)
        return httpx.Response(200,json=pair())
    async def check():
        service,http,clock=await get_service(handler)
        await authorize(service)
        assert await service.disconnect()==success
        assert service.store.pair is None and service.session is None
        assert service.state==('real_ready' if success else 'revoke_unconfirmed')
        if not success:assert not await service.disconnect()
        await http.aclose()
    asyncio.run(check())


def test_sensitive_transport_logs_and_browser_api(caplog):
    def handler(request):
        logging.getLogger('httpcore.test').debug('headers %r',request.headers)
        logging.getLogger('httpx').warning('body %s %s',request.content,TEST_SECRET)
        return httpx.Response(200,json=pair(),headers={'Set-Cookie':'provider=PRIVATE-COOKIE'})
    async def check():
        caplog.set_level(logging.DEBUG)
        async with browser(handler) as (client,app):
            csrf,state=await begin_browser(client)
            code='PRIVATE-CODE-SENTINEL'
            await client.get('/auth/spoon/callback',params={'state':state,'code':code})
            text=(await client.get('/api/status')).text+(await client.get('/auth/spoon/result')).text
            for value in [TEST_SECRET,base64.b64encode((TEST_ID+':'+TEST_SECRET).encode()).decode(),code,state,'test-access-one','test-refresh-one','PRIVATE-COOKIE']:
                assert value not in text+caplog.text
        # Production client also clears provider cookies instead of forwarding them.
        service,http,clock=await get_service(handler)
        await authorize(service)
        assert not http.http.cookies
        await http.aclose()
    asyncio.run(check())


def test_mock_real_settings_clients_and_tokens_cannot_mix():
    with pytest.raises(RealConfigurationError):RealOAuthClient(settings())
    with pytest.raises(RealConfigurationError):RealOAuthClient(OAuthSettings.mock(),enabled=True)
    with pytest.raises(ValueError):RealAuthService(settings(),MockOAuthClient())
    async def check():
        calls=[]
        real=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(lambda request:(calls.append(1) or httpx.Response(500))))
        with pytest.raises(ValueError):AuthService(OAuthSettings.mock(),real)
        for method,value in [(real.exchange,'mock-code-fake'),(real.refresh,'mock-refresh-fake'),(real.revoke,'mock-access-fake')]:
            with pytest.raises(OAuthHttpFailure):await method(value,settings())
        assert calls==[]
        await real.aclose()
    asyncio.run(check())


def test_real_mode_never_accesses_db_or_default_config(monkeypatch):
    def denied(*args,**kwargs):raise AssertionError('DB/default settings accessed')
    monkeypatch.setattr('spoon_local.main.initialize_database',denied)
    monkeypatch.setattr('spoon_local.main.load_settings',denied)
    monkeypatch.setattr('spoon_local.main.load_real_oauth_settings',denied)
    async def check():
        async with browser(lambda request:httpx.Response(500)) as (client,app):
            assert app.state.database_path is None
            assert (await client.get('/')).status_code==303
    asyncio.run(check())


def test_real_setting_loader_os_priority_and_no_secret_repr(tmp_path):
    (tmp_path/'.env').write_text(f'SPOON_CLIENT_ID={TEST_ID}\nSPOON_CLIENT_SECRET=file-only-dummy\nSPOON_REGION=kr\nSPOON_REDIRECT_URI={CALLBACK_URI}\n')
    loaded=load_real_oauth_settings(tmp_path,environ={'SPOON_CLIENT_SECRET':TEST_SECRET})
    assert loaded.client_secret==TEST_SECRET and TEST_SECRET not in repr(loaded)
    assert (tmp_path/'.env').read_text().endswith(CALLBACK_URI+'\n')


@pytest.mark.parametrize('change',[{}, {'SPOON_CLIENT_SECRET':''},{'SPOON_CLIENT_ID':'invalid'},{'SPOON_REGION':'xx'},{'SPOON_REDIRECT_URI':'https://evil.example'}])
def test_missing_or_invalid_settings_fail_safely(tmp_path,change):
    if change:
        values={'SPOON_CLIENT_ID':TEST_ID,'SPOON_CLIENT_SECRET':TEST_SECRET,'SPOON_REGION':'kr','SPOON_REDIRECT_URI':CALLBACK_URI,**change}
    else:values={}
    with pytest.raises(RealConfigurationError) as error:load_real_oauth_settings(tmp_path,environ=values)
    assert TEST_SECRET not in str(error.value)


def test_mock_path_never_loads_real_settings(tmp_path,monkeypatch):
    def denied(*args,**kwargs):raise AssertionError('Real env accessed')
    monkeypatch.setattr('spoon_local.main.load_real_oauth_settings',denied)
    monkeypatch.setattr('spoon_local.main.load_settings',denied)
    async def check():
        app=create_mock_app(tmp_path/'mock-sse.sqlite3')
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=LOCAL_ORIGIN) as client:
                assert (await client.get('/api/mode')).json()=={'auth_mode':'mock'}
    asyncio.run(check())


def test_invalid_client_reports_configuration_error_and_cleans_session():
    async def check():
        service,http,clock=await get_service(lambda request:httpx.Response(401,json={'error':'invalid_client','error_description':TEST_SECRET}))
        session=service.prepare(None)
        state=await service.begin(session.identifier)
        assert not await service.complete(session.identifier,state,'code',None)
        assert service.state=='configuration_error'
        assert service.store.pair is None and service.session is None
        await http.aclose()
    asyncio.run(check())


def test_forced_refresh_requests_share_one_rotation():
    calls=[]
    async def handler(request):
        grant=parse_qs(request.content.decode())['grant_type'][0]
        calls.append(grant)
        await asyncio.sleep(.01)
        return httpx.Response(200,json=pair('test-refresh-two' if grant=='refresh_token' else 'test-refresh-one'))
    async def check():
        service,http,clock=await get_service(handler)
        await authorize(service)
        assert all(await asyncio.gather(*(service.ensure_access(force_refresh=True) for _ in range(5))))
        assert calls==['authorization_code','refresh_token']
        await http.aclose()
    asyncio.run(check())


def test_transport_settings_and_provider_cookies_are_not_forwarded():
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=pair('test-refresh-two' if len(requests)==2 else 'test-refresh-one'),headers={'Set-Cookie':'provider=PRIVATE-COOKIE'})
    async def check():
        client=RealOAuthClient(settings(),enabled=True,transport=httpx.MockTransport(handler))
        assert client.http.trust_env is False and client.http.follow_redirects is False
        assert client.http.timeout.connect==5 and client.http.timeout.read==10
        await client.exchange('code',settings())
        await client.refresh('test-refresh-one',settings())
        assert 'cookie' not in requests[1].headers and not client.http.cookies
        await client.aclose()
    asyncio.run(check())


def test_japan_region_is_fixed_and_not_an_arbitrary_endpoint():
    async def check():
        configuration=OAuthSettings(TEST_ID,TEST_SECRET,region='jp')
        requests=[]
        client=RealOAuthClient(configuration,enabled=True,transport=httpx.MockTransport(lambda request:(requests.append(request) or httpx.Response(200,json=pair()))))
        assert client.authorization_url('state').startswith('https://developers.spooncast.net/jp/oauth/authorize?')
        await client.exchange('code',configuration)
        assert str(requests[0].url)=='https://jp-openapi.spooncast.net/v1/oauth/token'
        await client.aclose()
    asyncio.run(check())


def test_launcher_modes_are_mutually_exclusive(monkeypatch):
    from spoon_local.__main__ import main
    monkeypatch.setattr('sys.argv',['spoon_local','--real-oauth','--mock-auth'])
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2


def test_launcher_configuration_error_never_starts_server(monkeypatch,capsys):
    from spoon_local.__main__ import main
    def invalid():raise RealConfigurationError()
    def denied(*args,**kwargs):raise AssertionError('Server started')
    monkeypatch.setattr('sys.argv',['spoon_local','--real-oauth'])
    monkeypatch.setattr('spoon_local.__main__.create_real_app',invalid)
    monkeypatch.setattr('spoon_local.__main__.uvicorn.run',denied)
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2
    assert TEST_SECRET not in capsys.readouterr().err


def test_cancelled_exchange_clears_session_and_does_not_retry_code():
    async def check():
        entered=asyncio.Event()
        calls=[]
        async def handler(request):
            calls.append(1);entered.set()
            await asyncio.Event().wait()
        service,http,clock=await get_service(handler)
        session=service.prepare(None)
        state=await service.begin(session.identifier)
        task=asyncio.create_task(service.complete(session.identifier,state,'cancelled-code',None))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert service.session is None and service.store.pair is None and service.state=='reauth_required'
        with pytest.raises(OAuthHttpFailure):await http.exchange('cancelled-code',settings())
        assert len(calls)==1
        await http.aclose()
    asyncio.run(check())


def test_cancelled_refresh_drops_old_pair_to_avoid_reusing_rotated_token():
    async def check():
        entered=asyncio.Event()
        async def handler(request):
            if parse_qs(request.content.decode())['grant_type']==['authorization_code']:return httpx.Response(200,json=pair())
            entered.set();await asyncio.Event().wait()
        service,http,clock=await get_service(handler)
        await authorize(service)
        clock.now=service.store.pair.refresh_at
        task=asyncio.create_task(service.status())
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert service.store.pair is None and service.session is None and service.state=='reauth_required'
        await http.aclose()
    asyncio.run(check())


def test_handoff_consumed_callback_cannot_exchange_again_or_return_link():
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=pair())
    async def check():
        async with browser(handler) as (client,app):
            csrf,state=await begin_browser(client)
            assert calls==[]
            first=await client.get('/auth/spoon/callback',params={'state':state,'code':'invented-first-code'})
            assert first.status_code==303 and len(calls)==1
            assert (await client.get('/auth/spoon/continue')).status_code==400
            # Back/refresh of a provider page can deliver either the same or a
            # different code for the old state. Neither is exchanged again.
            for code in ['invented-first-code','invented-second-code']:
                await client.get('/auth/spoon/callback',params={'state':state,'code':code})
            assert len(calls)==1
            assert (await client.get('/api/status')).json()['spoon_auth']=='authenticated'
            retry=await client.post('/auth/spoon/start',data={'csrf':csrf},headers={'Origin':LOCAL_ORIGIN})
            assert retry.status_code==400
    asyncio.run(check())
