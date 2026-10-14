"use strict";
(()=>{
if(window.location.pathname!=='/broadcast')return;
const el=id=>document.getElementById(id);
// Claim layout ownership before subsequent deferred simulator scripts execute.
document.body.dataset.studioLayout='managed';
const sim=()=>Boolean(document.body&&document.body.dataset.simulator==='true');
function node(tag,className,text){const item=document.createElement(tag);item.className=className;if(text)item.textContent=text;return item;}
function metric(id,label){const card=node('div','studio-metric');card.appendChild(node('small','',label));const value=node('strong','','—');value.id=id;card.appendChild(value);return card;}
function fold(title,items){const details=node('details','studio-fold'),summary=node('summary','',title);details.appendChild(summary);for(const item of items)if(item)details.appendChild(item);return details;}
let detailSequence=0;
function detailDialog(title,items){
 const dialog=node('dialog','studio-detail-dialog'),heading=node('h2','',title),close=node('button','','닫기');close.type='button';dialog.id='studio-dialog-'+(++detailSequence);heading.id=dialog.id+'-title';dialog.setAttribute('aria-labelledby',heading.id);dialog.append(heading,close);for(const item of items)if(item)dialog.appendChild(item);
 const open=node('button','detail-open',title);open.type='button';open.setAttribute('aria-haspopup','dialog');open.setAttribute('aria-controls',dialog.id);open.setAttribute('aria-expanded','false');
 open.addEventListener('click',()=>{dialog.showModal();open.setAttribute('aria-expanded','true');});close.addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>{open.setAttribute('aria-expanded','false');open.focus();});document.body.appendChild(dialog);return open;
}
function managementCards(right){
 document.body.dataset.threeTabStudio='true';
 const existing=Array.from(right.children),bar=node('div','management-tabs'),panels=[],buttons=[];bar.setAttribute('role','tablist');bar.setAttribute('aria-label','방송 관리');
 for(const [name,label] of [['automation','봇 자동화'],['fans','후원 랭킹'],['listeners','실시간 청취자']]){
  const button=node('button','management-tab',label);button.type='button';button.id='management-tab-'+name;button.setAttribute('role','tab');button.setAttribute('aria-controls','management-panel-'+name);
  const panel=node('section','management-panel');panel.id='management-panel-'+name;panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);bar.appendChild(button);buttons.push(button);panels.push(panel);
 }
 function select(index,focus=false){buttons.forEach((button,i)=>{button.setAttribute('aria-selected',String(i===index));button.tabIndex=i===index?0:-1;panels[i].hidden=i!==index;});if(index>0){const original=el(index===1?'fans-tab':'listeners-tab');if(original&&typeof original.click==='function')original.click();}if(focus)buttons[index].focus();}
 buttons.forEach((button,index)=>{button.addEventListener('click',()=>select(index));button.addEventListener('keydown',event=>{let next;if(event.key==='ArrowRight')next=(index+1)%3;else if(event.key==='ArrowLeft')next=(index+2)%3;else if(event.key==='Home')next=0;else if(event.key==='End')next=2;else return;event.preventDefault();select(next,true);});});
 const auto=el('automation-panel');
 if(sim()&&auto){panels[0].appendChild(auto);for(const key of ['thanks','announcement']){const input=el('auto-'+key);if(input){const label=input.closest('label');if(label)auto.prepend(label);}}
 const settings=el('automation-form');if(settings){const holder=settings.parentElement;panels[0].appendChild(detailDialog('상세 설정',[settings]));if(holder&&holder.tagName==='DETAILS')holder.remove();}}
 else panels[0].appendChild(node('p','','자동화는 시뮬레이터에서만 사용할 수 있어요.'));
 for(const [index,name,title] of [[1,'fans','시청자 후원 랭킹'],[2,'listeners','실시간 청취자']]){panels[index].appendChild(node('h2','',title));const original=el(name+'-panel');if(original){original.setAttribute('role','region');original.setAttribute('aria-labelledby','management-tab-'+name);panels[index].appendChild(original);}}
 const total=node('p','','현재 청취자: —');total.id='studio-listener-summary';panels[2].prepend(total);
 // Existing hidden audience tabs retain their handlers; outer tabs trigger explicit queries.
 const oldAudience=existing[existing.length-1];if(oldAudience)oldAudience.hidden=true;
 for(const item of existing)if(item!==auto)right.removeChild(item);
 right.appendChild(bar);panels.forEach(panel=>right.appendChild(panel));select(0);
 const auxiliary=fold('관측 이벤트·연결 상세',existing.filter(item=>item!==auto&&item!==oldAudience));document.querySelector('[data-view="broadcast"]').appendChild(auxiliary);
 if(oldAudience){document.querySelector('[data-view="broadcast"]').appendChild(oldAudience);}
}
function set(id,value){const item=el(id);if(item)item.textContent=value;}
function setup(){
 const view=document.querySelector('[data-view="broadcast"]');if(!view||view.dataset?.studioMounted==='true')return;
 if(view.dataset)view.dataset.studioMounted='true';
 document.body.classList.add('broadcast-studio');view.classList.add('broadcast-workspace');
 el('page-title').textContent='방송 스튜디오';el('page-description').textContent='채팅과 응원을 한눈에, 방송에 집중하세요.';
 const stats=node('section','studio-statusbar');stats.setAttribute('aria-label','방송 관측 상태');
 for(const [id,label] of [['studio-air','방송 상태'],['studio-listeners',sim()?'가상 청취자':'현재 청취자'],['studio-hearts',sim()?'가상 관측 하트':'관측 하트'],['studio-spoons',sim()?'가상 저장 스푼':'현재 방송 후원 스푼'],['studio-helper',sim()?'가상 연결 상태':'도우미 연결 상태']])stats.appendChild(metric(id,label));
 const grid=node('div','broadcast-columns'),center=node('section','studio-center'),right=node('aside','studio-right');right.setAttribute('aria-label','방송 도우미');grid.append(center,right);
 const monitor=el('chat-monitor').closest('.dj-card'),send=el('chat-send-form').closest('.dj-card'),audience=el('listeners-list').closest('.dj-card'),hearts=el('heart-amount').closest('section'),donations=el('donation-list').closest('.dj-card'),preview=el('capture-unavailable').closest('.dj-card');
 monitor.classList.add('studio-chat');monitor.querySelector('h2').textContent='실시간 채팅';
 hearts.classList.add('studio-heart');donations.removeAttribute('data-view');donations.hidden=false;donations.classList.add('studio-events');const count=node('p','muted','현재 방송 후원 건수: — (회차 귀속 미확정)');count.id='studio-donation-count';donations.appendChild(count);donations.appendChild(hearts);donations.appendChild(node('p','muted','최근 저장 기록입니다. 현재 방송 합계나 전체 후원량을 의미하지 않습니다.'));right.appendChild(donations);
 send.classList.add('studio-composer');monitor.appendChild(send);center.appendChild(monitor);
 const helper=el('broadcast');
 if(!sim()){helper.removeAttribute('data-view');helper.hidden=false;helper.classList.add('studio-helper-card');right.appendChild(fold('방송 연결 및 정보', [helper]));}
 const auto=el('automation-panel');if(sim()&&auto){auto.hidden=false;auto.classList.add('studio-automation');const form=el('automation-form');auto.appendChild(fold('자동화 설정 열기',[form]));right.appendChild(auto);}else right.appendChild(node('section','dj-card','자동화는 시뮬레이션에서만 사용할 수 있어요.'));
 audience.classList.add('studio-audience');right.appendChild(fold('청취자 · 이번 방송 팬 랭킹',[audience]));
 // Keep state/errors and the mandatory send consent visible; fold secondary prose.
 const guidance=Array.from(monitor.children).filter(item=>item.tagName==='A'||(item.tagName==='P'&&item.classList.contains('muted')));
 guidance.push(...Array.from(send.children).filter(item=>item.tagName==='P'&&item.id!=='chat-send-state'));
 monitor.appendChild(fold('연결·수신 안내 자세히 보기',guidance));
 managementCards(right);
 view.prepend(stats,grid);
 const fit=()=>{if(typeof grid.getBoundingClientRect!=='function')return;const height=window.innerHeight-grid.getBoundingClientRect().top-96;grid.style.setProperty('--studio-available-height',Math.max(560,Math.floor(height))+'px');};
 fit();window.addEventListener('resize',fit);
 if(sim()){
   const tools=fold('시뮬레이터 도구',[el('sim-controls'),el('sim-summary')]);tools.id='studio-simulator-tools';
   if(auto){const toolbar=auto.querySelector('.toolbar');const records=el('auto-records');auto.appendChild(detailDialog('Mock 처리 기록',[records]));tools.appendChild(node('p','muted','Mock 처리 기록은 오른쪽 자동화 탭에서 확인하세요.'));if(toolbar){const advance=el('auto-advance');tools.appendChild(advance);}}
   view.appendChild(tools);
 }
 view.appendChild(fold('방송 화면 미리보기 · 준비 중',[preview]));
 const sidebar=document.querySelector('.sidebar');sidebar.id='studio-navigation';
 const menu=node('button','studio-menu','메뉴');menu.type='button';menu.setAttribute('aria-controls',sidebar.id);menu.setAttribute('aria-expanded','false');
 menu.addEventListener('click',()=>{const opened=menu.getAttribute('aria-expanded')!=='true';menu.setAttribute('aria-expanded',String(opened));document.body.classList.toggle('studio-menu-open',opened);});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'){document.body.classList.remove('studio-menu-open');menu.setAttribute('aria-expanded','false');}});
 document.querySelector('.topbar').prepend(menu);
 set('studio-air',el('air-status').textContent);set('studio-helper',el('broadcast-status').textContent);
 // Observe existing safe text renderers. No additional network requests or start actions.
 new MutationObserver(()=>{set('studio-air',el('air-status').textContent);set('studio-helper',el('broadcast-status').textContent);}).observe(document.querySelector('.header-actions'),{subtree:true,childList:true,characterData:true});
}
window.addEventListener('doha:live-state',event=>{if(event.detail.state!=='live')set('studio-fan-summary','이번 방송 팬 랭킹: 방송·조회 상태 확인이 필요해요.');set('studio-listener-summary',event.detail.state==='live'&&event.detail.live?'현재 청취자 '+String(event.detail.live.listener_count)+'명':'현재 청취자: —');set('studio-listeners',event.detail.state==='live'&&event.detail.live?String(event.detail.live.listener_count)+'명':'—');});
window.addEventListener('doha:realtime-state',event=>{const data=event.detail;set('studio-hearts',data.permissions?.like?String(data.hearts.observed_amount)+'개':'—');set('studio-heart-summary',data.permissions?.like?'관측 하트 '+String(data.hearts.observed_amount)+'개 · 이벤트 '+String(data.hearts.event_count)+'건':'아직 확인된 하트가 없습니다.');});
window.addEventListener('doha:simulator-updated',event=>{if(!sim())return;const data=event.detail;set('studio-listeners',String(data.listeners)+'명');set('studio-listener-summary','가상 청취자 '+String(data.listeners)+'명');set('studio-heart-summary','가상 관측 하트 '+String(data.hearts.observed_amount)+'개 · 이벤트 '+String(data.hearts.event_count)+'건');set('studio-hearts',String(data.hearts.observed_amount)+'개');set('studio-spoons',String(data.donation_spoons)+' 스푼');set('studio-donation-count','가상 저장 후원 '+String(data.donation_count)+'건 · 현재 방송 합계가 아닙니다.');});
window.addEventListener('doha:donations',event=>{
 const list=el('studio-recent-donations');if(!list)return;list.replaceChildren();
 const data=event.detail,allowed=data.source_mode==='real'||(sim()&&data.source_mode==='simulator'),items=allowed&&Array.isArray(data.items)?data.items.slice(0,3):[];
 if(!items.length)list.appendChild(node('p','','아직 관측한 후원이 없습니다.'));
 for(const item of items){const row=node('p','',`${sim()?'가상':'익명'} · ${String(item.amount_spoons)} 스푼`);const stamp=node('small','', '발생 '+(typeof studioTime==='function'?studioTime(item.occurred_at_utc):'시각 확인 필요'));row.appendChild(stamp);list.appendChild(row);}
});
window.addEventListener('doha:audience-summary',event=>{
 const data=event.detail;
 if(event.detail.kind!=='fans')return;
 const box=el('studio-fan-summary');if(!box)return;box.replaceChildren();
 if(data.state!=='ready'){box.appendChild(node('p','','이번 방송 팬 랭킹: 확인할 수 없어요.'));return;}
 const items=Array.isArray(data.items)?data.items.slice(0,3):[];
 if(!items.length)box.appendChild(node('p','','조회된 팬 랭킹이 없습니다.'));
 for(const item of items)box.appendChild(node('p','',`${item.rank}위 · ${item.nickname||'표시명 없음'} · ${item.spoon_count===null?'후원량 미확인':item.spoon_count+' 스푼'}`));
});
if(document.readyState!=='complete')document.addEventListener('DOMContentLoaded',setup,{once:true});else setup();
})();
