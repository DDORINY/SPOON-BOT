"use strict";
function studioTime(value,now=new Date()){
 if(typeof value!=='string'||!value.trim())return '시각 확인 불가';
 const date=new Date(value);if(!Number.isFinite(date.getTime()))return '시각 확인 불가';
 const day=d=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(d);
 const clock=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hourCycle:'h23',hour:'2-digit',minute:'2-digit'}).format(date);
 const prefix=day(date)===day(now)?'오늘':day(date)===day(new Date(now.getTime()-86400000))?'어제':new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'long',day:'numeric'}).format(date);
 return prefix+' '+clock;
}
