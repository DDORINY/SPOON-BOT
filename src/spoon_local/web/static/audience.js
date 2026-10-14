"use strict";
(()=>{
if(window.location.pathname!=='/broadcast')return;
const el=id=>document.getElementById(id),kinds=['listeners','fans'];
let current=null,request=null,version=0,liveId=null,cursor=null;const detailSnapshots={};
const messages={busy:'다른 조회가 진행 중이에요. 잠시 후 새로고침해 주세요.',waiting:'현재 진행 중인 방송이 없습니다.',reauth:'계정을 다시 연결해 주세요.',permission_required:'조회 권한이 없습니다. 계정을 해제한 뒤 다시 동의해 주세요.',not_configured:'현재 실행에서는 조회 기능이 꺼져 있어요.',broadcast_changed:'방송이 변경되었습니다. 첫 페이지부터 다시 조회해 주세요.',invalid_cursor:'청취자 페이지가 유효하지 않아요. 새로고침해 주세요.',upstream_error:'공식 응답을 확인하지 못했어요. 다시 시도해 주세요.',timeout:'조회 시간이 초과됐어요. 다시 시도해 주세요.',network_error:'연결하지 못했어요. 네트워크 상태를 확인해 주세요.'};
function controls(busy){for(const kind of kinds){el(kind+'-refresh').disabled=busy;el(kind+'-tab').disabled=false;}el('listeners-next').disabled=busy||!cursor;}
function reset(){for(const kind of kinds)detailSnapshots[kind]={state:'waiting',items:[]};version++;if(request)request.abort();request=null;cursor=null;liveId=null;for(const kind of kinds){el(kind+'-list').replaceChildren();el(kind+'-count').textContent='—';el(kind+'-time').textContent='아직 조회하지 않았어요.';el(kind+'-state').textContent='탭을 열거나 새로고침하여 조회하세요.';}controls(false);}
function render(kind,data){
if(typeof window.dispatchEvent==='function')window.dispatchEvent(new CustomEvent('doha:audience-summary',{detail:{...data,kind}}));
const list=el(kind+'-list');list.replaceChildren();detailSnapshots[kind]=data;
if(document.body&&document.body.dataset.threeTabStudio==='true'&&!el(kind+'-detail-open')){const button=document.createElement('button');button.id=kind+'-detail-open';button.type='button';button.textContent='조회된 전체 목록 보기';list.parentElement.appendChild(button);button.addEventListener('click',()=>{const dialog=document.createElement('dialog');dialog.className='studio-detail-dialog';const title=document.createElement('h2');title.textContent=kind==='fans'?'이번 방송·현재 방 후원 랭킹':'조회된 청취자 페이지';title.id=kind+'-full-title';dialog.setAttribute('aria-labelledby',title.id);const close=document.createElement('button');close.type='button';close.textContent='닫기';close.addEventListener('click',()=>dialog.close());dialog.append(title,close);const current=detailSnapshots[kind];if(current.state==='ready')for(const item of current.items){const row=document.createElement('p');row.textContent=(kind==='fans'?`${item.rank}위 · `:'')+(item.nickname||'표시명 없음')+(kind==='fans'?` · ${item.spoon_count===null?'후원량 미확인':item.spoon_count+' 스푼'}`:'');dialog.appendChild(row);}else{const empty=document.createElement('p');empty.textContent='현재 조회 상태를 확인해 주세요.';dialog.appendChild(empty);}document.body.appendChild(dialog);dialog.addEventListener('close',()=>{dialog.remove();button.focus();});dialog.showModal();});}
if(data.state!=='ready'){if(['waiting','reauth','broadcast_changed'].includes(data.state)){for(const name of kinds){el(name+'-list').replaceChildren();el(name+'-count').textContent='—';el(name+'-time').textContent='아직 조회하지 않았어요.';}liveId=null;cursor=null;}el(kind+'-state').textContent=data.state==='rate_limited'?`조회가 제한됐어요. ${data.retry_after_seconds||30}초 이후 다시 확인해 주세요.`:messages[data.state]||messages.upstream_error;el(kind+'-count').textContent='—';return;}
if(liveId!==null&&liveId!==data.live_id){cursor=null;for(const name of kinds){el(name+'-list').replaceChildren();el(name+'-count').textContent='—';el(name+'-time').textContent='아직 조회하지 않았어요.';el(name+'-state').textContent=messages.broadcast_changed;}}
liveId=data.live_id;if(kind==='listeners')cursor=data.next_cursor;
el(kind+'-state').textContent=data.items.length?'조회 완료':'현재 조회된 정보가 없습니다.';
el(kind+'-count').textContent=document.body&&document.body.dataset.threeTabStudio==='true'?`표시 ${Math.min(data.items.length,kind==='fans'?5:8)}명 · 이번 조회 ${data.returned_count}명 (전체 인원 아님)`:kind==='listeners'?`이 페이지의 청취자 ${data.returned_count}명 · 전체 청취자 수가 아닙니다.`:`공식 랭킹 ${data.returned_count}명 · 최대 30명`;
el(kind+'-time').textContent='마지막 조회 '+(typeof studioTime==='function'?studioTime(data.queried_at_utc):new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',dateStyle:'medium',timeStyle:'medium'}).format(new Date(data.queried_at_utc)));
for(const item of (document.body&&document.body.dataset.threeTabStudio==='true'?data.items.slice(0,kind==='fans'?5:8):data.items)){const row=document.createElement('div');row.className='audience-row';const avatar=document.createElement('span');avatar.className='audience-avatar';avatar.textContent='●';avatar.setAttribute('aria-hidden','true');const text=document.createElement('div');text.className='audience-name';const name=document.createElement('strong');name.textContent=(kind==='fans'?`${item.rank}위 · `:'')+(item.nickname||'표시명 없음');text.appendChild(name);if(kind==='fans'){const amount=document.createElement('small');amount.textContent=item.spoon_count===null?'후원량 정보 없음':`${item.spoon_count} 스푼`;text.appendChild(amount);}row.append(avatar,text);list.appendChild(row);}
}
async function query(kind,next=false){
if(document.hidden)return;
if(request)return;
detailSnapshots[kind]={state:'loading',items:[]};
const mine=++version,controller=new AbortController();request=controller;controls(true);el(kind+'-state').textContent='조회 중… 남아 있는 목록은 이전 조회 결과입니다.';
const parameters=new URLSearchParams();if(next&&cursor){parameters.set('cursor',cursor);if(liveId!==null)parameters.set('live_id',String(liveId));}
try{const response=await fetch('/api/live/'+kind+(parameters.size?'?'+parameters:''),{cache:'no-store',signal:AbortSignal.any([controller.signal,AbortSignal.timeout(50000)])});if(mine!==version||document.hidden)return;const data=await response.json();if(response.status===401){reset();el(kind+'-state').textContent=messages.reauth;return;}render(kind,data);}
catch{if(mine===version&&!controller.signal.aborted){detailSnapshots[kind]={state:'error',items:[]};el(kind+'-state').textContent='조회하지 못했어요. 남아 있는 목록은 이전 조회 결과입니다.';}}
finally{if(mine===version){request=null;controls(false);}}
}
function select(kind){version++;if(request)request.abort();request=null;current=kind;for(const name of kinds){el(name+'-tab').setAttribute('aria-selected',String(name===kind));el(name+'-tab').tabIndex=name===kind?0:-1;el(name+'-panel').hidden=name!==kind;}query(kind);}
for(const kind of kinds){el(kind+'-tab').disabled=false;el(kind+'-tab').addEventListener('click',()=>select(kind));el(kind+'-refresh').addEventListener('click',()=>query(kind));el(kind+'-tab').addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const chosen=event.key==='Home'?'listeners':event.key==='End'?'fans':kind==='listeners'?'fans':'listeners';select(chosen);el(chosen+'-tab').focus();}});}
el('listeners-next').addEventListener('click',()=>query('listeners',true));
document.addEventListener('visibilitychange',()=>{if(document.hidden){reset();for(const kind of kinds)el(kind+'-state').textContent='목록을 비웠어요. 탭을 열거나 새로고침하여 조회하세요.';}});
window.addEventListener('doha:live-state',event=>{const value=event.detail;if(value.state==='reauth_required'||value.state==='waiting'||(value.live&&liveId!==null&&value.live.live_id!==liveId)){reset();for(const kind of kinds)el(kind+'-state').textContent=value.state==='waiting'?messages.waiting:value.state==='reauth_required'?messages.reauth:messages.broadcast_changed;}});
window.addEventListener('doha:auth-lost',()=>{reset();for(const kind of kinds)el(kind+'-state').textContent=messages.reauth;});
if(document.body&&document.body.dataset.simulator==='true')window.addEventListener('doha:simulator-updated',async()=>{await query('listeners');await query('fans');});
// No automatic audience requests: tab selection and refresh are explicit.
controls(false);
})();
