"""Local-only browser routes. All redirects remain on a fixed localhost origin."""

from html import escape
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from spoon_local.spoon.auth_service import AuthService
from spoon_local.spoon.oauth import LOCAL_ORIGIN, OAuthFailure
from spoon_local.spoon.stream_service import MockStreamService

COOKIE = "spoon_mock_session"
SAFE_HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
}


def page(body: str, status: int = 200):
    return HTMLResponse(
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>로컬 Mock 인증</title>'
        '<body><h1>로컬 Mock 인증</h1>' + body + '</body></html>',
        status_code=status, headers=SAFE_HEADERS,
    )


def failure():
    return JSONResponse({"error": "auth_request_rejected"}, status_code=400, headers=SAFE_HEADERS)


def redirect(path: str):
    return RedirectResponse(LOCAL_ORIGIN + path, status_code=303, headers=SAFE_HEADERS)


def single(values: dict, name: str):
    found = values.get(name, [])
    return found[0] if len(found) == 1 else None


async def post_values(request: Request, auth: AuthService | None, cookie_name: str = COOKIE):
    if (
        not auth or request.headers.getlist("origin") != [LOCAL_ORIGIN]
        or request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded"
    ):
        return None
    # Bound the body while streaming, without requiring python-multipart.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4096:
            return None
    try:
        values = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=8)
    except (ValueError, UnicodeError):
        return None
    sid = request.cookies.get(cookie_name)
    if not auth.valid_csrf(sid, single(values, "csrf")):
        return None
    return values


def auth_router(auth: AuthService | None, stream: MockStreamService | None = None) -> APIRouter:
    router = APIRouter(prefix="/auth/spoon", include_in_schema=False)

    @router.get("/prepare")
    async def prepare(request: Request):
        if not auth:
            return page('<p>Mock 인증이 비활성화되어 있습니다. 실제 SPOON 로그인은 제공되지 않습니다.</p><a href="/">대시보드</a>')
        session = auth.prepare(request.cookies.get(COOKIE))
        active = auth.store.pair is not None
        path = "/disconnect" if active else "/start"
        label = "로컬 Mock 인증 해제" if active else "Mock 인증 시작"
        body = (
            '<p>외부 접속 없이 인증 흐름을 시험합니다. 실제 SPOON 계정·방송과 연결되지 않습니다.</p>'
            f'<form method="post" action="/auth/spoon{path}">'
            f'<input type="hidden" name="csrf" value="{escape(session.csrf, quote=True)}">'
            f'<button type="submit">{label}</button></form><a href="/">대시보드</a>'
        )
        if stream:
            body += '<p><a href="/auth/spoon/collection">Mock 후원 수집 관리</a></p>'
        if auth.state == "revoke_unconfirmed":
            body += '<p>폐기 미확인 상태입니다. 해제를 다시 시도하세요.</p>'
        response = page(body)
        response.set_cookie(COOKIE, session.identifier, max_age=300, httponly=True, samesite="lax", path="/auth/spoon")
        return response

    @router.post("/start")
    async def start(request: Request):
        values = await post_values(request, auth)
        if values is None:
            return failure()
        try:
            state = await auth.begin(request.cookies.get(COOKIE))
        except OAuthFailure:
            return failure()
        return redirect("/auth/spoon/mock-consent?" + urlencode({"state": state}))

    @router.get("/mock-consent")
    async def consent(request: Request):
        values = request.scope.get("oauth_query", {})
        state = single(values, "state")
        if not auth or not auth.valid_pending(request.cookies.get(COOKIE), state):
            return failure()
        return page(
            '<p>실제 로그인이 아닌 로컬 동의 모의 화면입니다.</p>'
            + '<form method="post" action="/auth/spoon/mock-consent">'
            + f'<input type="hidden" name="csrf" value="{escape(auth.session.csrf, quote=True)}">'
            + f'<input type="hidden" name="state" value="{escape(state, quote=True)}">'
            + '<button name="decision" value="allow">Mock 동의</button>'
            + '<button name="decision" value="deny">취소</button></form>'
        )

    @router.post("/mock-consent")
    async def decide(request: Request):
        values = await post_values(request, auth)
        if values is None:
            return failure()
        state = single(values, "state")
        if not auth.valid_pending(request.cookies.get(COOKIE), state):
            return failure()
        decision = single(values, "decision")
        if decision not in {"allow", "deny"}:
            return failure()
        try:
            result = {"code": auth.client.issue_code()} if decision == "allow" else {"error": "access_denied"}
        except OAuthFailure:
            return failure()
        return redirect("/auth/spoon/callback?" + urlencode({**result, "state": state}))

    @router.get("/callback")
    async def callback(request: Request):
        values = request.scope.get("oauth_query", {})
        if (
            not auth or set(values) - {"code", "state", "error"}
            or any(len(v) != 1 for v in values.values())
            or bool(single(values, "code")) == bool(single(values, "error"))
        ):
            return failure()
        success = await auth.complete(
            request.cookies.get(COOKIE), single(values, "state"),
            single(values, "code"), single(values, "error"),
        )
        if not success:
            return failure()
        return redirect("/")

    @router.post("/disconnect")
    async def disconnect(request: Request):
        values = await post_values(request, auth)
        if values is None:
            return failure()
        if stream:
            await stream.stop("auth_disconnected")
        if not await auth.disconnect():
            return JSONResponse({"error": "revoke_unconfirmed"}, status_code=503, headers=SAFE_HEADERS)
        response = redirect("/")
        response.delete_cookie(COOKIE, path="/auth/spoon")
        return response

    @router.get("/collection")
    async def collection(request: Request):
        if not auth or not stream:
            return page('<p>Mock 후원 수집은 비활성입니다.</p><a href="/">대시보드</a>')
        session = auth.prepare(request.cookies.get(COOKIE))
        body = '<p>모의 후원만 별도 DB에 저장합니다. 실제 방송과 연결되지 않으며 누락 없는 원장이 아닙니다.</p>'
        if await auth.authorized_for({'events.donation'}):
            for action, label in [('start', 'Mock 수집 시작'), ('stop', 'Mock 수집 중지')]:
                body += f'<form method="post" action="/auth/spoon/collection/{action}"><input type="hidden" name="csrf" value="{escape(session.csrf, quote=True)}"><button>{label}</button></form>'
        else:
            body += '<p>Mock 인증이 필요합니다.</p><a href="/auth/spoon/prepare">인증 준비</a>'
        body += '<p><a href="/">대시보드</a></p>'
        response = page(body)
        response.set_cookie(COOKIE, session.identifier, max_age=300, httponly=True, samesite='lax', path='/auth/spoon')
        return response

    @router.post("/collection/start")
    async def start_collection(request: Request):
        if await post_values(request, auth) is None or not stream:
            return failure()
        if not await stream.start():
            return JSONResponse({'error': 'mock_collection_not_ready'}, status_code=409, headers=SAFE_HEADERS)
        return redirect('/')

    @router.post("/collection/stop")
    async def stop_collection(request: Request):
        if await post_values(request, auth) is None or not stream:
            return failure()
        await stream.stop()
        return redirect('/')

    return router
