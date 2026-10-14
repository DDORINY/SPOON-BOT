"""Local dashboard and API; configuration is loaded only on app startup."""

from contextlib import asynccontextmanager
from pathlib import Path
from html import escape
import re

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
import sqlite3
from collections.abc import Callable
from fastapi.staticfiles import StaticFiles

from spoon_local.config import Settings, load_settings
from spoon_local.database import initialize_database
from spoon_local.logging_config import configure_safe_access_logs
from spoon_local.spoon.auth_service import AuthService, RealAuthService
from spoon_local.web.chat_routes import chat_router
from spoon_local.spoon.chat_send import ChatSendService
from spoon_local.web.history_routes import history_router
from spoon_local.web.auth_routes import auth_router
from spoon_local.web.auth_security import AuthSecurityMiddleware
from spoon_local.spoon.donation_repository import DonationRepository
from spoon_local.spoon.stream_service import MockStreamService, RealStreamService
from spoon_local.spoon.donation_repository import RealDonationRepository
from spoon_local.spoon.real_sse import RealSSESource
from spoon_local.spoon.audience import AudienceService
from spoon_local.spoon.live_read import LiveReadClient
from spoon_local.spoon.sse_source import MockSSESource, demo_source
from spoon_local.spoon.oauth import OAuthSettings, MockOAuthClient
from spoon_local.spoon.real_oauth import RealOAuthClient, load_real_oauth_settings
from spoon_local.web.real_auth_routes import real_auth_router, dashboard_session_bound, REAL_COOKIE

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def create_app(
    database_path: Path | None = None, *, settings: Settings | None = None,
    auth_service: AuthService | RealAuthService | None = None,
    stream_service: MockStreamService | RealStreamService | None = None,
    live_client: LiveReadClient | None = None
) -> FastAPI:
    if database_path is not None and settings is not None:
        raise ValueError("Provide either database_path or settings")

    if isinstance(auth_service,RealAuthService) and (settings is not None or database_path is not None or (stream_service is not None and not isinstance(stream_service, RealStreamService))):
        raise ValueError("Real auth-only mode cannot use a database or mock stream")

    if stream_service and (stream_service.auth is not auth_service or settings is not None or database_path is not None):
        raise ValueError("Mock SSE app must use only its dedicated repository")

    if live_client is not None and live_client.auth is not auth_service:
        raise ValueError("Live client must share the real authentication service")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if isinstance(auth_service,RealAuthService):
            app.state.database_path = None
        elif stream_service:
            stream_service.repository.initialize()
            app.state.database_path = stream_service.repository.path
        else:
            configuration = Settings(database_path) if database_path is not None else settings
            if configuration is None:
                configuration = load_settings()
            initialize_database(configuration.database_path)
            app.state.database_path = configuration.database_path
        configure_safe_access_logs()
        try:
            yield
        finally:
            if chat_sender:
                chat_sender.reset(None)
            if audience:
                audience.reset(None)
            if live_client:
                await live_client.aclose()
            if stream_service:
                await stream_service.close()
            if auth_service:
                auth_service.close()
                if isinstance(auth_service,RealAuthService):
                    await auth_service.client.aclose()

    app = FastAPI(title="DOHA-BOT", lifespan=lifespan)
    app.add_middleware(AuthSecurityMiddleware, protect_dashboard=isinstance(auth_service, RealAuthService))
    app.include_router(real_auth_router(auth_service, stream_service) if isinstance(auth_service,RealAuthService) else auth_router(auth_service, stream_service))
    history_routes, history_permitted = history_router(auth_service, stream_service)
    app.include_router(history_routes)
    chat_sender=ChatSendService(live_client) if live_client is not None else None
    app.include_router(chat_router(auth_service,chat_sender))
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    recorded_observation = None
    audience = AudienceService(live_client) if live_client is not None else None

    async def dashboard_authorized(request):
        if not isinstance(auth_service, RealAuthService):
            return False
        return bool(dashboard_session_bound(auth_service, request.cookies.get(REAL_COOKIE)) and await auth_service.ensure_access())

    def studio_html(path, controls=''):
        page = {'/': 'home', '/broadcast': 'broadcast', '/capture': 'capture', '/gallery': 'gallery', '/donations': 'donations', '/settings': 'settings'}[path]
        content = (STATIC_DIR / 'index.html').read_text().replace('<!--broadcast-controls-->', controls)
        titles = {'home': '오늘도 즐거운 방송을', 'broadcast': '내 방송 화면', 'capture': '소중한 순간을 담아요', 'gallery': '캡처 보관함', 'donations': '방송에 도착한 응원', 'settings': '내 스튜디오 설정'}
        content = content.replace('<h1 id="page-title">오늘도 즐거운 방송을</h1>', f'<h1 id="page-title">{titles[page]}</h1>')
        def visibility(match):
            tag = re.sub(r'\s+hidden\b', '', match[1])
            return tag + ('' if page in match[2].split() else ' hidden') + '>'
        return re.sub(r'(<(?:section|div)\b[^>]*\bdata-view="([^"]+)"[^>]*)(>)', visibility, content)

    @app.get("/", include_in_schema=False)
    @app.get("/donations", include_in_schema=False)
    @app.get("/settings", include_in_schema=False)
    @app.get("/broadcast", include_in_schema=False)
    @app.get("/capture", include_in_schema=False)
    @app.get("/gallery", include_in_schema=False)
    async def dashboard(request: Request):
        if isinstance(auth_service, RealAuthService) and not await dashboard_authorized(request):
            return RedirectResponse('/auth/spoon/prepare', status_code=303, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
        headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}
        if isinstance(auth_service, RealAuthService):
            session = auth_service.prepare(request.cookies.get(REAL_COOKIE))
            controls = ''
            if stream_service:
                active = stream_service.task and not stream_service.task.done()
                csrf = escape(session.csrf, quote=True)
                start_hidden = ' hidden' if active else ''
                stop_hidden = '' if active else ' hidden'
                controls = (f'<form id="helper-start" method="post" action="/auth/spoon/collection/start"{start_hidden}><input type="hidden" name="csrf" value="{csrf}"><label><input type="checkbox" name="dj_notice" value="confirmed" required>방송 DJ에게 연결을 알렸고 SPOON 방송에 도우미 입장 안내가 표시됨을 이해했어요.</label><button class="primary-button" type="submit">방송 도우미 연결</button></form>'
                            f'<form id="helper-stop" method="post" action="/auth/spoon/collection/stop"{stop_hidden}><input type="hidden" name="csrf" value="{csrf}"><button class="primary-button" type="submit">방송 연결 해제하기</button></form>')
            content = studio_html(request.url.path, controls)
            history_form = (f'<form id="history-access" method="post" action="/auth/spoon/history/allow"><input type="hidden" name="csrf" value="{escape(session.csrf,quote=True)}"><label><input type="checkbox" name="local_owner" value="confirmed" required>이 PC의 로컬 후원 기록을 조회할 권한이 있으며, 계정별로 분리되지 않은 기록임을 확인했어요.</label><button type="submit" class="primary-button">로컬 기록 조회 허용</button></form>')
            content = content.replace('<!--history-access-->',history_form)
            content=content.replace('<!--chat-csrf-->',f'<input type="hidden" name="csrf" value="{escape(session.csrf,quote=True)}">')
            response = HTMLResponse(content, headers=headers)
            response.set_cookie(REAL_COOKIE, session.identifier, httponly=True, samesite='lax', path='/')
            return response
        return HTMLResponse(studio_html(request.url.path), headers=headers)

    @app.get("/api/helper")
    async def helper_info(request: Request):
        from fastapi.responses import JSONResponse
        headers = {'Cache-Control':'no-store'}
        if not await dashboard_authorized(request):
            if isinstance(stream_service, RealStreamService) and auth_service.state != 'authenticated' and stream_service.task and not stream_service.task.done():
                await stream_service.stop('auth_disconnected')
            return JSONResponse({'state':'error','reason':'reauth_required','available':False,'active':False}, status_code=401, headers=headers)
        if not isinstance(stream_service, RealStreamService):
            return JSONResponse({'state':'disconnected','reason':'not_enabled','available':False,'active':False,'last_donation_at':None},headers=headers)
        raw = stream_service.state
        active = bool(stream_service.task and not stream_service.task.done())
        state = {'STOPPED':'disconnected','WAITING':'waiting_for_live','CONNECTING':'connecting','LIVE':'connected','BACKOFF':'reconnecting','REFRESHING':'reconnecting','RATE_LIMITED':'reconnecting'}.get(raw,'error')
        permission = await auth_service.authorized_for({'events.donation'})
        available = permission and raw not in {'PERMISSION_REQUIRED', 'BLOCKED', 'AUTH_REQUIRED'}
        return JSONResponse({'state':state,'reason':raw.lower() if permission else 'permission_required','available':available,'active':active,'last_donation_at':stream_service.last_donation_at},headers=headers)

    async def audience_info(request,kind):
        from fastapi.responses import JSONResponse
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
        if not await dashboard_authorized(request):
            if audience and (auth_service.state!='authenticated' or auth_service.store.pair is None):audience.reset(None)
            return JSONResponse({'state':'reauth','items':[]},status_code=401,headers=headers)
        if audience is None:
            return JSONResponse({'state':'not_configured','items':[]},headers=headers)
        query=request.query_params
        try:
            if set(query)-{'cursor','live_id'} or any(len(query.getlist(key))!=1 for key in query):raise ValueError()
            live_id=int(query['live_id']) if 'live_id' in query else None
            if live_id is not None and not 0<=live_id<=2**53-1:raise ValueError()
            data=await audience.get(kind,owner=auth_service.session,cursor=query.get('cursor'),expected_live_id=live_id)
        except (ValueError,TypeError):
            return JSONResponse({'state':'invalid_request','items':[]},status_code=400,headers=headers)
        return JSONResponse(data,status_code=400 if data['state']=='invalid_cursor' else 200,headers=headers)

    @app.get('/api/live/listeners')
    async def live_listeners(request:Request):
        return await audience_info(request,'listeners')

    @app.get('/api/live/fans')
    async def live_fans(request:Request):
        return await audience_info(request,'fans')

    @app.get('/api/realtime-events')
    async def realtime_events(request: Request):
        from fastapi.responses import JSONResponse
        headers={'Cache-Control':'no-store'}
        if not await dashboard_authorized(request):
            if isinstance(stream_service,RealStreamService) and (auth_service.state!='authenticated' or auth_service.store.pair is None):
                if stream_service.task and not stream_service.task.done():
                    await stream_service.stop('auth_disconnected')
                else:
                    stream_service.realtime.reset()
            return JSONResponse({'error':'login_required'},status_code=401,headers=headers)
        scopes=auth_service.store.pair.scopes
        if not isinstance(stream_service,RealStreamService):
            return JSONResponse({'configured':False,'state':'disconnected','permissions':{'chat':'events.chat' in scopes,'like':'events.like' in scopes},'chats':[],'hearts':{'observed_amount':'0','event_count':0,'last_received_at_utc':None}},headers=headers)
        owner=stream_service.collection_browser_session
        if owner is not None and owner is not auth_service.session:
            await stream_service.stop('browser_session_changed')
            return JSONResponse({'error':'collection_owner_changed'},status_code=403,headers=headers)
        active=bool(stream_service.task and not stream_service.task.done())
        state={'STOPPED':'disconnected','WAITING':'waiting_for_live','CONNECTING':'connecting','LIVE':'connected','BACKOFF':'reconnecting','REFRESHING':'reconnecting','RATE_LIMITED':'reconnecting'}.get(stream_service.state,'error')
        data=stream_service.realtime.snapshot(scopes)
        return JSONResponse({**data,'configured':True,'state':state,'active':active},headers=headers)

    @app.get("/api/live")
    async def live_info(request: Request):
        nonlocal recorded_observation
        from fastapi.responses import JSONResponse
        if not await dashboard_authorized(request):
            return JSONResponse({'state':'reauth_required','live':None,'retryable':False},status_code=401,headers={'Cache-Control':'no-store'})
        if live_client is None:
            return JSONResponse({'state':'not_configured','live':None,'retryable':False},headers={'Cache-Control':'no-store'})
        observation_account = getattr(auth_service, 'account_context', None)
        data = await live_client.get()
        if stream_service and observation_account and observation_account == getattr(auth_service, 'account_context', None):
            from spoon_local.spoon.broadcast_rounds import RoundRepository
            try:
                rounds = RoundRepository(stream_service.repository)
                signature = (auth_service.account_context,live_client.observation_sequence)
                if rounds.ready() and signature != recorded_observation:
                    if data['state'] == 'live':
                        await stream_service._write('observe_round', auth_service.account_context, data['live']['live_id'], data['live']['started_at_utc'],live_client.observed_at)
                    elif data['state'] in {'waiting','error','permission_required','reauth_required','rate_limited'}:
                        await stream_service._write('uncertain_rounds',auth_service.account_context)
                    recorded_observation = signature
            except (sqlite3.Error, ValueError, OSError):
                data = {**data, 'round_recording':'unavailable'}
        return JSONResponse(data,headers={'Cache-Control':'no-store'})

    async def round_repository(request):
        if not await dashboard_authorized(request):
            raise HTTPException(status_code=401, detail='login_required')
        if not stream_service:
            raise HTTPException(status_code=409, detail='round_storage_not_configured')
        if not getattr(auth_service, 'account_context', None):
            raise HTTPException(status_code=409, detail='dj_identity_unverified')
        from spoon_local.spoon.broadcast_rounds import RoundRepository
        repository = RoundRepository(stream_service.repository)
        return repository, auth_service.account_context

    @app.get('/api/broadcast-sessions')
    async def round_list(request: Request, limit: int = Query(20,ge=1,le=100), before: int | None = Query(None,ge=1)):
        repository, key = await round_repository(request)
        try:
            return repository.rounds(key,limit,before) if repository.ready() else {'items':[], 'next_before':None, 'migration_required':True}
        except (sqlite3.Error, ValueError, OSError):
            raise HTTPException(status_code=503,detail='round_storage_unavailable') from None

    @app.get('/api/broadcast-sessions/{round_id}')
    async def round_detail(request: Request, round_id: str):
        repository,key=await round_repository(request)
        try:
            if not repository.ready(): raise HTTPException(status_code=409,detail='round_migration_required')
            value=repository.detail(key,round_id)
            if value is None: raise HTTPException(status_code=404,detail='round_not_found')
            return value
        except (sqlite3.Error, ValueError, OSError):
            raise HTTPException(status_code=503,detail='round_storage_unavailable') from None

    @app.get('/api/session-donations')
    async def round_donations(request: Request, round_id: str | None = None, unconfirmed: bool = False, limit: int = Query(20,ge=1,le=100), before: int | None = Query(None,ge=1), date_from: str | None = None, date_to: str | None = None):
        repository,key=await round_repository(request)
        try:
            if not repository.ready(): raise HTTPException(status_code=409,detail='round_migration_required')
            return repository.donations(key,round_id,unconfirmed,limit,before,date_from,date_to)
        except ValueError:
            raise HTTPException(status_code=422,detail='invalid_round_filter') from None
        except (sqlite3.Error, OSError):
            raise HTTPException(status_code=503,detail='round_storage_unavailable') from None

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    async def status() -> dict[str, str]:
        auth_state = await auth_service.status() if auth_service else "not_configured"
        if isinstance(stream_service, RealStreamService) and auth_state != "authenticated" and stream_service.task and not stream_service.task.done():
            await stream_service.stop("auth_disconnected")
        return {
            "server": "online",
            "spoon_auth": auth_state,
            "broadcast": ("real_live" if stream_service.mode == "real" else "mock_live") if stream_service and stream_service.state == "LIVE" else "disconnected",
            "donation_events": stream_service.state.lower() if stream_service else "not_configured",
            "capture_agent": "not_configured",
        }

    @app.get("/api/mode")
    async def mode():
        return {"auth_mode": auth_service.mode if auth_service else "disabled"}

    @app.get("/api/collection-mode")
    async def collection_mode():
        return {"collection_mode": stream_service.mode if stream_service else "disabled"}

    @app.get("/api/donations")
    async def donations(request: Request, limit: int = Query(20, ge=1, le=100), before_sequence: int | None = Query(None, ge=1)):
        if isinstance(auth_service, RealAuthService) and not await dashboard_authorized(request):
            raise HTTPException(status_code=401, detail="login_required", headers={"Cache-Control": "no-store"})
        if not stream_service:
            return {"source_mode": "disabled", "items": [], "coverage": "observed_only", "observed_total_spoons": "0", "issues": {"conflicts": 0, "gaps": 0, "idless": 0}}
        try:
            if stream_service.mode == "real" and not stream_service.repository.path.exists():
                return {"source_mode": "real", "items": [], "coverage": "observed_only", "observed_total_spoons": "0", "issues": {"conflicts": 0, "gaps": 0, "idless": 0}, "collection_state": stream_service.state.lower()}
            from spoon_local.spoon.broadcast_rounds import RoundRepository
            scoped = RoundRepository(stream_service.repository) if stream_service.mode == 'real' else None
            if scoped and not scoped.ready() and not history_permitted():
                raise HTTPException(status_code=403,detail='local_history_confirmation_required',headers={'Cache-Control':'no-store'})
            if scoped and scoped.ready() and not getattr(auth_service,'account_context',None) and not history_permitted():
                return {'source_mode':'real','items':[],'coverage':'observed_only','observed_total_spoons':'0','issues':{'conflicts':0,'gaps':0,'idless':0},'issues_scope':'unavailable','identity_status':'unverified','collection_state':stream_service.state.lower()}
            if scoped and scoped.ready() and getattr(auth_service,'account_context',None):
                from decimal import Decimal, localcontext
                result = scoped.donations(auth_service.account_context,limit=limit,before=before_sequence)
                with stream_service.repository.connection() as db:
                    amounts = db.execute('SELECT amount_spoons_text FROM donations WHERE account_key=?',(auth_service.account_context,)).fetchall()
                    conflicts = db.execute('SELECT COUNT(*) FROM event_conflicts WHERE account_key=?',(auth_service.account_context,)).fetchone()[0]
                    gaps = db.execute('SELECT COUNT(*) FROM collection_gaps WHERE account_key=?',(auth_service.account_context,)).fetchone()[0]
                    idless = db.execute("SELECT COUNT(*) FROM donations WHERE account_key=? AND dedup_quality='unavailable'",(auth_service.account_context,)).fetchone()[0]
                with localcontext() as decimal_context:
                    decimal_context.prec=80
                    total=sum((Decimal(row[0]) for row in amounts),Decimal(0))
                result.update(source_mode='real',observed_total_spoons=format(total,'f'),next_before_sequence=result.pop('next_before'),issues={'conflicts':conflicts,'gaps':gaps,'idless':idless},issues_scope='current_login',legacy_owner_unverified=True)
            else:
                result = stream_service.repository.recent(limit, before_sequence)
            result["collection_state"] = stream_service.state.lower()
            result["gap_persistence_failed"] = stream_service.gap_persistence_failed
            return result
        except (sqlite3.Error, ValueError, OSError):
            raise HTTPException(status_code=503, detail="real_storage_unavailable" if stream_service.mode == "real" else "mock_storage_unavailable") from None

    return app


