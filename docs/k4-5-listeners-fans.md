# K4.5 청취자·팬 랭킹 조회

## 공식 계약

출처: [청취자](https://developers.spooncast.net/docs/api-reference/listeners), [팬 랭킹](https://developers.spooncast.net/docs/api-reference/fans), [공통 엔드포인트](https://developers.spooncast.net/docs/api-reference/endpoints).

| 기능 | 공식 요청 | 권한 | 공식 응답 |
| --- | --- | --- | --- |
| 청취자 | GET /v1/live/listeners, 선택 cursor | listeners.read | listeners[{id,nickname}], nextCursor(nullable) |
| 팬 랭킹 | GET /v1/live/fans, 파라미터 없음 | fans.read | totalCount, fans[{id,nickname,rank,spoonCount(nullable)}] |

Bearer 토큰이 방송 DJ를 정하며 liveId를 요청에 넣지 않는다. 청취자 nextCursor는 같은 봇·DJ 연동에서 반환된 값을 그대로 다음 요청에 쓴다. 청취자 API에는 전체 인원 필드가 없으므로 목록 길이를 전체 청취자 수로 해석하지 않는다. 팬 랭킹은 현재 방에 있는 청취자의 해당 방송 누적 후원 상위 30명이다. 방송 종료 시 초기화되고 방에서 나간 후원자는 빠진다. totalCount는 반환 목록 수이며 전체 팬 수가 아니다. 공식 순서·rank를 보존하고 임의 순위를 생성하지 않는다. spoonCount null은 정보 없음으로 표시하며 원화로 환산하지 않는다.

프로필 이미지 필드는 공식 계약에 없다. 임의 imageUrl/avatarUrl을 받아 외부에 연결하지 않으며 중립적인 로컬 아이콘만 표시한다. 개인정보 ID는 형식 검증 후 응답에서 제거한다. 이 청취자 ID를 OAuth DJ 신원으로 추정하거나 기존 회차 소유권에 쓰지 않는다.

공식 숫자 호출 제한 및 권장 조회 주기는 확인되지 않았다. 자동 청취자·팬 폴링은 도입하지 않는다. 실제 API 응답은 이번 작업에서 확인하지 않았다.

## 기존 구조 재사용

LiveReadClient의 고정 HTTPS 주소, 제한된 HTTP 연결, timeout, trust_env=False, redirect 금지, 응답 크기 보호, 쿠키 제거를 재사용한다. 새 HTTP 클라이언트나 인증 저장소를 생성하지 않았다. _request는 세 개의 읽기 전용 경로만 허용한다. 기존 get /api/live, OAuth/SSE, SQLite 후원 저장 및 검색은 유지한다.

AudienceService는 현재 인증 브라우저 세션의 토큰 lease로 권한을 먼저 검사한다. 해당 권한이 없으면 목록 API와 사전 방송 조회 모두 호출하지 않는다. 조회 전후 GET /v1/live로 동일 liveId를 확인하고, 중간 방송 변경/종료/로그아웃이 확인되면 수신된 목록을 버린다. 목록 응답에 liveId가 없으므로 이 검사는 가능한 시점 검증이며 방송 변화를 영구적으로 차단하는 보장은 아니다.

실제 설정 로더의 새 동의 요청은 live.read/events.donation/events.chat/events.like/listeners.read/fans.read를 포함한다. 기존 토큰은 최소 기존 권한만 필수로 유지하며 부가 기능은 독립적으로 거절한다. 기존 토큰에 권한을 추가하거나 자동 재로그인하지 않는다. chat.send는 요청·구현하지 않는다.

## 로컬 API

- GET /api/live/listeners: 첫 장. cursor와 live_id는 이어 읽을 때만 선택적으로 사용한다.
- GET /api/live/fans: 공식 상위 30명. cursor는 허용하지 않는다.
- 응답: state, items, next_cursor, queried_at_utc, 성공 시 live_id/returned_count 및 count_basis 또는 ranking_basis/unit.
- 청취자 items: nickname. 팬 items: nickname/rank/spoon_count(정밀도 유지를 위한 문자열 또는 null).

상태는 ready/waiting/reauth/permission_required/rate_limited/timeout/network_error/upstream_error/broadcast_changed/invalid_cursor/busy/not_configured다. 인증 없음은 HTTP 401, 입력·커서 오류는 400, 그 외 조회 상태는 로컬 HTTP 200의 state로 구분한다. upstream 404는 OAPI_MNGR_0301일 때만 waiting이다. 401은 기존 단일 refresh 회전을 재사용하고 실패/반복 401은 재인증한다. 403은 개별 권한 안내다. 429는 Retry-After 초 또는 HTTP 날짜를 해석하여 그 전에 재호출하지 않는다. 잘못된 헤더는 로컬 30초 보호를 적용한다. 500/리디렉션/잘못된 JSON·스키마는 안전한 일반 오류다.

로컬 보호 정책: 응답 본문 최대 64KiB, 청취자 한 응답 최대 1000개, 팬 최대 30개, 사용자 표시명/ID 최대 256자, 커서 최대 2048자와 제어 문자 거절. 이는 공식 최대값이 아니라 리소스 보호 제한이다. 실제 응답이 이를 넘는다면 수동 검증 후 조정해야 한다. 임의 쿼리·중복 쿼리·잘못된 live_id는 거절한다. 커서는 현재 세션·liveId에 대해 마지막으로 서버가 받은 값만 허용한다.

## 접근 통제와 보존

현재 OAuth를 연결한 브라우저만 접근하며 기존 Host/중복 쿠키/세션 검사와 no-store를 적용한다. 목록 결과를 서버 전역 캐시에 저장하지 않고 브라우저로 전달한 뒤 서버에서 유지하지 않는다. 세션 변경 시 커서·호출 제한 메타데이터를 초기화한다. 이미 진행 중인 이전 세션 요청은 응답을 전달하지 않는다. 토큰/쿠키/Secret/공식 사용자 ID/전체 응답을 로그에 쓰지 않는다. SQLite 및 운영 DB 스키마·데이터를 변경하지 않는다.

팬 수치는 공식 응답만 사용하며 K4.3 후원 기록에서 랭킹을 만들지 않는다. 신뢰할 수 있는 영구 DJ 신원 확인은 계속 미확정이며 이 읽기 경로로 계정별 통계나 과거 귀속을 구현하지 않는다.

## UI 및 조회 정책

/broadcast에 접근 가능한 청취자·팬 랭킹 탭을 추가했다. 클릭 또는 키보드 좌우/Home/End 선택과 새로고침으로만 조회한다. 청취자 다음 버튼은 공식 nextCursor가 있을 때만 활성화하며 한 페이지씩 교체한다. 프로필 사진 대신 기본 아이콘, 긴 표시명 줄바꿈, 실제 순위와 스푼/정보 없음, 마지막 조회 시각을 표시한다. 기존 채팅·하트와 비활성 방송 미리보기는 유지한다.

목록 자동 갱신은 없다. 브라우저는 중복 요청을 막고 탭 전환 시 이전 요청을 취소하며 오래된 응답을 무시한다. 서버도 동시 조회는 busy로 반환하고 동일 기능에 최소 2초 로컬 간격을 둔다. 이 값은 공식 rate limit이 아니다. 숨겨진 탭은 요청을 중단하고 목록을 지운다. 방송 변경/방송 대기/인증 해제는 기존 live.js/realtime.js 상태 전달을 통해 목록·시각·카운트를 초기화한다. 재표시는 명시적인 조회가 필요하다. 로그아웃을 다른 창에서 수행한 경우 실제 탐지는 기존 로컬 상태 조회 주기에 따른다.

조회 오류 시 남아 있는 목록은 이전 조회 자료라고 안내한다. 토큰/세션/방송 변경 상태에서는 이전 목록을 비운다. 동의 권한 부족은 계정 관리 페이지에서 직접 해제·재동의하도록 안내한다. 외부 JS/이미지/폰트 및 CSP 허용 호스트를 추가하지 않았다.

## 검증과 수동 확인

Mock HTTP Transport·가상 토큰·외부 소켓 차단만 사용한다. 정상·빈 응답, 커서, 방송 변경, 권한 부족, 만료/401 갱신, 403/404/429/500, 네트워크/timeout, 입력·스키마 검증, 세션 격리, 중복 조회, 개인정보 비노출 및 기존 회귀를 테스트한다. Node DOM 테스트는 탭 요청 취소, 늦은 응답 무시, XSS 텍스트, null 스푼, 커서 이동, 방송/인증/숨김 초기화를 확인한다. 실제 Chrome과 실제 방송 API는 실행하지 않았다.

Chrome 주소: http://localhost:8000/broadcast. 데스크톱·좁은 화면, Tab/방향키, 긴 닉네임, 로딩·오류·빈 상태, 탭 전환 취소, 새로고침 및 청취자 다음 페이지, 콘솔/CSP 오류를 수동 확인한다.

실제 방송 검증: 필요시 사용자가 직접 새 scope에 동의 → 방송 중 청취자/팬 탭 선택 → 청취자 전체 수와 페이지 수 구분 → 상위 30명·퇴장에 따른 공식 랭킹 변화 → 429 대기 → 방송 종료/새 방송 시 이전 자료 초기화 → 로그아웃·다른 브라우저 차단. SSE 연결이나 채팅 전송을 이 조회가 자동 실행하지 않는지도 확인한다.

K4.6 제안: chat.send 현재 토큰 권한 검사, 명시적 사용자 입력 POST와 CSRF, 길이·rate limit·금지/얼림 오류 처리, 중복 전송 방지 및 안전한 수동 테스트. 자동 응답과 방송 제어는 별도 범위로 남긴다.

최종 검증: pytest -q -W error 385 passed. JavaScript DOM/문법 검사, pip check, git diff --check 및 신규 파일 공백 검사가 통과했다. 브랜치·HEAD·.env.example 사용자 변경을 보존했으며 커밋·push하지 않았다.
