"""Manual Real login and optional confirmed collection routes; no mock consent."""

from html import escape
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from spoon_local.spoon.auth_service import RealAuthService, same
from spoon_local.spoon.oauth import LOCAL_ORIGIN, OAuthFailure
from spoon_local.web.auth_routes import SAFE_HEADERS, post_values, single
from spoon_local.web.auth_diagnostics import diagnose, rejection_headers
from spoon_local.web.oauth_ui import render_auth_page

REAL_COOKIE = 'spoon_real_session'
REAL_HEADERS = {**SAFE_HEADERS, 'Referrer-Policy': 'same-origin', 'Content-Security-Policy': "default-src 'none'; style-src 'self'; form-action 'self' https://developers.spooncast.net; frame-ancestors 'none'; base-uri 'none'"}


def real_page(body, status=200, **presentation):
    return HTMLResponse(render_auth_page(body, **presentation), status_code=status, headers=REAL_HEADERS)


def rejected(request=None):
    return JSONResponse({'error':'auth_request_rejected'},status_code=400,headers={**SAFE_HEADERS, **(rejection_headers(request.scope) if request else {})})


def local_redirect(path):
    return RedirectResponse(LOCAL_ORIGIN+path,status_code=303,headers=SAFE_HEADERS)


def dashboard_session_bound(auth, identifier):
    return bool(auth.state == 'authenticated' and auth.store.pair and auth.session and identifier and same(identifier, auth.session.identifier))


