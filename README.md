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

기본 앱은 인증 비활성(`not_configured`)이다. SPOON_CLIENT_ID/SECRET 등 환경변수가 있어도 OAuth에 사용하지 않는다. 실제 HTTP 클라이언트는 모든 요청을 거부하는 경계만 제공하며, 네트워크 구현은 없다. PKCE는 지원 미확인 상태로 활성화하지 않았다.

로컬 모의 인증을 실행하려면 다음 명령을 사용한다. 앱은 127.0.0.1:8000에만 바인딩한다.

```bash
.venv/bin/python -m spoon_local --mock-auth
```

`http://localhost:8000/auth/spoon/prepare`에서 Mock 시작 → 로컬 모의 동의 → localhost 콜백으로 진행한다. 대시보드의 준비 안내 링크도 같은 화면을 연다. 실제 로그인이나 방송 연결은 없다. 기본 DB 설정과 초기화는 기존과 같으며, 토큰은 DB와 .env에 기록하지 않는다.

세션은 5분, mock code는 60초·1회용이다. 시작·해제·모의 동의는 Host/Origin/CSRF 검증된 POST만 허용한다. 토큰은 단일 메모리 슬롯에 저장하고 종료 시 제거하므로 재시작하면 다시 mock 인증해야 한다. status 조회 시 만료 여유 구간에 들어온 토큰을 하나의 lock 안에서 갱신하며, 동시 요청도 한 번만 갱신한다. 백그라운드 갱신 워커는 없다. 실패하면 reauth_required, revoke 실패하면 revoke_unconfirmed로 표시하고 자동 갱신을 중지한다. 해제를 다시 시도할 수 있으며 후원 DB는 유지한다.

인증과 갱신은 단일 프로세스만 지원한다. 세션은 하나이므로 새 브라우저로 준비 화면에 들어가면 기존 pending 세션이 무효화될 수 있다. 사용된 code의 해시를 프로세스 동안 보관하며 4096개에 도달하면 더 교환하지 않는다. 메모리의 완전한 보안 삭제나 로컬 악성 프로그램 방어를 보장하지 않는다. HTTP localhost 쿠키는 HttpOnly/SameSite=Lax이며 Secure는 사용하지 않는 개발용 한계가 있다.

callback query는 ASGI scope에서 제거하고 Uvicorn access filter도 query를 제거한다. 앱은 provider 예외의 원문·헤더·토큰을 로그로 기록하지 않는다. 사용자 지정 로깅 미들웨어나 앞단 프록시를 추가하면 별도 검증이 필요하다. 브라우저 히스토리의 일회용 callback URL까지 완전히 없애는 기능은 아니다.

테스트는 실제 사용자 .env와 운영 DB 대신 임시 DB, 주입 설정, 모의 시계, 소켓 차단을 사용한다.

```bash
.venv/bin/python -m pytest -q -W error
.venv/bin/python -m pip check
```

실제 연결 전에는 앱 승인, localhost Redirect URI 등록과 Windows↔WSL 도달, PKCE/public client 사양, 안전한 토큰 영속화 정책을 확인해야 한다. 조사 근거는 `docs/k2-0-spoon-oauth-research.md`, 설계는 `docs/k2-0-spoon-oauth-design.md`에 있다.
