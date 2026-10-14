# OAuth 시작 POST 400 진단

관측 로그 GET /auth/spoon/prepare=200, POST /auth/spoon/start=400만으로 실제 실패 요청의 헤더/세션 상태를 확정할 수 없다. 비밀 값을 요구하거나 실제 로그인/API를 실행하지 않고 코드와 가상 요청으로 검사했다.

## 발견한 정책 충돌과 수정

기존 HTML의 Referrer-Policy:no-referrer는 일반 폼 POST에서 Origin:null을 만들 수 있다. 서버는 정확한 http://localhost:8000 Origin만 허용하므로 정상 브라우저 폼도 거절될 수 있다. 근거는 [WHATWG Fetch의 Origin 헤더 생성 규칙](https://fetch.spec.whatwg.org/#append-a-request-origin-header)이다. 기존 테스트는 Origin을 직접 지정해 이 정책 문제를 발견하지 못했다.

Real/Mock HTML 폼 화면만 same-origin으로 수정한다. 같은 origin POST는 올바른 Origin을 보내고, 외부 origin에는 Referer를 보내지 않는 정책이다. 콜백/외부 이동의 redirect 응답은 기존 no-referrer를 유지한다. Host/Origin/CSRF 검사와 쿠키 HttpOnly/SameSite=Lax/path 정책은 변경하지 않는다. Origin:null을 허용하거나 Referer를 인증 근거로 대신 사용하는 수정이 아니다.

## 400 발생 조건

| 경계 | 원인 코드 | 조건 |
|---|---|---|
| middleware | host_missing / host_duplicate / host_mismatch | Host 하나가 정확히 localhost:8000이어야 함 |
| middleware | cookie_duplicate | Cookie 헤더 여러 개 또는 같은 인증 세션 쿠키 이름 중복 |
| POST | auth_disabled | 인증 서비스 없음 |
| POST | origin_missing / origin_duplicate / origin_null / origin_mismatch | Origin 하나가 정확히 http://localhost:8000이어야 함 |
| POST | content_type_invalid | application/x-www-form-urlencoded 이외 |
| POST | body_too_large / form_invalid | body4096바이트 초과, UTF-8/필드수 파싱 실패 |
| POST | session_cookie_missing | 해당 Real 세션 쿠키 없음 |
| POST | session_unavailable | 서버 재시작 등으로 메모리 세션 없음 |
| POST | session_expired | 준비 세션5분 만료 |
| POST | session_mismatch | 다른 탭/호스트 등으로 브라우저 쿠키와 현재 메모리 세션 불일치 |
| POST | csrf_missing_or_duplicate / csrf_mismatch | CSRF 단일 값 누락/중복 또는 일치하지 않음 |
| 시작 | auth_already_active | 이미 토큰이 있는 상태에서 오래된 시작 폼 제출 |
| 시작 | auth_start_failed | begin/Authorization URL의 기타 OAuthFailure; 검증 후 세션 만료 등 |

Referer는 로그인 성공에 필요한 헤더가 아니다. 없다고 거부하지 않고, 올바른 Referer라도 잘못된 Origin을 우회할 수 없다. localhost와127.0.0.1은 인증상 같은 호스트가 아니다. IP 주소 Host는 middleware에서, IP 주소 Origin은 POST 경계에서 거절한다. prepare200이면 해당 GET의 Host/중복쿠키 검사는 통과했다는 뜻이지만 POST에서의 Origin/쿠키/세션/CSRF 상태를 증명하지 않는다.

## 안전한 확인 방법

기존 실행 서버를 종료하고 수정된 코드로 동일한 Real OAuth 명령을 다시 실행한다. http://localhost:8000/auth/spoon/prepare를 새로 열어 최신 폼/세션을 사용한다. 127.0.0.1 주소나 오래된 탭의 폼을 제출하지 않는다. 재시작은 기존 메모리 토큰을 지우므로 진행 중 수집은 먼저 중지한다.

사용자가 수동으로 시작할 때 다시400이면 로컬 터미널의 `auth_rejected:origin_null` 같은 **코드만** 확인한다. 브라우저 응답의 X-Auth-Failure-Code도 같은 고정 코드를 반환한다. 로그에는 Host/Origin/Referer 원문, Client ID/Secret, CSRF/쿠키/코드/state/토큰을 넣지 않는다. Request body·HAR·콜백 주소·쿠키 값을 공유하지 않는다. 표에 맞춰 올바른 localhost 주소/새 준비 폼/5분 이내 제출/중복 쿠키 정리를 확인한다.

## 검증 범위

Chrome 형태의 navigation/same-origin 폼 헤더를 갖는 localhost POST가303으로 정상 통과하고, 준비 화면same-origin·redirect no-referrer 정책을 확인했다. Origin:null·IP 불일치·누락/중복 헤더·중복쿠키·세션/CSRF 실패·이미 인증된 오래된 폼은400과 원인코드를 검증했다. Referer를 Origin 대체로 허용하지 않는 것과 로그에 민감 값이 없는 것도 확인했다.

실제 Chrome/Chromium 실행 파일은 이 WSL 환경에서 찾지 못했다. 자동 테스트는 HTTP 헤더 회귀 테스트이며 실제 Chrome E2E 성공을 주장하지 않는다. 가상 자격 증명, ASGITransport, 소켓 차단을 사용하고 provider transport 호출 자체도 거부한다. 실제 .env/운영 DB를 열지 않는다. 실제 사용자 실패의 확정 원인은 다음 수동 요청에서 나온 진단 코드로 확인해야 한다.
