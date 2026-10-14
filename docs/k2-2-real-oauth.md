# K2.2 — 명시적 Real OAuth 인증 모드

기준 main: `3fe68df3a95f14b759b5f51db4443470a0316c78`. 조사일: 2026-10-14 (Asia/Seoul).
DOHA bot 승인 및 로컬 .env 설정 완료는 사용자 확인 정보다. 이 작업에서는 실제 자격 증명을 읽어 검증하거나 SPOON 로그인·발급을 실행하지 않았다. 자동 테스트는 가상 설정과 Mock HTTP Transport만 사용했다.

## 공식 재확인 결과

| 항목 | 확인 내용 | 공식 출처 |
|---|---|---|
| 한국 Authorization | https://developers.spooncast.net/kr/oauth/authorize | [사용자 동의](https://developers.spooncast.net/docs/en/getting-started/user-authorization) |
| 한국 Token / Revoke | POST https://kr-openapi.spooncast.net/v1/oauth/token 및 /v1/oauth/revoke | [엔드포인트](https://developers.spooncast.net/docs/en/api-reference/endpoints), [토큰](https://developers.spooncast.net/docs/en/api-reference/tokens) |
| 요청 형식 | Token/Revoke는 form-urlencoded와 서버측 Basic(client_id:client_secret) | [토큰](https://developers.spooncast.net/docs/en/api-reference/tokens) |
| Code | 60초·1회용. 재사용은 연동 토큰 전체 폐기로 이어질 수 있음. 응답 유실 시 동일 코드 재시도 금지 | [토큰](https://developers.spooncast.net/docs/en/api-reference/tokens) |
| Redirect | 등록값과 authorize 값의 문자 단위 일치; 교환에도 동일값 사용 | [사용자 동의](https://developers.spooncast.net/docs/en/getting-started/user-authorization) |
| 개발 callback | http://localhost와 포트 허용; http://127.0.0.1 허용은 미확인 | [앱 등록](https://developers.spooncast.net/docs/en/getting-started/app-registration) |
| scope | 공백 구분, URL에는 %20. 등록된 권한만 요청 | [사용자 동의](https://developers.spooncast.net/docs/en/getting-started/user-authorization) |
| Access / Refresh | 기본1시간/30일; 실제 만료는 expires_in. Refresh 성공 시 이전 refresh 즉시 폐기, 응답 scope 재확인 | [토큰](https://developers.spooncast.net/docs/en/api-reference/tokens) |
| 오류 | invalid_request/invalid_client/invalid_grant/unsupported_grant_type. 설명 원문으로 분기하지 않음 | [토큰](https://developers.spooncast.net/docs/en/api-reference/tokens) |
| Revoke | refresh 폐기는 같은 연동 access도 폐기. 동의 grant 철회와 별개 | [토큰](https://developers.spooncast.net/docs/en/api-reference/tokens) |

PKCE/code_verifier 및 public/confidential client 등록 조건은 읽은 공식 문서에 명시되지 않아 **미확인**이다. PKCE를 추측하여 활성화하지 않았으며 지원하지 않는다고 단정하지도 않는다. 개인 PC에 Secret을 보관한다는 사실만으로 공급자의 confidential client 조건을 충족했다고 주장하지 않는다. [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html#section-2.1.1)의 PKCE 보안 기준과의 차이도 남아 있다. 지원·클라이언트 분류를 공식 지원에 확인하는 것이 필요하다.

Windows의 Chrome/Edge에서 WSL Linux 서비스를 localhost로 여는 방식은 [Microsoft 공식 WSL 네트워크 안내](https://learn.microsoft.com/en-us/windows/wsl/networking#accessing-linux-networking-apps-from-windows-localhost)에 설명되어 있다. 이 PC의 Chrome→WSL→OAuth callback을 검증했다는 뜻은 아니다.

## 구현 구조

- spoon/real_oauth.py: Real 설정 로더·가드, Authorization URL, 교환/갱신/폐기 HTTP 클라이언트
- spoon/auth_service.py: 공통 state/세션 로직, 기존 AuthService(Mock), 별도 RealAuthService
- web/real_auth_routes.py: Real 전용 준비·시작·callback·결과·로그아웃; mock consent/수집 route 없음
- logging_config.py: 접근 query 제거와 httpx/httpcore transport 로그 내용 제거
- main.py: create_real_app, Real auth-only lifespan, /api/mode
- __main__.py: --real-oauth와 --mock-auth 상호 배타적 실행
- static/auth-mode.js 및 dashboard: Real에서만 실제 로그인/인증관리 링크 표시
- pyproject.toml: 기존 httpx를 dev에서 런타임 의존성으로 이동

기본 실행은 기존 설정/DB 동작을 유지하며 실제 OAuth는 비활성이다. Mock 실행은 기존 명시 DB·오프라인 경로를 유지하고 실제 .env를 읽지 않는다. Real 모드는 인증 전용으로 DB를 초기화하거나 Mock SSE를 생성하지 않는다. 인증 후에도 /api/donations는 disabled/빈 목록이며 실제 수집 버튼과 endpoint가 없다.

## 실행 명령

```bash
cd /home/doha/projects/spoon-local
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m spoon_local --real-oauth
```

명령 자체는 로그인이나 Token API를 호출하지 않는다. 필요한 네 설정을 프로젝트 루트 .env에서 읽으며 기존 OS 환경변수가 우선한다. 값은 로그·에러·repr로 출력하지 않는다. SPOON_CLIENT_ID, SPOON_CLIENT_SECRET, SPOON_REGION, SPOON_REDIRECT_URI 중 누락/잘못된 값은 서버 시작을 거절한다. 입력 파일은 수정하지 않으며 사용자 .env.example 변경도 보존했다.

지원 redirect는 정확히 `http://localhost:8000/auth/spoon/callback`이다. 앱 콘솔의 등록값과 일치해야 한다. 실제 .env 값은 이 문서에 기재하지 않았다. region은 kr/jp allowlist이다. Mock ID/Secret, mock-code/access/refresh prefix는 Real client에 전달할 수 없다. Real service는 Mock SSE에 주입할 수 없으며 서로 다른 쿠키 이름을 사용한다.

기본 및 Mock 명령은 유지된다.

```bash
.venv/bin/python -m spoon_local
.venv/bin/python -m spoon_local --mock-auth --mock-db /tmp/spoon-local-demo/mock-sse.sqlite3
```

## 실제 로그인 전 체크리스트

1. 공식 앱 승인과 별도 봇 계정 소유 앱임을 확인한다. 동의 화면에서는 방송 DJ 계정을 사용한다.
2. 앱에 live.read와 events.donation이 등록되어 있는지 확인한다. 이 두 권한은 기존 프로젝트 목표의 최소 조합이며 chat.send는 요청하지 않는다. 이번 단계에서 이벤트 연결을 수행하지는 않는다.
3. 지역 및 등록 callback이 위 고정 주소와 정확히 같은지 확인한다. localhost와127.0.0.1을 서로 바꿔 쓰거나 임의 포트로 우회하지 않는다.
4. 아래 localhost 도달 검증을 먼저 완료한다. 이미8000 포트를 사용하는 Mock/기본 서버는 Ctrl+C로 중지한다.
5. PKCE·클라이언트 분류 미확인과 HTTP localhost 쿠키 Secure 미설정이라는 개발용 한계를 이해한다. 지원 사양을 확인하기 전 이 구성을 배포형 인증으로 간주하지 않는다.
6. 토큰은 재시작 시 사라지고 종료만으로 원격 토큰이 폐기되지 않음을 확인한다. 정상 종료 전 명시적 로그아웃을 권장한다.

## Windows↔WSL 콜백 검증 — 사용자 수동 실행

실제 계정을 사용하지 않는 Mock 서버부터 실행한다.

```bash
.venv/bin/python -m spoon_local --mock-auth --mock-db /tmp/spoon-local-demo/mock-sse.sqlite3
```

Windows Chrome에서 `http://localhost:8000/api/health`의 ok를 확인하고 `http://localhost:8000/auth/spoon/prepare`에서 로컬 모의 동의까지 완료한다. callback 후 localhost 화면에 돌아오는지 확인한다. 필요하면 Windows PowerShell에서 아래 읽기 요청을 사용한다.

```powershell
Invoke-WebRequest http://localhost:8000/api/health
```

접속이 안 되면 WSL 실행 상태, 포트 충돌, Windows/WSL localhost 전달과 방화벽/VPN 상태를 점검한다. 이번 구현을 위해 0.0.0.0 바인딩, LAN 공개, 클라우드 터널을 사용하지 않는다. Microsoft의 일반 localhost 지원과 개별 PC의 실측을 구분한다. 이 검증도 이번 작업에서는 실제 Chrome으로 수행하지 않았다.

## 안전한 실제 로그인 수동 테스트

1. Mock 서버를 중지한 후 --real-oauth로 실행한다. health/mode/status 조회만으로 외부 요청은 발생하지 않는다.
2. `http://localhost:8000/`의 **실제 SPOON 로그인** 링크를 연다. 준비 화면에서 **실제 SPOON 로그인** POST 버튼을 사용자가 직접 누른다.
3. 주소가 공식 developers.spooncast.net의 등록 지역 authorize 화면인지 확인하고 DJ 계정으로 로그인·최소 권한에 동의한다. ID/비밀번호는 Spoon 화면에서만 입력한다.
4. 콜백 code/state를 복사·공유하지 않는다. 앱은 거래를 소비한 후 Token API를1회 호출하고 query 없는 결과 화면으로303 이동한다. 성공 시 authenticated, 실패 시 일반 안내와 재인증/설정 오류 상태를 확인한다.
5. 실패 응답의 code를 재사용하지 않는다. 네트워크 응답 유실, invalid_grant, schema/scope 실패는 새 동의 흐름으로 시작한다. invalid_client 등 설정 오류는 앱·지역·등록 URI·설정을 먼저 확인하고 서버를 다시 시작한다.
6. 성공 후 실제 후원/캡처는 여전히 비활성인지 확인한다. 대시보드 status 조회는 토큰 만료 여유 구간에 도달한 뒤 메모리의 가장 최근 refresh로 갱신할 수 있다. 실제 rotation 실측은 이번 자동 테스트에 포함하지 않았다.
7. 인증 관리에서 **로그아웃 및 토큰 해제**를 누른다. 성공은 real_ready, 실패는 revoke_unconfirmed로 표시하며 어느 경우든 로컬 토큰·세션은 삭제한다. 미확인 폐기를 성공으로 표시하지 않는다. 실패 후 로컬 token이 없으므로 앱은 동일 revoke를 재시도하지 않으며 Spoon 측 동의/연동 관리 또는 공식 지원으로 확인해야 한다(정확한 관리 화면 경로는 미확인).
8. 서버를 종료한다. 토큰/Secret/cookie/code가 포함된 DevTools 캡처, callback URL, HAR, 터미널 dump를 공유하지 않는다. 문제가 있으면 안전한 상태 코드·로컬 상태만 기록한다.

## 보안·토큰 정책

바인딩은127.0.0.1:8000/단일 워커다. 인증 route는 정확한 localhost:8000 Host, POST Origin, 세션·CSRF 및 중복 쿠키를 검증한다. nonce는256비트 난수이며 session/state는5분·일회용이다. 공급자의 code 생성시각은 앱이 알 수 없으므로60초 유효성은 공급자가 검증한다. 앱은 callback 즉시 교환하며 프로세스 내 code 해시를 다시 사용하지 않는다. 재시작 후에도 오래된 callback을 재활용하는 기능을 제공하지 않는다.

HTTP는 고정 HTTPS token/revoke만 사용하고 TLS 검증을 유지한다. 임의 URL 설정, response redirect 추종, 환경 proxy 사용, 자동 retry는 없다. connect5초/read10초, 전체15초 및 응답64KiB 상한을 적용한다. 401/403/429/5xx는 안전한 내부 category로 변환하며 설명 원문/본문을 그대로 전달하지 않는다. 교환·회전 응답의 token_type/수명/scope/새 refresh를 검증한다. transport가 받은 provider cookie도 지워 다음 요청에 보내지 않는다.

Access/Refresh는 메모리 슬롯에만 보관한다. actual expires_in의10%(최대5분) 여유 시 status 조회를 통해 갱신하며 lock·pair 비교로 동시 갱신을 한 번으로 제한한다. 갱신/발급 실패와 요청 취소는 토큰·세션을 정리하며 응답 유실 후 기존 값을 재사용하지 않는다. malformed/위조 callback이 유효한 기존 인증을 임의 해제하지는 않는다. revoke 실패는 원격 미확인과 로컬 삭제를 구분한다. 파일·SQLite·브라우저 저장소로 token을 쓰지 않는다.

쿠키는 HttpOnly/SameSite=Lax, auth 경로·localhost 호스트 전용이며 HTTP 개발 환경에서는 Secure=false다. Real 준비 화면의 CSP form-action은 self와 공식 developers.spooncast.net만 허용하여 로그인 POST의 공식 redirect를 막지 않도록 한다. 임의 redirect는 허용하지 않는다. Auth 응답은 no-store/no-referrer이고 callback query는 ASGI scope에서 제거한다. Uvicorn access query도 제거하며, Real 모드에서는 httpx/httpcore LogRecord의 메시지·args·exception/stack 내용을 일반 메시지로 바꿔 헤더/body 비밀정보가 출력되지 않도록 한다. 새 프록시·사용자 로깅 미들웨어·네트워크 추적기를 추가하면 별도 검증해야 한다. 메모리 덤프/로컬 악성 프로그램과 브라우저 히스토리까지 완전히 제거하는 보장은 없다.

## 검증 및 후속 조건

최종 결과는 **203 passed**, 경고 없음, pip check 정상이다. 자동 테스트는 실제 사용자 .env를 사용하지 않고 임시 .env·가상 UUID/Secret·httpx.MockTransport·소켓 차단으로 수행했다. 기존142개 회귀와 URL/scope, state/CSRF/callback, 만료·중복 code, 교환/rotation/revoke, timeout/status/schema, 동시 갱신, 로그/모드 분리를 검증했다. 실제 OAuth E2E는 수행하지 않았다.

실제 SSE는 아직 클라이언트·task·저장소를 활성화하지 않았다. 후속 단계는 사용자 수동 OAuth 성공과 rotation/revoke 확인, PKCE/public client 답변, 실제 토큰 안전 관리, SSE 고지·ID범위·동시연결·누락 정책 확인 후 별도로 요청해야 한다.
