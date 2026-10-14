# K3.2 — 명시적으로 시작하는 실제 SSE 후원 수집

기준 main `5cbfe11ada9a7449fe9ff975b4ef93e715705ac5`, 기존 203개 테스트를 먼저 확인했다. K3.0/K3.1/K2.2 문서와 구현을 유지·확장했다. 앱 승인 완료는 사용자 제공 정보다. 실제 OAuth 수동 성공 여부는 별도 사용자 확인이 필요하며, 이 작업에서는 실제 로그인·SSE·후원을 자동 검증하지 않는다. 실제 `.env`를 검사하거나 수정하지 않았고 운영 DB를 열지 않았다. 사용자 `.env.example` 변경은 커밋에서 제외한다.

## 공식 근거와 확인 범위

2026-10-14 공개 [SSE 명세](https://developers.spooncast.net/docs/en/api-reference/live-events)와 [청취자 고지](https://developers.spooncast.net/docs/en/getting-started/listener-notice)를 재확인했다. GET /v1/live/events, 서버측 Bearer/Accept 헤더, 후원 권한 events.donation, 성공 text/event-stream, 미방송 404/OAPI_MNGR_0301을 적용한다. 지역별 HTTPS 호스트는 기존 OAuth의 고정 kr/jp 목록을 공유한다. 연결 대상 DJ는 토큰이 결정하며 URL에 DJ를 지정하지 않는다.

후원은 공식 user/amount/message/time 필드를 기존 정규화기로 검증한다. amount는 총 스푼이고 재곱셈하지 않는다. 원문 user.id/nickname/message는 검증 후 폐기한다. SSE 명시 ID만 중복 키로 사용한다. Last-Event-ID 재개는 현재 공식 지원하지 않으므로 헤더를 보내지 않는다.

연결 시 봇 계정 이름의 서버측 고지가 방에 표시된다. 등록 앱 이름과 고지 이름이 반드시 같은 것은 아니다. 스트림을 닫아도 방의 입장 기록이 즉시 사라진다는 보장은 없다. 별도 채팅 API로 고지하거나 계정 정보를 수집하지 않는다.

## 구현 구조

- `spoon/real_sse.py`: 명시적 RealAuthService만 허용하는 고정 GET 클라이언트와 바이트 연결. httpx TLS 검증 유지, proxy/redirect/replay 비활성, 연결5초·무수신45초, 컨트롤러의 연결 전체 제한10초. 실패 본문64KiB 한도와 허용 detailCode만 추출하며 설명 원문은 버린다. SSE와 OAuth는 서로 다른 HTTP 연결 풀을 사용한다.
- `spoon/auth_service.py`: 서버 내부 access lease와 무효화 경계. 강제 갱신은 연결 당시 pair identity로 이미 갱신된 토큰의 추가 rotation을 막는다. 토큰은 기존 메모리 저장소만 사용한다.
- `spoon/stream_service.py`: 공통 `_StreamController`, 별도 Mock/Real 컨트롤러. 인증·소스·저장소 모드 일치 검사, 단일 producer/SQLite writer와 큐256개, 명시적 시작/중지, 재시도/gap/실패 처리.
- `spoon/donation_repository.py`: 기존 Mock schema를 유지하면서 Real 전용 filename/application_id/schema/CHECK를 추가한다. 운영 DB migration은 없다.
- `main.py`, `__main__.py`: 선택적 Real SSE factory/CLI. 기본 실행, Mock 실행, OAuth-only 실행은 유지한다. 새 GET /api/collection-mode는 수집 가능 모드만 반환하고 기존 /api/mode 응답은 유지한다.
- `web/real_auth_routes.py`: 별도 수집 관리 화면, DJ 고지 확인 checkbox, CSRF POST 시작·중지. 로그아웃은 SSE를 먼저 닫고 revoke한다.
- `web/static/`: 실제 관측/Mock 라벨, 익명 후원 목록, 관측 합계, 불완전성. 외부 CDN과 HTML 삽입 없이 textContent로 표시한다.

SSE source는 factory 실행이나 대시보드 GET만으로 열리지 않는다. Real DB도 인증·scope 검증과 사용자 시작 POST가 성공한 뒤 처음 생성한다. 인증 갱신 실패가 status 조회에서 발견되면 활성 수집을 닫는다. 수집 중 바이트 전후에도 인증을 확인한다. 별도 logout 없이 외부에서 인증이 해제되는 경우 heartbeat/무수신 제한 내에서 감지하며, 이를 즉시 감지하는 서버 push 기능은 없다.

## 수동 실행과 중지

아래 명령은 사용자가 수동 실행할 단계다. 이 작업에서는 실행하지 않았다.

```bash
cd /home/doha/projects/spoon-local
.venv/bin/python -m spoon_local --real-oauth \
  --real-sse-db /home/doha/projects/spoon-local/data/real/real-sse.sqlite3
```

바인딩은 127.0.0.1:8000, 단일 워커다. `--real-sse-db`는 `--real-oauth`와 함께만 허용한다. 절대 경로와 파일명 real-sse.sqlite3를 요구한다. 기존 일반 DB나 Mock DB를 이 이름으로 옮겨 쓰지 않는다. 기존 K2.2 `--real-oauth`만 쓰면 수집 라우트는 제공하지 않는다.

1. Windows 브라우저에서 http://localhost:8000/를 연다. 인증은 등록된 localhost callback과 같은 호스트를 사용한다. localhost 전달 검증은 K2.2 문서를 따른다.
2. 실제 SPOON 로그인에서 **방송 DJ 계정**으로 동의하고 authenticated를 확인한다. 프로세스를 재시작하면 메모리 토큰이 사라져 다시 로그인해야 한다.
3. DJ에게 수집 목적·최소 저장·누락 가능성을 고지한다. 수집 관리 화면의 봇 고지 안내를 이해하고 checkbox를 선택한 다음 **실제 후원 수집 시작**을 누른다. 이 POST부터 외부 SSE 요청이 발생한다.
4. 방송이 없으면 WAITING 상태로 기다린다. 방송이 시작되면 연결하고 관측된 후원만 표시한다. 실수집 확인은 사용자의 수동 방송에서만 한다. 자동 테스트 후원이 실제 방송에 전송되지는 않는다.
5. 수집 관리에서 **수집 중지**를 누른다. 토큰을 유지하므로 다시 명시적으로 시작할 수 있다. **로그아웃 및 토큰 해제**는 스트림을 닫고 토큰을 폐기한다. revoke 실패는 로컬 삭제와 원격 폐기 미확인을 구분한다.
6. 종료 시 Ctrl+C로 스트림과 HTTP 클라이언트를 닫는다. 프로세스 종료만으로 원격 OAuth 토큰 폐기를 보장하지 않는다.

시작·중지는 `/auth/spoon/collection/start`, `/stop` POST이며 localhost Host, 정확한 Origin, 세션/CSRF를 검증한다. GET은 수집을 시작하지 않는다. 시작은 `dj_notice=confirmed` 단일 값도 요구한다. 오류 원문·토큰·Secret·쿠키는 API에 반환하지 않는다.

## DB·중복·누락 정책

Real application_id는 0x53525345, Mock은 기존 0x534D5345다. source_mode CHECK는 각각 real/mock, integration_key는 real-single-dj/mock-single-dj이며 DB를 합치지 않는다. Real 테이블은 real_metadata, broadcast_sessions, donations, event_conflicts, collection_gaps다. 새 DB0600/새 디렉터리0700, symlink/hardlink/static 경로 거절, 기존 파일 헤더를 SQLite 연결 전에 검사한다. 기존 DB 버전1을 유지하며 자동 migration이나 삭제는 없다.

후원 Decimal 문자열·UTC 발생/수신시각·로컬 record/session ID·명시 이벤트 ID·비개인 핵심 fingerprint만 저장한다. 토큰 및 원문 개인정보 컬럼은 없다. 같은 ID+같은 금액/시각은 중복, 같은 ID+다른 핵심값은 충돌 기록만 남기고 합계에 더하지 않는다. ID가 없으면 모두 저장한다. 이름/메시지 차이만 있는 충돌은 식별하지 않는다. salt는 DB 안의 비교 보조값이며 암호화 키라고 주장하지 않는다.

실제 SSE에는 liveId가 없어 연결마다 새로운 **로컬 관측 세션**을 만든다. 실제 방송 회차 식별·현재 방송 메타데이터 조회는 이번 구현에 포함하지 않았다. ID 중복 정책은 연결마다 초기화하지 않는다. 전역 ID 유일성이 미확인이라 서로 다른 방송의 동일 ID 충돌은 사용자가 확인해야 한다. 한 DB는 같은 앱·DJ 전용이며 다른 DJ로 로그인하여 재사용하지 않는다. 현재 API만으로 이 조건을 자동 확인하는 기능은 없다.

collection_started, live_ended, 중지/EOF/재연결/인증/손상/큐 초과/저장 장애 등을 gap으로 남긴다. 복귀 시각은 수신 재개를 뜻하며 누락 복원이 아니다. 디스크가 쓰기를 거절하면 gap 원인/시각을 메모리에 보관하며 해당 프로세스 내 재시작 시 저장한다. 비정상 종료 전에 저장되지 않은 큐/gap은 유실될 수 있다. 다음 명시 시작 자체도 gap으로 기록한다. 중지는 미커밋 큐를 취소할 수 있다.

조회 API는 기존 `/api/donations`로 활성 모드의 별도 DB만 조회하고 source_mode=real, coverage=observed_only를 명시한다. 화면의 합계는 저장된 관측 건 기준이며 실제 총 후원 원장이 아니다. 기본/인증-only 모드에서는 이 DB를 조회하지 않는다. 자동 보존기간·안전 삭제·백업 암호화는 미구현이며 임의로 데이터를 삭제하지 않는다.

## 상태와 재연결

404/OAPI_MNGR_0301 및 LIVE_ENDED는 WAITING(5초부터 최대30초+jitter), EOF/네트워크/500/502/503은 BACKOFF(1초부터 최대30초+jitter), 429는 유효 Retry-After 숫자 초 또는 제한된 백오프다. 401은 연결 lease 기준 갱신1회 후 재시도하고 계속401이면 토큰·세션을 지워 재인증을 요구한다. TOKEN_EXPIRED도 공유 rotation을 사용한다. 403 권한/입장 차단은 자동 반복하지 않는다. 알 수 없는 404/잘못된 요청/redirect 응답은 중단한다.

RECONNECT는 즉시 재접속하지만 한 수집 실행당3회를 넘으면 로컬 안전장치로 중단한다. 다른 백오프는 연결이 짧게 성공했다고 바로 초기화하지 않아 반복 폭주를 피한다. 이 한도/대기값은 프로젝트 정책이며 공급자 SLA가 아니다. 저장 실패는 STORAGE_ERROR로 정지하고 원인을 해결한 뒤 사용자 명시 시작으로만 회복한다.

## 안전 점검·미확인 사항

수동 실행 전에 K2.2 OAuth 성공·정확한 지역/redirect·events.donation 동의·Windows localhost 전달을 확인한다. 다른 수집 프로세스를 띄우지 않고 별도 DB 경로와 디스크 공간을 확인한다. 봇 고지는 끌 수 없고 연결 중단 시 즉시 퇴장하는 기능은 없다. 원문/토큰이 담긴 HAR·콜백 주소·로그를 공유하지 않는다.

PKCE 및 public/confidential client 조건은 여전히 공식 미확인이다. 이벤트 ID의 전역 유일성/순서/동시 연결 상한, 후원 취소·정정·환불, 누락 후원 복원, nullable key 생략 동작, 실제 금액 범위·방송 경계 귀속은 보장하지 않는다. 실제 OAuth/SSE 및 Windows 브라우저 결과는 자동 검증 범위 밖이다.

## 검증

가상 UUID/Secret/Access·Refresh, httpx.MockTransport와 임시 SQLite만 사용한다. 새 테스트에서 외부 socket connect와 실제 설정/운영 DB 초기화 경로를 차단한다. 기존203개 회귀를 포함해233개 테스트가 경고를 오류로 처리하는 조건에서 통과했고 pip check는 정상이다. SSE 쪼갠 바이트·중복/충돌/ID없음·HTTP 오류/재연결·rotation identity·권한 부족·저장 장애·재시작·명시 POST/고지/CSRF·logout close·로그/API 최소화·Mock/Real 마커 분리를 검증했다.

Node 구문 검사와 별도 DOM stub 실행에서 실제/Mock 라벨, XSS 문자열의 textContent 처리, 비활성 데이터 제외를 검증했다. 실제 브라우저 렌더링이나 실제 방송 E2E가 성공했다는 뜻은 아니다. git diff --check와 신규 파일 공백·민감정보 검사도 별도로 수행한다.
