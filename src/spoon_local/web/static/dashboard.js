"use strict";

const byId = (id) => document.getElementById(id);
const stateLabels = {
  spoon_auth: { not_configured: "미연결", disconnected: "미연결", mock_ready: "Mock 준비", mock_authenticated: "Mock 인증됨", reauth_required: "재인증 필요", revoke_unconfirmed: "폐기 미확인" },
  broadcast: { not_configured: "미연결", disconnected: "미연결" },
  donation_events: { not_configured: "대기 중", disconnected: "미연결" },
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
    byId("status-message").textContent = "서버가 실행 중입니다. SPOON 연결과 자동 캡처 기능은 아직 제공되지 않습니다.";
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
