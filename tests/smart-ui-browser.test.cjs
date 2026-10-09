const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('playwright');
const {messages,clone,createServer}=require('./helpers/smart-ui-fixtures.cjs');
let browser,server,origin,requests;
before(async()=>{({server,requests}=createServer());await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'msedge',headless:true});});
after(async()=>{await browser?.close();await new Promise(resolve=>server?.close(resolve));});
async function open(width=1280,height=1100){
 const page=await browser.newPage({viewport:{width,height}});page.setDefaultTimeout(8000);page.errors=[];page.on('pageerror',e=>page.errors.push(e.message));
 await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
 await page.addInitScript(()=>{
  localStorage.setItem('iru_token','smart-ui-fixture');
  window.speechCounts={instances:0,start:0,stop:0,abort:0};
  class FakeRecognition {constructor(){speechCounts.instances++;window.lastFakeRecognition=this;}start(){speechCounts.start++;queueMicrotask(()=>this.onstart?.());}stop(){speechCounts.stop++;this.onend?.();}abort(){speechCounts.abort++;this.onend?.();}}
  window.SpeechRecognition=window.webkitSpeechRecognition=FakeRecognition;
 });
 await page.goto(origin,{waitUntil:'networkidle'});await page.locator('#appRoot.active').waitFor();
 return page;
}
async function seed(page,data=messages,pending=[]){await page.evaluate(({data,pending})=>{state.messages=data;state.pendingTasks=pending;renderMessages();}, {data:clone(data),pending});}
const effectRequests=()=>requests.filter(r=>/^\/api\/tasks\/|\/api\/voice\/|\/command$|\/api\/download_request$|\/nl_command$/.test(r.path));
function compactFor(width){return width<=720;}
for(const width of [320,360,480,768,1280]){
 test(`one renderer, safe states and actions at ${width}px`,async()=>{
  const page=await open(width);try{
   assert.equal(await page.locator('[data-block-type="task"]').count(),4);
   assert.equal(await page.locator('[data-block-type="file"]').count(),1);
   assert.equal(await page.locator('[data-block-type="action"] .btn-confirm-yes').count(),1);
   assert.equal(await page.locator('[data-block-type="task"][data-status="blocked"]').count(),1);
   assert.equal(await page.locator('[data-block-type="task"][data-status="success"]').count(),1);
   assert.equal(await page.locator('[data-block-type="task"][data-status="running"]').count(),1);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert.ok(await page.locator('#chatMessages').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
   const confirm=page.locator('[data-action="confirm-task"]');assert.equal(await confirm.isVisible(),true);
   const bounds=await confirm.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+1);
   const text=page.locator('[data-message-key="smart-1-demo-completed"] .smart-text');
   const original=messages[2].content;assert.equal(await text.locator('.smart-text-content').textContent(),original);
   // Messages container is narrower than viewport when desktop sidebar is visible.
   if((await page.locator('.chat-view').boundingBox()).width<=720){await text.locator('.smart-text-toggle').click();assert.equal(await text.locator('button').getAttribute('aria-expanded'),'true');await text.locator('.smart-text-toggle').click();}
   await page.locator('#chatMessages').evaluate(el=>el.scrollTop=0);
   if(process.env.IRU_SCREENSHOT_DIR&&[320,480,1280].includes(width)){
    await page.screenshot({path:path.join(process.env.IRU_SCREENSHOT_DIR,`smart-ui-${width}.png`)});
    await page.locator('#chatMessages').evaluate(el=>el.scrollTop=el.scrollHeight);
    await page.screenshot({path:path.join(process.env.IRU_SCREENSHOT_DIR,`smart-ui-${width}-actions.png`)});
   }
   assert.deepEqual(page.errors,[]);
  }finally{await page.close();}
 });
}
test('container width inside wide viewport controls layout; resize keeps DOM, voice and data',async()=>{
 const page=await open();try{
  await seed(page,[{role:'assistant',content:'Первая строка\n'+('Длинный исходный текст.\n'.repeat(30)),taskStatus:'blocked',taskReceipt:{task_status:'partial',goal_completed:false},confirmTaskId:'demo-confirm',_taskId:'demo-confirm',commandConfirmation:messages[4].commandConfirmation}]);
  await page.locator('#voiceBtn').click();await page.waitForFunction(()=>speechCounts.start===1);
  const speech=await page.evaluate(()=>({...speechCounts})),baseline=effectRequests().length;
  const saved=await page.evaluate(()=>{window.originalSmartNode=document.querySelector('.smart-text-content');window.renderCount=0;window.originalRender=renderMessages;renderMessages=(...args)=>{window.renderCount++;return window.originalRender(...args);};return JSON.stringify([state.currentChatId,state.messages]);});
  for(const width of [320,360,480,768,1280]){
   await page.locator('#appRoot').evaluate((el,w)=>el.style.width=w+'px',width);
   await page.waitForTimeout(80);
   assert.ok(await page.locator('#appRoot').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
   assert.equal(await page.locator('.smart-text-toggle').isVisible(),(await page.locator('.chat-view').boundingBox()).width<=720);
   assert.equal(await page.locator('[data-action="confirm-task"]').isVisible(),true);
   assert.equal(await page.evaluate(()=>originalSmartNode===document.querySelector('.smart-text-content')),true);
   assert.equal(await page.evaluate(()=>renderCount),0);
   assert.equal(await page.evaluate(()=>JSON.stringify([state.currentChatId,state.messages])),saved);
   assert.deepEqual(await page.evaluate(()=>({...speechCounts})),speech);
  }
  await page.locator('#appRoot').evaluate(el=>el.style.width='360px');
  await page.locator('#mobileHeaderToggle').click();assert.equal(await page.locator('#headerActions').isVisible(),true);
  await page.keyboard.press('Escape');assert.equal(await page.locator('#headerActions').isVisible(),false);
  assert.equal(effectRequests().length,baseline);assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
test('text injection is inert, expansion and command details survive rerender',async()=>{
 const page=await open(480);try{
  const content='Первая\n\n'+('<img src=x onerror="window.bad=1">\n<script>window.bad=2</script>\n'.repeat(15));
  await seed(page,[{role:'assistant',content,taskStatus:'blocked',commands:messages[3].commands,_taskId:'safe'}]);
  assert.equal(await page.locator('.smart-text-content').textContent(),content);assert.equal(await page.locator('.smart-text-content img, .smart-text-content script').count(),0);
  await page.locator('.smart-text-toggle').click();await page.locator('.smart-task-toggle').click();
  await page.locator('.cmd-log').click();await page.locator('[data-action="toggle-cmd-entry"]').click();
  const n=effectRequests().length;await page.evaluate(()=>renderMessages());
  assert.equal(await page.locator('.smart-text').evaluate(el=>el.classList.contains('expanded')),true);
  assert.equal(await page.locator('.smart-task').evaluate(el=>el.classList.contains('expanded')),true);
  assert.equal(await page.locator('.cmd-entry').evaluate(el=>el.classList.contains('open')),true);
  assert.equal(await page.locator('.cmd-log').evaluate(el=>el.classList.contains('expanded')),true);
  assert.equal(await page.evaluate(()=>window.bad),undefined);assert.equal(effectRequests().length,n);
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
test('confirmation uses previous endpoint once; receipt remains blocked',async()=>{
 const page=await open(320);try{
  await seed(page,[messages[4]]);const before=effectRequests().length;
  await page.evaluate(()=>{renderMessages();renderMessages();});assert.equal(effectRequests().length,before);
  await page.locator('[data-action="confirm-task"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-block-type="task"]')?.dataset.status==='blocked');
  const decisions=effectRequests().slice(before).filter(r=>r.path.endsWith('/command-decision'));
  assert.equal(decisions.length,1);assert.deepEqual(JSON.parse(decisions[0].body),{confirmation_id:'demo-confirmation',accepted:true,via_voice:false});
  assert.equal(await page.locator('[data-action="confirm-task"]').count(),0);
  assert.equal(await page.locator('[data-block-type="task"][data-status="success"]').count(),0);
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
test('PLAN editing keeps draft across rerender and revision goes to previous API',async()=>{
 const page=await open(360);try{
  const m={role:'assistant',_taskId:'demo-plan',taskStatus:'confirm',content:'План\n1. Подготовить документ.',planReview:{revision:3,steps:[{title:'Подготовить документ',instruction:'Создать отчёт'}]}};
  await seed(page,[m]);await page.locator('[data-action="edit-plan"]').click();await page.locator('#plan-changes-0').fill('Добавить Excel');
  await page.evaluate(()=>renderMessages());assert.equal(await page.locator('#plan-changes-0').inputValue(),'Добавить Excel');assert.equal(await page.locator('#plan-changes-0').isVisible(),true);
  await page.locator('[data-action="revise-plan"]').click();
  await page.waitForFunction(()=>state.messages[0]?.loading);
  const request=requests.findLast(r=>r.path==='/api/tasks/demo-plan/review-plan');assert.deepEqual(JSON.parse(request.body),{revision:3,action:'revise',changes:'Добавить Excel'});
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
test('cancel and file download keep target and use existing APIs only',async()=>{
 const page=await open(480);try{
  await seed(page,[messages[1]],[{task_id:'demo-running',msgIndex:0}]);
  await page.evaluate(()=>pollTask('demo-running',0));
  await page.locator('[data-action="cancel-smart-task"]').click();await page.waitForFunction(()=>document.querySelector('.smart-task')?.dataset.status==='cancelled');
  assert.equal(requests.findLast(r=>r.path==='/api/tasks/demo-running/cancel').method,'POST');
  await seed(page,[messages[2]]);await page.locator('.smart-file-toggle').click();assert.equal(await page.locator('.smart-file-path').isVisible(),true);
  await page.locator('.smart-file [data-action="download-message-file"]').click();
  await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('mock'));
  const request=requests.findLast(r=>r.path==='/api/download_request');assert.deepEqual(JSON.parse(request.body),{device_id:'Second',file_path:'C:\\Users\\Demo\\Desktop\\report.docx'});
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
test('initial ordinary loading placeholder renders before a task ID or pending task exists',async()=>{
 const page=await open(360);try{
  await seed(page,[{role:'assistant',loading:true,currentStatus:'thinking',content:''}]);
  assert.equal(await page.locator('.smart-task[data-status="running"]').count(),1);
  assert.equal(await page.locator('[data-action="cancel-smart-task"]').count(),0);
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
test('partial PLAN and unknown command retain their meaning in expanded legacy details',async()=>{
 const page=await open(480);try{
  const command={tool_name:'execute_cmd',action:'execute_cmd',step_index:0,result:{},device_id:'Second'};
  await seed(page,[{role:'assistant',content:'Часть работы выполнена.',tasks:[{id:90,goal:'Частичный PLAN',status:'partial',steps:[{idx:0,title:'Частично выполненный шаг',status:'partial'}]}],commands:[command]}]);
  assert.equal(await page.locator('.smart-task').getAttribute('data-status'),'partial');
  await page.locator('.smart-task-toggle').click();
  assert.equal(await page.locator('.pipeline-progress-head').textContent().then(t=>t.includes('частично')),true);
  assert.equal(await page.locator('.step-status').textContent(),'частично');
  assert.equal(await page.evaluate(()=>getStepCommandStatusIcon(getCommandStatus(state.messages[0].commands[0]))),'○');
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});

test('medium text is not cropped without disclosure and rerender preserves reading anchor',async()=>{
 const page=await open(320);try{
  const content='ОченьДлинноеСлово'.repeat(10);
  await seed(page,[{role:'assistant',id:1,content}]);
  assert.equal(await page.locator('.smart-text-toggle').count(),0);
  assert.equal(await page.locator('.smart-text-content').evaluate(el=>getComputedStyle(el).webkitLineClamp),'none');
  const history=Array.from({length:24},(_,i)=>({role:'assistant',id:i+1,content:`Сообщение ${i} `+('исходный текст '.repeat(5))}));
  await seed(page,history);
  await page.locator('#chatMessages').evaluate(el=>{el.scrollTop=el.querySelector('[data-message-key="smart-1-12"]').offsetTop-el.offsetTop;});
  const before=await page.locator('[data-message-key="smart-1-12"]').evaluate(el=>el.getBoundingClientRect().top);
  await page.evaluate(()=>{state.messages[0].content+='\nБольшой новый результат.\n'.repeat(25);renderMessages();});
  const after=await page.locator('[data-message-key="smart-1-12"]').evaluate(el=>el.getBoundingClientRect().top);
  assert.ok(Math.abs(after-before)<2,`reading anchor moved ${after-before}px`);
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});

test('legacy step-indexed logs do not fabricate a complete PLAN or progress',async()=>{
 const page=await open(480);try{
  await seed(page,[{role:'assistant',content:'История операции.',commands:[{tool_name:'execute_cmd',action:'execute_cmd',step_index:0,result:{},device_id:'Second'}]}]);
  await page.locator('.smart-task-toggle').click();
  assert.equal(await page.locator('.task-block, .pipeline-progress').count(),0);
  assert.equal(await page.locator('.cmd-entry .cmd-status').textContent(),'○');
  assert.equal(await page.locator('.smart-task[data-status="success"]').count(),0);
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});

test('get_file_link is rendered from tool metadata and refreshes download on its exact device',async()=>{
 const page=await open(480);try{
  const command={action:'get_file_link',tool_name:'get_file_link',status:'success',device_id:'Second',result:{url:'/api/download/012abc',file_path:'C:\\Users\\Demo\\Desktop\\linked.docx'}};
  const before=effectRequests().length;
  await seed(page,[{role:'assistant',content:'Ссылка: /api/download/012abc',commands:[command]}]);
  await page.evaluate(()=>{state.selectedDevice='givi';renderMessages();renderMessages();});
  assert.equal(await page.locator('[data-block-type="file"]').count(),1);
  assert.equal(await page.locator('a[href="/api/download/012abc"]').count(),0);
  assert.equal(effectRequests().length,before);
  await page.locator('.smart-file [data-action="download-message-file"]').click();
  await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('mock'));
  const request=requests.findLast(r=>r.path==='/api/download_request');
  assert.deepEqual(JSON.parse(request.body),{device_id:'Second',file_path:command.result.file_path});
  await seed(page,[{role:'assistant',content:'Ссылка: /api/download/012abc'}]);
  assert.equal(await page.locator('[data-block-type="file"]').count(),0);
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
test('runtime done does not paint failed/partial/blocked nested steps as completed',async()=>{
 const page=await open(360);try{
  for(const status of ['failed','partial','blocked']){
   const task={id:94,status:'completed',goal:'Проверка приоритетов',steps:[{idx:0,title:'Проблемный шаг',status}]};
   await seed(page,[{role:'assistant',content:'Ответ получен.',taskStatus:'done',tasks:[task]}]);
   assert.equal(await page.locator('.smart-task').getAttribute('data-status'),status);
   assert.equal(await page.locator('.smart-task[data-status="success"]').count(),0);
   await page.locator('.smart-task-toggle').click();
   const label={failed:'ошибка',partial:'частично',blocked:'заблокировано'}[status];
   assert.ok((await page.locator('.pipeline-progress-head').textContent()).includes(label));
   await seed(page,[{role:'assistant',content:'Итог подтверждён.',taskStatus:'done',tasks:[task],taskReceipt:{task_status:'completed_with_recovery',final_verification_status:'verified'}}]);
   assert.equal(await page.locator('.smart-task').getAttribute('data-status'),'success');
  }
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});

for(const [width,height] of [[400,210],[400,280],[320,200]]){
 test('widget low height '+width+'x'+height+' preserves actions and voice',async()=>{
  const page=await open(width,height);try{
   await seed(page,[messages[2],messages[3],messages[4]]);
   await page.locator('#voiceBtn').click();await page.waitForFunction(()=>speechCounts.start===1);
   const counts=await page.evaluate(()=>({...speechCounts}));
   const baseline=effectRequests().length;
   for(const size of [{width,height},{width:1000,height:700},{width,height}]){
    await page.setViewportSize(size);
    await page.waitForTimeout(90);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    const input=await page.locator('#chatInput').boundingBox();
    const mic=await page.locator('#voiceBtn').boundingBox();
    const send=await page.locator('#btnSend').boundingBox();
    for(const box of [input,mic,send])assert.ok(box && box.y>=0 && box.y+box.height<=size.height+1,JSON.stringify(box));
    assert.ok(input.x+input.width<=mic.x+1 || mic.x+mic.width<=input.x+1);
    assert.ok(input.x+input.width<=send.x+1);
    await page.locator('[data-action="confirm-task"]').scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
    const c=await page.locator('[data-action="confirm-task"]').boundingBox();
    const chat=await page.locator('#chatMessages').boundingBox();
    assert.ok(c.y>=chat.y-1 && c.y+c.height<=chat.y+chat.height+1,JSON.stringify({c,chat}));
    assert.equal(await page.locator('.btn-confirm-no').count(),1);
    assert.equal(await page.locator('[data-block-type="task"][data-status="blocked"]').count(),1);
    assert.equal(await page.locator('[data-block-type="file"]').count(),1);
    assert.deepEqual(await page.evaluate(()=>({...speechCounts})),counts);
   }
   assert.equal(effectRequests().length,baseline);assert.deepEqual(page.errors,[]);
  }finally{await page.close();}
 });
}
test('widget authentication is scrollable at low height',async()=>{
 const page=await browser.newPage({viewport:{width:400,height:210}});try{
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await page.goto(origin);await page.locator('#authInput').waitFor();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  for(const id of ['authInput','authBtn']){
   await page.locator('#'+id).scrollIntoViewIfNeeded();
   const b=await page.locator('#'+id).boundingBox();assert.ok(b.y>=0&&b.y+b.height<=210);
  }
  assert.equal(await page.locator('.auth-contact a').count(),2);
 }finally{await page.close();}
});

for(const width of [400,1280]){
 test('A-FIX compact success and retained journal at '+width,async()=>{
  const page=await open(width,600);try{
   const m={id:901,role:'assistant',content:'Вкладка открыта.',taskStatus:'done',taskElapsedMs:1800,
    taskReceipt:{task_status:'completed',goal_completed:true},taskMode:'ordinary',_taskId:'simple-final',tasks:[],
    commands:[{tool_name:'app.open_url',step_id:'step_1',device_id:'Second',status:'success',result:{status:'opened_verified',url:'https://example.invalid'}}]};
   await seed(page,[m]);const row=page.locator('.smart-task');
   assert.equal(await row.evaluate(el=>el.classList.contains('compact-result')),true);
   assert.equal(await row.getAttribute('data-status'),'success');
   assert.equal(await row.locator('.smart-task-title').count(),0);
   assert.ok((await row.boundingBox()).height<=60);
   const before=effectRequests().length;
   await row.locator('[data-action="toggle-smart-details"]').click();
   assert.equal(await row.locator('.smart-task-details').isVisible(),true);
   assert.equal(await row.locator('.cmd-log').count(),1);
   await page.evaluate(()=>renderMessages());
   assert.equal(await row.locator('.smart-task-details').isVisible(),true);
   assert.equal(effectRequests().length,before);
   await page.route('**/api/chats/1/messages',route=>route.fulfill({json:{status:'ok',messages:[m]}}));
   await page.evaluate(async()=>{state.messages=[];await openChat(1);});
   assert.equal(await page.locator('.smart-task').getAttribute('data-status'),'success');
   assert.equal(await page.locator('.compact-result').count(),1);
   for(const update of [{taskMode:'plan'},{taskElapsedMs:45000},{commands:[...m.commands,...m.commands]},
      ...['partial','blocked','failed','unknown'].map(status=>({taskStatus:status,taskReceipt:{task_status:status}}))]){
    await seed(page,[{...m,...update}]);assert.equal(await page.locator('.compact-result').count(),0);
    if(update.taskStatus)assert.equal(await page.locator('.smart-task').getAttribute('data-status'),update.taskStatus);
   }
   assert.deepEqual(page.errors,[]);
  }finally{await page.close();}
 });
}
test('A-FIX history preserves PLAN receipt and never relabels unknown as waiting',async()=>{
 const page=await open(400,300);try{
  const history=[
   {id:1,role:'assistant',content:'Итог проверен.',taskStatus:'completed_with_recovery',taskMode:'plan',taskElapsedMs:45000,
    taskReceipt:{task_status:'completed_with_recovery',final_verification_status:'verified'},tasks:[{status:'done',steps:[{status:'failed'}]}]},
   {id:2,role:'assistant',content:'Неподтверждённый исход.',taskStatus:'unknown'},
   {id:3,role:'assistant',content:'Ожидание.',taskStatus:'pending'},
   {id:4,role:'assistant',content:'Только старый журнал.',commands:[{tool_name:'execute_cmd',result:{}}]},
  ];
  await page.route('**/api/chats/1/messages',route=>route.fulfill({json:{status:'ok',messages:history}}));
  for(let i=0;i<2;i++){
   await page.evaluate(async()=>{state.messages=[];await openChat(1);});
   assert.deepEqual(await page.locator('.smart-task').evaluateAll(nodes=>nodes.map(n=>n.dataset.status)),['success','unknown','waiting','unknown']);
   assert.equal(await page.locator('.compact-result').count(),0);
   assert.ok((await page.locator('.smart-task[data-status="unknown"]').first().textContent()).includes('Результат не подтверждён'));
   assert.equal(await page.locator('[data-action="confirm-task"]').count(),0);
  }
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});


test('OW real page: voice asks three questions during Worker; result stays bound across chat switch',async()=>{
 const page=await open(400,600);try{
  await seed(page,[]);
  const jobs=new Map();let questions=0;
  await page.route('**/api/voice/tasks/**/speech?*',route=>route.fulfill({status:204}));
  await page.route('**/nl_command',route=>{
   const body=JSON.parse(route.request().postData());assert.equal(body.orchestrate,true);assert.ok(body.request_id);
   const dialogue='dialogue-'+questions;questions++;
   jobs.set(dialogue,{task_id:dialogue,chat_id:1,status:'done',kind:'orchestrator',device_ids:[],message:body.message,answer:'Ответ '+questions,commands:[{tool_name:'answer.text',status:'terminal',result:{answer_type:'pure_text'}}],tasks:[],task_mode:'conversation',elapsed_ms:20});
   if(questions===1)jobs.set('worker-A',{task_id:'worker-A',chat_id:1,status:'running',kind:'worker',worker_id:'worker-1',device_ids:['Second'],message:'Презентация',answer:'',commands:[],tasks:[],task_mode:'ordinary',elapsed_ms:10,worker_report:{schema_version:1,status:'running',goal_completed:false},current_step:'Подготовка'});
   route.fulfill({json:{status:'ok',response_type:'orchestrator',task_id:dialogue,chat_id:1,worker_task_id:questions===1?'worker-A':null,worker_status:'running'}});
  });
  await page.route('**/api/tasks/*',route=>{
   const id=new URL(route.request().url()).pathname.split('/').pop();
   route.fulfill({json:{status:'ok',task:jobs.get(id)}});
  });
  await page.locator('#voiceBtn').click();
  await page.waitForFunction(()=>window.lastFakeRecognition && speechCounts.start===1);
  for(const text of ['Иру создай презентацию','Иру объясни квантовый компьютер','Иру объясни фотосинтез','Иру что такое нейрон']){
   const count=questions;
   await page.evaluate(text=>{const result=[{transcript:text}];result.isFinal=true;window.lastFakeRecognition.onresult({resultIndex:0,results:[result]});},text);
   await page.waitForFunction(count=>document.querySelectorAll('.msg').length>0 && state.messages.filter(m=>m.role==='user').length>=count+1,count);
   await page.waitForFunction(count=>state.messages.some(m=>m.content==='Ответ '+(count+1)) && (window.iruVoice.phase==='listening'||window.iruVoice.phase==='idle'),count);
   assert.equal(jobs.get('worker-A').status,'running');
  }
  assert.equal(questions,4);assert.equal(await page.evaluate(()=>state.messages.filter(m=>m._taskId==='worker-A').length),1);
  const sourceContents=await page.evaluate(()=>state.messages.filter(m=>m.taskKind==='orchestrator'||m.content.startsWith('Ответ')).map(m=>m.content));
  assert.equal(sourceContents.filter(text=>text.startsWith('Ответ')).length,4);
  await page.route('**/api/chats/2/messages',route=>route.fulfill({json:{messages:[{role:'assistant',content:'Другой чат'}]}}));
  await page.evaluate(()=>openChat(2));
  jobs.get('worker-A').status='done';jobs.get('worker-A').answer='Поздний результат A';
  await page.waitForTimeout(1000);
  assert.deepEqual(await page.evaluate(()=>state.messages.map(m=>m.content)),['Другой чат']);
  assert.equal(await page.evaluate(()=>state.pendingTasks.some(t=>t.task_id==='worker-A')),false);
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
test('OW SQL placeholders are restored once and queued tasks do not claim running',async()=>{
 const page=await open(400,280);try{
  await seed(page,[{role:'assistant',_taskId:'queued-A',taskKind:'worker',taskStatus:'queued',loading:true,content:''}]);
  assert.equal(await page.locator('.smart-task').getAttribute('data-status'),'waiting');
  await page.evaluate(()=>{sessionStorage.setItem('iru_active_tasks',JSON.stringify([{taskId:'queued-A',chatId:1}]));state.pendingTasks=[];});
  await page.route('**/api/tasks/queued-A',route=>route.fulfill({json:{status:'ok',task:{task_id:'queued-A',chat_id:1,status:'queued',kind:'worker',worker_id:'worker-1',message:'Очередь',commands:[],tasks:[],worker_report:{status:'queued'},created_at:Date.now()/1000}}}));
  await page.evaluate(()=>{restoreActiveChatTasks(1);restoreActiveChatTasks(1);});
  assert.equal(await page.evaluate(()=>state.messages.filter(m=>m._taskId==='queued-A').length),1);
  assert.equal(await page.evaluate(()=>state.pendingTasks.filter(m=>m.task_id==='queued-A').length),1);
  await page.waitForTimeout(500);assert.equal(await page.locator('.smart-task').getAttribute('data-status'),'waiting');
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});


test('OW global Stop selects running Worker by task ID ahead of queued jobs',async()=>{
 const page=await open(400,600);try{
  await seed(page,[{role:'assistant',_taskId:'running-A',taskKind:'worker',taskStatus:'running',loading:true,content:''},{role:'assistant',_taskId:'queued-B',taskKind:'worker',taskStatus:'queued',loading:true,content:''}],
   [{task_id:'running-A',kind:'worker',chatId:1,msgIndex:99},{task_id:'queued-B',kind:'worker',chatId:1,msgIndex:0}]);
  assert.equal(await page.evaluate(()=>getActivePendingTask().task_id),'running-A');
  await page.route('**/api/tasks/running-A/cancel',route=>route.fulfill({json:{status:'ok'}}));
  await page.evaluate(()=>cancelActiveTask());
  assert.equal(await page.evaluate(()=>state.messages.find(m=>m._taskId==='running-A').cancelRequested),true);
  assert.equal(await page.evaluate(()=>state.messages.find(m=>m._taskId==='queued-B').cancelRequested),undefined);
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
