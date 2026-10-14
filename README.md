# SPOON Local

개인용 SPOON 로컬 방송 도우미. Python 백엔드는 WSL2 Ubuntu 24.04에서 실행하고, 향후 화면 캡처 프로그램은 Windows 11에서 실행한다.

현재 구현은 health·상태 API, SQLite 초기화, .env 자동 로딩, 기본 로컬 대시보드이다. 공식 API 이벤트 수신, 방송 연결, 후원 기록, Windows 캡처는 이후 단계에서 구현한다. LLM, Ollama, 클라우드 서비스, 다중 DJ, Docker는 범위에서 제외한다.

## 설치

Python 3.12를 사용한다. 프로젝트 루트에서 실행한다.

```bash
cd /home/doha/projects/spoon-local
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## 실행

```bash
.venv/bin/python -m uvicorn spoon_local.main:app --host 127.0.0.1 --port 8000 --reload
```

다른 WSL 터미널에서 확인한다.

```bash
curl http://127.0.0.1:8000/api/health
```

응답: `{"status":"ok"}`. 앱 시작 시 `data/spoon_local.sqlite3`와 스키마 버전 테이블을 생성한다. import만으로 DB를 생성하지 않는다.

앱 시작 시 프로젝트 루트의 `.env`를 python-dotenv로 명시적으로 읽는다. 파일이 없어도 정상 실행하며, OS 환경변수가 파일 값보다 우선한다. 설정 로딩은 OS 환경변수를 수정하지 않는다. `${...}` 변수 보간은 하지 않는다.

`SPOON_LOCAL_DATA_DIR`로 데이터 디렉터리를 지정할 수 있다. 비어 있으면 프로젝트 루트의 `data/`를 사용하며, 상대 경로도 프로젝트 루트 기준이다. 현재 작업 디렉터리에 영향을 받지 않는다. `.env.example`을 참고하여 `.env`를 직접 작성하고 설정 변경 후 서버를 재시작한다. `.env`는 Git에 포함하지 않는다.

```bash
SPOON_LOCAL_DATA_DIR=/tmp/spoon-local-data .venv/bin/python -m uvicorn spoon_local.main:app --host 127.0.0.1 --port 8000
```

로컬 전용으로 실행한다. Windows 브라우저의 `http://localhost:8000` 접근과 Windows 캡처 연동은 별도 검증이 필요하다.

## 테스트

```bash
.venv/bin/python -c 'import spoon_local; import spoon_local.main'
.venv/bin/python -m pytest
git diff --check
```

API 테스트는 httpx ASGITransport와 명시적인 lifespan 실행을 사용한다. 실제 사용자 .env를 읽지 않도록 설정을 주입하고 임시 DB에서 실행한다. 설정 테스트는 임시 프로젝트 루트와 임시 .env를 사용한다. 앱 시작 시 DB 초기화와 health·상태 API, HTML·정적 파일 제공도 확인한다. DB 테스트는 임시 파일에서 반복 초기화 및 데이터 저장·조회를 확인한다.

## 디렉터리

- `src/spoon_local/`: 백엔드, 설정, SQLite 초기화
- `src/spoon_local/spoon/`: 향후 공식 API 연동
- `src/spoon_local/capture/`: 향후 Windows 캡처 연결
- `src/spoon_local/web/static/`: 한국어 다크 테마 로컬 대시보드
- `windows_capture/`: 향후 Windows 네이티브 프로그램
- `tests/`: 자동 테스트
- `docs/`: 설계 문서
- `data/`: 실행 시 생성되는 로컬 DB, Git 제외

패키지 설치에는 패키지 저장소 접근이 필요하다. 현재 앱 실행에는 외부 서비스가 필요하지 않다. 의존성 버전은 범위로 지정했으며 잠금 파일은 아직 없다.

## 대시보드 및 상태 API

브라우저에서 `http://127.0.0.1:8000/`에 접속한다. 외부 CDN·폰트 없이 정적 HTML/CSS/JavaScript를 제공하며, `/api/status`를 5초 간격으로 확인한다. 요청 실패 시 서버를 오프라인으로 표시하고 다른 상태는 확인 불가로 표시한다. 기본 상태는 미연결·대기 중·미설정이며, 최근 후원·캡처는 데이터 없음, 설정·로그는 준비 중이다. 가짜 데이터나 동작하지 않는 버튼은 제공하지 않는다.

```json
{
  "server": "online",
  "spoon_auth": "not_configured",
  "broadcast": "disconnected",
  "donation_events": "not_configured",
  "capture_agent": "not_configured"
}
```

API에는 설정 값, DB 경로, 인증정보를 포함하지 않는다. `.env`와 DB는 정적 파일 경로 밖에 있다. 실행 주소는 `127.0.0.1`을 사용하며 `0.0.0.0`으로 바인딩하지 않는다.

## K2.1 오프라인 Mock 인증

