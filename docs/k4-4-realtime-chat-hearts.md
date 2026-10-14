# K4.4 실시간 채팅·하트 모니터

## 공식 계약 확인

- [공식 이벤트 명세](https://developers.spooncast.net/docs/en/api-reference/live-events)
- [권한 선택](https://developers.spooncast.net/docs/en/getting-started/choosing-permissions)

GET /v1/live/events의 기존 Bearer SSE 연결을 재사용한다. 서버가 동의받은 이벤트만 같은 연결로 전달하며 별도 구독 URL/쿼리는 없다. 채팅은 user.id/user.nickname, isDj, message, sentTime이다. 하트는 user, type(FREE/PAID), totalAmount, amount, extraAmount, combo(nullable), time이다. 누적에는 totalAmount를 사용한다. amount만 더하거나 이벤트 하나를 하트 하나로 계산하지 않는다. 하트와 스푼은 다른 단위다.

명시 SSE id는 epochMillis-offset 형식이다. Last-Event-ID 재개는 현재 지원되지 않으며 재전송/누락 복원을 보장하지 않는다. 관측 순서대로 로컬 순번을 부여하지만 제공자가 완전한 시간순 전달을 보장한다고 가정하지 않는다.

chat의 isDj와 이벤트 user.id는 공식 제공 필드지만 이번 단계에서 OAuth 계정 신원 확인이나 과거 기록 소유권 추정에 사용하지 않는다. user.id는 파싱 시 검증 후 버린다. nickname은 공식 이벤트로 받은 값만 표시하고 null은 익명 청취자로 표시한다.

## 기존 구조 및 변경 경계

기존 _StreamController._collect가 증분 SSEParser로 프레임을 만들고 donation만 SQLite writer 큐로 보냈다. chat/like는 기존 단일 수신 루프에 분기만 추가했다. 후원 큐·중복 인덱스·Decimal·DB 스키마는 변경하지 않았다. 별도 SPOON 연결, 채팅 보내기/자동 응답, 청취자/팬 조회는 없다.

기존 상태 STOPPED/WAITING/CONNECTING/LIVE/BACKOFF/REFRESHING과 token refresh·재연결을 유지한다. WAITING은 방송 시작 대기이며 접속 성공으로 표시하지 않는다. 새로고침/GET 조회는 provider 연결을 시작하지 않는다. 최초 연결과 다시 연결 시작은 기존 DJ 동의 POST 절차를 따른다. 제공자의 봇 고지를 끄지 않는다.

## OAuth 권한

OAuthSettings는 기본 live.read/events.donation을 유지하며 events.chat/events.like만 선택적으로 추가 허용한다. 실제 실행 설정 로더는 이 네 scope를 요청한다. 기존 Mock 실행과 테스트 기본 설정은 두 scope를 유지한다. TokenPair는 기존 최소 권한만 필수로 검증하므로 기존 후원 전용 토큰도 유효하다. 새 기능은 현재 토큰 scope에서 독립적으로 검사하며, 콘솔 승인으로 토큰에 권한이 있다고 추정하지 않는다. 권한 부족은 계정 해제 후 새 동의를 안내한다. 자동 재로그인이나 권한 확장은 하지 않는다.

기존 수집의 events.donation 조건은 유지한다. 이번 목적은 이미 동작하는 후원 연결에 선택적 이벤트를 추가하는 것이므로 채팅 전용 토큰 수집 모드를 새로 도입하지 않았다. listeners.read/fans.read/chat.send는 요청하거나 호출하지 않는다.

## 파서와 메모리 정책

realtime_events.py는 chat/like 검증과 RealtimeBuffer를 담당한다. JSON은 32KiB, 채팅 문자열은 4096자, 닉네임은 256자로 제한한다. 잘못된 JSON/중복 JSON 키/날짜/타입/수량은 내용 없는 invalid 카운터로 분류하고 다음 이벤트를 처리한다. 이 제한은 공식 최대값이 아니라 로컬 보호 정책이다.

채팅은 최근 100개·최대 300초만 보관한다. 하트는 현재 수집 요청에서 관측한 totalAmount와 이벤트 수만 메모리 집계한다. 재연결은 같은 수집 요청이므로 유지하지만 중지/새 시작/인증 해제/세션 변경/해당 권한 상실 시 초기화한다. 여러 방송에 걸친 수집 요청이면 방송별 통계로 해석하면 안 된다. 서버 재시작 후 데이터가 남지 않는다.

명시 ID는 종류와 함께 해시 키로 저장하고 정규화 payload 지문을 비교한다. 최근 최대 2048개·300초 안의 동일 ID/동일 내용은 건너뛰며 다른 내용은 conflict로 분류한다. 누락/빈 ID는 별개 이벤트로 처리한다. ID 재전송이 버퍼 범위를 벗어나면 중복을 완전히 막지 못한다. payload·사용자 ID·메시지·닉네임은 로그나 SQLite에 기록하지 않는다. 하트 수량은 Decimal 집계 후 문자열로 반환해 JS 정밀도 손실을 피한다.

## 소유권 및 API

GET /api/realtime-events는 기존 OAuth 브라우저 세션·Host·중복 쿠키 검사 뒤에만 접근 가능하다. 응답은 no-store이며 토큰/쿠키/공식 사용자 ID/파일 경로는 포함하지 않는다. 수집 시작 시 실제 인증 브라우저 세션 객체를 고정한다. 다른 브라우저는 401이며 그 요청 때문에 정당한 소유자의 버퍼를 지우지 않는다. 브라우저 세션이 재발급되거나 바뀌면 기존 수집을 정리하고 이전 데이터를 전달하지 않는다. 사용자가 다시 명시적으로 연결해야 한다. 이는 현재 수집의 소유권 통제이며 영구적인 DJ 계정 식별이 아니다.

응답: configured/state/active, generation/sequence, permissions.chat/like, 최근 chats, hearts.observed_amount/event_count/last_received_at_utc, 최근 채팅 수신 시각, 버퍼·중복·관측 범위 안내 및 숫자 진단 카운터. generation은 메모리 수집 요청 구분용이며 공식 방송/계정 ID가 아니다. 401은 재로그인, 403은 수집 소유 세션 변경이다. 기본·Mock 앱에서는 이 API로 실제 피드를 공개하지 않는다.

## 브라우저/UI

/broadcast에서만 realtime.js가 로컬 API를 호출한다. 수집 요청 활성 시 3초, 미연결 시 15초이고 숨겨진 탭은 조회를 쉰다. 호출은 이전 요청 완료 후 예약해 겹치지 않는다. SPOON 서버에 직접 접속하지 않는다. 기존 도우미·방송 정보 조회는 유지한다.

채팅은 안전한 textContent DOM으로 표시하며 이미 표시한 로컬 순번은 다시 추가하지 않는다. 사용자가 아래쪽에 있을 때만 새 메시지로 스크롤한다. 이전 내용을 읽는 중에는 위치를 유지하고 새 채팅 보기 버튼을 제공한다. 서버 메모리에 남은 범위 내에서 새로고침 복원한다. 인증/소유권 오류 시 이전 텍스트와 수량을 지운다. 네트워크 조회 오류는 마지막 표시가 최신인지 보장하지 않는 오류 안내를 제공한다.

방송 여부와 도우미 연결은 상단의 기존 독립 상태를 사용한다. 연결됨은 관측 이벤트 수신과 구분하고 마지막 채팅/하트 수신 시각을 따로 표시한다. 권한 부족·대기·미연결·빈 목록·조회 오류를 구분한다. 16:9 Windows 미리보기와 캡처 기능은 비활성 그대로다. CSP/CSRF/Origin/Host 검사를 완화하지 않았고 외부 리소스나 패키지를 추가하지 않았다.

## 검증 및 수동 확인

테스트는 가상 토큰·Mock HTTP Transport·외부 소켓 차단·임시 SQLite를 사용한다. 기존 후원/OAuth/회차/검색 회귀에 더해 파싱, 잘못된 입력, FREE/PAID totalAmount, 순서/중복/충돌/ID 없음, 버퍼·TTL, 권한 부족/상실, 재연결/방송 대기, 다른 브라우저/세션 재발급/인증 해제, 개인정보 로그·DB 비저장을 검증한다. Node DOM 테스트는 XSS 문자열, 중복 표시 방지와 수동 스크롤 상태를 검증한다. 실제 Chrome과 실제 SPOON 연결은 실행하지 않았다.

Chrome 확인: http://localhost:8000/broadcast에서 데스크톱/좁은 화면 레이아웃, 콘솔/CSP 오류, Tab 포커스, 긴 채팅 줄바꿈, 과거 채팅 읽기와 새 메시지 버튼, 권한 부족/대기/미연결 안내를 확인한다.

실제 방송 테스트는 별도 사용자 수동 단계다. 현재 토큰 권한을 확인하고 필요하면 직접 재동의 → DJ 고지 확인 후 연결 → 실제 채팅/하트 수신 시각·단위 → 재연결과 누락 가능성 → 연결 해제/재로그인 후 버퍼 초기화 → 다른 브라우저 접근 차단을 확인한다. 실제 후원·채팅·하트 발생은 이번 자동 테스트에서 수행하지 않는다.

K4.5는 현재 토큰의 listeners.read/fans.read를 독립 검사한 뒤 읽기 전용 청취자 커서 조회·팬 랭킹을 별도 구현한다. 공개 이벤트의 사용자 ID와 OAuth DJ 신원의 역할을 구분하고 영구 저장/회차 귀속은 추가 승인 및 공식 근거 확보 전 보류한다.
