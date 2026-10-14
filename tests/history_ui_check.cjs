const fs=require('fs'),vm=require('vm'),assert=require('assert');
class Element{
constructor(){this.listeners={};this.children=[];this.value='';this.disabled=true;this.textContent='';this.fields={};}
addEventListener(name,fn){this.listeners[name]=fn;}append(...children){this.children.push(...children);}appendChild(child){this.children.push(child);}replaceChildren(){this.children=[];}querySelector(name){return this.fields[name]||(this.fields[name]=new Element());}reportValidity(){return true;}set innerHTML(value){throw Error('Unsafe HTML');}
}
const elements=new Map();const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
el('history-period').value='all';el('history-size').value='12';el('history-access').action='/auth/spoon/history/allow';el('history-access').querySelector('[name=csrf]').value='invented-csrf';
const calls=[];let forbidden=false;
const context={document:{getElementById:el,createElement:()=>new Element()},window:{location:{pathname:'/donations'}},Intl,Date,Error,URLSearchParams,AbortSignal,fetch:async(url,options)=>{
assert(String(url).startsWith('/'));calls.push({url:String(url),options});
if(String(url).includes('/allow'))return {ok:true,status:200};
if(forbidden)return {ok:false,status:403};
return {ok:true,status:200,json:async()=>({snapshot:25,items:[{amount_spoons:'<img onerror=alert(1)>',time_basis:'occurred',display_at_utc:'2026-10-14T15:00:00Z',received_at_utc:'2026-10-14T15:00:01Z'}],statistics:{event_count:25,amount_spoons:'2.5',today_event_count:25,latest_at_utc:'2026-10-14T15:00:00Z'},total:25,pages:3,has_previous:true,has_next:true})};
}};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/spoon_local/web/static/history.js','utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
assert.equal(calls.length,0);assert(el('history-search').disabled);
await el('history-access').listeners.submit({preventDefault(){}});
assert.equal(calls.length,2);assert.equal(el('history-amount').textContent,'2.5 스푼');assert(el('history-list').children[0].children[0].textContent.includes('<img'));
el('history-next').listeners.click();await tick();assert(calls.at(-1).url.includes('page=2'));assert(calls.at(-1).url.includes('snapshot=25'));
el('history-period').value='today';el('history-period').listeners.change();assert(el('history-next').disabled);assert(el('history-prev').disabled);
el('history-filter').listeners.submit({preventDefault(){}});await tick();assert(calls.at(-1).url.includes('page=1'));assert(!calls.at(-1).url.includes('snapshot='));
forbidden=true;el('history-refresh').listeners.click();await tick();assert(el('history-search').disabled);assert.equal(el('history-list').children.length,0);
console.log('PASS: explicit access, no automatic requests, snapshot pagination, filter reset, text-only XSS, access revocation');
})().catch(error=>{console.error(error);process.exitCode=1;});
