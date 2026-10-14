const fs=require('fs'),vm=require('vm'),assert=require('assert');
class E{
constructor(){this.children=[];this.listeners={};this.attrs={};this.textContent='';this.disabled=false;this.hidden=false;}
addEventListener(name,fn){this.listeners[name]=fn;}setAttribute(name,value){this.attrs[name]=value;}append(...children){this.children.push(...children);}appendChild(child){this.children.push(child);}replaceChildren(){this.children=[];}focus(){this.focused=true;}set innerHTML(value){throw Error('Unsafe HTML');}
}
const elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,new E());return elements.get(id);};
const docListeners={},windowListeners={},pending=[],calls=[];
const context={document:{getElementById:el,createElement:()=>new E(),hidden:false,addEventListener(name,fn){docListeners[name]=fn;}},window:{location:{pathname:'/broadcast'},addEventListener(name,fn){windowListeners[name]=fn;}},Intl,Date,Error,URLSearchParams,AbortController,AbortSignal,fetch(url,options){assert(url.startsWith('/api/live/'));calls.push({url,options});return new Promise(resolve=>pending.push(resolve));}};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/spoon_local/web/static/audience.js','utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const data=(kind,id=100)=>({state:'ready',items:kind==='fans'?[{nickname:'<script>name</script>',rank:2,spoon_count:null}]:[{nickname:'<img onerror=alert(1)>',imageUrl:'javascript:alert(1)'}],live_id:id,returned_count:1,next_cursor:kind==='listeners'?'opaque-next':null,queried_at_utc:'2026-10-14T00:00:00Z'});
const reply=(resolve,value)=>resolve({status:200,json:async()=>value});
(async()=>{
assert.equal(calls.length,0);
el('listeners-tab').listeners.click();assert.equal(calls.length,1);assert(el('listeners-refresh').disabled);
el('listeners-refresh').listeners.click();assert.equal(calls.length,1);
el('fans-tab').listeners.click();assert.equal(calls.length,2);assert(calls[0].options.signal.aborted);
reply(pending[1],data('fans'));await tick();reply(pending[0],data('listeners'));await tick();
assert.equal(el('listeners-list').children.length,0);assert.equal(el('fans-list').children[0].children[1].children[0].textContent,'2위 · <script>name</script>');assert.equal(el('fans-list').children[0].children[1].children[1].textContent,'후원량 정보 없음');
el('listeners-tab').listeners.keydown({key:'Home',preventDefault(){}});reply(pending[2],data('listeners'));await tick();assert(el('listeners-tab').focused);assert.equal(el('listeners-list').children[0].children[1].children[0].textContent,'<img onerror=alert(1)>');assert(!el('listeners-next').disabled);
el('listeners-next').listeners.click();assert(calls.at(-1).url.includes('cursor=opaque-next'));reply(pending[3],{...data('listeners'),next_cursor:null});await tick();assert(el('listeners-next').disabled);
windowListeners['doha:live-state']({detail:{state:'live',live:{live_id:101}}});assert.equal(el('listeners-list').children.length,0);assert.equal(el('fans-list').children.length,0);
el('fans-tab').listeners.click();context.document.hidden=true;docListeners.visibilitychange();assert(calls.at(-1).options.signal.aborted);reply(pending[4],data('fans'));await tick();assert.equal(el('fans-list').children.length,0);
context.document.hidden=false;windowListeners['doha:auth-lost']();assert(el('fans-state').textContent.includes('다시 연결'));
console.log('PASS: manual queries, duplicate guard, tab cancellation, late response ignored, XSS text, null amount, cursor paging, broadcast/auth/visibility cleanup');
})().catch(error=>{console.error(error);process.exitCode=1;});
