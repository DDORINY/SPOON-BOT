"use strict";
(()=>{
if(window.location.pathname!=='/donations')return;
const el=id=>document.getElementById(id);
let page=1,snapshot=null,busy=false,allowed=false;
const korean=value=>value?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',dateStyle:'medium',timeStyle:'medium'}).format(new Date(value)):'—';
function enable(){for(const id of ['history-period','history-size','history-search','history-refresh'])el(id).disabled=!allowed||busy;const custom=el('history-period').value==='custom';for(const id of ['history-start','history-end']){el(id).disabled=!allowed||busy||!custom;el(id).required=custom;}}
async function load(reset=false){
if(!allowed||busy)return;
if(reset){page=1;snapshot=null;}
busy=true;enable();el('history-prev').disabled=true;el('history-next').disabled=true;el('history-error').textContent='불러오는 중이에요.';
const q=new URLSearchParams({period:el('history-period').value,page:String(page),page_size:el('history-size').value});
if(q.get('period')==='custom'){q.set('start',el('history-start').value);q.set('end',el('history-end').value);}
if(snapshot!==null)q.set('snapshot',String(snapshot));
try{
const response=await fetch('/api/donation-history?'+q,{cache:'no-store',signal:AbortSignal.timeout(15000)});
if(!response.ok){if(response.status===401||response.status===403){allowed=false;el('history-list').replaceChildren();for(const id of ['history-count','history-amount','history-today','history-latest'])el(id).textContent='—';}throw new Error(response.status===400?'시작일·종료일과 페이지 조건을 확인해 주세요.':response.status===403?'로컬 기록 조회 권한을 다시 확인해 주세요.':response.status===401?'계정을 다시 연결해 주세요.':'내역을 조회하지 못했어요. 다시 시도해 주세요.');}
const data=await response.json();snapshot=data.snapshot;
el('history-count').textContent=`${data.statistics.event_count}건`;el('history-amount').textContent=`${data.statistics.amount_spoons} 스푼`;el('history-today').textContent=`${data.statistics.today_event_count}건`;el('history-latest').textContent=korean(data.statistics.latest_at_utc);
const list=el('history-list');list.replaceChildren();
if(!data.items.length){const p=document.createElement('p');p.textContent='선택한 기간에 저장된 후원이 없습니다.';list.appendChild(p);}
for(const item of data.items){const row=document.createElement('div');row.className='donation-row';const amount=document.createElement('strong');amount.textContent=`익명 청취자 · ${item.amount_spoons} 스푼`;const time=document.createElement('span');time.textContent=`${item.time_basis==='received'?'수신':'발생'} ${korean(item.display_at_utc)} · 수신 ${korean(item.received_at_utc)}`;row.append(amount,time);list.appendChild(row);}
el('history-page').textContent=data.pages?`${page} / ${data.pages} 페이지 · ${data.total}건`:'0건';el('history-prev').disabled=!data.has_previous;el('history-next').disabled=!data.has_next;el('history-error').textContent='';
}catch(error){el('history-error').textContent=error instanceof Error&&error.name==='Error'?error.message:'조회가 지연되고 있어요. 다시 시도해 주세요.';}
finally{busy=false;enable();}
}
const access=el('history-access');if(access)access.addEventListener('submit',async event=>{
event.preventDefault();if(busy||!access.reportValidity())return;busy=true;access.querySelector('button').disabled=true;
try{const values=new URLSearchParams({csrf:access.querySelector('[name=csrf]').value,local_owner:'confirmed'});const response=await fetch(access.action,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:values,redirect:'error',signal:AbortSignal.timeout(15000)});allowed=response.ok;if(!allowed)el('history-error').textContent='로그인과 접근 확인을 점검하고 페이지를 새로고침해 주세요.';}
catch{el('history-error').textContent='접근 확인 요청을 완료하지 못했어요.';}
finally{busy=false;access.querySelector('button').disabled=false;enable();if(allowed)await load(true);}
});
for(const id of ['history-period','history-size','history-start','history-end'])el(id).addEventListener('change',()=>{snapshot=null;page=1;el('history-prev').disabled=true;el('history-next').disabled=true;el('history-error').textContent='변경한 조건으로 조회해 주세요.';enable();});
el('history-filter').addEventListener('submit',event=>{event.preventDefault();load(true);});el('history-refresh').addEventListener('click',()=>load(true));el('history-prev').addEventListener('click',()=>{if(!busy){page--;load();}});el('history-next').addEventListener('click',()=>{if(!busy){page++;load();}});
// Historical pages stay on a fixed snapshot. New events appear only on refresh.
enable();
})();
