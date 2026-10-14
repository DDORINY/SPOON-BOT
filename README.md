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
