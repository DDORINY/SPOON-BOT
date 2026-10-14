# K4.1.1 / K4.2 — 조사 및 모델 결정

기존 SSE STOPPED/CONNECTING/LIVE/WAITING/BACKOFF/REFRESHING/오류 상태를 재사용한다. WAITING은 이미 동의한 요청이 살아 있는 방송 대기다. 표시용 waiting_for_live만 추가하고 대기 취소는 기존 stop을 사용한다. 다음 방송 재시도는 최초 동의 범위에서만 유지한다.

기존 broadcast_sessions는 SSE 연결 관측 세션이며 실제 방송 회차가 아니다. donations는 명시 event ID·Decimal 금액·발생UTC/수신UTC·session_key·HMAC fingerprint이고 unique(integration_key,stream_event_id)와 conflict/gap을 유지한다.

공식 GET /v1/live의 liveId와 startedAt을 회차 관측에 쓴다. donation에는 liveId가 없다. LIVE_ENDED는 방송 종료 신호이고 EOF/404/API 오류는 종료시각 확정 근거가 아니다. 마지막 수신시각은 종료시각이 아니다. 공식 closeAirTime은 예정 종료이므로 실제 종료에 쓰지 않는다.

새 broadcast_rounds는 local id, local account namespace, 확인된 provider_live_id(nullable), provider started_at(nullable), first/last observation, state, end observation time(nullable), 실제 ended_at(nullable), created/updated UTC를 가진다. 인증/API 오류는 unknown으로 표시할 수 있으나 실제 종료시각을 채우지 않는다. 같은 namespace+확인 liveId는 unique다.

donations에 nullable account_key, broadcast_round_id와 attribution_basis(default unconfirmed)를 추가한다. 기존 레코드는 모두 그대로 NULL/unconfirmed로 남긴다. 추가 context는 공식 필드가 아니라 프로젝트 판정이며 같은 계정·확인된 회차여야 한다. 중복 재수신은 기존 레코드의 귀속을 바꾸지 않는다. 기존 unique index도 유지하며 계정별 ID 중복 범위는 공식 미확인이다.

운영 DB 적용은 자동화하지 않는다. Real 마커/user_version1을 검사한 뒤 명시적인 migrate_rounds(repository)가 BEGIN IMMEDIATE 트랜잭션으로 v2를 적용한다. 재실행은 검사만 하고 데이터 보정/과거 귀속을 하지 않는다. Mock/일반 DB는 거절한다. 신규 DB도 기존 v1 생성 뒤 이 명시 함수로만 v2를 적용한다. 읽기 API는 v1이면 migration_required 안내와 빈 회차 목록을 제공한다.

계정은 토큰 자체나 방송ID/날짜로 식별하지 않는다. 공식 현재 응답에 안정적인 DJ ID가 없으므로 임의 계정병합을 금지한다. 사용자 후속 지시에 따라 로그인별 UUID namespace 생성은 철회했다. 공식 토큰 응답에는 안정적인 DJ ID 또는 검증 가능한 ID token이 명시되지 않는다. 따라서 런타임 account_context는 미확인(None)이며, 계정별 회차 조회와 과거 기록 연결을 보류한다. 안정적인 공식 식별자가 확인될 때만 같은 DJ의 재로그인 기록 연결을 구현한다. 토큰 문자열/해시, Client ID, 임의 응답 user_id/sub는 신원 근거로 사용하지 않는다. 테스트에서만 명시적인 가상 검증 신원을 주입한다. 과거 데이터의 소유자를 자동 결정하지 않는다.

API는 인증된 브라우저·Host 검사, 계정 namespace 강제, 날짜 aware UTC 검증·범위 상한·cursor/limit 상한, 바인딩SQL을 사용한다. /api/broadcast-sessions 목록/상세, /api/session-donations 회차/미확정 조회를 기존 /api/donations와 별도로 제공해 기존 계약을 유지한다. 미확정 역사 기록은 삭제하지 않으며 소유자 확인 없는 과거 기록의 다른 계정 노출을 막는다.

