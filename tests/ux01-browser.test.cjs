/* Local fake APIs. Screenshots are fixture evidence, never LIVE receipts. */
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {chromium}=require('playwright');
const {createServer}=require('./helpers/smart-ui-fixtures.cjs');
let browser,server,origin;
const shots=path.resolve(__dirname,'../../notes/ux01-20261031/screenshots');
before(async()=>{fs.mkdirSync(shots,{recursive:true});({server}=createServer({view:'overview'}));await new Promise(r=>server.listen(0,'127.0.0.1',r));origin=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'msedge',headless:true});});
after(async()=>{await browser?.close();if(server)await new Promise(r=>server.close(r));});
function result(status='success',files=1){
 const presentation={goal_id:'g',status,terminal:status!=='running',goal_completed:status==='success',completed_count:status==='success'?2:status==='partial'?1:0,total_count:2,task_ids:['a','b'],
  message:status==='success'?'Документы готовы. Можно открывать.':status==='running'?'Подготавливаю документы.':status==='partial'?'Первый документ готов. Второй создать не удалось.':status==='unknown'?'Запись могла пройти, но подтвердить результат не удалось.':'Создать документ не удалось: доступ к папке ограничен.',
  result_artifacts:Array.from({length:files},(_,i)=>({path:`C:\\Demo\\${i?'summary.txt':'report.txt'}`,name:i?'summary.txt':'report.txt',device_id:'Second',task_id:i?'b':'a',verified:true,content_verified:true}))};
 return [{role:'user',content:'Подготовь документы для встречи.'},{role:'assistant',_taskId:'turn',taskKind:'goal',resultFirst:true,goalId:'g',taskStatus:status,content:presentation.message,conversationalResponse:presentation.message,goalPresentation:presentation}];
}
async function setup(width=1200,height=800){
 const page=await browser.newPage({viewport:{width,height}}),errors=[],writes=[];page.setDefaultTimeout(8000);
 page.on('pageerror',e=>errors.push(e.message));
 let history=[],operations=[],detail={task_id:'approval',chat_id:1,status:'cancelled',kind:'worker',answer:'Действие отклонено.',commands:[],tasks:[]};
 await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
 await page.route('**/api/chats/*/messages',route=>route.fulfill({json:{status:'ok',messages:history}}));
 await page.route('**/api/operations',route=>route.fulfill({json:{status:'ok',operations}}));
 await page.route('**/api/tasks/**',route=>{const req=route.request();if(req.method()==='POST')writes.push({path:new URL(req.url()).pathname,body:req.postDataJSON()});return route.fulfill({json:{status:'ok',task:detail}});});
 await page.route('**/command',route=>{writes.push({path:'/command',body:route.request().postDataJSON()});return route.fulfill({json:{status:'ok',result:{returncode:0}}});});
 await page.route('**/nl_command',route=>{writes.push({path:'/nl_command',body:route.request().postDataJSON()});return route.fulfill({json:{status:'ok',task_id:'reply',response_type:'orchestrator',chat_id:1}});});
 await page.addInitScript(()=>{localStorage.setItem('iru_token','smart-ui-fixture');class Recognition{start(){queueMicrotask(()=>this.onstart?.());}stop(){this.onend?.();}abort(){this.onend?.();}}window.SpeechRecognition=window.webkitSpeechRecognition=Recognition;});
 await page.goto(origin,{waitUntil:'networkidle'});await page.locator('#appRoot.active').waitFor();
 return {page,errors,writes,async seed(data,items=[]){history=data;operations=items;await page.evaluate(data=>{state.currentChatId=1;state.messages=data;renderMessages();},data);await page.evaluate(()=>IRUOperations.refresh());},setHistory(data){history=data;},setDetail(value){detail=value;}};
}
async function noOverflow(page){assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'horizontal viewport overflow');}
test('Desktop overview is a workspace, core follows running to confirmed files, one Goal for two Workers',async()=>{
 const f=await setup(),p=f.page;try{
  await f.seed([]);assert.equal(await p.locator('#uxHero h1').innerText(),'Готов к разговору');
  await p.screenshot({animations:'disabled',path:path.join(shots,'desktop-idle.png')});
  const items=['a','b'].map(task_id=>({task_id,goal_id:'g',goal_title:'Подготовить документы для встречи',title:'Документ',chat_id:1,status:'running',device_ids:['Second']}));
  await f.seed(result('running',0),items);
  assert.equal(await p.locator('#uxActive [data-goal-id=g]').count(),1);
  assert.equal(await p.locator('#uxHero .ux-core').getAttribute('data-state'),'working');
  await p.screenshot({animations:'disabled',path:path.join(shots,'desktop-working.png')});
  await f.seed(result('success',2));
  assert.equal(await p.locator('#uxHero .ux-core').getAttribute('data-state'),'success');
  assert.equal(await p.locator('#uxResults [data-block-type=file]').count(),2);
  await p.locator('#uxResults [data-action=open-message-file]').first().click();
  assert.equal(f.writes.at(-1).body.device_id,'Second');assert.ok(f.writes.at(-1).body.params.command.includes('Get-Item -LiteralPath'));
  await p.locator('#uxOverview').evaluate(el=>el.scrollTo({top:0,behavior:'instant'}));
  await p.screenshot({animations:'disabled',path:path.join(shots,'desktop-results.png')});await noOverflow(p);assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});
for(const status of ['partial','unknown','failed'])test('Desktop '+status+' is visible and never green success',async()=>{
 const f=await setup(),p=f.page;try{
  await f.seed(result(status,status==='partial'?1:0));
  assert.equal(await p.locator('#uxHero .ux-core').getAttribute('data-state'),status==='failed'?'error':status);
  assert.ok((await p.locator('#uxResults').innerText()).includes(status==='unknown'?'подтвердить':status==='partial'?'Второй':'доступ'));
  await p.screenshot({animations:'disabled',path:path.join(shots,`desktop-${status}.png`)});
 }finally{await p.close();}
});
test('Approval uses existing nonce, reject works and stale request is not accepted as a new one',async()=>{
 const f=await setup(),p=f.page;try{
  const confirmation={role:'assistant',_taskId:'approval',confirmTaskId:'approval',taskStatus:'confirm',content:'Нужно разрешение',commandConfirmation:{confirmation_id:'shown-nonce',command:'Удалить выбранную копию',risk:'dangerous'}};
  await f.seed([{role:'user',content:'Удалить выбранную копию документа'},confirmation]);
  assert.equal(await p.locator('#uxHero .ux-core').getAttribute('data-state'),'awaiting');
  await p.screenshot({animations:'disabled',path:path.join(shots,'desktop-approval.png')});
  await p.locator('#uxDecisions [data-action=deny-task]').click();
  await p.waitForFunction(()=>!taskPollLoops.has('approval') && state.messages[1]?.content==='Действие отклонено.');
  assert.equal(f.writes.length,1);assert.equal(f.writes[0].body.confirmation_id,'shown-nonce');assert.equal(f.writes[0].body.accepted,false);
  await f.seed([{role:'user',content:'Удалить выбранную копию документа'},confirmation]);
  await p.locator('#uxDecisions [data-action=confirm-task]').click();assert.equal(f.writes[1].body.accepted,true);
  await p.waitForFunction(()=>!taskPollLoops.has('approval') && state.messages[1]?.content==='Действие отклонено.');
  await f.seed([{role:'user',content:'Удалить выбранную копию документа'},confirmation]);
  await p.evaluate(()=>{state.messages[1].commandConfirmation.confirmation_id='new-unseen-nonce';});
  await p.locator('#uxDecisions [data-action=confirm-task]').click();
  assert.equal(f.writes.length,2);assert.ok((await p.locator('#toast').innerText()).includes('изменилось'));
  await f.seed([{role:'assistant',content:'Подтверждение устарело.',taskStatus:'unknown'}]);
  assert.equal(await p.locator('#uxDecisions [data-action=confirm-task]').count(),0);
 }finally{await p.close();}
});
test('F5, view persistence, conversation switch and HTML escaping',async()=>{
 const f=await setup(),p=f.page;try{
  const data=result('success',1);data[1].content=data[1].conversationalResponse='<img src=x onerror=alert(1)> Документ готов.';
  await f.seed(data);assert.equal(await p.locator('#uxResults img').count(),0);assert.ok((await p.locator('#uxResults').innerText()).includes('<img'));
  await p.reload({waitUntil:'networkidle'});await p.evaluate(()=>openChat(1));
  assert.equal(await p.locator('#uxResults [data-block-type=file]').count(),1);
  await p.locator('#ux-tab-conversation').click();await p.reload({waitUntil:'networkidle'});await p.evaluate(()=>openChat(1));
  assert.equal(await p.locator('#chatMessages').isVisible(),true);assert.equal(await p.locator('#uxOverview').isVisible(),false);
  f.setHistory([{role:'assistant',content:'Второй разговор'}]);await p.evaluate(()=>openChat(2));
  assert.ok((await p.locator('#chatMessages').innerText()).includes('Второй разговор'));
  assert.ok(!(await p.locator('#chatMessages').innerText()).includes('Документ готов'));
  await noOverflow(p);assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});
test('Component catalogue is isolated, interactive and has no diagnostic application',async()=>{
 const f=await setup(),p=f.page;try{
  await p.locator('#ux-tab-components').click();
  assert.ok((await p.locator('#uxGallery').innerText()).includes('Демонстрация'));
  await p.locator('[data-ux-demo-state=unknown]').click();assert.equal(await p.locator('#uxGallery .ux-core').getAttribute('data-state'),'unknown');
  await p.locator('[data-ux-variant=compact]').click();assert.equal(await p.locator('#uxGallery .ux-core').getAttribute('class'),'ux-core small');
  await p.locator('[data-ux-entry=approval]').click();await p.locator('[data-ux-demo]').first().click();
  assert.equal(f.writes.length,0);assert.ok((await p.locator('#uxDemoNotice').innerText()).includes('ничего не отправлено'));
  await p.screenshot({animations:'disabled',path:path.join(shots,'desktop-components.png')});
  const nav=await p.locator('.ux-navigation').innerText();assert.ok(!/Диагностика|Topology|EvidenceStream|Dashboard/.test(nav));
  assert.equal(await p.locator('script[src*="agent-os"]').count(),0);
 }finally{await p.close();}
});
test('text submission, attachment, voice toggle and Stop retain existing routes',async()=>{
 const f=await setup(),p=f.page;try{
  await f.seed([]);f.setDetail({task_id:'reply',chat_id:1,status:'done',kind:'orchestrator',task_mode:'conversation',
    presentation_status:'success',answer:'Привет! Чем могу помочь?',conversational_response:'Привет! Чем могу помочь?',commands:[],tasks:[]});
  await p.locator('#fileInput').setInputFiles({name:'context.txt',mimeType:'text/plain',buffer:Buffer.from('Контекст встречи')});
  await p.locator('#chatInput').fill('Привет');await p.locator('#btnSend').click();
  await p.waitForFunction(()=>state.messages.some(m=>m.content==='Привет! Чем могу помочь?'));
  const sent=f.writes.find(w=>w.path==='/nl_command');assert.ok(sent.body.message.includes('context.txt') && sent.body.message.includes('Контекст встречи'));
  assert.ok((await p.locator('#uxResults').innerText()).includes('Чем могу помочь'));
  await p.locator('#voiceBtn').click();await p.waitForFunction(()=>document.getElementById('voiceBtn').getAttribute('aria-pressed')==='true');
  await p.locator('#voiceBtn').click();await p.waitForFunction(()=>document.getElementById('voiceBtn').getAttribute('aria-pressed')==='false');
  const active=result('running',0);active[1].loading=true;await f.seed(active);
  await p.route('**/api/goals/g/cancel*',route=>{f.writes.push({path:'/api/goals/g/cancel'});return route.fulfill({json:{status:'ok'}});});
  await p.evaluate(()=>{state.pendingTasks=[{task_id:'turn',kind:'goal',goalId:'g',chatId:1,msgIndex:1}];updateStopButton();});
  await Promise.all([p.waitForResponse(r=>r.url().includes('/api/goals/g/cancel')),p.locator('#btnStopTask').click()]);
  assert.equal(f.writes.filter(w=>w.path==='/api/goals/g/cancel').length,1);
 }finally{await p.close();}
});
test('long analysis stays complete and Mini remains scrollable without changing native sizing',async()=>{
 const f=await setup(),p=f.page;try{
  const data=result('partial',0),text='Вывод исследования. '+Array.from({length:90},(_,i)=>`Пункт ${i+1}: подтверждённые данные и ограничения.`).join('\n');
  data[1].content=data[1].conversationalResponse=text;await f.seed(data);
  assert.equal(await p.locator('#uxResults .ux-result-full').getAttribute('open'),null);
  await p.locator('#uxResults .ux-result-full summary').click();assert.ok((await p.locator('#uxResults').innerText()).includes('Пункт 90'));
  await p.setViewportSize({width:360,height:400});await p.evaluate(()=>setAdaptiveSurface(true));
  assert.ok(await p.locator('#chatMessages').evaluate(el=>el.scrollHeight>el.clientHeight));
  assert.ok((await p.locator('#adaptiveConversation').innerText()).includes('Пункт 90'));
  assert.equal(await p.locator('#adaptiveBody>#uxMiniStatus').count(),1);
  await p.locator('#chatMessages').evaluate(el=>el.scrollTop=0);await p.screenshot({animations:'disabled',path:path.join(shots,'mini-long-analysis.png')});await noOverflow(p);
 }finally{await p.close();}
});
test('device reconnect and open/closed sections preserve real data and current conversation',async()=>{
 const f=await setup(),p=f.page;let connected=false;try{
  await f.seed([]);
  await p.route('**/api/devices',r=>r.fulfill({json:{status:'ok',devices:{Second:{connected,info:{hostname:'Рабочий компьютер',os:'Windows'}}}}}));
  await p.evaluate(()=>fetchDevices());assert.ok((await p.locator('#uxDevices').innerText()).includes('Нет связи'));
  connected=true;await p.evaluate(()=>fetchDevices());assert.ok((await p.locator('#uxDevices').innerText()).includes('На связи'));
  await p.locator('#ux-tab-conversation').click();await p.locator('#operationsToggle').click();assert.equal(await p.locator('#operationsDock').isVisible(),true);
  await p.locator('#operationsClose').click();assert.equal(await p.locator('#operationsDock').isVisible(),false);
  await p.locator('#ux-tab-overview').click();assert.equal(await p.locator('#uxOverview').isVisible(),true);
  assert.equal(f.writes.length,0);assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});
for(const long of [false,true])test('Mini '+(long?'long results and compact file actions':'quiet empty presence'),async()=>{
 const f=await setup(360,400),p=f.page;try{
  await p.evaluate(()=>setAdaptiveSurface(true));await f.seed(long?result('success',2):[]);
  assert.equal(await p.locator('.ux-navigation').isVisible(),false);assert.equal(await p.locator('#uxMiniStatus').isVisible(),true);
  if(long){
   assert.equal(await p.locator('#chatMessages [data-block-type=file]').count(),2);
   const file=p.locator('#chatMessages .smart-file').first();
   assert.equal(await file.locator('[data-action=download-message-file]').isVisible(),false);
   await file.locator('[data-action=toggle-smart-file]').click();assert.equal(await file.locator('[data-action=download-message-file]').isVisible(),true);
  }
  if(!long){
    const measured=await p.evaluate(()=>Math.ceil(document.getElementById('adaptiveBody').getBoundingClientRect().height+document.querySelector('.chat-input-area').getBoundingClientRect().height+24));
    assert.ok(measured<200,'idle native measurement must not include a large empty panel');
    // Preview the existing host floor; production geometry remains host-owned.
    await p.setViewportSize({width:360,height:Math.max(160,measured)});
    await p.screenshot({animations:'disabled',path:path.join(shots,'mini-auto-idle.png')});
    await p.setViewportSize({width:360,height:400});
  }
  await p.screenshot({animations:'disabled',path:path.join(shots,`mini-${long?'result':'idle'}.png`)});await noOverflow(p);assert.deepEqual(f.errors,[]);
 }finally{await p.close();}
});
test('Mobile navigation, results, composer, attachments and voice controls stay available',async()=>{
 const f=await setup(390,844),p=f.page;try{
  await f.seed(result('partial',1));await p.locator('#ux-tab-conversation').click();
  await p.locator('#chatInput').fill('Уточнение по документу');assert.equal(await p.locator('#chatInput').inputValue(),'Уточнение по документу');
  assert.equal(await p.locator('#voiceBtn').isVisible(),true);assert.equal(await p.locator('#mobilePlusBtn').isVisible(),true);
  await p.locator('#mobilePlusBtn').click();assert.equal(await p.locator('#mobilePlusAttachAction').isVisible(),true);
  await p.locator('#mobilePlusBtn').click();await p.screenshot({animations:'disabled',path:path.join(shots,'mobile-conversation.png')});
  await p.locator('#ux-tab-overview').click();assert.equal(await p.locator('#uxOverview').isVisible(),true);
  assert.equal(await p.locator('#ux-tab-overview').getAttribute('aria-selected'),'true');
  assert.equal(await p.locator('#ux-tab-components').isVisible(),false);
  await p.screenshot({animations:'disabled',path:path.join(shots,'mobile-overview.png')});await noOverflow(p);
  await p.emulateMedia({reducedMotion:'reduce'});assert.ok(await p.locator('.ux-core-ring').first().evaluate(el=>parseFloat(getComputedStyle(el).animationDuration)<.01));
 }finally{await p.close();}
});
