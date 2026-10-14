const fs=require('fs'),vm=require('vm'),assert=require('assert');
class E{
constructor(){this.children=[];this.dataset={};this.listeners={};this.hidden=false;this.disabled=false;this.textContent='';this.scrollHeight=500;this.scrollTop=400;this.clientHeight=100;}
addEventListener(name,fn){this.listeners[name]=fn;}append(...children){children.forEach(child=>this.appendChild(child));}appendChild(child){child.parent=this;this.children.push(child);}replaceChildren(...children){this.children=[];this.append(...children);}remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);}set innerHTML(value){throw Error('Unsafe HTML');}
}
const elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,new E());return elements.get(id);};
const context={document:{getElementById:el,createElement:()=>new E(),hidden:true},window:{location:{pathname:'/broadcast'},setTimeout(){}},Intl,Date,Error,URLSearchParams,AbortSignal,fetch(){throw Error('No upstream or automatic requests');}};
vm.createContext(context);
const source=fs.readFileSync('src/spoon_local/web/static/realtime.js','utf8').replace('poll();','globalThis.testRender=render;');vm.runInContext(source,context);
const chat=n=>({sequence:n,nickname:'<script>name</script>',message:'<img onerror=alert(1)>',is_dj:false,received_at_utc:'2026-10-14T00:00:00Z'});
const data={generation:'test-generation',state:'connected',active:true,permissions:{chat:true,like:true},chats:[chat(1)],hearts:{observed_amount:'30',event_count:1,last_received_at_utc:null},last_chat_received_at_utc:null};
context.testRender(data);assert.equal(el('chat-monitor').children.length,1);assert.equal(el('chat-monitor').children[0].children[2].textContent,'<img onerror=alert(1)>');assert.equal(el('heart-amount').textContent,'30 하트');
el('chat-monitor').scrollTop=100;context.testRender({...data,chats:[chat(1),chat(2)]});assert.equal(el('chat-monitor').scrollTop,100);assert.equal(el('chat-jump').hidden,false);
context.testRender({...data,chats:[chat(1),chat(2)]});assert.equal(el('chat-monitor').children.length,2);
el('chat-jump').listeners.click();assert(el('chat-jump').hidden);assert.equal(el('chat-monitor').scrollTop,500);
context.testRender({...data,permissions:{chat:false,like:false},chats:[]});assert(el('chat-permission').textContent.includes('권한'));assert.equal(el('heart-amount').textContent,'—');
context.testRender({...data,state:'waiting_for_live',chats:[],generation:'new'});assert(el('realtime-status').textContent.includes('방송 시작 대기'));assert(el('chat-monitor').children[0].textContent.includes('아직 관측'));
console.log('PASS: literal XSS text, deduplicated rows, preserved reading position, explicit scroll, permission states, waiting state');