실제 후원 회차 귀속은 임의 UI 캐시를 사용하지 않는다. 공식 event ID가 회차를 보장하지 않으므로 직접 확인된 컨텍스트를 명시 주입할 수 있는 저장 경계를 제공한다. 자동 runtime 귀속이 확인되지 않은 이벤트는 NULL이다. 후속 실제 검증에서 안정적인 컨텍스트 획득과 계정 식별을 확인하기 전 이 한계를 숨기지 않는다.


## 공식 출처와 미확인 범위

- [현재 방송 조회](https://developers.spooncast.net/docs/api-reference/live): liveId는 공식 방송 회차 ID, startedAt은 시작 시각, closeAirTime은 종료 예정 시각이다.
- [이벤트 스트림](https://developers.spooncast.net/docs/api-reference/live-events): donation 명세에는 방송 ID가 없다. end/LIVE_ENDED는 종료 관측 근거이나 어느 로컬 회차인지 확정할 컨텍스트가 없는 상태에서 임의 귀속하지 않는다.
- [엔드포인트 공통 규칙](https://developers.spooncast.net/docs/api-reference/endpoints): 토큰이 대상 DJ를 정하지만 이 조회 응답 자체에는 영구적인 DJ 식별자가 없다.

공식 문서만 조회했다. 실제 API 응답이나 실제 방송 통합 검증을 수행하지 않았다.

## 구현 파일 및 동작

broadcast_rounds.py에 명시 마이그레이션과 회차/후원 조회를 분리했다. 기존 broadcast_sessions는 수집 세션으로 유지한다. schema v2는 broadcast_rounds, donations의 account_key/broadcast_round_id/attribution_basis, 충돌·누락 기록의 account_key, donations_rounds 인덱스를 추가한다. 기존 HMAC salt와 unique donation_id 인덱스는 그대로다. 서로 다른 namespace에서 같은 event ID가 들어오면 기존 전역 unique 정책에 따라 충돌로 보관하며 서로 합치지 않는다. 이 전역 ID 범위가 적절한지는 실제 검증 후 별도 결정한다.

GET /api/live의 실제 HTTP 관측 결과가 있을 때만 회차 first/last observation을 기록한다. 캐시 응답은 시각을 갱신하지 않는다. DB가 v1이거나 없으면 읽기로 생성/마이그레이션하지 않는다. 실제 writer는 계정 namespace를 기록하지만 방송 컨텍스트가 없으므로 회차는 NULL이다. 명시적 확인 컨텍스트를 저장 계층에 주입하는 테스트 경계만 구현했다. end_observed도 확정된 회차를 가진 호출자를 위한 경계이며 현재 SSE에서 임의로 호출하지 않는다. 일시 오류는 unknown으로 처리하고 실제 종료시각을 채우지 않는다.

API:

- GET /api/broadcast-sessions?limit=20&before=<round_sequence>: 회차 목록, 최대 100개.
- GET /api/broadcast-sessions/{round_id}: 같은 로그인 구역의 상세, 다른 구역은 404.
- GET /api/session-donations?round_id=... 또는 unconfirmed=true: 회차/미확정 기록.
- limit(1~100), before(received_sequence), date_from/date_to(시간대 포함 ISO-8601) 지원. 시작 포함/종료 제외 UTC이며 양 끝이 있으면 366일 이하. round_id와 unconfirmed 동시 사용은 거절한다.

모든 새 API는 인증된 브라우저 세션과 Host/중복 쿠키 검사를 거친다. account_key는 클라이언트에서 지정할 수 없다. SQL 바인딩과 textContent를 사용한다. v1에서는 목록에 migration_required=true, 상세/회차 후원은 409로 적용 필요 상태를 표시한다. /api/donations의 v1 계약은 유지하고 v2는 현재 로그인 구역만 조회한다. 소유자 미확인 과거 기록은 삭제하지 않으며 자동 노출/귀속하지 않는다. 이는 회차만 미확정인 현재 구역 기록과 구별한다.

프론트는 전체/회차별/회차 미확정 선택을 제공한다. v1에서는 필터 비활성, 적용 필요 안내와 기존 조회 유지. 통계와 날짜 UI는 다음 단계로 남긴다. 운영 DB를 v2로 적용하기 전에는 회차 UI가 활성화되지 않는 것이 정상이다.

## 운영 적용 제안 — 이번 작업에서 실행하지 않음

1. 서버와 수집 writer를 중지하고 대상 Real DB 경로·application_id·user_version을 검증한다.
2. SQLite backup API로 일관된 전체 백업을 별도 파일에 만든다. 실행 중인 WAL DB 본체만 복사하는 방법은 사용하지 않는다.
3. 기존 행 수·인덱스·salt를 비교할 검증 정보를 보관하고 사용자 승인 후 migrate_rounds를 명시 실행한다.
4. foreign_key_check 및 무결성, 행 수, 기존 행의 NULL/unconfirmed 유지, 버전 2와 재실행을 확인한다.
5. 실패하면 트랜잭션 롤백한다. 적용 후 복구가 필요하면 서버를 중지한 뒤 전체 백업을 복원하고 호환 버전으로 실행한다. 그 사이 새 기록이 있으면 보존 계획 없이 덮어쓰지 않는다.

회차 적용과 별개로, 안정적인 DJ 소유자 확인/재로그인 구역 연결 정책은 사용자와 추가 합의가 필요하다. 이전 운영 기록의 소유자를 확인하기 전 자동 backfill을 하지 않는다.

## 닫기 요소 조사 및 수동 검증

로컬 HTML/JavaScript의 동적 생성 코드, dialog/popover/toast, CSS content, 외부 리소스 참조를 조사했으나 문제의 닫기 요소를 생성하는 코드가 확인되지 않았다. 임의 삭제하지 않았다. Chrome 확장 비활성 창에서 재현하고 DevTools로 해당 요소의 생성 주체를 확인해야 한다. 이 환경에서는 실제 Chrome 화면 검증을 하지 못했다.

실제 방송 검증은 별도 수동 단계다. 대기 요청의 재시도와 취소, 방송 시작 시 연결, 방송 종료/네트워크 단절 구분, liveId 안정성, 후원 중복/충돌/미확정 표시를 확인한다. 화면 진입과 OAuth 성공만으로 SSE를 시작하지 않는다.

K4.3 제안: DJ 소유자 확인 정책 확정 → 실제 회차 귀속 근거 검증 → 날짜·cursor UI 연결 → 관측 범위와 미확정 기록을 구분한 후원 통계. 누락 복원 및 전체 원장 완전성은 보장하지 않는다.


## 공식 DJ 신원 재조사 결과

[토큰 명세](https://developers.spooncast.net/docs/api-reference/tokens)의 발급/갱신 응답은 access_token, token_type, expires_in, refresh_token, scope다. 계정 ID/id_token은 명시되지 않았다. 현재 TokenPair도 이 정보만 검증·보관한다. 공식 현재 방송 조회의 liveId는 회차 ID이며 DJ ID로 사용하지 않는다. SSE user.id는 이벤트를 발생시킨 청취자 정보이므로 인증된 DJ 신원으로 사용하지 않는다. 공식 공개 엔드포인트 목록에서 검증 가능한 현재 DJ 프로필/userinfo 경로는 확인되지 않았다. 실제 토큰은 열람하거나 임의 디코딩하지 않았다.

공식 신원 미확인 상태의 v2 회차 API는 409 dj_identity_unverified를 반환한다. v2 전체 후원 API도 과거 소유자 미확인 기록을 노출하지 않고 identity_status=unverified를 알린다. 수집은 기존 동의 계약을 유지하며 새 기록은 계정/회차 NULL로 보존한다. 계정 ID가 확인된 척하지 않는다. 실제 공식 신원 확인 경로와 검증 규칙 확보 후 재로그인 연속성을 구현해야 한다. 이전 로그인별 구역 구현 설명은 이 결정으로 대체한다. 운영 DB와 기존 기록은 이번 조정에서도 변경하지 않았다.
