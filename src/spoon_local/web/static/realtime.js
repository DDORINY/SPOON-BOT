"use strict";
(()=>{
if(window.location.pathname!=='/broadcast')return;
const el=id=>document.getElementById(id),box=el('chat-monitor'),jump=el('chat-jump');
let generation=null,last=0,active=false,busy=false,newMessages=0,initialized=false;
const labels={disconnected:'도우미 미연결',waiting_for_live:'방송 시작 대기',connecting:'연결 중',connected:'도우미 연결됨',reconnecting:'재연결 중',error:'연결 오류'};
const time=value=>value?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',}).format(new Date(value)):'없음';
function clear(){box.replaceChildren();el('heart-amount').textContent='—';el('heart-events').textContent='—';jump.hidden=true;jump.disabled=true;last=0;generation=null;}
function render(data){
if(typeof window.dispatchEvent==='function')window.dispatchEvent(new CustomEvent('doha:realtime-state',{detail:data}));
active=Boolean(data.active);el('realtime-status').textContent=(data.simulation?'[가상] ':'')+(labels[data.state]||labels.error);
const permissions=data.permissions||{};
el('chat-permission').textContent=permissions.chat?'':'채팅 권한이 없습니다. 계정을 해제한 뒤 다시 로그인하여 동의해 주세요.';
el('like-permission').textContent=permissions.like?'':'하트 권한이 없습니다. 계정을 해제한 뒤 다시 로그인하여 동의해 주세요.';
const items=permissions.chat&&Array.isArray(data.chats)?data.chats:[];
const changed=data.generation!==generation;
const atBottom=box.scrollHeight-box.scrollTop-box.clientHeight<60;
const scroll=box.scrollTop;
const additions=changed?items.length:items.filter(item=>item.sequence>last).length;
// Reuse existing nodes while browsing older messages; prune only expired IDs.
if(changed){box.replaceChildren();newMessages=0;}
const ids=new Set(items.map(item=>String(item.sequence)));
for(const child of Array.from(box.children))if(!ids.has(child.dataset.sequence))child.remove();
for(const item of items){if(!changed&&item.sequence<=last)continue;const row=document.createElement('div');row.className='chat-row';row.dataset.sequence=String(item.sequence);const author=document.createElement('strong');const role=item.role||(item.is_dj===true?'dj':item.is_dj===false?'viewer':'unknown');const labels={viewer:'시청자',dj:'DJ',bot:'BOT',unknown:'출처 미확인'};row.dataset.role=role;author.textContent=(item.nickname||'익명')+' · '+(labels[role]||labels.unknown)+(item.simulation?' · 가상':'');const message=document.createElement('p');message.textContent=String(item.message);const stamp=document.createElement('small');stamp.textContent=`수신 ${time(item.received_at_utc)}`;row.append(author,stamp,message);box.appendChild(row);}
if(!items.length){const p=document.createElement('p');p.textContent=permissions.chat?'아직 관측한 채팅이 없습니다.':'채팅 권한 확인 후 표시할 수 있어요.';box.replaceChildren(p);}
if(atBottom||changed||!initialized){box.scrollTop=box.scrollHeight;newMessages=0;}else{box.scrollTop=scroll;newMessages+=additions;}
jump.hidden=newMessages===0;jump.disabled=newMessages===0;jump.textContent=`새 채팅 ${newMessages}개 보기`;
generation=data.generation;last=items.length?Math.max(...items.map(item=>item.sequence)):last;initialized=true;
el('chat-received').textContent=data.last_chat_received_at_utc?`최근 채팅 수신 ${time(data.last_chat_received_at_utc)}`:'채팅 수신 기록 없음';
el('heart-amount').textContent=permissions.like?`${data.hearts.observed_amount} 하트`:'—';el('heart-events').textContent=permissions.like?`관측 이벤트 ${data.hearts.event_count}건`:'—';el('heart-received').textContent=data.hearts.last_received_at_utc?`최근 하트 수신 ${time(data.hearts.last_received_at_utc)}`:'하트 수신 기록 없음';
}
jump.addEventListener('click',()=>{box.scrollTop=box.scrollHeight;newMessages=0;jump.hidden=true;jump.disabled=true;});
box.addEventListener('scroll',()=>{if(box.scrollHeight-box.scrollTop-box.clientHeight<60){newMessages=0;jump.hidden=true;jump.disabled=true;}});
async function poll(schedule=true){if(!document.hidden&&!busy){busy=true;try{const response=await fetch('/api/realtime-events',{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok){if(response.status===401||response.status===403){window.dispatchEvent(new CustomEvent('doha:auth-lost'));clear();el('chat-received').textContent='채팅 수신 기록 없음';el('heart-received').textContent='하트 수신 기록 없음';}throw new Error();}render(await response.json());el('realtime-error').textContent='';}catch{el('realtime-status').textContent='이벤트 조회 오류 · 마지막 표시가 최신 상태인지 확인할 수 없어요.';el('realtime-error').textContent='이벤트를 조회하지 못했어요. 로그인과 도우미 연결 상태를 확인해 주세요.';}finally{busy=false;}}if(schedule)window.setTimeout(poll,active?3000:15000);}
if(document.body&&document.body.dataset.simulator==='true')window.addEventListener('doha:simulator-updated',()=>poll(false));
poll();
})();
