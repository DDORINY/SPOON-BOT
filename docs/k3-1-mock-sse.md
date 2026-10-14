# K3.1 — 오프라인 Mock SSE 구현

K3.0 문서가 main `da734e1`에 병합된 것을 확인하고 기존66개 테스트를 유지했다. DOHA bot 승인 완료는 사용자 확인 정보이며, 실제 자격 증명·SPOON HTTP·SSE는 이 구현에 사용하지 않는다.

## 파일 구조

- spoon/sse_parser.py: UTF-8/BOM·LF/CRLF/CR·comment·다중data·명시ID·프레임한도
- spoon/event_models.py: 공식 donation 필수 필드/null/type·Decimal·UTC·깊이/크기 검증, 개인 필드 즉시 폐기
- spoon/sse_source.py: 오프라인 바이트 소스 인터페이스와 dummy fixture
- spoon/stream_service.py: 단일 수집 task·bounded queue·단일 writer·gap·연결 상태/재시도
- spoon/donation_repository.py: Mock 전용 마커 SQLite·중복·충돌·최근 조회
- main.py / __main__.py: create_mock_app과 명시 DB 런처; 실제 .env·운영 DB 경로를 건너뜀
- auth_service.py / web/auth_routes.py: 비밀 값을 노출하지 않는 권한/강제 갱신 경계와 CSRF 시작·중지·인증 해제 연동
- web/static/donations.js 및 기존 dashboard: Mock 기록·관측 합계·불완전성 표시, textContent만 사용
- tests/test_sse_parser.py, test_donation_repository.py, test_mock_stream.py, test_mock_sse_api.py

## 실행

```bash
.venv/bin/python -m spoon_local --mock-auth --mock-db /tmp/spoon-local-demo/mock-sse.sqlite3
```

localhost:8000/auth/spoon/prepare에서 Mock 동의 후 /auth/spoon/collection에서 수집을 명시적으로 시작한다. 대시보드 조회만으로 수집을 시작하지 않는다. 기본 앱은 SSE 비활성이다. 시작/중지는 localhost Host, 정확한 Origin, 세션, CSRF를 확인한 POST만 허용한다. 반복 시작은 거절하고 중지/인증해제/종료 시 source와 task를 닫는다. --mock-auth만으로 실행하면 별도 DB를 요구하며 실행을 거절한다.

## DB와 안전 경계

Mock DB는 절대 경로·mock-sse.sqlite3 파일명·자체 application_id/user_version 마커를 요구한다. symlink·hardlink·static 디렉터리와 일반 DB를 거절한다. 새 파일은0600, 새 디렉터리는0700으로 만들며 기존 디렉터리 권한을 바꾸지 않는다. 초기화 후 재연결/재시작은 기존 기록과 fingerprint salt를 유지한다. 운영 schema_version/DB를 migration하지 않는다.

mock_metadata는 비교용 salt, broadcast_sessions는 모의 회차, donations는 최소 후원 기록, event_conflicts는 기존 기록+incoming fingerprint, collection_gaps는 최소 원인·발견/복귀시각을 저장한다. source_mode는 DB CHECK로 mock만 허용한다. 토큰·원본JSON·user.id·닉네임·메시지 컬럼은 없다. 전체 writer 작업(후원·회차·gap·복귀)은 하나의 큐에서 직렬 실행한다. 시작/종료의 active writer가 없는 상태에서는 metadata를 직접 직렬 저장한다.

금액은 Decimal→문자열이며 합계도 Decimal이다. 명시ID+금액+UTC 발생시각의 HMAC fingerprint로 같은ID 중복을 확인하고 다른 핵심값은 충돌로 남긴다. 충돌 입력은 합계에 추가하지 않는다. 이름/메시지만 다른 원문 차이는 수집 최소화를 위해 비교하지 않으며 보장 밖이다. ID가 없으면 각각 저장한다. 재접속 connection ID나 nickname으로 중복 제거하지 않는다.

