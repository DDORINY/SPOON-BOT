const fs=require('fs'),vm=require('vm'),assert=require('assert');
class E{constructor(){this.children=[];this.events={};this.value='invented';}appendChild(x){this.children.push(x);}addEventListener(k,fn){this.events[k]=fn;}}
const nodes=new Map(),el=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);};let queries=0;
const context={document:{body:{dataset:{simulator:'true',studioLayout:'managed'}},getElementById:el,createElement:()=>new E(),querySelector(){queries++;throw Error('Competing layout mutation');}},window:{location:{pathname:'/broadcast'}},URLSearchParams,AbortSignal:{timeout(){}},setInterval(){},fetch:()=>new Promise(()=>{})};
vm.createContext(context);
for(const asset of ['simulator.js','automation.js'])vm.runInContext(fs.readFileSync('src/spoon_local/web/static/'+asset,'utf8'),context);
assert.equal(queries,0);assert(el('sim-buttons').children.length>0);assert(el('automation-form').events.submit);assert(el('sim-run').events.click);
console.log('PASS: managed layout prevents simulator/automation remounting while preserving control bindings');