def real_auth_router(auth: RealAuthService, stream=None):
    router=APIRouter(prefix='/auth/spoon',include_in_schema=False)

    @router.get('/prepare')
    async def prepare(request: Request):
        if auth.store.pair is not None:
            if not dashboard_session_bound(auth, request.cookies.get(REAL_COOKIE)):
                return real_page('<p>계정을 연결한 브라우저 창에서 이용해 주세요.</p>', status=401, title='다시 로그인해 주세요')
            response = local_redirect('/')
            response.set_cookie(REAL_COOKIE, auth.session.identifier, httponly=True, samesite='lax', path='/')
            return response
        session=auth.prepare(request.cookies.get(REAL_COOKIE))
        active=auth.store.pair is not None
        action='disconnect' if active else 'start'
        label='로그아웃 및 토큰 해제' if active else 'SPOON으로 로그인'
        body='<h2>나만의 방송 도우미</h2><p>SPOON 공식 페이지에서 안전하게 로그인하세요.</p>'
        if auth.state=='revoke_unconfirmed':
            body+='<p class="notice">로컬 토큰은 삭제했지만 서버측 폐기는 미확인입니다. SPOON 측 연동 관리에서 확인하세요.</p>'
        body+=f'<form method="post" action="/auth/spoon/{action}"><input type="hidden" name="csrf" value="{escape(session.csrf,quote=True)}"><button type="submit">{label}</button></form><p class="microcopy">다음 화면에서 계정 연결을 완료하세요.</p><a class="secondary-link" href="/">대시보드로 돌아가기</a>'
        response=real_page(body, title='방송을 더 즐겁게', description='SPOON 계정으로 로그인하고 시작하세요.', connected=auth.state=='authenticated')
        response.delete_cookie(REAL_COOKIE, path='/auth/spoon')
        response.set_cookie(REAL_COOKIE,session.identifier,max_age=300,httponly=True,samesite='lax',path='/')
        return response

    @router.post('/start')
    async def start(request: Request):
        if await post_values(request,auth,REAL_COOKIE) is None:
            return rejected(request)
        try:
            await auth.begin(request.cookies.get(REAL_COOKIE))
        except OAuthFailure:
            diagnose(request.scope, 'auth_already_active' if auth.store.pair is not None else 'auth_start_failed')
            return rejected(request)
        return local_redirect('/auth/spoon/continue')

    @router.get('/continue')
    async def continue_login(request: Request):
        # GET never creates state or contacts the provider. Refresh reuses the
        # pending transaction; a consumed/expired/cross-session one is rejected.
        pending = auth.pending
        if (request.scope.get('oauth_query') or pending is None
            or not auth.valid_pending(request.cookies.get(REAL_COOKIE), pending.state)):
            if pending is not None and auth.clock() >= pending.deadline:
                response = real_page('<p>안전을 위해 인증 요청 시간이 제한됩니다. 준비 화면에서 다시 시작하세요.</p><a class="primary-button" href="/auth/spoon/prepare">처음으로 돌아가기</a>', status=400, title='인증 요청이 만료되었습니다', description='새 인증 요청으로 다시 연결해 주세요.')
                response.headers['Referrer-Policy'] = 'no-referrer'
                return response
            return rejected(request)
        destination = auth.client.authorization_url(pending.state)
        body = ('<p>다음 링크를 직접 누르면 공식 SPOON 인증 화면으로 이동합니다. '
                '앱 등록 봇 계정과 다른 방송 DJ 계정으로 동의하세요.</p>'
                f'<a class="primary-button" id="official-oauth-link" href="{escape(destination, quote=True)}" rel="noreferrer noopener">공식 SPOON 인증으로 이동</a>'
                '<p>이 요청은 5분 안에 완료하세요. 만료되면 준비 화면부터 다시 시작하세요.</p>'
                '<a class="secondary-link" href="/auth/spoon/prepare">처음으로 돌아가기</a>')
        response = real_page(body, title='SPOON 인증 준비 완료', description='아래 버튼을 눌러 SPOON 공식 인증 페이지에서 계정 연결을 완료하세요.', eyebrow='READY TO CONNECT')
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @router.get('/callback')
    async def callback(request: Request):
        values=request.scope.get('oauth_query',{})
        if (set(values)-{'code','state','error'} or any(len(value)!=1 for value in values.values()) or bool(single(values,'code'))==bool(single(values,'error'))):
            return rejected(request)
        success=await auth.complete(request.cookies.get(REAL_COOKIE),single(values,'state'),single(values,'code'),single(values,'error'))
        response=local_redirect('/' if success else '/auth/spoon/result')
        if success:
            response.set_cookie(REAL_COOKIE, auth.session.identifier, httponly=True, samesite='lax', path='/')
        if not success and not auth.valid_session(request.cookies.get(REAL_COOKIE)):
            response.delete_cookie(REAL_COOKIE,path='/')
        return response

    @router.get('/result')
    async def result():
        labels={
            'authenticated':'계정 연결이 완료됐어요. 방송 연결은 별도로 시작해 주세요.',
            'reauth_required':'로그인에 실패했거나 시간이 지났어요. 다시 로그인해 주세요.',
            'revoke_unconfirmed':'로그아웃했지만 SPOON의 연결 해제를 확인하지 못했어요. SPOON에서 연결 상태를 확인해 주세요.',
            'configuration_error':'앱 연결 설정을 확인할 수 없어요. 방송 도우미를 설정한 곳에 문의해 주세요.',
            'real_ready':'아직 로그인하지 않았어요. 로그인 화면에서 시작해 주세요.',
        }
        if stream:
            labels['authenticated'] = '계정 연결이 완료됐어요. 방송 관리에서 연결을 시작해 주세요.'
        connected = auth.state == 'authenticated'
        title = 'SPOON 계정 연결 완료' if connected else '계정 연결을 완료하지 못했습니다'
        if auth.state == 'real_ready':
            title = 'SPOON 계정 연결을 시작하세요'
        return real_page('<div class="security-card"><strong>연결 상태 안내</strong><p>'+labels.get(auth.state,'인증 상태 확인이 필요합니다.')+'</p></div><a class="primary-button" href="/auth/spoon/prepare">인증 관리</a><a class="secondary-link" href="/">대시보드로 돌아가기</a>', title=title, description='연결 상태는 서버에서 확인한 결과입니다.', connected=connected)

    @router.get('/manage')
    async def manage(request: Request):
        if not dashboard_session_bound(auth, request.cookies.get(REAL_COOKIE)) or not await auth.ensure_access():
            return local_redirect('/auth/spoon/prepare')
        session = auth.prepare(request.cookies.get(REAL_COOKIE))
        csrf = escape(session.csrf, quote=True)
        body = '<p>방송에 연결하면 방송 안에 도우미 안내가 표시될 수 있어요.</p>'
        if stream:
            if stream.task and not stream.task.done():
                body += f'<form method="post" action="/auth/spoon/collection/stop"><input type="hidden" name="csrf" value="{csrf}"><button>방송 연결 해제하기</button></form>'
            else:
                body += f'<form method="post" action="/auth/spoon/collection/start"><input type="hidden" name="csrf" value="{csrf}"><label><input type="checkbox" name="dj_notice" value="confirmed" required>방송 DJ에게 연결을 알렸고 도우미 안내 표시를 이해했어요.</label><button>방송 연결하기</button></form>'
        else:
            body += '<p>방송 연결 기능이 아직 준비되지 않았어요.</p>'
        body += f'<form method="post" action="/auth/spoon/disconnect"><input type="hidden" name="csrf" value="{csrf}"><button>로그아웃</button></form><a class="secondary-link" href="/">홈으로 돌아가기</a>'
        response = real_page(body, title='내 방송 관리', description='계정과 방송 연결을 관리하세요.', connected=True)
        response.set_cookie(REAL_COOKIE, session.identifier, httponly=True, samesite='lax', path='/')
        return response

    @router.post('/disconnect')
    async def disconnect(request: Request):
        if await post_values(request,auth,REAL_COOKIE) is None:
            return rejected(request)
        if stream:
            await stream.stop("auth_disconnected")
        confirmed=await auth.disconnect()
        response=local_redirect('/auth/spoon/result')
        response.delete_cookie(REAL_COOKIE,path='/')
        if not confirmed:
            response.headers['X-Auth-Revocation']='unconfirmed'
        return response

    if stream:
        @router.get('/collection')
        async def collection(request: Request):
            if not dashboard_session_bound(auth, request.cookies.get(REAL_COOKIE)) or not await auth.ensure_access():
                return local_redirect('/auth/spoon/prepare')
            session = auth.prepare(request.cookies.get(REAL_COOKIE))
            csrf = escape(session.csrf, quote=True)
            body = '<p>실제 방송에 봇 계정의 청취자 고지가 표시됩니다. DJ에게 수집 사실을 먼저 고지하세요. 관측된 금액만 저장하며 누락 복원은 보장하지 않습니다.</p>'
            body += f'<form method="post" action="/auth/spoon/collection/start"><input type="hidden" name="csrf" value="{csrf}"><label><input type="checkbox" name="dj_notice" value="confirmed" required>DJ에게 수집 사실을 고지했고 연결 시 봇 고지가 표시됨을 이해했습니다.</label><button>실제 후원 수집 시작</button></form>'
            body += f'<form method="post" action="/auth/spoon/collection/stop"><input type="hidden" name="csrf" value="{csrf}"><button>수집 중지</button></form><a href="/">대시보드</a>'
            response = real_page(body)
            response.set_cookie(REAL_COOKIE, session.identifier, max_age=300, httponly=True, samesite='lax', path='/')
            return response

        @router.post('/collection/start')
        async def collection_start(request: Request):
            values = await post_values(request, auth, REAL_COOKIE)
            if values is None or single(values, 'dj_notice') != 'confirmed':
                return rejected(request)
            if not await stream.start():
                return JSONResponse({'error': 'collection_not_started', 'state': stream.state.lower()}, status_code=409, headers=SAFE_HEADERS)
            if request.headers.get('accept') == 'application/json':
                return JSONResponse({'state':stream.state.lower()},headers=SAFE_HEADERS)
            return local_redirect('/')

        @router.post('/collection/stop')
        async def collection_stop(request: Request):
            if await post_values(request, auth, REAL_COOKIE) is None:
                return rejected(request)
            await stream.stop()
            if request.headers.get('accept') == 'application/json':
                return JSONResponse({'state':'stopped'},headers=SAFE_HEADERS)
            return local_redirect('/')

    return router
