"use strict";
const byId=id=>document.getElementById(id);
const helperLabels={disconnected:'도우미 미연결',waiting_for_live:'방송 시작 대기',connecting:'연결 중',connected:'도우미 연결됨',reconnecting:'재연결 중',disconnecting:'연결 해제 중',error:'연결 오류'};
const helperReasons={waiting:'현재 방송이 없습니다. 방송이 시작되면 도우미 연결을 시도합니다.',backoff:'연결이 끊겨 잠시 후 다시 시도해요.',rate_limited:'연결이 제한되어 재시도를 기다리고 있어요.',auth_required:'계정을 다시 연결해 주세요.',permission_required:'후원 수신 권한을 확인해 주세요.',blocked:'방송 입장이 제한됐어요. DJ가 차단 상태를 확인해 주세요.',storage_error:'후원 내역을 저장하지 못했어요. 저장 설정을 확인한 뒤 다시 시도해 주세요.',not_enabled:'현재 실행에서는 도우미 연결이 꺼져 있어요. 연결 설정을 확인해 주세요.'};
let actionBusy=false,readBusy=false,donationsRead=false,lastRows='',roundReady=false;
let donationScope='all';
async function loadRounds(){
  if(page!=='donations')return;
  const response=await fetch('/api/broadcast-sessions?limit=100',{cache:'no-store',signal:AbortSignal.timeout(20000)});
  const notice=byId('round-notice');
  if(!response.ok){roundReady=false;byId('round-view').disabled=true;byId('round-choice').disabled=true;const failure=await response.json();notice.textContent=failure.detail==='dj_identity_unverified'?'공식 DJ 계정 식별자가 확인되지 않아 회차 조회와 과거 기록 연결을 보류했어요.':'방송 회차 저장소 설정이 필요해요. 기존 후원 기록은 보존되어 있어요.';return;}
  const data=await response.json();roundReady=!data.migration_required;
  byId('round-view').disabled=!roundReady;byId('round-choice').disabled=!roundReady;
  const choice=byId('round-choice');const previous=choice.value;choice.replaceChildren();
  for(const item of data.items||[]){const option=document.createElement('option');option.value=item.round_id;option.textContent=item.provider_live_id?`방송 ${String(item.provider_live_id)} · ${String(item.started_at_utc||item.first_observed_at_utc)}`:'회차 미확정';choice.appendChild(option);}
  if(previous)choice.value=previous;
  notice.textContent=data.migration_required?'회차 저장 기능 적용 전이에요. 기존 기록은 수정하지 않았어요.':data.items.length?'이번 로그인에서 관측한 회차입니다. 과거 계정 구역은 자동 병합하지 않아요.':'기록된 방송 회차가 없습니다. 실제 방송이 관측되면 이곳에 표시됩니다.';
}
function donationUrl(){
  if(page!=='donations'||!roundReady)return '/api/donations?limit=20';
  const query=new URLSearchParams({limit:'20'});
  if(donationScope==='unconfirmed')query.set('unconfirmed','true');
  if(donationScope==='round'){const selected=byId('round-choice').value;if(!selected)return null;query.set('round_id',selected);}
  return '/api/session-donations?'+query.toString();
}
let helper={state:'disconnected',active:false,available:false};
const page={'/':'home','/broadcast':'broadcast','/capture':'capture','/gallery':'gallery','/donations':'donations','/settings':'settings'}[window.location.pathname]||'home';
function renderHelper(value){
  helper=value;
  const label=helperLabels[value.state]||helperLabels.error;
  if(!(document.body&&document.body.dataset.simulator==='true'))byId('broadcast-status').textContent=label;
  byId('helper-state').textContent=label;
  byId('connection-message').textContent=helperReasons[value.reason]||(value.state==='connected'?'도우미가 연결되어 후원을 기다리고 있어요.':'연결하면 SPOON 방송에 도우미 입장 안내가 표시됩니다.');
  byId('unavailable').hidden=value.reason!=='not_enabled';
  byId('unavailable').textContent=helperReasons.not_enabled;
  byId('login-link').hidden=true;byId('connect-link').hidden=true;
  byId('manage-link').hidden=value.reason==='not_enabled'&&value.available===undefined;
  const start=byId('helper-start'),stop=byId('helper-stop');
  if(start){start.hidden=Boolean(value.active);start.querySelector('button').disabled=actionBusy||!value.available;}
  if(stop){stop.hidden=!value.active;stop.querySelector('button').disabled=actionBusy;stop.querySelector('button').textContent=value.state==='waiting_for_live'?'연결 대기 취소':'연결 해제';}
  byId('event-received').textContent=value.last_donation_at?`최근 후원 확인: ${String(value.last_donation_at)}`:'아직 확인된 후원 수신이 없어요.';
}
function renderDonations(data){
  if(typeof window.dispatchEvent==='function')window.dispatchEvent(new CustomEvent('doha:donations',{detail:data}));
  const items=(data.source_mode==='real'||(data.source_mode==='simulator'&&document.body&&document.body.dataset.simulator==='true'))&&Array.isArray(data.items)?data.items.slice(0,20):[];
  const signature=JSON.stringify(items);
  if(signature!==lastRows){
    const list=byId('donation-list');list.replaceChildren();
    if(!items.length){const p=document.createElement('p');p.textContent='아직 확인된 후원이 없어요.';list.appendChild(p);}
    for(const item of items){
      const row=document.createElement('div');row.className='donation-row';
      const amount=document.createElement('strong');amount.textContent=`${data.source_mode==='simulator'?'[가상] 청취자':'익명 청취자'} · ${String(item.amount_spoons)} 스푼`;
      const time=document.createElement('span');time.textContent=`발생 ${typeof studioTime==='function'?studioTime(item.occurred_at_utc):'시각 확인 필요'} · 수신 ${typeof studioTime==='function'?studioTime(item.received_at_utc):'시각 확인 필요'}`;
      row.append(amount,time);list.appendChild(row);
    }
    lastRows=signature;
  }
  byId('donation-warning').textContent=data.issues_scope==='unavailable'||(data.issues&&(data.issues.gaps||data.issues.conflicts||data.gap_persistence_failed))?'연결이 끊기거나 확인이 필요한 기록이 있어요. 전체 후원 내역과 다를 수 있어요.':data.legacy_owner_unverified?'현재 로그인 구역의 후원만 표시해요. 과거 기록은 보존되어 있으며 소유자 확인 전에는 합치지 않아요.':'';
}
async function refresh(forceDonations=false){
  if(readBusy||actionBusy)return;
  readBusy=true;
  try{
    const response=await fetch('/api/helper',{cache:'no-store',signal:AbortSignal.timeout(20000)});
    if(response.status===401){renderHelper({state:'error',reason:'auth_required',available:false,active:false});return;}
    if(!response.ok)throw new Error('Unavailable');
    const value=await response.json();
    if(!actionBusy)renderHelper(value);
    if(['home','broadcast'].includes(page)&&(!donationsRead||value.state==='connected'||forceDonations)){
      if(page==='donations')await loadRounds();
      const url=donationUrl();
      if(url===null){renderDonations({source_mode:'real',items:[],issues:{}});return;}
      const result=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(20000)});
      if(result.status===403){renderDonations({source_mode:'real',items:[],issues:{}});byId('donation-warning').textContent='후원 내역 페이지에서 로컬 기록 조회 권한을 확인해 주세요.';donationsRead=true;return;}
      if(!result.ok)throw new Error('Unavailable');
      const data=await result.json();renderDonations(url.startsWith('/api/session-donations')?{...data,source_mode:'real'}:data);donationsRead=true;
    }
  }catch{if(!actionBusy)renderHelper({...helper,state:'error',reason:'status_unavailable'});byId('donation-warning').textContent='상태를 갱신하지 못했어요. 표시된 내역은 이전 자료일 수 있어요.';}
  finally{readBusy=false;}
}
async function submitControl(event){
  event.preventDefault();if(actionBusy)return;
  const form=event.currentTarget;
  if(!form.reportValidity())return;
  byId('helper-action-error').textContent='';
  actionBusy=true;
  const stopping=form.id==='helper-stop';
  renderHelper({...helper,state:stopping?'disconnecting':'connecting'});
  const values=new URLSearchParams({csrf:form.querySelector('[name=csrf]').value});
  if(!stopping)values.set('dj_notice','confirmed');
  try{
    const response=await fetch(form.getAttribute('action'),{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},body:values,redirect:'error',signal:AbortSignal.timeout(30000)});
    if(!response.ok){byId('helper-action-error').textContent=response.status===400?'동의와 로그인 상태를 확인하고 페이지를 새로고침해 주세요.':'연결 요청을 완료하지 못했어요. 현재 상태를 다시 확인해 주세요.';}
  }catch{byId('helper-action-error').textContent='요청 결과를 확인하지 못했어요. 중복 요청 없이 현재 상태를 확인해 주세요.';}
  finally{actionBusy=false;await refresh(true);}
}
for(const id of ['helper-start','helper-stop']){const form=byId(id);if(form)form.addEventListener('submit',submitControl);}
if(byId('round-view'))byId('round-view').addEventListener('change',async event=>{donationScope=event.target.value;byId('round-choice-label').hidden=donationScope!=='round';if(!roundReady&&donationScope!=='all'){byId('round-notice').textContent='회차 저장 기능을 적용한 뒤 사용할 수 있어요.';return;}donationsRead=false;lastRows='';await refresh(true);});
if(byId('round-choice'))byId('round-choice').addEventListener('change',async()=>{donationsRead=false;lastRows='';await refresh(true);});
const titles={home:'오늘도 즐거운 방송을',broadcast:'내 방송 화면',capture:'소중한 순간을 담아요',gallery:'캡처 보관함',donations:'방송에 도착한 응원',settings:'내 스튜디오 설정'};
byId('page-title').textContent=titles[page];
for(const panel of document.querySelectorAll('[data-view]'))panel.hidden=!panel.getAttribute('data-view').split(' ').includes(page);
for(const link of document.querySelectorAll('nav a'))if(link.getAttribute('href')===window.location.pathname)link.setAttribute('aria-current','page');
async function pollHelper(){if(!document.hidden)await refresh();const delay=helper.reason==='not_enabled'?60000:helper.active?3000:15000;window.setTimeout(pollHelper,delay);}
pollHelper();
