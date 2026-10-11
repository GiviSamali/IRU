/* Prepared P0 regressions. No real provider/device/TTS requests. Owner runs separately. */
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {createServer}=require('./helpers/smart-ui-fixtures.cjs');
let browser,server,origin;
before(async()=>{({server}=createServer());await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'msedge',headless:true});});
after(async()=>{await browser?.close();if(server)await new Promise(resolve=>server.close(resolve));});
async function setup(width=1280){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[],writes=[];
 page.on('pageerror',error=>errors.push(error.message));
 let items=[],details={};
 await page.addInitScript(()=>{localStorage.setItem('iru_token','smart-ui-fixture');window.voiceChanges={start:0,stop:0};});
 await page.route('**/api/chats/1/messages',route=>route.fulfill({json:{status:'ok',messages:[]}}));
 await page.route('**/api/operations',route=>route.fulfill({json:{status:'ok',operations:items}}));
 await page.route('**/api/tasks/**',route=>{
  const req=route.request(),parts=new URL(req.url()).pathname.split('/'),id=parts[3];
  if(req.method()==='POST'){writes.push({id,path:parts[4],body:req.postDataJSON()});return route.fulfill({json:{status:'ok'}});}
  return route.fulfill({json:{status:'ok',task:details[id]}});
 });
 await page.goto(origin,{waitUntil:'networkidle'});await page.locator('#appRoot.active').waitFor();
 await page.evaluate(()=>{state.currentChatId=1;state.messages=[];renderMessages();});
 return {page,writes,errors,async snapshot(rows,taskDetails){items=rows;details=taskDetails;await page.evaluate(()=>IRUOperations.refresh());},setDetails(value){details=value;}};
}
const item=(id,status='running',chat_id=1)=>({task_id:id,chat_id,title:'Работа с документом '+id,status,device_ids:['Second'],summary:'Данные API',waiting_confirmation:status==='waiting_confirmation',can_cancel:['running','queued','waiting_confirmation'].includes(status)});
const task=(id,status='running',chat_id=1)=>({task_id:id,chat_id,status,kind:'worker',worker_id:'worker-1',message:'Работа с документом',device_ids:['Second'],answer:'Результат API',conversational_response:'Результат API',commands:[],tasks:[]});
for(const width of [1280,360])test(`P0 conversation, mirror, tasks and isolation at ${width}px`,async()=>{
 const f=await setup(width),p=f.page;try{
  assert.equal(await p.locator('#operationMirror').isVisible(),false);
  assert.equal(await p.locator('#operationsDock').isVisible(),false);
  await f.snapshot([item('a'),item('b','queued'),item('foreign','running',2)],{a:task('a'),b:task('b'),foreign:task('foreign','running',2)});
  assert.equal(await p.locator('#operationMirror').isVisible(),true);
  assert.equal(await p.locator('#mirrorContent [data-task-id]').getAttribute('data-task-id'),'a');
  await p.locator('#chatInput').fill('Продолжаем разговор');
  assert.equal(await p.locator('#btnSend').isEnabled(),true);
  const before=await p.evaluate(()=>({value:chatInput.value,voice:JSON.stringify(voiceChanges)}));
  await p.locator('#mirrorClose').click();await p.evaluate(()=>IRUOperations.refresh());
  assert.equal(await p.locator('#operationMirror').isVisible(),false);
  assert.equal(f.writes.length,0);
  await p.locator('#operationsToggle').click();
  assert.equal(await p.locator('#operationsDock').isVisible(),true);
  assert.equal(await p.locator('#operationsItems [data-task-id="foreign"]').count(),0);
  await p.locator('#operationsItems [data-task-id="b"] [data-operation="details"]').click();
  assert.equal(await p.locator('#mirrorContent [data-task-id]').getAttribute('data-task-id'),'b');
  await p.locator('#operationsClose').click();
  assert.equal(await p.locator('#operationsDock').isVisible(),false);
  assert.equal(await p.locator('#operationMirror').isVisible(),true);
  assert.deepEqual(await p.evaluate(()=>({value:chatInput.value,voice:JSON.stringify(voiceChanges)})),before);
  await p.evaluate(()=>{state.currentChatId=3;state.messages=[];renderMessages();});
  assert.equal(await p.locator('#operationMirror').isVisible(),false);
  assert.equal(await p.locator('#operationsItems [data-task-id]').count(),0);
  assert.equal(f.writes.length,0);assert.deepEqual(f.errors,[]);
  assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 }finally{await p.close();}
});
test('P0 command decision uses viewed nonce and refuses changed command',async()=>{
 const f=await setup(),p=f.page;try{
  const d={...task('confirm','confirm'),confirm_data:{confirmation_id:'old',command:'Remove-Item selected-file'}};
  await f.snapshot([item('confirm','waiting_confirmation')],{confirm:d});
  f.setDetails({confirm:{...d,confirm_data:{confirmation_id:'new',command:'Remove-Item other-file'}}});
  await p.locator('#mirrorContent [data-operation="confirm"]').click();
  await p.waitForTimeout(100);assert.equal(f.writes.length,0);
  await p.locator('#mirrorContent [data-operation="deny"]').click();
  await p.waitForTimeout(100);assert.equal(f.writes.length,1);
  assert.deepEqual(f.writes[0],{id:'confirm',path:'command-decision',body:{confirmation_id:'new',accepted:false,via_voice:false}});
 }finally{await p.close();}
});
test('P0 plan approval refuses changed revision',async()=>{
 const f=await setup(),p=f.page;try{
  const d={...task('plan','confirm'),plan_review:{revision:'r1',steps:[{title:'Read',instruction:'Read selected file'}]}};
  await f.snapshot([item('plan','waiting_confirmation')],{plan:d});
  f.setDetails({plan:{...d,plan_review:{revision:'r2',steps:[{title:'Changed',instruction:'Different operation'}]}}});
  await p.locator('#mirrorContent [data-operation="approve"]').click();
  await p.waitForTimeout(100);assert.equal(f.writes.length,0);
 }finally{await p.close();}
});

test('P0 result prose stays primary and partial stays partial',async()=>{
 const f=await setup(),p=f.page;try{
  const report={schema_version:1,task_id:'result',status:'partial',goal_completed:false,target_device_ids:['Second'],
   artifacts:[{path:'C:/IRU/scripts/helpers/swap_rows.ps1',device_id:'Second',verified:true}],summary:'Перестановка выполнена частично.'};
  await f.snapshot([item('result','partial')],{result:{...task('result','partial'),worker_report:report,
   conversational_response:'Перестановка выполнена частично. Остальные изменения не подтверждены.'}});
  await p.locator('#operationsToggle').click();await p.locator('#operationsItems [data-operation="details"]').click();
  assert.equal(await p.locator('#mirrorContent article').getAttribute('data-status'),'partial');
  assert.ok((await p.locator('#mirrorContent .mirror-answer').innerText()).includes('не подтверждены'));
  assert.ok(!(await p.locator('#mirrorContent .mirror-object').innerText()).includes('swap_rows'));
  assert.equal(await p.locator('#mirrorContent details').evaluate(node=>node.open),false);
 }finally{await p.close();}
});
