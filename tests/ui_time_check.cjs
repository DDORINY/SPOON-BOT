const fs=require('fs'),vm=require('vm'),assert=require('assert'),context={Intl,Date};vm.createContext(context);vm.runInContext(fs.readFileSync('src/spoon_local/web/static/ui-time.js','utf8'),context);
const now=new Date('2026-10-15T00:00:00Z');
assert.equal(context.studioTime('2026-10-14T20:30:00Z',now),'오늘 05:30');
assert.equal(context.studioTime('2026-10-14T14:15:00Z',now),'어제 23:15');
assert(context.studioTime('2026-10-10T11:29:00Z',now).includes('10월 10일 20:29'));
for(const value of [null,undefined,'','nonsense'])assert.equal(context.studioTime(value,now),'시각 확인 불가');
console.log('PASS: Seoul date boundaries, today/yesterday, short times and invalid timestamps');
