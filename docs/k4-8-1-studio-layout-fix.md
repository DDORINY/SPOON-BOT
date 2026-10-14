# K4.8.1 방송 스튜디오 레이아웃 안정화

## 증상과 확인된 원인

사용자 Chrome 약 1666×895에서 통계 아래 빈 공간이 생기고 중앙 채팅/오른쪽 패널이 첫 화면에서 사라졌다.

`studio.js`는 defer 실행 중 readyState가 interactive이면 setup을 즉시 실행했다. 스크립트 순서는 기본 studio.js 이후 시뮬레이터에서 추가되는 simulator.js/automation.js다. 두 후속 스크립트가 기존 카드를 다시 방송 section으로 append하여 앞서 만들어진 center/right grid 밖으로 이동시켰다. 따라서 높이가 지정된 빈 grid가 남고 실제 카드가 아래쪽으로 밀릴 수 있는 실행 경로가 확인됐다. 기존 Node 테스트는 readyState=complete와 단독 studio 실행만 모사해 이 충돌을 검출하지 못했다.

후원 카드에는 원래 홈 화면용 data-view=home 및 hidden이 남아 있었다. 이동만으로 hidden을 제거하지 않아 오른쪽 후원도 숨겨질 수 있었다.

## 수정

- studio.js가 먼저 `body.dataset.studioLayout=managed`로 레이아웃 소유권을 선언한다.
- loading/interactive에서는 DOMContentLoaded까지 setup을 기다려 모든 defer 스크립트 초기화 이후 한 번만 배치한다.
- simulator.js/automation.js는 managed이면 자체 카드 재배치를 생략한다. 버튼/폼 핸들러·조회 루프는 그대로 유지한다.
- 후원 카드를 이동할 때 홈 전용 data-view/hidden을 제거한다. 부모 broadcast 페이지·권한 검증을 우회하는 변경은 아니다.
- 실제 grid 상단 위치와 viewport 높이를 사용해 남은 높이를 계산하고 resize에 갱신한다. 기존 460/500px 고정 최소 높이는 320px 안전 폴백으로 줄인다. 작은 화면은 기존 한 열과 전체 스크롤 폴백을 유지한다.
- 통계 카드 패딩/글자 간격을 줄인다. 중앙 chat 내부·입력 섹션·오른쪽·후원·청취자·상세 설정의 독립 스크롤을 유지한다.

기존 사이드바, 데이터 계약, API, OAuth/SSE/수동 전송 동의·시뮬레이터 격리·자동화 엔진은 변경하지 않는다. 방송 대기에서도 채팅/오른쪽 카드를 마운트하며 실행 가능 여부는 기존 서버/클라이언트 상태 검증이 결정한다. 도구 및 개발 JSON은 기본 접힘이다.

변경 파일: studio.js/css, simulator.js, automation.js, Node 레이아웃 테스트, Python 테스트, 본 문서.

## 검증과 한계

Node에서 interactive→DOMContentLoaded 순서, 이동한 후원 hidden 해제, 실제/가상 DOM 배치, managed일 때 후속 스크립트의 재마운트 차단과 기존 버튼 바인딩을 검증한다. Python 전체 회귀, 전체 Node 연계 테스트, JavaScript 문법, pip check, git diff --check를 실행한다.

프로젝트 웹 소스에서 ‘닫기’ 문자열을 찾지 못했다. 확장 프로그램 또는 외부 주입 여부는 실제 Chrome DOM에서 확인해야 한다. 출처 미확인 요소를 삭제하거나 CSS로 감추지 않았다.

Chrome/Playwright/Puppeteer가 실행 가능한 상태가 아니므로 실제 렌더링·스크린샷·가로 스크롤/첫 화면 배치는 미검증이다. 정적/Node 검증을 실제 Chrome 검증으로 간주하지 않는다.

## 수동 확인

`.venv/bin/python -m spoon_local --simulator` 실행 후 `http://localhost:8000/broadcast` 새로고침. 방송 대기에서 중앙 제목/빈 채팅/입력창과 오른쪽 후원/자동화/청취자 접이식 카드가 grid 안에 표시되는지 확인한다. 시뮬레이터 도구를 열어 시작→이벤트→끊김→재연결→종료를 확인한다.

1920×1080,1666×895,1600×900,1366×768,1280×720,1024×768,390×844에서 사이드바 겹침·입력 가림·가로 스크롤·목록 내부 스크롤·모바일 메뉴를 확인한다. ‘닫기’는 개발자 도구 요소 선택과 확장 비활성 비교로 출처를 확인한다. 실제 SPOON 호출이나 운영 DB 검증은 수행하지 않는다.
