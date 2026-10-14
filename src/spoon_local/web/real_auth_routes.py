"""Manual Real login and optional confirmed collection routes; no mock consent."""

from html import escape
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from spoon_local.spoon.auth_service import RealAuthService
from spoon_local.spoon.oauth import LOCAL_ORIGIN, OAuthFailure
from spoon_local.web.auth_routes import SAFE_HEADERS, post_values, single

REAL_COOKIE = 'spoon_real_session'
REAL_HEADERS = {**SAFE_HEADERS, 'Content-Security-Policy': "default-src 'none'; form-action 'self' https://developers.spooncast.net; frame-ancestors 'none'; base-uri 'none'"}


def real_page(body, status=200):
    return HTMLResponse('<!doctype html><html lang="ko"><meta charset="utf-8"><title>SPOON 실제 인증</title><h1>SPOON 실제 인증</h1>'+body+'</html>',status_code=status,headers=REAL_HEADERS)


def rejected():
    return JSONResponse({'error':'auth_request_rejected'},status_code=400,headers=SAFE_HEADERS)


def local_redirect(path):
    return RedirectResponse(LOCAL_ORIGIN+path,status_code=303,headers=SAFE_HEADERS)


def real_auth_router(auth: RealAuthService, stream=None):
    router=APIRouter(prefix='/auth/spoon',include_in_schema=False)

    @router.get('/prepare')
    async def prepare(request: Request):
        session=auth.prepare(request.cookies.get(REAL_COOKIE))
        active=auth.store.pair is not None
        action='disconnect' if active else 'start'
        label='로그아웃 및 토큰 해제' if active else '실제 SPOON 로그인'
        body='<p>버튼을 누르면 공식 SPOON 동의 화면으로 이동합니다. DJ 계정으로 동의하세요. 후원 수집은 별도 수집 관리에서 명시적으로 시작해야 합니다.</p><p>PKCE/public client 조건은 공식 문서에서 미확인입니다. 토큰은 이 프로세스 메모리에만 보관합니다.</p>'
        if stream is None:
            body = body.replace('후원 수집은 별도 수집 관리에서 명시적으로 시작해야 합니다.', '실제 후원 수신은 비활성입니다.')
        if auth.state=='revoke_unconfirmed':
            body+='<p>로컬 토큰은 삭제했지만 서버측 폐기는 미확인입니다. SPOON 측 연동 관리에서 확인하세요.</p>'
        body+=f'<form method="post" action="/auth/spoon/{action}"><input type="hidden" name="csrf" value="{escape(session.csrf,quote=True)}"><button>{label}</button></form><a href="/">대시보드</a>'
        response=real_page(body)
        response.set_cookie(REAL_COOKIE,session.identifier,max_age=300,httponly=True,samesite='lax',path='/auth/spoon')
        return response

    @router.post('/start')
    async def start(request: Request):
        if await post_values(request,auth,REAL_COOKIE) is None:
            return rejected()
        try:
            state=await auth.begin(request.cookies.get(REAL_COOKIE))
            destination=auth.client.authorization_url(state)
        except OAuthFailure:
            return rejected()
        # The only external redirect is this fixed official authorization endpoint.
        return RedirectResponse(destination,status_code=303,headers=SAFE_HEADERS)

    @router.get('/callback')
    async def callback(request: Request):
        values=request.scope.get('oauth_query',{})
        if (set(values)-{'code','state','error'} or any(len(value)!=1 for value in values.values()) or bool(single(values,'code'))==bool(single(values,'error'))):
            return rejected()
        success=await auth.complete(request.cookies.get(REAL_COOKIE),single(values,'state'),single(values,'code'),single(values,'error'))
        response=local_redirect('/auth/spoon/result')
        if not success and not auth.valid_session(request.cookies.get(REAL_COOKIE)):
            response.delete_cookie(REAL_COOKIE,path='/auth/spoon')
        return response

    @router.get('/result')
    async def result():
        labels={
            'authenticated':'인증 성공. 실제 SSE·후원 수집은 아직 비활성입니다.',
            'reauth_required':'인증에 실패했거나 만료되었습니다. 새 동의 흐름으로 다시 시작하세요.',
            'revoke_unconfirmed':'로컬 인증정보는 삭제했지만 서버측 폐기를 확인하지 못했습니다.',
            'configuration_error':'앱 승인·지역·Client 설정·리디렉션 등록을 확인한 후 서버를 다시 시작하세요.',
            'real_ready':'인증되지 않았습니다. 준비 화면에서 수동으로 시작하세요.',
        }
        if stream:
            labels['authenticated'] = '인증 성공. 수집 관리에서 DJ 고지 확인 후 직접 시작하세요.'
        return real_page('<p>'+labels.get(auth.state,'인증 상태 확인이 필요합니다.')+'</p><a href="/auth/spoon/prepare">인증 관리</a> · <a href="/">대시보드</a>')

    @router.post('/disconnect')
    async def disconnect(request: Request):
        if await post_values(request,auth,REAL_COOKIE) is None:
            return rejected()
        if stream:
            await stream.stop("auth_disconnected")
        confirmed=await auth.disconnect()
        response=local_redirect('/auth/spoon/result')
        response.delete_cookie(REAL_COOKIE,path='/auth/spoon')
        if not confirmed:
            response.headers['X-Auth-Revocation']='unconfirmed'
        return response

    if stream:
        @router.get('/collection')
        async def collection(request: Request):
            session = auth.prepare(request.cookies.get(REAL_COOKIE))
            csrf = escape(session.csrf, quote=True)
            body = '<p>실제 방송에 봇 계정의 청취자 고지가 표시됩니다. DJ에게 수집 사실을 먼저 고지하세요. 관측된 금액만 저장하며 누락 복원은 보장하지 않습니다.</p>'
            body += f'<form method="post" action="/auth/spoon/collection/start"><input type="hidden" name="csrf" value="{csrf}"><label><input type="checkbox" name="dj_notice" value="confirmed" required>DJ에게 수집 사실을 고지했고 연결 시 봇 고지가 표시됨을 이해했습니다.</label><button>실제 후원 수집 시작</button></form>'
            body += f'<form method="post" action="/auth/spoon/collection/stop"><input type="hidden" name="csrf" value="{csrf}"><button>수집 중지</button></form><a href="/">대시보드</a>'
            response = real_page(body)
            response.set_cookie(REAL_COOKIE, session.identifier, max_age=300, httponly=True, samesite='lax', path='/auth/spoon')
            return response

        @router.post('/collection/start')
        async def collection_start(request: Request):
            values = await post_values(request, auth, REAL_COOKIE)
            if values is None or single(values, 'dj_notice') != 'confirmed':
                return rejected()
            if not await stream.start():
                return JSONResponse({'error': 'collection_not_started', 'state': stream.state.lower()}, status_code=409, headers=SAFE_HEADERS)
            return local_redirect('/')

        @router.post('/collection/stop')
        async def collection_stop(request: Request):
            if await post_values(request, auth, REAL_COOKIE) is None:
                return rejected()
            await stream.stop()
            return local_redirect('/')

    return router
