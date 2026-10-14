"use strict";
(()=>{
if(!document.body||document.body.dataset.simulator!=='true'||window.location.pathname!=='/broadcast')return;
const el=id=>document.getElementById(id);let busy=false,reading=false,previous=null;
const controls=[];
const labels={waiting:'가상 방송 대기',live:'가상 방송 중',ended:'가상 방송 종료',connected:'가상 연결됨',disconnected:'가상 미연결',reconnecting:'가상 재연결 중',error:'가상 오류'};
// Relocate existing panels only in simulator mode; their renderers remain unchanged.
if(window.location.pathname==='/broadcast'&&document.body.dataset.studioLayout!=='managed'){
 const view=document.querySelector('[data-view="broadcast"]');
 const target=view;
 if(target){const cards=['sim-controls','sim-summary','chat-monitor','listeners-list','chat-send-form'].map(id=>el(id)?.closest('.dj-card')).filter(Boolean);for(const card of cards)target.appendChild(card);const preview=target.querySelector('.preview')?.closest('.dj-card');if(preview)target.appendChild(preview);}
}
function render(data){
 if(data.simulation!==true)throw Error('Invalid simulation response');
 const broadcast=labels[data.broadcast_state]||'가상 상태 확인 중',connection=labels[data.connection_state]||'가상 상태 확인 중';
 el('air-status').textContent='시뮬레이션 · '+broadcast;el('broadcast-status').textContent=connection;
 el('sim-state').textContent=broadcast+' · '+connection+(data.simulated_error?' · 가상 오류 '+data.simulated_error:'');
 el('sim-command').textContent=(data.last_command?'마지막 명령: '+data.last_command+' · ':'')+(data.last_result||'준비 완료')+(data.scenario_running?' · 시나리오 실행 중':'');
 const metrics=el('sim-metrics');metrics.replaceChildren();
 for(const [kind,label] of [['chat','채팅'],['like','하트'],['donation','후원']]){const card=document.createElement('p');card.textContent=`${label} · 생성 ${data.generated?.[kind]??0}건 / 처리 ${data.processed?.[kind]??0}건`;metrics.appendChild(card);}
 el('sim-extra').textContent=`관측 하트 ${data.hearts?.observed_amount??'—'}개 · 저장 후원 ${data.donation_count??0}건 / ${data.donation_spoons??'—'} 스푼 · 가상 청취자 ${data.listeners??0}명 · 팬 랭킹 ${data.fan_count??0}명 · 중복 ${data.duplicates??0}건 · 파싱 실패 ${data.invalid??0}건 · 마지막 수신 ${data.last_event_at||'없음'}`;
 el('sim-results').textContent=JSON.stringify(data,null,2);
 const signature=JSON.stringify(data);
 if(previous!==signature){previous=signature;window.dispatchEvent(new CustomEvent('doha:simulator-updated',{detail:data}));}
}
async function refresh(){if(reading)return;reading=true;try{const response=await fetch('/api/simulator/results',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error();render(await response.json());}catch{el('sim-state').textContent='로컬 시뮬레이터 조회 오류';el('sim-error').textContent='로컬 시뮬레이터 결과를 조회하지 못했어요. 실제 SPOON 연결 오류가 아닙니다.';}finally{reading=false;}}
async function action(command,count=1){if(busy)return;busy=true;for(const button of controls)button.disabled=true;el('sim-command').textContent='가상 명령 처리 중…';el('sim-error').textContent='';try{const values=new URLSearchParams({csrf:el('sim-csrf').value,action:command,count:String(count),scenario:el('sim-scenario').value});const response=await fetch('/api/simulator/control',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:values,signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error();render(await response.json());if(typeof refreshLive==='function')await refreshLive();}catch{el('sim-error').textContent='가상 명령을 처리하지 못했어요. 로컬 서버 상태를 확인해 주세요.';el('sim-command').textContent='명령 실패';}finally{busy=false;for(const button of controls)button.disabled=false;}}
const groups=[['방송',[['start','가상 방송 시작'],['end','가상 방송 종료'],['change','가상 방송 변경']]],['이벤트',[['join','가상 입장'],['leave','가상 퇴장'],['chat','가상 시청자 채팅'],['dj_chat','가상 DJ 채팅'],['bot_chat','가상 BOT 채팅'],['unknown_chat','가상 출처 미확인 채팅'],['like','가상 하트 +30'],['donation','가상 후원']]],['대량 이벤트',[['chat','채팅 10건',10],['chat','채팅 100건',100],['like','하트 30건',30],['donation','후원 30건',30]]],['연결 및 오류',[['duplicate','중복 재생'],['invalid','잘못된 이벤트'],['disconnect','연결 끊김'],['reconnect','재연결'],['401','가상 토큰 만료 401'],['403','가상 권한 부족 403'],['429','가상 호출 제한 429'],['500','가상 서버 오류 500'],['timeout','가상 타임아웃'],['uncertain','가상 전송 결과 불확실'],['recover','오류 복구']]]];
for(const [title,items] of groups){const group=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=title;group.appendChild(legend);for(const [command,label,count] of items){const button=document.createElement('button');button.type='button';button.textContent=label;button.addEventListener('click',()=>action(command,count||1));controls.push(button);group.appendChild(button);}el('sim-buttons').appendChild(group);}
for(const [id,command] of [['sim-run','run'],['sim-stop','stop'],['sim-reset','reset']]){controls.push(el(id));el(id).addEventListener('click',()=>action(command));}
setInterval(()=>{if(!document.hidden&&!busy)refresh();},2000);refresh();
})();