salt가 같은 DB에 있어 암호화나 비밀정보 보호 수단으로 주장하지 않는다. 원문 개인정보를 fingerprint 입력에도 사용하지 않는다. API는 source event ID·salt·fingerprint·계정정보를 반환하지 않는다. SQL은 바인딩을 사용한다. source error text나 token/cookie를 로그에 기록하지 않는다.

## 상태와 복구

STOPPED, CONNECTING, LIVE, WAITING, BACKOFF, REFRESHING, AUTH_REQUIRED, PERMISSION_REQUIRED, BLOCKED, RATE_LIMITED, STORAGE_ERROR를 구분한다. 종료 사유와 실패 status를 따로 처리하며 TOKEN_EXPIRED에는 OAuth Mock 갱신을 요청한다. AuthService는 갱신 lock과 pair 비교로 동시에 바뀐 토큰의 중복 force-refresh를 방지한다. 401은 갱신1회 후 중지, 권한/차단은 무한 연결하지 않는다.

연결/바이트 제한시간10/45초, 네트워크 지수 백오프1초부터30초+jitter, 미방송대기5초부터30초를 적용한다. 즉시 RECONNECT는 최대3회로 로컬 안전장치를 둔다. EOF·손상·중단·재연결·overflow·저장실패는 gap으로 취급하며 completeness는 항상unknown이다. gap 복귀시각은 다시 수신했다는 뜻이고 누락 복원을 뜻하지 않는다. SQLite가 쓰기를 거절하면 gap 원인/시각을 메모리에 보관하고 같은 프로세스에서 회복 후 저장한다. 프로세스가 그 전에 죽으면 메모리 gap도 잃을 수 있다.

명시 중지는 미커밋 큐를 취소하고 해당 누락 가능성을 표시한다. DB 실패 후 자동으로 무한 저장 재시도하지 않고 사용자가 명시적으로 다시 시작한다. 회차는 MockConnection fixture 메타데이터이며 실제 liveId와 연결 race 검증은 구현하지 않았다. provider source 및 Last-Event-ID 재개는 없다.

## 대시보드

GET /api/donations는 limit1..100과 before_sequence 양수 cursor를 사용한다. 관측합계, source_mode=mock, coverage=observed_only, 충돌/gap/ID없음 건수와 최소 행을 반환한다. 기본 앱은 disabled+빈 목록이며 Mock 저장소를 조회하지 않는다. 화면은 익명·Mock 라벨, Decimal 문자열 금액, UTC 시각을 textContent로 표시한다. 요청 실패는 이전 자료일 수 있음을 알린다. 캡처는 미구현이다.

## 검증과 한계

최종 결과는 **142 passed**, 경고 없음, pip check 정상이다. 기존66개 테스트와 파서·Decimal·SQLite 장애/재시작·중복/충돌/ID없음·단일writer·네트워크단절·상태·강제갱신·CSRF·저장소분리·비밀정보 검사 테스트를 실행했다. Mock 경로에서 dotenv와 운영 DB 초기화 함수를 차단하고 socket connect도 금지했다. JavaScript에서 XSS 문자열이 HTML로 해석되지 않고 실제 source_mode 자료가 섞이지 않는 것을 별도 검증했다.

실제 loopback Uvicorn 시연에서 4건/12.25 스푼/충돌1건/ID없음2건을 저장·조회·중지했고 접근 로그의 code/state/쿠키/토큰 및 원문 비노출을 확인했다. 임시 DB만 사용하고 종료 후 정리했다. 실제 브라우저 렌더링, 실제 OAuth/SSE, 캡처는 검증 범위가 아니다.

한계: 단일 프로세스·모의 회차·개인정보 핵심값만 비교, 공급자 ID 범위 미확인, replay·누락복원 없음,30일 자동보존/안전삭제 미구현. 파일명·마커 검사는 로컬 악성 프로그램이나 DB를 임의로 바꾸는 공격을 완전히 막는 장치가 아니다. 실제 연결 전에 K2.0 PKCE/public client·Redirect URI·Windows↔WSL, credential 보관, OAuth HTTP 활성화, 공급자 이벤트ID/동시연결/정정정책을 확인해야 한다.
