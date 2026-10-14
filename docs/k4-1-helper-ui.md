# K4.1 — 명시적 도우미 연결 UI

## 기존 기능과 원인

기존 RealStreamService/RealSSESource가 고정 GET /v1/live/events, RealAuthService access lease/rotation, events.donation gate, 단일 bounded queue/SQLite writer, Decimal 정규화, ID 중복/충돌/ID 없음 저장, gap, 재연결, 종료를 구현한다. 새 SSE 시스템이나 원문 개인정보 저장을 만들지 않았다.

기존 POST /auth/spoon/collection/start 및 /stop은 localhost Host·Origin·CSRF·세션을 검사하며 시작은 dj_notice=confirmed도 요구한다. 기존 HTML 폼 사용자는303 이동을 유지하고 프론트 fetch의 Accept:application/json만 안전한 상태 JSON을 받는다. duplicate start는 기존409다.

'준비되지 않았어요'는 --real-oauth만 실행했을 때 별도 Real SSE repository/controller가 없는 정상 설정 상태였다. --real-sse-db 절대 경로가 있어야 제어가 제공된다. 저장 경로를 임의 선택하거나 초기화하지 않았다. 코드에서 '닫기' 텍스트를 찾지 못했으므로 screenshot의 버튼 출처를 확정하지 못한다. 임의의 DOM/확장프로그램 요소를 삭제하지 않는다. 실제 DOM에서 버튼 id/생성 주체를 수동 확인해야 하며 전체 페이지/쿠키/링크를 공유하지 않는다.

## 실행 — 사용자 수동 단계

```bash
.venv/bin/python -m spoon_local --real-oauth \
  --real-sse-db /home/doha/projects/spoon-local/data/real/real-sse.sqlite3
```

이 명령은 사용자가 선택한 전용 저장 경로를 사용한다. 일반/Mock DB를 대신 지정하지 않는다. 실행 자체는 SSE에 연결하지 않고 첫 명시 시작에서만 marked repository를 초기화한다. 작업 중에는 이 명령을 실행하지 않았다. 서버를 재시작하면 메모리 로그인이 사라져 다시 인증해야 한다. 브라우저에서 DJ 고지 checkbox를 확인하고 연결 버튼을 눌러야 서버가 연결한다.

## UI와 API

GET /api/helper는 인증 브라우저·Host를 검사하며 기존 연결 상태를 표시용으로 매핑한다. 반환 필드는 state, reason(고정 코드), available, active, last_donation_at이다. available은 scope/실행 설정/권한·입장 차단 상태를 반영한다. 토큰·원문·세션값은 반환하지 않는다. OAuth-only면 not_enabled이고 연결 버튼 자체가 없다.

- stopped → disconnected
- connecting → connecting
- live → connected
- backoff/refreshing/rate_limited → reconnecting
- waiting → disconnected + active=true (방송 대기 예약은 유지)
- auth/permission/storage/blocked 등 → error, 안전한 한국어 설명
- disconnecting은 사용자 중지 POST 처리 중 프론트의 임시 상태다.

상단과 카드의 도우미 상태는 같은 함수로 표시한다. 방송 상태는 별도 GET /api/live/live.js에서 가져오며 도우미 상태로 방송 여부를 추측하지 않는다. 공식404에서는 WAITING이고 connected라고 표시하지 않는다. 방송 시작 전 연결을 원하면 명시 동의 후 대기할 수 있으며 대기 취소는 해제 버튼이다. 방송 조회30초 캐시가 SSE 연결 판정을 강제하지 않는다.

연결/해제 submit은 실제 POST 폼과 CSRF를 재사용하고, 중복 클릭을 막으며 요청 성공 후 서버 상태를 다시 조회한다. POST 실패/시간 초과는 재전송하지 않고 안전한 안내와 GET 동기화를 제공한다.5분 CSRF가 만료되면 페이지를 새로고침해 새 폼을 받는다. 자바스크립트 fetch는 same-origin 기본 cors 방식으로 Origin 검사를 통과하며 검사를 우회하지 않는다.

## 이벤트 갱신과 수명

기존 저장소 GET /api/donations를 재사용한다. 앞단은 보이는 탭에서 활성 도우미 상태3초/정지15초/실행 미설정60초로 조회한다. 후원 목록은 대시보드/후원 페이지에서 최초1회, connected일 때와 명시 연결/해제 뒤에만 읽는다. 기존 별도5초/3개 상태 polling을 없애고 GET /api/helper로 합쳤다. 목록 snapshot이 같으면 DOM을 다시 만들지 않아 중복 행을 추가하지 않는다. 갱신 지연은 조회 주기와 writer 커밋에 따라 생긴다.

후원은 익명 스푼·발생 UTC·수신 UTC만 표시한다. nickname/message/user.id를 다시 저장하거나 event liveId를 생성하지 않는다. last_donation_at은 writer가 실제 정규화 후원을 처리한 시각이다. 연결만 됐다고 후원 수신 성공을 표시하지 않는다. 최근20건은 전체 원장이 아니다.

탭을 닫거나 새로고침한다고 서버의 사용자 시작 SSE를 종료하지 않는다. 새로고침은 상태를 복원하고 추가 SSE를 만들지 않는다. 명시 해제·로그아웃·서버 종료 시 리소스를 닫는다. LIVE_ENDED는 기존 WAITING/다음 방송 대기, EOF는 backoff, TOKEN_EXPIRED는 공유 rotation, RECONNECT는 기존 즉시 재연결3회 안전장치를 유지한다. 나중에 자동 해제를 원하면 별도 정책 승인이 필요하다.

## 공식 근거 및 검증 한계

[공식 SSE](https://developers.spooncast.net/docs/en/api-reference/live-events)는404 미방송 대기, 종료 사유별 재연결과 서버측 봇 이름 고지를 설명한다. 고지는 끌 수 없고 연결이 끊겨도 즉시 퇴장 보장은 없다. Last-Event-ID 재개와 누락 복원은 보장하지 않는다.

테스트는 가상 토큰·Mock HTTP·임시 DB·소켓 차단으로 실행한다. 실제 SPOON/SSE/방송/후원/Windows 캡처를 실행하지 않는다. 현 환경에 Chrome/Chromium 실행 파일이 없어 브라우저 렌더링·콘솔 검증은 남아 있다. Node DOM stub로6상태·방송 대기·XSS·중복 표시·자동 네트워크 없음과 기존 전체 Python 회귀를 검증한다.

수동 확인은 별도 동의 후 실제 방송에서: 방송 중/도우미 미연결 구분 → 고지 확인/연결 → connected 확인 → 실제 관측 후원 확인 → 갱신/재연결/방송 종료 대기 → 명시 중지 → 로그아웃. 토큰/콜백URL/쿠키/HAR/원문 응답은 공유하지 않는다. '닫기' 출처와 데스크톱/좁은 화면도 확인한다. 후속은 실제 방송 회차 귀속/추가 scope 승인 설계이며 캡처 기능은 보류한다.