기본 앱은 인증 비활성(`not_configured`)이다. SPOON_CLIENT_ID/SECRET 등 환경변수가 있어도 OAuth에 사용하지 않는다. Mock 경로는 외부 HTTP 요청을 사용하지 않는다. 실제 HTTP 클라이언트는 아래의 명시적 Real 모드에서만 사용한다. PKCE는 지원 미확인 상태로 활성화하지 않았다.

로컬 모의 인증을 실행하려면 다음 명령을 사용한다. 앱은 127.0.0.1:8000에만 바인딩한다.

```bash
.venv/bin/python -m spoon_local --mock-auth --mock-db /tmp/spoon-local-demo/mock-sse.sqlite3
```

`http://localhost:8000/auth/spoon/prepare`에서 Mock 시작 → 로컬 모의 동의 → localhost 콜백으로 진행한다. 대시보드의 준비 안내 링크도 같은 화면을 연다. 실제 로그인이나 방송 연결은 없다. Mock 실행은 명시한 전용 DB만 초기화하고 실제 .env·운영 DB를 사용하지 않는다. 토큰은 DB와 .env에 기록하지 않는다.

세션은 5분, mock code는 60초·1회용이다. 시작·해제·모의 동의는 Host/Origin/CSRF 검증된 POST만 허용한다. 토큰은 단일 메모리 슬롯에 저장하고 종료 시 제거하므로 재시작하면 다시 mock 인증해야 한다. status 조회 시 만료 여유 구간에 들어온 토큰을 하나의 lock 안에서 갱신하며, 동시 요청도 한 번만 갱신한다. 백그라운드 갱신 워커는 없다. 실패하면 reauth_required, revoke 실패하면 revoke_unconfirmed로 표시하고 자동 갱신을 중지한다. 해제를 다시 시도할 수 있으며 후원 DB는 유지한다.

인증과 갱신은 단일 프로세스만 지원한다. 세션은 하나이므로 새 브라우저로 준비 화면에 들어가면 기존 pending 세션이 무효화될 수 있다. 사용된 code의 해시를 프로세스 동안 보관하며 4096개에 도달하면 더 교환하지 않는다. 메모리의 완전한 보안 삭제나 로컬 악성 프로그램 방어를 보장하지 않는다. HTTP localhost 쿠키는 HttpOnly/SameSite=Lax이며 Secure는 사용하지 않는 개발용 한계가 있다.

callback query는 ASGI scope에서 제거하고 Uvicorn access filter도 query를 제거한다. 앱은 provider 예외의 원문·헤더·토큰을 로그로 기록하지 않는다. 사용자 지정 로깅 미들웨어나 앞단 프록시를 추가하면 별도 검증이 필요하다. 브라우저 히스토리의 일회용 callback URL까지 완전히 없애는 기능은 아니다.

테스트는 실제 사용자 .env와 운영 DB 대신 임시 DB, 주입 설정, 모의 시계, 소켓 차단을 사용한다.

```bash
.venv/bin/python -m pytest -q -W error
.venv/bin/python -m pip check
```

실제 연결 전에는 앱 승인, localhost Redirect URI 등록과 Windows↔WSL 도달, PKCE/public client 사양, 안전한 토큰 영속화 정책을 확인해야 한다. 조사 근거는 `docs/k2-0-spoon-oauth-research.md`, 설계는 `docs/k2-0-spoon-oauth-design.md`에 있다.

## K3.1 오프라인 Mock SSE 후원 시연

Mock 경로는 실제 `.env`를 읽지 않으며 운영 SQLite 초기화도 하지 않는다. 실행에는 **절대 경로의 별도 DB**가 필수다. 이름은 `mock-sse.sqlite3`이어야 한다. 기존 DB는 이 앱의 Mock 마커와 버전이 있어야 열 수 있으며, 이름이 맞아도 일반/운영 DB이면 거절한다. DB·부모 경로의 symlink, 파일 hardlink, 정적 파일 디렉터리도 허용하지 않는다. 기본 실행은 SSE 비활성이다.

```bash
.venv/bin/python -m spoon_local --mock-auth --mock-db /tmp/spoon-local-demo/mock-sse.sqlite3
```

1. `http://localhost:8000/auth/spoon/prepare`에서 Mock 인증을 시작하고 로컬 모의 동의한다.
2. `http://localhost:8000/auth/spoon/collection`에서 **Mock 수집 시작**을 누른다. 인증 전에는 수집할 수 없다.
3. `http://localhost:8000/`에서 최근 Mock 후원과 관측 합계를 확인한다.
4. 같은 수집 관리 화면에서 **Mock 수집 중지**를 누른다. 인증 해제와 앱 종료도 수집을 중지하며 기록은 보존한다.

fixture는 실제 후원이 아니다. 4건/12.25 스푼을 저장하고 같은 ID 중복 1건을 제외하며, 다른 금액의 같은 ID 충돌 1건과 ID 없는 2건을 보여준다. 충돌 입력은 합계에 넣지 않는다. fixture 종료 후 상태는 Mock 방송 대기이다. 재시작 시 기존 Mock 기록을 보존하지만 인증은 다시 필요하다. `/tmp` 자체는 OS에 의해 정리될 수 있으므로 영구 저장으로 보장하지 않는다.

