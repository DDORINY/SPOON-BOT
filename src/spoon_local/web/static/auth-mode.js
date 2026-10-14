"use strict";

async function showAuthMode() {
  try {
    const response = await fetch("/api/mode", {cache: "no-store", signal: AbortSignal.timeout(4000)});
    if (!response.ok) throw new Error("Mode unavailable");
    const configuration = await response.json();
    const mode = configuration.auth_mode;
    const real = mode === "real";
    document.getElementById("real-login-link").hidden = !real;
    document.getElementById("auth-prepare-link").hidden = real;
    document.getElementById("collection-link").hidden = mode !== "mock";
    let realCollection = false;
    if (real) {
      const collection = await fetch("/api/collection-mode", {cache: "no-store", signal: AbortSignal.timeout(4000)});
      if (collection.ok && (await collection.json()).collection_mode === "real") {
        realCollection = true;
        document.getElementById("collection-link").hidden = false;
        document.getElementById("collection-link").textContent = "실제 후원 수집 시작·중지 관리";
      }
    }
    document.getElementById("auth-mode-label").textContent = real ? "Real OAuth · 메모리 보관" : "실제 SPOON 로그인 비활성";
    document.getElementById("auth-mode-notice").textContent = real ? "실제 SPOON 로그인 버튼에서 localhost 준비 화면으로 이동한 뒤 수동으로 동의합니다. " + (realCollection ? "후원 수집은 수집 관리에서 DJ 고지 확인 후 직접 시작해야 합니다." : "실제 후원 수집은 비활성입니다.") : "Mock 인증은 실제 계정과 연결되지 않습니다.";
  } catch {
    document.getElementById("real-login-link").hidden = true;
    document.getElementById("collection-link").hidden = true;
    document.getElementById("auth-mode-label").textContent = "인증 모드 확인 실패";
  }
}
showAuthMode();
