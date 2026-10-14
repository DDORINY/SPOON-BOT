# OAuth CSP: 로컬 POST와 공식 페이지 이동 분리

기존 prepare → POST start → 외부303 방식 대신 다음 흐름을 사용한다.

1. GET /auth/spoon/prepare: 기존 localhost Host·쿠키 검사와 세션 준비.
2. POST /auth/spoon/start: 기존 Host·Origin·CSRF·세션 검사. 유효 pending이 있으면 재사용하고 없으면 난수 state를 생성한다. 응답은 쿼리 없는 고정 로컬 /auth/spoon/continue로303 이동한다.
3. GET /auth/spoon/continue: 세션에 결합된 pending과 만료를 검증하고 서버가 생성한 공식 OAuth URL을 HTML escape한 링크로 표시한다. 임의 destination/query 입력은 거절한다. GET은 state를 생성하거나 실제 인증 요청을 하지 않는다.
4. 사용자 링크 클릭: 폼 제출과 별개로 공식 인증 화면을 탐색한다. 자동 redirect·JavaScript·meta refresh를 사용하지 않는다. noreferrer/noopener와 no-referrer를 적용한다.
5. 콜백: 기존 세션/state 검증, pending 선소비, 코드1회 교환 정책을 유지한다.

CSP·Host·Origin·CSRF 검사와 쿠키 정책은 제거하거나 완화하지 않는다. HTML의 기존 CSP와 서버 고정 지역별 공식 endpoint만 사용하며 외부 호스트를 추가하지 않는다. 준비 화면의 same-origin 정책은 기존 로컬400 수정대로 유지하고 중간 페이지는 no-referrer/no-store를 사용한다.

## 새로고침·뒤로 가기

중간 페이지 새로고침, 준비 화면으로 돌아간 뒤 다시 POST, 동시 시작 POST는 유효한 동일 pending을 재사용한다. state나 만료기한을 계속 새로 만드는 동작은 없다. 세션 만료/불일치/없는 pending/콜백 소비 후에는 중간 페이지가400을 반환하고 인증 링크를 주지 않는다. 이미 인증된 상태의 오래된 시작 폼도 거절한다.

링크를 사용자가 여러 번 클릭하는 것 자체를 서버가 감지·금지한다고 주장하지 않는다. 브라우저에 이미 표시된 링크는 회수할 수 없다. 대신 이전 state로 같은 코드 또는 새 코드가 돌아와도 최초 유효 콜백 이후 교환하지 않는다. no-store는 재조회 정책이고 브라우저 뒤로 가기 캐시까지 완전히 차단하는 보장이 아니다. 보안 경계는 서버의 세션·pending·코드 일회용 검사다.

## 수동 확인

수집을 중지하고 서버를 수정된 코드로 재시작한다. 사용자가 localhost 준비 폼을 제출하면 먼저 localhost 중간 페이지가 나타나야 한다. 사용자가 링크를 누르기 전에는 공식 인증 페이지로 이동하지 않는다. 봇 앱 등록 계정과 다른 방송 DJ 계정을 사용한다. 실제 연결 검증은 사용자의 수동 단계이며 이번 자동 작업에서는 수행하지 않는다.

민감한 링크/URL/state/쿠키/코드/토큰은 로그로 남기거나 공유하지 않는다. 문제가 있으면 쿼리 없는 로컬 경로·상태 코드·기존 고정 진단 코드만 확인한다. .env/사용자 .env.example/운영 DB를 수정하지 않는다. 토큰 영구 저장은 도입하지 않는다.

## 자동 검증

가상 자격 증명과 ASGI/Mock HTTP Transport를 사용한다. 중간 페이지 새로고침/뒤로 가기 상당 재조회·중복 및 동시 POST·공식 목적지·쿼리 주입 거부·Host/세션/만료 거부·콜백 중복 교환 방지·no-store/no-referrer·폼/스크립트 자동 이동 없음·로그 비노출을 검증한다. 기존 Mock OAuth와 Real SSE 회귀도 유지한다. 실제 Chrome CSP 동작이나 실제 provider 로그인/SSE 성공을 자동 검증했다는 뜻은 아니다.
