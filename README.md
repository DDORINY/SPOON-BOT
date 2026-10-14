# SPOON Local

개인용 SPOON 로컬 방송 도우미. Python 백엔드는 WSL2 Ubuntu 24.04에서 실행하고, 향후 화면 캡처 프로그램은 Windows 11에서 실행한다.

현재 구현은 health API와 SQLite 초기화뿐이다. 공식 API 이벤트 수신, 방송 상태, 후원 기록, Windows 캡처, 대시보드는 이후 단계에서 구현한다. LLM, Ollama, 클라우드 서비스, 다중 DJ, Docker는 범위에서 제외한다.

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

`SPOON_LOCAL_DATA_DIR` 환경변수로 데이터 디렉터리를 지정할 수 있다. 기본 `data/` 경로는 실행 디렉터리를 기준으로 한다. `.env.example`은 설정 이름의 예시이며 `.env` 파일을 자동으로 읽지는 않는다.

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

API 테스트는 앱 시작 시 DB 초기화와 health 응답을 확인한다. DB 테스트는 임시 파일에서 반복 초기화 및 데이터 저장·조회를 확인한다.

## 디렉터리

- `src/spoon_local/`: 백엔드, 설정, SQLite 초기화
- `src/spoon_local/spoon/`: 향후 공식 API 연동
- `src/spoon_local/capture/`: 향후 Windows 캡처 연결
- `src/spoon_local/web/static/`: 향후 로컬 대시보드
- `windows_capture/`: 향후 Windows 네이티브 프로그램
- `tests/`: 자동 테스트
- `docs/`: 설계 문서
- `data/`: 실행 시 생성되는 로컬 DB, Git 제외

패키지 설치에는 패키지 저장소 접근이 필요하다. 현재 앱 실행에는 외부 서비스가 필요하지 않다. 의존성 버전은 범위로 지정했으며 잠금 파일은 아직 없다.
