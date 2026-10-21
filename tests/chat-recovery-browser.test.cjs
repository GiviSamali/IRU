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
test('Goal dependency wait survives a suspended-tab clock gap',async()=>{
 const f=await setup(),p=f.page;let reads=0;
 try{
  await p.route('**/api/tasks/queue-pause',route=>{
   reads++;return route.fulfill({json:{status:'ok',task:task('queue-pause',reads===1?'waiting_dependency':'success')}});
  });
  await p.evaluate(()=>{
   state.messages=[{role:'assistant',_taskId:'queue-pause',content:'',loading:true}];
   state.pendingTasks=[{task_id:'queue-pause',chatId:1,kind:'worker'}];
   window.queueVoice={finished:0,lost:0};
   window.iruVoice={watchTask(){},taskFinished(){queueVoice.finished++;},taskLost(){queueVoice.lost++;}};
   pollTask('queue-pause',0,undefined,1);
   const suspendedTime=Date.now()+2*3600000;
   Date.now=()=>suspendedTime;
  });
  await p.waitForFunction(()=>!taskPollLoops.has('queue-pause'));
  assert.equal(reads,2);
  assert.deepEqual(await p.evaluate(()=>queueVoice),{finished:1,lost:0});
  assert.equal(await p.evaluate(()=>state.messages[0].content),'Результат API');
  assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});
test('Background goal completion releases voice tracking after a chat switch',async()=>{
 const f=await setup(),p=f.page;
 try{
  await p.route('**/api/tasks/other-chat-result',route=>route.fulfill({json:{status:'ok',task:task('other-chat-result','success')}}));
  await p.evaluate(()=>{
   state.currentChatId=2;state.messages=[];
   state.pendingTasks=[{task_id:'other-chat-result',chatId:1,kind:'worker'}];
   window.queueVoice={finished:0,lost:0};
   window.iruVoice={watchTask(){},taskFinished(){queueVoice.finished++;},taskLost(){queueVoice.lost++;}};
   pollTask('other-chat-result',-1,undefined,1);
  });
  await p.waitForFunction(()=>!taskPollLoops.has('other-chat-result'));
  assert.deepEqual(await p.evaluate(()=>queueVoice),{finished:1,lost:0});
  assert.equal(await p.evaluate(()=>state.messages.length),0);
  assert.equal(await p.evaluate(()=>state.pendingTasks.length),0);
  assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});
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
  // The click starts an asynchronous details fetch; wait for the exact chosen task.
  await p.locator('#mirrorContent [data-task-id="b"]').waitFor();
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
test('Historical PLAN review has no executable UI controls',async()=>{
 const f=await setup(),p=f.page;
 try{
  const historical={...task('legacy-plan','confirm'),plan_review:{revision:'r1',steps:[{title:'Read',instruction:'Read selected file'}]}};
  await f.snapshot([item('legacy-plan','waiting_confirmation')],{'legacy-plan':historical});
  await p.locator('#mirrorContent .operation-confirmation-invalid').waitFor();
  for(const action of ['approve','revise','confirm','deny'])
   assert.equal(await p.locator(`#mirrorContent [data-operation="${action}"]`).count(),0);
  assert.ok((await p.locator('#mirrorContent').innerText()).includes('Результат API'));
  assert.equal(f.writes.length,0);
  assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});

test('Goal details show related tasks and dependencies without aggregate success',async()=>{
 const f=await setup(),p=f.page;
 try{
  const common={goal_id:'goal-one',goal_title:'Research and documents',goal_status:'waiting_dependency',goal_task_count:3};
  const rows=[{...item('research','failed'),...common,title:'Research'},
   {...item('word','waiting_dependency'),...common,title:'Word document',dependencies:[{source_task_id:'research'}]},
   {...item('slides','waiting_dependency'),...common,title:'Slides',dependencies:[{source_task_id:'research'}]}];
  await f.snapshot(rows,{research:task('research','failed'),word:task('word','waiting_dependency'),slides:task('slides','waiting_dependency')});
  await p.locator('#operationsToggle').click();
  await p.locator('#operationsItems [data-task-id="word"] [data-operation="details"]').click();
  await p.locator('#mirrorContent [data-task-id="word"]').waitFor();
  await p.waitForFunction(()=>document.querySelector('#mirrorContent')?.textContent.includes('Зависит от: Research'));
  const text=await p.locator('#mirrorContent').innerText();
  assert.ok(text.includes('Research and documents'));
  assert.ok(text.includes('Поручений: 3'));
  assert.ok(text.includes('Зависит от: Research'));
  assert.equal(await p.locator('#mirrorContent article').getAttribute('data-status'),'waiting');
  assert.equal(f.writes.length,0);
  assert.deepEqual(f.errors,[]);
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


test('window input uses the shown task/question binding; unrelated text remains a new request',async()=>{
 const f=await setup(),p=f.page,requests=[];
 try{
  const qid='a'.repeat(32), question={question_id:qid,phase:'waiting',question:'Какое окно выбрать?',options:[{title:'First'},{title:'Second'}]};
  f.setDetails({window:{...task('window','waiting_input'),input_required:question,answer:question.question,
   conversational_response:question.question,presentation_status:'waiting_input'}});
  await p.route('**/nl_command',async route=>{requests.push(route.request().postDataJSON());await route.fulfill({json:{status:'ok',response_type:'orchestrator',task_id:'reply-'+requests.length,worker_task_id:null}});});
  await p.route('**/api/tasks/reply-*',route=>route.fulfill({json:{status:'ok',task:{task_id:new URL(route.request().url()).pathname.split('/').pop(),chat_id:1,kind:'orchestrator',status:'done',answer:'Принято.',conversational_response:'Принято.',commands:[],tasks:[]}}}));
  await p.evaluate(()=>{state.messages=[{role:'assistant',_taskId:'window',content:'',loading:true}];renderMessages();pollTask('window',0);});
  await p.waitForFunction(()=>state.messages[0]?.inputRequired?.phase==='waiting');
  const savedQuestion=await p.evaluate(()=>state.messages[0]);
  await p.unroute('**/api/chats/1/messages');
  await p.route('**/api/chats/1/messages',route=>route.fulfill({json:{status:'ok',messages:[savedQuestion]}}));
  await p.reload({waitUntil:'networkidle'});
  await p.waitForFunction(()=>state.messages.some(row=>row.inputRequired?.phase==='waiting' && !row.loading));
  await p.locator('#chatInput').fill('Второе');await p.locator('#btnSend').click();
  await p.waitForFunction(()=>state.messages.every(row=>!row.loading));
  assert.equal(requests[0].input_task_id,'window');assert.equal(requests[0].input_question_id,qid);
  await p.locator('#chatInput').fill('Сколько свободного места?');await p.locator('#btnSend').click();
  await p.waitForFunction(()=>state.messages.every(row=>!row.loading));
  assert.equal(requests.length,2);assert.equal(requests[1].input_task_id,undefined);
  assert.equal(requests[1].input_question_id,undefined);
 }finally{await p.close();}
});


test('Owner can inspect historical operation missing from the last 20 jobs',async()=>{
 const f=await setup(),p=f.page;
 try{
  const recent=Array.from({length:20},(_,n)=>item('recent-'+n,'success',2));
  await f.snapshot(recent,{archived:{...task('archived','success'),conversational_response:'Saved historical report'}});
  await p.evaluate(()=>{
   state.messages=[{role:'assistant',content:'Saved historical report',taskKind:'worker',
     _taskId:'archived',taskStatus:'success'}];
   renderMessages();
  });
  await p.locator('#chatMessages [data-action="show-operation"]').click();
  await p.locator('#mirrorContent [data-task-id="archived"]').waitFor();
  await p.evaluate(()=>IRUOperations.refresh());
  assert.equal(await p.locator('#mirrorContent [data-task-id="archived"]').count(),1);
  assert.ok((await p.locator('#mirrorContent').innerText()).includes('Saved historical report'));
  assert.equal(await p.locator('#mirrorContent [data-operation="confirm"]').count(),0);
  assert.equal(f.writes.length,0);
  assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});

test('Owner may inspect a different chat task, but cannot act on its confirmation',async()=>{
 const f=await setup(),p=f.page;
 try{
  const elsewhere={...task('elsewhere','confirm',2),presentation_status:'waiting_confirmation',
   confirm_data:{confirmation_id:'remote',command:'Remove-Item protected-file'}};
  await f.snapshot([],{elsewhere});
  await p.evaluate(()=>IRUOperations.show('elsewhere'));
  await p.locator('#mirrorContent [data-task-id="elsewhere"]').waitFor();
  const body=await p.locator('#mirrorContent').innerText();
  assert.ok(body.includes('Чат №2'));
  assert.ok(body.includes('Только просмотр'));
  for(const action of ['confirm','deny','cancel'])
   assert.equal(await p.locator('#mirrorContent [data-operation="'+action+'"]').count(),0);
  await p.evaluate(()=>IRUOperations.refresh());
  assert.equal(await p.locator('#mirrorContent [data-task-id="elsewhere"]').count(),1);
  assert.equal(f.writes.length,0);
  assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});

test('Owner-scoped endpoint refusal prevents opening another user task',async()=>{
 const f=await setup(),p=f.page;
 try{
  await f.snapshot([],{});
  await p.route('**/api/tasks/denied',route=>route.fulfill({status:404,json:{detail:'Задача не найдена'}}));
  await assert.rejects(p.evaluate(()=>IRUOperations.show('denied')),/Задача недоступна или не найдена/);
  assert.equal(await p.locator('#mirrorContent [data-task-id="denied"]').count(),0);
  assert.equal(f.writes.length,0);
 }finally{await p.close();}
});


test('Result First shows one Goal answer and no sibling Worker chat messages',async()=>{
 const f=await setup(),p=f.page;
 try{
  f.setDetails({turn:{
   task_id:'turn',chat_id:1,status:'done',kind:'goal',goal_id:'one-goal',
   message:'Create two documents',device_ids:[],answer:'Готово. Подтверждены все 2 результата.',
   conversational_response:'Готово. Подтверждены все 2 результата.',
   presentation_status:'success',commands:[],tasks:[]
  }});
  await p.route('**/nl_command',route=>route.fulfill({json:{
   status:'ok',response_type:'orchestrator',task_id:'turn',goal_id:'one-goal',
   worker_task_ids:['word-task','slides-task'],worker_task_id:'word-task',chat_id:1
  }}));
  await p.evaluate(async()=>{
   document.querySelector('#chatInput').value='Create two documents';
   await sendMessage();
  });
  await p.waitForFunction(()=>!taskPollLoops.has('turn') &&
   state.messages.some(m=>m._taskId==='turn' && m.content?.includes('Готово.')));
  const observed=await p.evaluate(()=>({
   assistant:state.messages.filter(m=>m.role==='assistant').map(m=>({id:m._taskId,kind:m.taskKind,content:m.content})),
   pending:state.pendingTasks.map(m=>m.task_id)
  }));
  assert.equal(observed.assistant.length,1);
  assert.equal(observed.assistant[0].id,'turn');
  assert.ok(observed.assistant[0].content.includes('2 результата'));
  assert.equal(observed.assistant.filter(m=>m.kind==='worker').length,0);
  assert.equal(observed.pending.length,0);
  assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});
