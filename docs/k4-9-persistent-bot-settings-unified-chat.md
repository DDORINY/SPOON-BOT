# K4.9 영구 봇 설정·통합 채팅

## 요구사항과 UI

중앙은 통합 수신 채팅, 오른쪽은 저장 설정·현재 실행·방송별 시작 확인·중지와 기존 후원/청취자 요약이다. 반복 설명은 기존 상세 영역을 이용한다. 저장과 실행은 별개로 표시한다. 설정 저장 완료는 현재 실행을 중지하며 새 확인을 요구한다. 즉시 중지 버튼은 다른 제어 요청 중에도 접근 가능하다. 기존 입력 전송 동의와 봇 계정 게시 안내를 제거하지 않는다.

## 공식 계약 재확인

2026-10-15 공개 문서 재확인:

- [이벤트 스트림](https://developers.spooncast.net/docs/api-reference/live-events): chat은 `user.id`, `user.nickname`, 최상위 `isDj`, `message`, `sentTime`이다. author 필드는 확인되지 않는다. `isDj=true`는 DJ 발화, false는 명세의 청취자 발화다. 이벤트 ID는 epochMillis-offset이며 버퍼 내 중복 판정에 사용한다. user.id는 봇/DJ 조합 범위의 식별자지만 OAuth DJ 신원 확인 정보는 아니므로 계정 소유권으로 추정하지 않는다.
- [봇 만들기](https://developers.spooncast.net/docs/en/getting-started/build-a-bot): 봇 자신의 전송은 그 봇의 SSE로 돌아오지 않는다. 전송 성공은 수신 확인이 아니다.
- [앱 등록](https://developers.spooncast.net/docs/en/getting-started/app-registration): 앱을 등록한 계정이 봇 계정이며 자신의 DJ 방송에 같은 계정으로 연결할 수 없다. 제한을 우회하지 않는다.

안정적인 OAuth DJ 계정 ID/봇 ID를 얻는 별도 계약은 여전히 미확인이다. 실제 BOT 수신을 합성하거나 nickname/message 비교로 추정하지 않는다. 다른 봇 역할도 확인된 식별 정보 없이는 보장하지 않는다. 후속 자동 환영은 events.presence 권한·계약과 별도 승인이 필요하며 이번에는 구현하지 않는다.

## 통합 채팅

기존 RealtimeBuffer의 공식 파싱 단계에서 role=dj/viewer와 role_basis=official_isDj를 추가한다. 사용자 ID는 계속 반환/저장하지 않는다. 기존 형식의 역할 근거가 부족하면 UI는 unknown으로 표시한다. 실제 bot 역할은 현재 제공하지 않는다.

시뮬레이터에 시청자/DJ/BOT/미확인 버튼을 추가했다. 동일 공식 바이트 파서/버퍼를 이용한 후 시뮬레이터 API에서만 별도 메모리 메타데이터로 role을 덧붙인다. simulation=true/role_basis=simulator_metadata로 실제 계약과 구분한다. 가상 BOT은 공식 수신을 의미하지 않는다. 자동 Mock ACK와 수동 Mock ACK는 실제/가상 수신 목록을 자동 생성하지 않는다. 명시적으로 생성한 가상 역할 이벤트만 목록에 나온다.

도착 순서(sequence)로 정렬한다. 발생/수신 시각은 별개이며 기존 잘못된 시각 이벤트 거부와 최근 100건/5분 정책을 유지한다. ID 중복/충돌은 기존 bounded best effort 처리이고 ID 없는 이벤트의 완전한 제거를 보장하지 않는다. 채팅과 실행 메시지는 SQLite에 저장하지 않는다. 표시 역할은 텍스트와 색상으로 구분하고 textContent를 사용한다.

## 설정 DB·영속성

일반 CLI 시뮬레이터 실행:

```bash
.venv/bin/python -m spoon_local --simulator
```

설정은 프로젝트 `local_settings/simulator/bot-settings.sqlite3` 전용 파일에 저장된다. 자동화/후원/채팅 데이터는 기존 메모리 경로를 유지한다. 실제 .env/후원 DB는 읽지 않는다. 파일 권한은 0600이다. 설정 파일은 기존 *.sqlite3 제외 규칙에 따라 Git에서 제외된다. 테스트용 create_simulator_app() 기본은 메모리 설정 DB로 실제 저장 파일에 접근하지 않는다.

명시적으로 다른 전용 파일 사용:

```bash
.venv/bin/python -m spoon_local --simulator --simulator-settings-db /절대/전용/경로/bot-settings.sqlite3
```

상대 경로, 다른 파일명, 심볼릭 링크 파일은 거부한다. 기존 외부 스키마 파일은 스키마를 변경하지 않고 거부한다. 운영 DB 마이그레이션을 실행하지 않았다.

bot_settings 단일 행: id=1, schema_version=1, version, updated_at(UTC), value(JSON). 저장값은 thanks/announcement, 각 템플릿, cooldown/interval만 포함한다. OAuth/token/password/Client Secret/공식 계정 ID 및 runtime 상태 필드는 없다. CREATE IF NOT EXISTS는 설정 전용 DB에서만 수행한다. 본 작업에서 실제 기본 저장 파일을 생성하거나 운영 데이터를 수정하지 않았다.

## 소유권·API

로컬 단일 운영자 시뮬레이터 프로필이며 DJ 계정별 격리를 주장하지 않는다. 첫 로컬 브라우저 세션이 현재 앱 인스턴스를 소유한다. 다른 세션은 읽기/쓰기 401, 페이지 점유는 기존 403이다. 재시작 후 로컬 접근자가 같은 프로필을 사용하므로 다중 사용자 환경에 적합하지 않다. 파일 권한/로컬 PC 접근 통제가 전제다. 실제 모드는 공식 DJ 소유권 미확정으로 설정 API/편집을 제공하지 않는다.

GET/POST `/api/simulator/bot-settings`: 보호된 조회 및 8개 필드 폼 저장(csrf/version/6개 설정). POST는 기존 Host/Origin/세션/CSRF와 스트리밍 본문 4096byte 상한을 사용한다. SQL 파라미터 바인딩 및 UPDATE WHERE version으로 버전 충돌을 409 처리한다. 검증 실패 400, 저장 실패 503이며 원본 예외/개인정보를 반환하지 않는다. UI 편집 버전은 폴링으로 덮어쓰지 않아 오래된 편집이 다른 저장을 덮지 않는다.

기존 automation configure 명령은 호환성 검증/중지만 수행하며 영구값을 바꾸지 않는다. 실제 저장은 versioned bot-settings API로만 한다. 설정 초기화는 별도 명시적 기존 reset 명령이다.

## 방송별 실행 확인

저장 ON이어도 runtime 엔진은 OFF로 시작한다. 가상 방송 시작 → 시작 버튼 → 현재 가상 방송/저장 기능을 표시 → 체크박스 동의 → 확인하고 시작 순서다. 서버는 방송 ID·설정 버전·현재 확인 context·동의 및 연결/오류 상태를 검사한다. context는 소비/중지/변경/끊김/종료 때 무효화되며 중복 시작을 거부한다.

실행 상태는 stopped/ready/running/error를 사용한다. ready는 시작 확인 가능한 가상 방송이며 실행 중을 의미하지 않는다. 끊김·오류는 안전하게 중지하고 재연결 후 자동 재개하지 않는다. 새 방송/시뮬레이터 초기화/프로그램 재시작에서도 과거 확인을 복원하지 않는다. 설정 수정·전체 중지·방송 종료는 예약/큐를 취소한다. 저장 ON 선호값은 중지/종료에도 보존한다.

K4.7의 UTF-16 200단위/{spoons}/10초 쿨다운/3초 간격/큐 20건/실패·불확실 자동 재전송 금지/첫 안내 지연/큰 시간 점프 비폭주를 유지한다. 엔진에 실제 HTTP 클라이언트나 OAuth 토큰을 주입하지 않는다.

## 검증과 후속

임시 전용 SQLite 두 연결·재열기·새 앱 인스턴스로 저장 유지와 실행 미복원을 확인한다. 기본 OFF/검증/버전 충돌/외부 스키마 보존/권한·CSRF/반복·과거 확인 거부/설정 변경 중지/끊김·재연결/역할별 가상 수신/ACK 비에코를 검증한다. 새 Python 프로세스 재조회까지 포함해 전체 479개 테스트가 통과했다. JavaScript 문법/pip check/git diff --check도 통과했다. pip 캐시 권한 경고만 있으며 의존성 오류는 없다.

실제 SPOON 호출·운영 DB 접근·실제 Chrome 렌더링은 수행하지 않았다. Chrome에서 `http://localhost:8000/broadcast`의 저장→종료→재시작→저장 ON/실행 중지→새 확인, 역할 이벤트, 모바일 및 125/150% 확대를 확인해야 한다. ‘닫기’ 출처는 여전히 미확인이다.

K5.x 실제 자동 전송에는 별도 승인, 공식 정책·scope, 소유권·DJ 분리, 방송별 명시적 동의와 전송 통제, 불확실 처리/비에코 UI 검증이 필요하다. 실제 BOT SSE 합성이나 계정 제한 우회는 후속에서도 허용하지 않는다.
