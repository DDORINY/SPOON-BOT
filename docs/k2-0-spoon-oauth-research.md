# K2.0 — SPOON 공식 OAuth 조사

조사일: 2026-10-14 (Asia/Seoul). 기준: main `58b4261e7e128b833ec67286675a4b04dcdb3ffb`.

이 문서는 공개 공식 문서의 조사 결과이다. 실제 로그인, 앱 등록, 토큰 발급, SSE 접속은 하지 않았다. 콘솔 내부와 사용자 계정에서의 동작은 검증하지 않았다. 설계 제안은 [별도 문서](k2-0-spoon-oauth-design.md)에 분리했다.

## 조사 방법과 현재 코드

AGENTS.md 및 config.py, main.py, database.py, pyproject.toml, src/tests 파일 구조를 확인했다. 현재 설정은 프로젝트 루트 .env를 읽고 OS 환경변수를 우선한다. 앱은 lifespan에서 SQLite를 초기화하고 health/status 및 정적 대시보드를 제공한다. SPOON 패키지는 자리만 있으며 인증 기능은 없다. 실제 .env 내용과 운영 DB는 읽거나 수정하지 않았다.

웹 도구의 한국어 문서 직접 열기는 실패했다. 공개 HTML GET으로 소개·앱 등록·사용자 동의·권한·토큰·엔드포인트·청취자 고지 본문을 확인하고, 검색으로 공식 이벤트 명세도 확인했다. 추측한 `/api-reference/event-stream`은 404였으며, 공식 링크의 실제 경로는 `/api-reference/live-events`이다. 어떠한 authorize/token/revoke API도 호출하지 않았다.

## 공식 확인 사항

