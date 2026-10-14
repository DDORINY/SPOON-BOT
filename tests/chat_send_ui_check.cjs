const fs=require('fs'),vm=require('vm'),assert=require('assert');
class E{constructor(){this.listeners={};this.value='';this.checked=false;this.disabled=true;this.textContent='';this.fields={};}addEventListener(k,fn){this.listeners[k]=fn;}querySelector(k){return this.fields[k]||(this.fields[k]=new E());}reportValidity(){return true;}set innerHTML(v){throw Error('Unsafe HTML');}}
const map=new Map(),el=id=>{if(!map.has(id))map.set(id,new E());return map.get(id);},calls=[],events={};let outcome='sent';
const context={document:{getElementById:el},window:{location:{pathname:'/broadcast'},addEventListener(k,fn){events[k]=fn;}},AbortSignal,JSON,fetch:async(url,options)=>{assert(url.startsWith('/api/live/chat'));calls.push({url,options});return {ok:true,json:async()=>url.endsWith('/prepare')?{state:'ready',request_id:'invented-ticket'}:{state:outcome}};}};
vm.createContext(context);vm.runInContext(fs.readFileSync('src/spoon_local/web/static/chat-send.js','utf8'),context);
(async()=>{
assert.equal(calls.length,0);assert(el('chat-send-button').disabled);
await el('chat-prepare').listeners.click();el('chat-message').value='<img onerror=alert(1)>';el('chat-message').listeners.input();el('chat-notice').checked=true;el('chat-notice').listeners.change();assert(!el('chat-send-button').disabled);
const first=el('chat-send-form').listeners.submit({preventDefault(){}});assert(el('chat-send-button').disabled);await el('chat-send-form').listeners.submit({preventDefault(){}});await first;
assert.equal(calls.filter(c=>c.options.method==='POST').length,1);assert.equal(el('chat-message').value,'');assert(!map.has('chat-monitor'));
await el('chat-prepare').listeners.click();el('chat-message').value='😀';el('chat-message').listeners.input();assert(el('chat-length').textContent.startsWith('2 /'));outcome='uncertain';await el('chat-send-form').listeners.submit({preventDefault(){}});assert.equal(el('chat-message').value,'😀');assert(el('chat-send-state').textContent.includes('전송 여부를 확인할 수 없습니다'));const count=calls.length;await el('chat-send-form').listeners.submit({preventDefault(){}});assert.equal(calls.length,count);
events['doha:auth-lost']();assert(el('chat-send-button').disabled);assert(!el('chat-notice').checked);
console.log('PASS: no automatic send, explicit acknowledgement, duplicate click guard, UTF-16 count, success clears draft, uncertainty preserves draft, no synthetic SSE message');
})().catch(e=>{console.error(e);process.exitCode=1;});
