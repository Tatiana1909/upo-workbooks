/* Read-only dashboard aggregation. Edition histories remain on the server. */
(function(root){
'use strict';
const mean=(rows,key)=>rows.length?Math.round(rows.reduce((sum,r)=>sum+(Number(r[key])||0),0)/rows.length):0;
function stats(course,rows){return {...course,users:new Set(rows.map(r=>r.user_id)).size,completed:rows.filter(r=>r.status==='completed').length,active:rows.filter(r=>r.status==='active').length,average_progress:mean(rows,'progress_percent'),average_success:mean(rows,'success_percent')}}
function combine(data){
 const aliases=new Map(),groups=new Map();
 for(const c of [...data.courses].sort((a,b)=>(b.edition_version||0)-(a.edition_version||0))){
  const id=c.group_id||c.course_id;aliases.set(c.course_id,id);
  if(!groups.has(id))groups.set(id,{...c,course_id:id,course_title:c.group_title||c.course_title});
 }
 const byUser=new Map();
 for(const record of data.records){
  const id=aliases.get(record.course_id);if(!id)continue;
  const key=JSON.stringify([record.user_id,id]),previous=byUser.get(key);
  const latest=!previous||String(record.updated_at)>String(previous.updated_at)?record:previous;
  byUser.set(key,{...latest,course_id:id,course_title:groups.get(id).course_title,
   attempts_count:(previous?.attempts_count||0)+(record.attempts_count||0),
   pending_review:(previous?.pending_review||0)+(record.pending_review||0),
   needs_recheck:(previous?.needs_recheck||0)+(record.needs_recheck||0)});
 }
 const records=[...byUser.values()],distribution={completed:0,active:0,not_started:0};
 records.forEach(r=>distribution[r.status]++);
 return {...data,records,courses:[...groups.values()].map(c=>stats(c,records.filter(r=>r.course_id===c.course_id))),distribution,
  kpi:{...data.kpi,users_active:new Set(records.filter(r=>r.status==='active').map(r=>r.user_id)).size,
   users_completed:new Set(records.filter(r=>r.status==='completed').map(r=>r.user_id)).size,
   courses_completed:distribution.completed,average_progress:mean(records,'progress_percent'),average_success:mean(records,'success_percent')}};
}
function directions(courses,records){
 return [...new Set(courses.map(c=>c.direction))].map(direction=>{
  const ids=new Set(courses.filter(c=>c.direction===direction).map(c=>c.course_id));
  return stats({course_id:direction,course_title:direction},records.filter(r=>ids.has(r.course_id)));
 });
}
const api={combine,directions};root.IFCMSummary=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
