const {test}=require('node:test'),assert=require('node:assert/strict');
const model=require('../ui/js/ux01-model.js');
const input={userId:2,chatId:1};
const message=(status='success',complete=status==='success')=>({role:'assistant',goalId:'g',taskKind:'goal',content:'Результат',
  goalPresentation:{goal_id:'g',status,goal_completed:complete}});
test('only canonical completion can make the core successful',()=>{
 assert.equal(model.derive({...input,messages:[message()]}).core,'success');
 assert.equal(model.derive({...input,messages:[message('success',false)]}).core,'unknown');
 assert.equal(model.derive({...input,messages:[{role:'assistant',taskStatus:'done',content:'Готово.'}]}).core,'idle');
});
for(const [status,core] of [['partial','partial'],['unknown','unknown'],['failed','error'],['blocked','error']])test(status+' is a distinct outcome',()=>{
 const result=model.derive({...input,messages:[message(status)]});assert.equal(result.core,core);assert.equal(result.results[0].status,status);
});
test('parallel Workers remain one Goal; member success is not goal success',()=>{
 const items=['a','b'].map(task_id=>({task_id,goal_id:'g',chat_id:1,status:'running',title:'Подготовить документы'}));
 const result=model.derive({...input,items});assert.equal(result.active.length,1);assert.equal(result.core,'working');
 items.forEach(i=>i.status='success');assert.notEqual(model.derive({...input,items}).core,'success');
});
test('ownership, chat isolation, retired attempts and empty account',()=>{
 const items=[{task_id:'a',chat_id:2,status:'running'},{task_id:'b',chat_id:1,status:'running',required_for_goal:false}];
 assert.equal(model.derive({...input,items}).active.length,0);
 assert.equal(model.derive({messages:[message()]}).results.length,0);
});
test('attention beats activity, ordinary conversation never invents work',()=>{
 assert.equal(model.derive({...input,messages:[{role:'assistant',confirmTaskId:'x'}]}).core,'awaiting');
 assert.equal(model.derive({...input,messages:[{role:'assistant',loading:true}]}).core,'thinking');
 assert.equal(model.derive({...input,voicePhase:'listening'}).core,'listening');
 assert.equal(model.derive({...input,messages:[{role:'assistant',content:'Привет'}]}).core,'idle');
});
test('old Operations outcomes cannot replace the latest canonical Goal result',()=>{
 const result=model.derive({...input,messages:[message()],items:[{task_id:'old',goal_id:'old',chat_id:1,status:'unknown'}]});
 assert.equal(result.core,'success');
});
test('a stale refresh is labelled stale, not a fabricated unknown outcome',()=>{
 const value=model.derive({...input,stale:true,items:[{task_id:'a',chat_id:1,status:'running'}]});
 assert.equal(value.core,'working');assert.equal(value.stale,true);
});
test('Goal revisions produce one result and the most recent canonical outcome',()=>{
 const a=message('partial'),b=message();
 const value=model.derive({...input,messages:[a,{...message('unknown'),goalId:'other',goalPresentation:{goal_id:'other',status:'unknown'}},b]});
 assert.equal(value.core,'success');assert.equal(value.results.filter(r=>r.message.goalId==='g').length,1);
});