API: `GET /api/donations?limit=20&before_sequence=...`. 최대100개이며 `source_mode=mock`, `coverage=observed_only`, 금액 문자열, 수신 순번과 발생/수신 UTC 시각을 반환한다. 원본 사용자 ID·닉네임·메시지·event ID·fingerprint·토큰은 반환하지 않는다. 기본 앱은 `source_mode=disabled`와 빈 목록을 반환한다.

DB 테이블은 mock_metadata, broadcast_sessions, donations, event_conflicts, collection_gaps이다. 토큰은 없다. 명시 ID가 같고 금액·발생시각도 같으면 중복, 핵심값이 다르면 충돌을 기록한다. 개인정보 필드만 다른 경우를 구분하기 위해 원문을 저장하지 않으므로 충돌 탐지에도 한계가 있다. ID 없는 이벤트는 각각 저장한다. 모의 session 메타데이터만 사용하며 실제 liveId 검증이나 회차 귀속을 구현하지 않았다.

수집은 단일 controller/task, 제한 큐, 단일 writer를 사용한다. queue overflow·손상된 프레임·중단·재연결·저장 실패는 gap 또는 저장 실패 표시를 남긴다. DB 장애로 gap조차 기록하지 못하면 메모리에 최소 원인을 보관해 같은 프로세스에서 복구 후 저장한다. 그 상태에서 프로세스가 죽으면 gap 기록도 소실될 수 있다. Last-Event-ID replay 및 누락 후원 복원은 제공하지 않는다. 합계는 완전한 후원 원장이 아니다.

금액은 Decimal로 검증하고 canonical 문자열로 저장한다. number의 정수/범위는 공식 보장으로 가정하지 않으며 로컬 자릿수/크기 제한을 적용한다. 저장은 금액·시각·중복 메타데이터만 사용하고 채팅/입장/하트도 저장하지 않는다. HMAC fingerprint의 salt는 Mock DB에 함께 보관하며 암호화·기밀성 보장이 아니라 안정적인 비교용이다. 30일 자동 삭제와 캡처는 아직 구현하지 않았다.

구현·검증 요약은 `docs/k3-1-mock-sse.md`에 있다. 앱 승인 완료는 사용자 확인 정보이며, 이 실행이 실제 OAuth/SSE 연결을 활성화하지 않는다.

## K2.2 명시적 Real OAuth

실제 인증은 별도 모드에서만 사용할 수 있다. 기존 기본/Mock 명령은 유지되며 Mock은 실제 .env를 읽지 않는다.

```bash
.venv/bin/python -m spoon_local --real-oauth
```

Real 모드는 로컬 .env의 SPOON_CLIENT_ID, SPOON_CLIENT_SECRET, SPOON_REGION, SPOON_REDIRECT_URI를 읽고 OS 환경변수를 우선한다. 누락/잘못된 설정은 시작을 거절한다. 설정 값은 출력하지 않는다. callback은 정확히 `http://localhost:8000/auth/spoon/callback`이어야 한다.

서버 실행만으로 로그인·외부 요청을 하지 않는다. `http://localhost:8000/`에서 실제 SPOON 로그인 링크를 열고, localhost 준비 화면의 POST 버튼을 사용자가 직접 눌러야 공식 동의 화면으로 이동한다. 토큰 교환/갱신/폐기는 서버에서만 실행하고 메모리에만 저장한다. Real 모드는 DB를 열거나 초기화하지 않으며 실제/Mock 후원을 조회·수집하지 않는다. 실제 수집 route와 버튼도 비활성이다.

로그아웃은 토큰 폐기를 시도한 후 로컬 토큰/세션을 정리한다. 폐기 실패는 미확인으로 표시한다. 단순 서버 종료는 원격 토큰을 폐기하지 않으므로 먼저 인증 관리에서 로그아웃하는 것이 좋다. PKCE/public client 조건과 이 PC의 Chrome↔WSL callback은 미확인이다.

실제 자격 증명과 브라우저 로그인은 이번 작업에서 실행·검증하지 않았다. 자동 테스트는 가상 설정과 Mock HTTP Transport만 사용한다. 공식 근거·실행 전 체크리스트·수동 테스트는 [K2.2 문서](docs/k2-2-real-oauth.md)를 따른다.

## 실제 SSE 수집 (K3.2)

기본 실행과 OAuth-only 실행에서는 수집하지 않습니다. 사용자가 DJ 계정 인증 후 고지 확인과 시작 버튼을 눌러야 실제 SSE에 접속합니다. Mock DB와 별도인 절대 경로를 사용하세요.

```bash
.venv/bin/python -m spoon_local --real-oauth --real-sse-db /home/doha/projects/spoon-local/data/real/real-sse.sqlite3
```

실행·중지, 최소 수집, 누락 위험과 수동 점검은 [K3.2 문서](docs/k3-2-real-sse.md)를 참고하세요. 실제 방송 E2E는 자동 실행하지 않습니다.
