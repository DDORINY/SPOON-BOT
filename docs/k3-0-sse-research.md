# K3.0 — SPOON 공식 SSE 조사

조사일: 2026-10-14 (Asia/Seoul). 기준 main: `e33637f48be2840d233cf583018ad4e97a4f7cca`.
사용자 제공 앱 정보: **DOHA bot / 심사 대기 중**. 콘솔에서 확인한 승인 상태가 아니다.

공개 문서만 조회했다. 로그인·토큰 발급·실제 API/SSE 접속을 하지 않았으며 실제 이벤트를 관측하지 않았다. 기존 K2.1 코드, .env, .env.example, 운영 DB는 변경하지 않았다. 사용자 .env.example의 로컬 변경을 보존한다. 설계 제안은 [SSE 저장 설계](k3-0-sse-design.md)에 있다.

## 요청·권한

한국 주소는 `GET https://kr-openapi.spooncast.net/v1/live/events`, 일본은 `https://jp-openapi.spooncast.net/v1/live/events`이다. `Authorization: Bearer {access_token}`으로 서버에서 호출한다. 토큰이 DJ를 결정한다. 성공은 200(text/event-stream)이다. 근거: [엔드포인트](https://developers.spooncast.net/docs/api-reference/endpoints).

후원 수신 권한은 events.donation이다. 다른 권한은 events.chat / events.presence / events.like이며 동의받은 종류만 전달된다. presence에는 매니저 지정도 필요하다. 현재 제품은 events.donation과 방송 메타데이터용 live.read만 제안한다. 근거: [권한](https://developers.spooncast.net/docs/getting-started/choosing-permissions).

## 공식 이벤트 명세 요약

다음은 [공식 SSE 명세](https://developers.spooncast.net/docs/api-reference/live-events)의 필드 표 요약이다. `?`는 null 허용이다.

| 이벤트 | JSON 구조 |
|---|---|
| 공통 사용자 | user.id:string, user.nickname:string? |
| donation | user,amount:number,message:string?,time:string |
| chat | user,isDj:boolean,message:string,sentTime:string |
| presence | user,type:JOIN,fanRank:number?,isManager:boolean,favoriteTemperature:number?,time:string |
| like | user,type:FREE/PAID,totalAmount:number,amount:number,extraAmount:number,combo:number?,time:string |
| end | reason:LIVE_ENDED/TOKEN_EXPIRED/RECONNECT |

후원 amount는 총 스푼이며 재곱셈하지 않는다. 하트는 totalAmount로 집계한다. 시각은 ISO-8601 UTC이다. isDj는 chat에만 있다. user.id는 봇·DJ 조합의 식별자이며 nickname은 식별키가 아니다. donation에는 liveId·donationId가 없다.

요청에는 Accept:text/event-stream도 필요하다. heartbeat는 기본 15초 comment이다. event ID는 `{epochMillis}-{offset}`이다. Last-Event-ID 재개는 아직 지원하지 않는다. 네트워크 단절은 지수 대기(1,2,4…최대30초)+jitter, RECONNECT는 즉시, TOKEN_EXPIRED는 갱신 후 즉시 연결한다. 미방송404는 몇 초 대기한다. LIVE_ENDED 설명은 다음 방송 대기이나 연결 루프는 백오프 후 재시도를 제시한다.

전역 ID 유일성, 재전송 여부, 순서 보장, 동시 연결 상한은 미확인이다. 공식 예시값은 실측값이 아니다.

## 상태 코드·호출 한도

[공식 오류 명세](https://developers.spooncast.net/docs/api-reference/error-codes)의 status/detailCode로 분기하며 메시지 문구에 의존하지 않는다.

| 결과 | 처리 근거 |
|---|---|
| 401 / 없거나 OAPI_MNGR_0203 | 공통 문서는 refresh 1회 후 계속401이면 재인증 안내 |
| 403 / 없음 | 필요한 권한 재동의 |
| 403 / OAPI_MNGR_0209 | 입장 차단; DJ 확인까지 중단 |
| 404 / OAPI_MNGR_0301 | 미방송 상태; 대기 |
| 400·405·415 / OAPI_MNGR_0103 | 요청 오류; 같은 요청 반복 금지 |
| 500 / OAPI_MNGR_1002 | 서버 오류; 백오프 |
| 502 / OAPI_MNGR_2302 | 방송 서버 오류; 백오프 |
| 429 | 빈 본문 가능; Retry-After가 있으면 기다리고 없으면 백오프 |

SSE 실패 본문도 data: 프레임일 수 있으므로 전체 본문을 JSON으로 바로 해석하지 않는다. 게이트웨이의 빈 본문도 허용한다.

SSE 경로는 호출량 집계에서 제외되어 quota 헤더가 없다. 일반 API는 초당 DJ 연동별, 일일 앱/권한별 한도를 가진다. 수치 한도는 확인되지 않았다. 일반 헤더는 x-ratelimit-remaining / replenish-rate / burst-capacity / daily-limit / daily-remaining / daily-reset / daily-scope이다(각 항목에 x-ratelimit- 접두사). daily-reset은 epoch초다. 일반429 설명을 SSE에 대한 상시 quota 적용으로 해석하지 않는다. 그렇다고429가 절대 없거나 연결수가 무제한이라고 단정하지 않는다.

## 방송 재시작·복원

방송 회차는 `GET /v1/live`의 number형 liveId로 구분한다. startedAt로 대신 구분하지 않는다. live.read가 필요하며 재연결 후 메타데이터로 같은 방송인지 확인할 수 있다. SSE 필드와 이 조회 응답의 필드를 합쳐 공식 후원 payload인 것처럼 설명하지 않는다. 근거: [현재 방송 조회](https://developers.spooncast.net/docs/api-reference/live).

후원 이력을 완전히 복원하는 API는 조사 범위에서 확인하지 못했다. 팬 랭킹은 현재 방에 남은 청취자 중심·최대30명이라 누락 후원 원장 대체물이 아니다. 근거: [팬 랭킹](https://developers.spooncast.net/docs/api-reference/fans).

## 방송 고지와 개인정보

SSE 연결 시 서버가 봇 계정 이름으로 청취자 고지를 보낸다. 연결 직후·주기적으로 방 전체, 신규 입장 때 해당 청취자에게 표시하며 끌 수 없다. 매니저 지정/presence 권한과 무관하고 봇 호출량에서 차감되지 않는다. 주기의 구체 초수는 미확인이다. **등록 앱 이름 DOHA bot이 반드시 고지 표시 이름이라는 보장은 없다**; 공식 문서는 봇 계정 이름을 지정한다. 근거: [청취자 고지](https://developers.spooncast.net/docs/getting-started/listener-notice).

공식 고지를 별도의 데이터 보존 동의나 완전한 법적 적합성 증명으로 간주하지 않는다. 수집 최소화·보존기간·삭제 방식은 프로젝트 설계 제안이며 공식 보존정책으로 제시하지 않는다.

## 미확인과 문서 차이

- SSE의 ID 유일성 범위·중복 전달·서버 정렬·최대 프레임 크기·이벤트율·연결 TTL·동시 연결 수.
- 후원 number의 정수 여부·최솟값·상한, 취소/환불/정정 이벤트와 누락 이력 조회.
- LIVE_ENDED 표의 대기 설명과 루프의 재시도 설명 차이: 즉시 재연결을 피하는 WAITING 정책으로 설계한다.
- SSE401 설명은 재연동 안내, 공통 오류 문서는 refresh 1회 허용. 두 근거를 보존하고 무한 갱신/재시도는 금지한다.
- 앱 승인, 실제 Redirect URI 수용, PKCE/public client, 실제 토큰/권한, Windows↔WSL 동작은 여전히 미검증이다.

K3.1 mock 파서·정규화·상태 머신·임시 SQLite 구현은 가능하다. 실제 인증이나 SSE 구현이 승인된 상태라는 뜻은 아니다.

## 검증 범위

기존 66개 pytest를 경고=오류 조건에서 실행해 통과했고 pip check도 정상이다. 새 파서/스키마가 실행된 결과는 아니다. 문서 공백·민감정보 검사와 docs-only 커밋 범위를 별도로 검증한다.
