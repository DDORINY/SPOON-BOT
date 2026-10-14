# K2.1 — 오프라인 OAuth 기반 구현

기준 main: `8f11bc9` (K2.0 병합 확인). 실제 SPOON 요청과 OAuth .env 로딩은 비활성화되어 있다. 예시와 테스트 자격 증명은 고정 mock 값이며 실제 앱 자격 증명이 아니다.

## 구조

- `spoon/oauth.py`: OAuthSettings 검증, OAuthClient 인터페이스, MockOAuthClient, 항상 거부하는 DisabledHttpOAuthClient
- `spoon/auth_service.py`: 단일 브라우저 세션, CSRF, 일회용 state·code 해시, 교환·갱신·해제 lock, 주입 monotonic 시계
- `spoon/token_store.py`: access/refresh 단일 메모리 슬롯, 응답·scope·만료 검증
- `web/auth_routes.py`: localhost:8000 준비 GET, 시작 POST, 모의 동의 GET/POST, callback GET, 해제 POST
- `web/auth_security.py`, `logging_config.py`: 인증 Host 제한, 중복 쿠키 거절, query 제거, Uvicorn access log query 필터
- `__main__.py`: loopback 전용 런처와 명시적 --mock-auth 옵션
- `main.py`, 정적 대시보드: 실제 service 상태 연결 및 mock/실제 인증 구분

## 실행과 상태

`.venv/bin/python -m spoon_local --mock-auth` 후 `http://localhost:8000/auth/spoon/prepare`에서 시작한다. 모의 동의 후 고정 localhost callback을 거쳐 대시보드로 돌아온다. 외부 URL로 이동하지 않는다. 기본 실행은 not_configured이며 실제 ID/Secret 환경변수가 있어도 사용하지 않는다.

status의 spoon_auth 값은 not_configured / mock_ready / mock_authenticated / reauth_required / revoke_unconfirmed이다. 방송·후원·캡처 상태는 미구현 값을 유지한다. mock_authenticated는 Spoon 권한이나 계정 연결을 입증하지 않는다.

## 안전 경계

세션/state는 5분, mock code는 60초·1회용이다. 시작·모의 동의·해제는 정확한 Host·Origin, 브라우저 세션, CSRF를 확인한다. callback 거래는 provider 호출 전에 소비한다. 교환 유실·중복은 재시도하지 않는다. 설정 repr와 token repr는 자격 증명을 제외한다. provider 예외는 일반 오류로 변환하며 raw body/header/query를 기록하지 않는다. Uvicorn에서 query 제거를 실제로 확인했다. 사용자 정의 로깅·프록시가 추가되면 다시 검증해야 한다.

토큰은 메모리에만 저장한다. 만료의 10%(최대 5분) 여유 구간에서 status 호출 시 갱신하며 동시 갱신은 한 번만 수행한다. 갱신 응답은 단일 슬롯 교체로 회전하고 실패하면 기존 토큰을 지워 재인증 상태로 전환한다. revoke 실패는 폐기 미확인으로 남기고 갱신을 멈춘 뒤 다시 해제할 수 있다. 성공 시 토큰/세션만 제거하며 DB는 유지한다. 종료 시 메모리를 정리한다.

단일 프로세스/브라우저 세션만 지원한다. 재시작 후 인증은 유지되지 않는다. 새 브라우저의 준비 요청은 기존 pending 세션을 무효화할 수 있다. 사용 code 해시는 최대 4096개까지 보관하고 이후 교환을 거부한다. 로컬 악성 프로그램, 메모리 덤프, 브라우저 히스토리까지 방어하는 것은 아니다. HTTP localhost 쿠키의 Secure 미설정은 개발 환경 한계다. PKCE는 지원 확인 전까지 활성화하지 않는다.

## 검증

기존 11개 테스트와 OAuth mock 공격·회전·해제·로그 테스트를 함께 실행한다. mock 테스트는 socket 접속을 차단하며 임시 DB·설정·시계를 사용한다. 실제 loopback Uvicorn에서도 모의 인증, 상태 조회, 해제 및 code/state/cookie/token 접근 로그 비노출을 확인했다. 실제 사용자 .env·운영 DB는 이 검증에 사용하지 않았다.

실제 연결 전 앱 승인과 최소 scope, localhost Redirect URI 등록, Windows↔WSL 도달, PKCE/public client 사양, 안전한 영속화 방법을 확정해야 한다. HTTP 클라이언트 구현·실제 로그인·SSE는 이후 별도 작업이다. 공식 미확인 항목은 K2.0 문서에 유지한다.
