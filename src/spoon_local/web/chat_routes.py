"""JSON adapter to existing strict Origin/session/CSRF validation."""
import json
from urllib.parse import urlencode
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from spoon_local.web.auth_routes import post_values,SAFE_HEADERS
from spoon_local.web.real_auth_routes import REAL_COOKIE,dashboard_session_bound
from spoon_local.spoon.auth_service import RealAuthService


def chat_router(auth,service):
    router=APIRouter()
    def reply(data,status=200):return JSONResponse(data,status_code=status,headers=SAFE_HEADERS)
    async def authorized(request):return isinstance(auth,RealAuthService) and dashboard_session_bound(auth,request.cookies.get(REAL_COOKIE)) and await auth.ensure_access()
    @router.get('/api/live/chat/prepare')
    async def prepare(request:Request):
        if not await authorized(request):return reply({'state':'reauth'},401)
        return reply(await service.prepare(auth.session) if service else {'state':'not_configured'})
    @router.post('/api/live/chat')
    async def send(request:Request):
        if not await authorized(request):return reply({'state':'reauth'},401)
        if auth.clock()>=auth.session.deadline:return reply({'state':'session_expired'},400)
        try:
            if request.headers.get('content-type','').split(';')[0]!='application/json':raise ValueError()
            body=bytearray()
            async for chunk in request.stream():
                if len(body)+len(chunk)>4096:raise ValueError()
                body.extend(chunk)
            def unique(pairs):
                result={}
                for key,value in pairs:
                    if key in result:raise ValueError()
                    result[key]=value
                return result
            values=json.loads(body,object_pairs_hook=unique)
            if not isinstance(values,dict) or set(values)!={'csrf','message','request_id','notice'} or any(not isinstance(value,str) for value in values.values()) or len(values['csrf'])>256 or len(values['request_id'])>256 or values['notice']!='confirmed':raise ValueError()
            guarded=dict(request.scope);guarded['headers']=[(key,value) for key,value in request.scope['headers'] if key.lower()!=b'content-type']+[(b'content-type',b'application/x-www-form-urlencoded')]
            async def receive():return {'type':'http.request','body':urlencode({'csrf':values['csrf']}).encode(),'more_body':False}
            if await post_values(Request(guarded,receive),auth,REAL_COOKIE) is None:raise ValueError()
            result=await service.send(auth.session,values['message'],values['request_id']) if service else {'state':'not_configured'}
        except (ValueError,TypeError,UnicodeError,RecursionError):return reply({'state':'invalid_request'},400)
        return reply(result)
    return router
