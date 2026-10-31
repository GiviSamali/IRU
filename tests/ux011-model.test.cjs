const {test}=require('node:test'),assert=require('node:assert/strict');
const model=require('../ui/js/ux01-model.js');
const base={userId:2,chatId:1};
const message=(id,status,complete=false)=>({role:'assistant',goalId:id,content:'Сохранённый итог',goalPresentation:{goal_id:id,status,goal_completed:complete}});
test('old partial unknown and failed stay results during new work or listening',()=>{
 for(const status of ['partial','unknown','failed']){
  const old=message('old',status);
  assert.equal(model.derive({...base,messages:[old]}).core,'idle');
  const listening=model.derive({...base,messages:[old],voicePhase:'listening'});
  assert.equal(listening.core,'listening');assert.equal(listening.results[0].status,status);
  const working=model.derive({...base,messages:[old,message('new','running')]});
  assert.equal(working.core,'working');assert.equal(working.results[0].status,status);
 }
});
test('current chat projection never counts another conversation as its live work',()=>{
 const result=model.derive({...base,items:[{task_id:'foreign-chat',chat_id:9,status:'running'}]});
 assert.equal(result.core,'idle');assert.equal(result.active.length,0);assert.equal(result.results.length,0);
});
test('separating idle from outcomes never promotes unconfirmed success or mutates evidence',()=>{
 const input={...base,messages:[message('g','success',false)]},before=JSON.stringify(input);
 const result=model.derive(input);
 assert.equal(result.core,'idle');assert.equal(result.results[0].status,'unknown');
 assert.equal(JSON.stringify(input),before);
});
