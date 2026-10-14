"use strict";

function renderDonations(data) {
  const list = document.getElementById("donation-list");
  const mode = document.getElementById("donation-mode");
  const total = document.getElementById("donation-total");
  const warning = document.getElementById("donation-warning");
  list.replaceChildren();
  if (!["mock", "real"].includes(data.source_mode)) {
    mode.textContent = "수신 미설정";
    total.textContent = "";
    const empty = document.createElement("p");
    empty.textContent = "데이터 없음";
    list.appendChild(empty);
    warning.textContent = "Mock 수집이 비활성입니다. 실제 후원 자료는 조회하지 않습니다.";
    return;
  }
  const label = data.source_mode === "real" ? "실제 관측" : "Mock";
  mode.textContent = `${label} 데이터`;
  total.textContent = `관측된 ${label} 후원 합계: ${String(data.observed_total_spoons)} 스푼`;
  const issues = data.issues || {};
  warning.textContent = `누락 없는 원장이 아닙니다. 충돌 ${Number(issues.conflicts) || 0} · gap ${Number(issues.gaps) || 0} · ID 없음 ${Number(issues.idless) || 0}${data.gap_persistence_failed ? " · gap 저장 실패" : ""}`;
  const items = Array.isArray(data.items) ? data.items.slice(0, 100) : [];
  if (!items.length) {
    const empty = document.createElement("p");
    empty.textContent = "데이터 없음";
    list.appendChild(empty);
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "donation-row";
    const amount = document.createElement("strong");
    amount.textContent = `${label} · 익명 · ${String(item.amount_spoons)} 스푼`;
    const time = document.createElement("small");
    time.textContent = `발생 ${String(item.occurred_at_utc)} · ${item.dedup_quality === "unavailable" ? "ID 없음" : "ID 중복 검사"}`;
    row.appendChild(amount);
    row.appendChild(time);
    list.appendChild(row);
  }
}

async function refreshDonations() {
  try {
    const response = await fetch("/api/donations?limit=20", { cache: "no-store", signal: AbortSignal.timeout(4000) });
    if (!response.ok) throw new Error("Donation query failed");
    renderDonations(await response.json());
  } catch {
    document.getElementById("donation-mode").textContent = "조회 실패";
    document.getElementById("donation-warning").textContent = "자료를 갱신하지 못했습니다. 표시된 자료는 이전 조회 결과일 수 있습니다.";
  }
  window.setTimeout(refreshDonations, 5000);
}

refreshDonations();
