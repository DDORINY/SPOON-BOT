"""Explicit, short-lived local-history authorization; never DJ identity proof."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import sqlite3
from spoon_local.spoon.donation_history import search
from spoon_local.web.auth_routes import SAFE_HEADERS, post_values, single
from spoon_local.web.real_auth_routes import REAL_COOKIE, dashboard_session_bound
from spoon_local.spoon.auth_service import RealAuthService


def history_router(auth, stream):
    router=APIRouter()
    permit=None

    async def logged_in(request):
        return isinstance(auth,RealAuthService) and dashboard_session_bound(auth,request.cookies.get(REAL_COOKIE)) and await auth.ensure_access()

    def response(body,status=200):return JSONResponse(body,status_code=status,headers=SAFE_HEADERS)

    @router.post('/auth/spoon/history/allow')
    async def allow(request:Request):
        nonlocal permit
        if not await logged_in(request):return response({'error':'login_required'},401)
        values=await post_values(request,auth,REAL_COOKIE)
        if values is None or single(values,'local_owner')!='confirmed':return response({'error':'history_confirmation_required'},400)
        if not stream or stream.mode!='real':return response({'error':'history_not_configured'},409)
        permit=(auth.session,auth.clock()+900)
        return response({'access':'allowed','expires_in':900})

    def permitted():
        return bool(permit is not None and permit[0] is auth.session and auth.clock()<permit[1])

    @router.get('/api/donation-history')
    async def history(request:Request):
        if not await logged_in(request):return response({'error':'login_required'},401)
        if not permitted():
            return response({'error':'local_history_confirmation_required'},403)
        if not stream or stream.mode!='real':return response({'error':'history_not_configured'},409)
        try:
            query=request.query_params
            if set(query)-{'period','start','end','page','page_size','snapshot'} or any(len(query.getlist(k))!=1 for k in query):raise ValueError()
            data=search(stream.repository,period=query.get('period','all'),start=query.get('start'),end=query.get('end'),page=int(query.get('page','1')),page_size=int(query.get('page_size','12')),snapshot=int(query['snapshot']) if 'snapshot' in query else None)
        except (ValueError,OverflowError):return response({'error':'invalid_history_filter'},400)
        except (sqlite3.Error,OSError):return response({'error':'history_unavailable'},503)
        return response(data)
    return router,permitted
