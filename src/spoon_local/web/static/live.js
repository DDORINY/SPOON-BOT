"use strict";
let liveBusy = false;
function renderLive(data) {
  if(typeof window.dispatchEvent==='function')window.dispatchEvent(new CustomEvent('doha:live-state',{detail:data}));
  const message = document.getElementById('live-status');
  const details = document.getElementById('live-details');
  const retry = document.getElementById('live-retry');
  const labels = {waiting:'현재 진행 중인 방송이 없습니다',reauth_required:'계정을 다시 연결해 주세요',permission_required:'방송 정보 확인에 추가 권한이 필요해요',error:'방송 정보를 확인하지 못했어요. 잠시 후 다시 시도해 주세요.',rate_limited:'조회가 잠시 제한됐어요. 잠시 후 다시 확인해 주세요.',not_configured:'방송 정보 조회가 아직 준비되지 않았어요.'};
  const header = document.getElementById('air-status');
  if (header && !(document.body&&document.body.dataset.simulator==='true')) header.textContent = data.state === 'live' ? '방송 중' : data.state === 'waiting' ? '방송 대기' : '방송 확인 필요';
  details.hidden=true;
  retry.hidden=!data.retryable;
  if (data.state !== 'live' || !data.live) {
    message.textContent=labels[data.state] || labels.error;
    return;
  }
  message.textContent='방송 중';
  const live=data.live;
  document.getElementById('live-title').textContent=String(live.title);
  const started=new Date(live.started_at_utc);
  document.getElementById('live-started').textContent=Number.isNaN(started.getTime())?'확인할 수 없어요':started.toLocaleString('ko-KR');
  const minutes=Math.max(0,Math.floor((Date.now()-started.getTime())/60000));
  document.getElementById('live-duration').textContent=Number.isFinite(minutes)?`${Math.floor(minutes/60)}시간 ${minutes%60}분`:'확인할 수 없어요';
  document.getElementById('live-listeners').textContent=`${String(live.listener_count)}명`;
  details.hidden=false;
}
async function refreshLive() {
  if (liveBusy) return;
  liveBusy=true;
  try {
    const mode=await fetch('/api/mode',{cache:'no-store',signal:AbortSignal.timeout(5000)});
    if (!mode.ok || !['real','simulator'].includes((await mode.json()).auth_mode)) { renderLive({state:'not_configured'});return; }
    const response=await fetch('/api/live',{cache:'no-store',signal:AbortSignal.timeout(20000)});
    if (!response.ok && response.status!==401) throw new Error('Unavailable');
    renderLive(await response.json());
  } catch { renderLive({state:'error',retryable:true}); }
  finally { liveBusy=false; }
}
document.getElementById('live-retry').addEventListener('click',refreshLive);
async function pollLive() {
  if (!document.hidden) await refreshLive();
  window.setTimeout(pollLive,30000);
}
pollLive();
