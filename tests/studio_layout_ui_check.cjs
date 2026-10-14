const fs=require('fs'),vm=require('vm'),assert=require('assert');
class E{
 constructor(tag='div'){this.tagName=tag;this.children=[];this.attrs={};this.hidden=false;this.textContent='';this.events={};this.classList={add(){},remove(){},toggle(){}};}
 appendChild(item){if(item.parent)item.parent.children=item.parent.children.filter(x=>x!==item);this.children.push(item);item.parent=this;return item;}
 removeChild(item){this.children=this.children.filter(x=>x!==item);item.parent=null;}
 append(...items){items.forEach(x=>this.appendChild(x));}prepend(...items){for(const item of items.slice().reverse()){this.appendChild(item);this.children=[item,...this.children.filter(x=>x!==item)];}}
 setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}removeAttribute(k){delete this.attrs[k];}addEventListener(k,v){this.events[k]=v;}
 closest(selector){return this.card||this;}querySelector(selector){if(selector==='h2')return new E('h2');return null;}
 focus(){this.focused=true;}
 set innerHTML(_){throw Error('unsafe HTML');}
}
for(const sim of [false,true]){
 const map=new Map(),el=id=>{if(!map.has(id)){const x=new E();x.id=id;map.set(id,x);}return map.get(id);},view=new E(),events={};
 for(const id of ['chat-monitor','chat-send-form','listeners-list','donation-list','capture-unavailable']){el(id).card=new E();view.appendChild(el(id).card);}
 el('donation-list').card.hidden=true;el('donation-list').card.attrs['data-view']='home';
 const body=new E();body.dataset={simulator:String(sim)};el('broadcast').hidden=true;el('broadcast').attrs['data-view']='home';
 const document={body,readyState:'interactive',getElementById:id=>map.get(id)||null,createElement:tag=>new E(tag),querySelector:selector=>selector==='[data-view="broadcast"]'?view:el(selector),addEventListener:(name,fn)=>events['document:'+name]=fn};
 for(const id of ['page-title','page-description','heart-amount','air-status','broadcast-status','sim-controls','sim-summary','automation-panel','automation-form','auto-records','auto-advance','.sidebar','.topbar','.header-actions'])el(id);
 const context={document,window:{location:{pathname:'/broadcast'},addEventListener:(name,fn)=>events[name]=fn},MutationObserver:class{observe(){}}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('src/spoon_local/web/static/studio.js','utf8'),context);
 assert.equal(body.dataset.studioLayout,'managed');
 assert(!view.children.some(x=>x.className==='broadcast-columns'));
 assert(events['document:DOMContentLoaded']);events['document:DOMContentLoaded']();
 assert.equal(el('donation-list').card.hidden,false);assert.equal(el('donation-list').card.getAttribute('data-view'),undefined);
 const descendants=x=>[x,...x.children.flatMap(descendants)],all=descendants(view);
 assert(all.some(x=>x.className==='broadcast-columns'));assert(all.some(x=>x.className==='studio-center'));assert(all.some(x=>x.className==='studio-right'));
 assert(all.includes(el('chat-send-form').card));assert(all.includes(el('listeners-list').card));
 const tabs=all.filter(x=>x.attrs.role==='tab');const panels=all.filter(x=>x.attrs.role==='tabpanel');assert.equal(tabs.length,3);assert.equal(tabs[0].textContent,'봇 자동화');assert.equal(tabs[0].attrs['aria-selected'],'true');assert(!panels[0].hidden&&panels[1].hidden&&panels[2].hidden);tabs[1].events.click();assert(panels[0].hidden&&!panels[1].hidden);tabs[1].events.keydown({key:'ArrowRight',preventDefault(){}});assert(!panels[2].hidden&&tabs[2].focused);assert(all.includes(el('chat-send-form').card));
 const tools=all.find(x=>x.id==='studio-simulator-tools');assert.equal(Boolean(tools),sim);if(sim){assert(!tools.attrs.open);assert(!descendants(el('chat-monitor').card).includes(tools));tools.open=true;const guidance=descendants(el('chat-monitor').card).find(x=>x.tagName==='details');assert(guidance);guidance.open=true;assert(!descendants(guidance).includes(tools));assert.equal(el('broadcast').hidden,true);}else assert.equal(el('broadcast').hidden,false);
 // Stats never guess an unconfirmed real current-broadcast donation total.
 const metric=all.find(x=>x.id==='studio-spoons');assert.equal(metric.textContent,'—');
 assert(!fs.readFileSync('src/spoon_local/web/static/studio.js','utf8').includes('fetch('));
}
console.log('PASS: real/simulator layout, preserved control nodes, collapsed tools, unknown real totals, no network actions');
