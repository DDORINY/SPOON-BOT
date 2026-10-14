# K4 — 방송 정보 조회와 스튜디오 연결

## 공식 조사와 기존 구현

[엔드포인트](https://developers.spooncast.net/docs/api-reference/endpoints), [권한](https://developers.spooncast.net/docs/en/getting-started/choosing-permissions), [방송 조회](https://developers.spooncast.net/docs/en/api-reference/live), [SSE](https://developers.spooncast.net/docs/api-reference/live-events), [오류](https://developers.spooncast.net/docs/en/api-reference/error-codes)를 확인했다. 한국 권한 문서 URL은 조회 도구 오류가 있어 같은 공식 영문 문서를 사용했다. 승인된 앱 권한은 개발자 센터를 확인하지 않았으므로 미확인이다. 로컬 설정의 요청 scope나 테스트 토큰을 실제 앱 승인 증거로 보지 않는다.

| 기능 | 작업 전 상태 | 처리 |
|---|---|---|
| OAuth 로그인/state/코드1회/Basic/rotation/revoke | 구현됨 | 기존 RealAuthService 재사용 |
| 실제 SSE 명시 시작/중지·고지·재연결 | 구현됨 | 변경하지 않음 |
| 방송 제목·시작·현재 청취자 조회 | 미구현 | 이번 live.read GET 구현 |
| 후원 파싱·Decimal·SQLite·중복/충돌/ID없음 | 구현됨 | 중복 구현하지 않음 |
| 후원 화면 갱신·최근 조회 | 구현됨 | 기존5초 조회·최대20건·textContent 유지 |
| 후원 회차 구분 | 일부 구현 | 로컬 연결 session만 존재; 실제 liveId 귀속 없음 |
| 시간대 | 일부 구현 | 후원 UTC 저장·표시, 날짜별/사용자 시간대 집계 미구현 |
| 방송 조회와 SSE 상태 분리 | 일부 구현 | 별도 방송 정보와 도우미 연결 표시 |
| 캡처/갤러리 | 미구현 | 보류 |
| 추가 권한 기능 | 미구현 | 아래 설계만 작성 |

후원 명시 ID+금액/UTC시각 fingerprint로 중복 확인, 다른 핵심값은 충돌 기록, ID 없음은 각각 저장한다. reconnect/gap은 복원이 아니다. live.read가 liveId를 제공하더라도 SSE 이벤트에는 liveId가 없고 조회와 연결이 원자적이지 않아 기존 후원을 자동 귀속시키지 않는다. 서버 수신/DB 커밋 순서가 공급자 순서 보장은 아니다.

## 백엔드와 API 계약

spoon/live_read.py의 LiveReadClient는 RealAuthService만 허용하고 고정 지역 HTTPS GET /v1/live를 사용한다. 임의 DJ·URL·redirect·proxy·쿠키는 전달하지 않는다. Bearer 토큰은 서버 메모리 lease로만 얻는다. scope live.read 확인 후 호출하고 기존 갱신 lock/rotation을 재사용한다. 401은 lease 기준 refresh1회 후 재시도, 계속401/갱신 실패는 재인증이다. 403은 정상 인증을 임의 폐기하지 않는다.

GET /api/live는 localhost Host·중복 쿠키·인증된 브라우저 바인딩을 검사하고 no-store 응답을 제공한다. 기존 API 계약은 변경하지 않는다.

```json
{"state":"live","live":{"live_id":123,"title":"방송 제목","started_at_utc":"2026-10-14T01:00:00Z","listener_count":42},"retryable":false}
```

위 값은 가상 예시다. 비방송은 state=waiting, live=null이고 정상 로컬200이다. 공식404/OAPI_MNGR_0301만 기다림으로 해석한다. permission_required, reauth_required, error, rate_limited, not_configured도 live=null이다. 인증된 조회의 공급자 오류는 로컬200+명시 state, 로그인 브라우저가 아니면 로컬401, Host 공격은 기존400이다. provider 메시지/Secret/token/코드/쿠키는 응답이나 로그에 넣지 않는다.

응답의 사용하는 필드만 검증하고 불필요한 환영 메시지·추가 속성은 반환/저장하지 않는다. 제목1000자, 숫자는 non-bool·비음수·정수값·JS안전정수 이내, 시각은 timezone-aware UTC로 정규화한다. 이는 프로젝트 제한이며 공급자가 숫자 전체 범위를 보장한 것은 아니다. 예상 밖 payload는 가짜 숫자로 대체하지 않는다.

연결5초/HTTP10초/전체15초, 본문64KiB 상한, 단일 HTTP pool과 lock. 여러 탭은 같은 조회 캐시30초를 공유한다. 일시 오류는30초부터 최대300초 백오프, 유효 Retry-After 초(최대86400)는 그보다 먼저 재호출하지 않는다. 403은 재동의로 토큰이 바뀔 때까지 캐시한다. 캐시에는 토큰 원문/TokenPair 참조를 저장하지 않고 현재 access의 메모리 hash로 분리한다. 실제 공급자 quota 수치는 미확인이며 이 간격이 할당량 준수를 보장하지 않는다.

## 프론트

live.js는 초기 로딩 문구, 방송 중 제목/시작/로컬 시계 기준 경과시간/현재 청취자, 방송 대기, 재인증, 권한 부족, 오류/제한 안내를 표시한다. 조회는 보이는 탭에서30초 주기이고 재시도 버튼도 서버 캐시/백오프를 우회하지 못한다. 브라우저 로컬 시계 오차가 경과시간에 영향을 줄 수 있다. 방송 시각은 UTC 원본에서 사용자 브라우저 시간대로 표시한다.

방송 정보는 /api/live, 도우미 연결은 기존 /api/status에서 가져온다. 방송 정보 조회는 봇 SSE 입장이 아니며 SSE 연결·고지는 기존 명시적 동의 POST만 시작한다. 기본/Mock 실행에서 실제 live HTTP는 사용하지 않는다. 페이지 진입만으로 서버 factory/lifespan이 외부 요청을 만들지는 않지만, 로그인된 Real 브라우저의 방송 조회 UI는 API를 호출한다. 이번 작업에서는 실제 브라우저/자격 증명으로 이를 실행하지 않았다.

## 후속 권한 설계 — 구현/호출하지 않음

| 권한 | 공식 기능 | 제안 |
|---|---|---|
| listeners.read | GET /v1/live/listeners 커서 목록 | 최소 식별/표시 정책과 커서 API, 청취자 패널 |
| fans.read | GET /v1/live/fans 상위30 팬 | 랭킹 화면; 전체 후원 원장이나 누락 복원으로 사용 금지 |
| events.chat | 기존 단일 SSE의 chat | 임시 채팅 패널, 기본 DB 원문 저장 금지 |
| events.presence | 기존 단일 SSE의 presence | 입장 알림; 동의 외 DJ의 매니저 지정도 필요 |
| events.like | 기존 단일 SSE의 like | 무료/유료 좋아요 표시; 후원 스푼과 합산 금지 |
| chat.send | POST /v1/live/chat | 명시 사용자 전송·CSRF·권한·방송 제한·빈204·rate limit 검증 |

설정 UI는 추가 권한 필요 및 미구현이라고 표시한다. 현재 앱 승인 여부는 추측하지 않는다. 구현 전에 등록 승인과 DJ 동의를 확인하고, 현재 OAuthSettings/TokenPair의 고정 두 scope 정책을 별도 승인된 설계로 확장해야 한다. 임의 scope 추가 또는 실제 요청을 하지 않았다. 이벤트 종류별로 별도 SSE를 늘리지 않고 기존 단일 연결을 확장할 계획이다.

방송 시작/종료 제어·화면 획득·후원 이력 완전 복원은 검토한 공식 endpoint 목록에 없다. 이를 확인된 지원 기능으로 표시하지 않으며, 공식 API 전체에 절대 없다고 단정하지 않는다.

## 검증/다음 순서

가상 UUID/Secret/토큰, Mock HTTP Transport, 임시 DB, 소켓 차단으로 검증한다. 실제 .env/운영 DB를 변경하지 않고 실제 OAuth/SSE/live API를 호출하지 않는다. Windows Chrome 및 실제 API 응답은 수동 검증이 남는다.

후속 순서: 사용자가 앱 live.read 승인/동의 확인 → 수동 조회 확인 → liveId와 SSE 세션 경계 설계 → 필요한 추가 권한 승인·동의·최소 수집 정책 → 청취자/팬 읽기 → 단일 SSE 확장 → 명시 채팅 전송. 캡처 개발은 보류한다.
