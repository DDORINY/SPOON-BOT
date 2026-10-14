# K4.8.3 토글 레이아웃·페이지 격리

## 원인과 수정

기존 simulator.py의 공통 페이지 라우트는 경로 조건 없이 main 끝에 sim-controls/sim-summary/automation.html과 스크립트를 삽입했다. 따라서 모든 시뮬레이터 페이지에서 조작 도구가 노출됐다. 이제 서버가 시뮬레이터 앱의 `/broadcast`일 때만 해당 DOM/스크립트를 주입한다. 공통 안전 배너와 body의 서버 모드 표시를 유지한다. simulator.js도 서버가 지정한 모드 및 `/broadcast` 둘 다 검증한다. 일반 앱에는 기존대로 시뮬레이터 API가 등록되지 않으며 세션/CSRF/Origin/Host 검증을 변경하지 않는다.

토글 겹침을 만들 수 있는 원인은 viewport로 고정된 broadcast-columns 높이와 자식 채팅 콘텐츠의 자연 높이 충돌이다. 입력·안내가 지정 높이를 넘더라도 다음 도구 영역은 고정 grid 아래에서 시작했다. 이제 grid 행과 채팅 카드는 콘텐츠에 맞춰 높이가 증가한다. 메시지 목록만 viewport 기반 제한 높이로 스크롤하고, 입력·펼친 안내는 자연 흐름에 둔다. 시뮬레이터 도구는 채팅 카드 밖의 다음 형제 영역이므로 늘어난 카드 높이를 따라 내려간다. z-index나 큰 margin으로 가리지 않는다. 오른쪽 활성 탭의 스크롤은 유지하고 작은 화면/확대에서는 페이지 스크롤 폴백을 허용한다.

단일 studio.js 배치 책임과 DOMContentLoaded 순서를 유지한다. 기존 폼·버튼 ID·자동화/시나리오·Mock 기록·가상 시간·데이터 계약은 변경하지 않는다.

## 페이지 정책

시뮬레이터 `/broadcast`: 도구·자동화 DOM 및 스크립트 주입, 도구 기본 접힘.
시뮬레이터 `/`, `/capture`, `/gallery`, `/donations`, `/settings`: 조작 UI 미주입, 공통 안전 배너 유지.
일반/실제 모드: 도구·자동화 UI 미주입 및 시뮬레이터 API 미등록.

변경 파일: simulator.py, simulator.js, studio.css, Python/Node 회귀 테스트, 본 문서.

## 검증과 한계

실제 네트워크 차단과 메모리 DB 기반으로 6개 경로의 서버 DOM/자산 주입·재방문/새로고침·배너를 검사한다. Node에서 두 토글의 서로 다른 DOM 소유권과 동시에 open인 경우 기존 폼 유지, 초기화 순서 및 버튼 바인딩을 검증한다. 전체 Python/Node 회귀·JavaScript 문법·pip check·git diff --check를 실행한다.

Chrome 렌더링/bounding rectangle은 미검증이다. CSS/DOM 구조 수정과 테스트 통과를 실제 시각적 겹침 해결 확인으로 주장하지 않는다. 시뮬레이터에서 `/broadcast`의 두 토글을 동시에 펼치고 390px 모바일·125/150% 확대에서 입력/버튼/도구 경계를 수동 확인해야 한다. 다른 5개 경로에는 안전 배너만 남는지 확인한다. 실제 API/운영 DB/커밋/push 작업은 수행하지 않는다.