| 항목 | 확인된 사양 | 근거 |
|---|---|---|
| 앱 등록 | 개발자 센터에서 Spoon 계정으로 로그인 → 콘솔 새 앱 등록 → 이름, 리디렉션, 목적, 권한 입력 → 심사 승인 | [앱 등록](https://developers.spooncast.net/docs/getting-started/app-registration) |
| 계정 분리 | 앱 소유 계정이 봇 계정이다. DJ 본인 계정으로 등록한 앱은 그 DJ 방송에 연동할 수 없다. 별도 봇 계정 필요 | [앱 등록](https://developers.spooncast.net/docs/getting-started/app-registration) |
| 자격 증명 | Client ID와 Client Secret 필요. Secret은 승인 후 확인하며 서버에만 보관. 재발급 시 기존 Secret 즉시 폐기 | [앱 등록](https://developers.spooncast.net/docs/getting-started/app-registration) |
| 등록 이름 | `spoon` 또는 `스푼`을 포함한 이름 금지. 저장소/UI 이름 SPOON-BOT과 등록 앱 이름은 구분해야 한다 | [앱 등록](https://developers.spooncast.net/docs/getting-started/app-registration) |
| Redirect URI 등록 | HTTPS가 원칙. 개발 중 `http://localhost`와 포트 허용 | [앱 등록](https://developers.spooncast.net/docs/getting-started/app-registration) |
| Redirect URI 일치 | 등록값과 authorize 요청값이 문자 단위로 같아야 하며, code 교환 때도 동의 요청값 사용 | [사용자 동의](https://developers.spooncast.net/docs/getting-started/user-authorization), [토큰](https://developers.spooncast.net/docs/api-reference/tokens) |
| Grant | Authorization Code 및 Refresh Token. authorize의 response_type은 code | [사용자 동의](https://developers.spooncast.net/docs/getting-started/user-authorization), [토큰](https://developers.spooncast.net/docs/api-reference/tokens) |
| 동의 요청 | client_id, redirect_uri, scope 필수. scope는 공백 구분. state 권장, 최대 512바이트 | [사용자 동의](https://developers.spooncast.net/docs/getting-started/user-authorization) |
| PKCE | 읽은 공식 authorize/token 명세에 code_challenge, code_challenge_method, code_verifier가 없어 지원 여부 미확인. 미지원으로 단정하지 않음 | [사용자 동의](https://developers.spooncast.net/docs/getting-started/user-authorization), [토큰](https://developers.spooncast.net/docs/api-reference/tokens) |
| 방송 대상 | 토큰이 동의한 DJ 한 명을 지정. DJ 지정 query/path가 없다 | [엔드포인트](https://developers.spooncast.net/docs/api-reference/endpoints) |

### 지역별 주소

| 용도 | 한국 | 일본 |
|---|---|---|
| Authorization URL (브라우저 이동) | `https://developers.spooncast.net/kr/oauth/authorize` | `https://developers.spooncast.net/jp/oauth/authorize` |
| Token URL (서버 POST) | `https://kr-openapi.spooncast.net/v1/oauth/token` | `https://jp-openapi.spooncast.net/v1/oauth/token` |
| Revoke URL (서버 POST) | `https://kr-openapi.spooncast.net/v1/oauth/revoke` | `https://jp-openapi.spooncast.net/v1/oauth/revoke` |

앱·DJ 지역에 맞는 주소를 사용한다. OAuth POST는 form-urlencoded이며 Client ID(UUID)와 Secret의 Basic 인증을 우선한다. form 자격 증명 방식도 문서에 있지만 설계에서는 Basic으로 통일한다. 근거: [사용자 동의](https://developers.spooncast.net/docs/getting-started/user-authorization), [엔드포인트](https://developers.spooncast.net/docs/api-reference/endpoints), [토큰](https://developers.spooncast.net/docs/api-reference/tokens).

### 토큰 수명과 실패 정책

- code는 60초 유효·1회 사용. 같은 code를 재사용하면 해당 연동의 토큰 전체가 폐기될 수 있다. 응답 유실 시 같은 code를 재시도하지 않는다.
- Access Token 기본 수명은 3600초, Refresh Token 기본 수명은 30일. 실제 access 만료는 응답 expires_in으로 계산한다.
- refresh 성공 시 이전 refresh는 즉시 무효화되므로 새 access/refresh 쌍을 저장한다. 이전 access는 자연 만료한다. 매 갱신 응답 scope를 다시 확인한다.
- 문서는 만료 전에 넉넉한 갱신을 권장하면서 1시간 전 예시를 든다. 기본 access 수명도 1시간이므로 이 예시를 상수로 쓰면 안 된다.
- refresh token revoke는 같은 연동의 access도 폐기한다. revoke와 DJ의 동의 해제는 다르다. revoke는 동의를 남긴다.
- OAuth 오류는 error 코드로 처리한다. invalid_grant는 재동의, invalid_client는 자격 증명·앱 상태 확인 대상으로 분류한다. error_description으로 분기하지 않는다.

근거: [공식 토큰 명세](https://developers.spooncast.net/docs/api-reference/tokens). 기본값은 고정 보장으로 간주하지 않으며 refresh 만료시각 반환 필드·재사용 탐지 정책은 미확인이다.

### 필요한 최소 권한과 계정 역할

| 목적 | 요청할 scope | 이번 제품의 판단 |
|---|---|---|
| 현재 방송 정보 | live.read | 방송 상태 관리에 필요 |
| 후원 이벤트 | events.donation | 후원 기록과 후원 기반 캡처에 필요 |
| 채팅 전송 | chat.send | 현재 목표에 불필요 |
| 채팅·입장·하트·팬/청취자 목록 | events.chat / events.presence / events.like / fans.read / listeners.read | 현재 목표에 불필요 |

최종 최소 요청은 `live.read events.donation`으로 제안한다. 등록된 scope만 요청 가능하며 DJ의 승인과 실제 token scope 모두 확인해야 한다. DJ는 본인 방송 권한에 동의하고, 별도 봇 계정은 앱 소유자이다. 봇 계정용 두 번째 OAuth 토큰을 요구하는 흐름은 문서에 없다. 근거: [권한 고르기](https://developers.spooncast.net/docs/getting-started/choosing-permissions), [앱 등록](https://developers.spooncast.net/docs/getting-started/app-registration).

후원은 `GET /v1/live/events` SSE의 donation 이벤트이며 events.donation 동의가 필요하다. 방송이 꺼져 있으면 404이며 미동의 이벤트는 필터링된다. 후원 scope에 매니저 지정이 필요하다는 규칙은 확인되지 않았다; presence에만 별도 지정 요구가 명시되어 있다. 현재 SSE 이벤트 재개는 지원되지 않는다고 명세에 설명되어 있으므로 단절 중 후원 복원을 보장하면 안 된다. 근거: [이벤트 스트림](https://developers.spooncast.net/docs/api-reference/live-events), [권한 고르기](https://developers.spooncast.net/docs/getting-started/choosing-permissions).

향후 SSE 연결은 청취자에게 봇 이름의 고지를 발생시킨다. 비활성화할 수 없으며 별도 chat.send를 요청해야 고지가 나간다는 조건은 없다. 개인용 프로그램이라도 이 방송상 효과를 사용자에게 설명해야 한다. 근거: [청취자 고지](https://developers.spooncast.net/docs/getting-started/listener-notice).

## 사용자 준비 사항 — 이후 단계용

1. DJ 계정과 다른 봇 전용 Spoon 계정 및 사용 지역을 준비한다. 이 문서 작업에서는 로그인하거나 정보를 입력받지 않는다.
2. Spoon을 포함하지 않는 등록 앱 이름(예: Local Broadcast Helper)과 목적을 정하고 최소 scope를 선택한다. 실제 콘솔 권한 제공 여부는 심사 때 확인한다.
3. `http://localhost:8000/auth/spoon/callback`을 후보로 정확히 등록 가능한지 확인하고 승인받는다. 이 경로는 설계안이며 아직 구현되지 않았다.
4. 승인 후 Client ID/Secret을 사용자 PC의 보호된 서버 설정으로만 관리한다. 채팅·문서·Git·브라우저에 붙여 넣지 않는다.
5. Windows 브라우저 → WSL localhost 콜백 도달과 PKCE 사양을 확인한다. 필요 시 개발자 센터 지원에 문의한다.

## 미확인 및 구현 진입 조건

| 미확인 항목 | 영향 / 확인 방법 |
|---|---|
| PKCE S256 지원·필수 여부, public client 지원 | OAuth 보안과 로컬 앱 배포 모델. 공식 추가 사양 또는 지원 답변 필요 |
| http://127.0.0.1 허용, 동적 포트·복수 Redirect URI | localhost 외 주소나 포트 대체를 가정하지 않음. 콘솔 규칙 확인 |
| Windows localhost와 WSL 콜백 연결 | 문서상 허용과 실제 네트워크 동작은 별개. 다음 단계 로컬 검증 필요 |
| 실제 앱 승인 가능 여부·권한 제공·심사 기간 | 공개 안내만 확인. 콘솔·심사 결과 확인 필요 |
| refresh 수명 연장 방식·유실/재사용 세부 정책 | 자동 retry 금지와 재동의 전략 필요 |
| 사용자 식별 전용 API/OIDC·id_token | 문서에서 확인하지 못함. JWT 해석으로 DJ 신원을 만들어내지 않음 |
| 배포용 confidential/native client 분류 | 개인 PC에서 Secret 저장만으로 배포형 앱 비밀성이 확보되지는 않음 |

공식 문서에서 빠진 내용을 일반 OAuth 관행으로 채우지 않았다. K2.1의 오프라인 설정·state·mock token client·메모리 저장소 테스트는 구현 가능하다. 실제 로그인·발급과 재시작 후 자동 인증은 localhost 수용 여부, PKCE 및 저장소 정책을 확정한 다음 단계로 분리해야 한다.
