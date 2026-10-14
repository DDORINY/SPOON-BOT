"use strict";

const byId = (id) => document.getElementById(id);
const stateLabels = {
  spoon_auth: { not_configured: "미연결", disconnected: "미연결", mock_ready: "수집 준비", mock_authenticated: "수집 인증됨", reauth_required: "재인증 필요", revoke_unconfirmed: "폐기 미확인", real_ready: "실제 인증 준비", authenticated: "SPOON 인증됨", configuration_error: "인증 설정 오류" },
  broadcast: { not_configured: "미연결", disconnected: "미연결", mock_live: "수집 연결", real_live: "실제 방송 연결" },
  donation_events: { not_configured: "대기 중", disconnected: "미연결", stopped: "수집 중지", connecting: "수집 연결 중", live: "수집 수집 중", waiting: "수집 방송 대기", backoff: "수집 재연결 대기", auth_required: "수집 인증 필요", refreshing: "수집 갱신 중", permission_required: "수집 권한 필요", blocked: "수집 차단", rate_limited: "수집 제한 대기", storage_error: "수집 저장 오류" },
  capture_agent: { not_configured: "미설정", disconnected: "미연결" },
};
const stateElements = {
  spoon_auth: "auth-status",
  broadcast: "broadcast-status",
  donation_events: "events-status",
  capture_agent: "capture-status",
};

async function refreshStatus() {
  let online = false;
  try {
    const response = await fetch("/api/status", {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) throw new Error("Status request failed");
    const status = await response.json();
    if (status.server !== "online") throw new Error("Server unavailable");
    for (const [key, element] of Object.entries(stateElements)) {
      byId(element).textContent = stateLabels[key][status[key]] || "확인 필요";
    }
    online = true;
    byId("status-message").textContent = "서버가 실행 중입니다. 수집 상태를 확인하세요. 자동 캡처는 미설정입니다.";
    byId("last-update").textContent = `최근 확인 ${new Date().toLocaleTimeString("ko-KR")}`;
  } catch {
    for (const element of Object.values(stateElements)) {
      byId(element).textContent = "확인 불가";
    }
    byId("status-message").textContent = "서버에 연결할 수 없습니다. 백엔드 실행 상태를 확인해주세요.";
    byId("last-update").textContent = "상태 확인 실패";
  }
  byId("server-status").textContent = online ? "온라인" : "오프라인";
  byId("server-summary").textContent = online ? "서버 온라인" : "서버 오프라인";
  byId("server-status").className = online ? "online" : "offline";
  byId("server-dot").className = online ? "dot online" : "dot offline";
  window.setTimeout(refreshStatus, 5000);
}

refreshStatus();