def create_mock_app(mock_db: Path, source_factory: Callable[[], MockSSESource] = demo_source) -> FastAPI:
    """No dotenv loading, operational DB initialization, or real credentials."""
    repository = DonationRepository(mock_db)
    auth = AuthService(OAuthSettings.mock(), MockOAuthClient())
    stream = MockStreamService(auth, repository, source_factory)
    return create_app(auth_service=auth, stream_service=stream)


def create_real_app(oauth_settings: OAuthSettings | None = None, *, project_root: Path | None = None, transport=None, real_sse_db: Path | None = None, sse_transport=None, live_transport=None) -> FastAPI:
    """Explicit Real factory: loads credentials, but never starts login or HTTP."""
    configuration = oauth_settings if oauth_settings is not None else (load_real_oauth_settings(project_root) if project_root is not None else load_real_oauth_settings())
    client = RealOAuthClient(configuration,enabled=True,transport=transport)
    auth = RealAuthService(configuration,client)
    stream = None
    if real_sse_db is not None:
        repository = RealDonationRepository(real_sse_db)
        stream = RealStreamService(auth, repository, lambda: RealSSESource(auth, transport=sse_transport))
    return create_app(auth_service=auth, stream_service=stream, live_client=LiveReadClient(auth, transport=live_transport))


app = create_app()
