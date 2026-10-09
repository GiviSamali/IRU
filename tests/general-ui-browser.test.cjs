const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {createServer}=require('./helpers/smart-ui-fixtures.cjs');
let browser,server,origin,requests;
before(async()=>{({server,requests}=createServer());await new Promise(r=>server.listen(0,'127.0.0.1',r));origin=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'msedge',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
const operations=[{task_id:'own-active',chat_id:2,title:'Создать презентацию',status:'running',device_ids:['Second'],summary:'Работа идёт',can_cancel:true},{task_id:'own-queued',chat_id:3,title:'Подготовить таблицу',status:'queued',device_ids:['givi'],summary:'В очереди',can_cancel:true},{task_id:'own-confirm',chat_id:2,title:'Удаление',status:'waiting_confirmation',device_ids:['Second'],summary:'Нужно подтверждение',can_cancel:true}];
async function open(width=1280){
 const page=await browser.newPage({viewport:{width,height:900}});page.errors=[];page.on('pageerror',e=>page.errors.push(e.message));
 await page.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
 await page.route('**/api/operations',r=>r.fulfill({json:{status:'ok',operations}}));
 await page.route('**/api/tasks/own-confirm',r=>r.fulfill({json:{status:'ok',task:{task_id:'own-confirm',status:'confirm',confirm_data:{confirmation_id:'exact-nonce',command:'Удалить тестовый файл',voice_allowed:false},commands:[]}}}));
 await page.addInitScript(()=>{localStorage.setItem('iru_token','smart-ui-fixture');window.speechStarts=0;class SR{start(){speechStarts++;queueMicrotask(()=>this.onstart?.());}abort(){this.onend?.();}stop(){this.onend?.();}}window.SpeechRecognition=window.webkitSpeechRecognition=SR;});
 await page.goto(origin,{waitUntil:'networkidle'});await page.locator('#appRoot.active').waitFor();await page.evaluate(()=>{state.user.token='smart-ui-fixture';return IRUOperations.refresh();});return page;
}
for(const width of [360,1280])test(`dock independent, own queue, exact decisions, voice and reload ${width}`,async()=>{
 const page=await open(width);try{
  await page.evaluate(()=>{state.messages=[{role:'assistant',id:600,_taskId:'own-active',taskKind:'worker',taskStatus:'running',loading:true,content:'Работа идёт.'},{role:'assistant',id:601,content:'Пока работаю, можем поговорить.'}];renderMessages();});
  assert.equal(await page.locator('#chatMessages .smart-task').count(),0);
  await page.locator('#operationsToggle').click();assert.equal(await page.locator('.operation-item').count(),3);
  assert.ok((await page.locator('[data-task-id="own-confirm"] .smart-status').textContent()).includes('подтверждение'));
  await page.locator('#chatMessages').evaluate(el=>el.scrollTop=10000);assert.ok(await page.locator('#operationsToggle').isVisible());
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  const input=await page.locator('.chat-input-area').boundingBox(),dock=await page.locator('#operationsDock').boundingBox();assert.ok(dock.y+dock.height<=input.y);
  await page.locator('[data-task-id="own-queued"] [data-operation="cancel"]').click();
  await page.waitForFunction(()=>document.getElementById('operationsItems').getAttribute('aria-busy')==='false');
  assert.ok(requests.some(r=>r.path==='/api/tasks/own-queued/cancel'&&r.method==='POST'));
  await page.locator('[data-task-id="own-confirm"] [data-operation="details"]').click();
  await page.locator('[data-task-id="own-confirm"] [data-operation="confirm"]').click();
  await page.waitForFunction(()=>document.getElementById('operationsItems').getAttribute('aria-busy')==='false');
  const decision=requests.find(r=>r.path==='/api/tasks/own-confirm/command-decision');assert.deepEqual(JSON.parse(decision.body),{confirmation_id:'exact-nonce',accepted:true,via_voice:false});
  await page.waitForFunction(()=>document.getElementById('voiceBtn').disabled===false);await page.locator('#voiceBtn').click();await page.waitForFunction(()=>speechStarts>0);
  const speech=await page.evaluate(()=>speechStarts);await page.locator('#operationsToggle').click();await page.locator('#operationsToggle').click();assert.equal(await page.evaluate(()=>speechStarts),speech);
  const effects=requests.filter(r=>r.method==='POST'&&r.path.startsWith('/api/tasks/')).length;await page.reload({waitUntil:'networkidle'});await page.evaluate(()=>IRUOperations.refresh());
  assert.equal(await page.locator('#operationsToggle').getAttribute('aria-expanded'),'true');assert.equal(await page.locator('.operation-item').count(),3);
  await page.evaluate(()=>{state.currentChatId=8;renderMessages();});assert.equal(await page.locator('.operation-item').count(),3);
  assert.equal(requests.filter(r=>r.method==='POST'&&r.path.startsWith('/api/tasks/')).length,effects);assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
for(const width of [360,1280])test(`ordinary reply has no card editing controls and remains selectable at ${width}`,async()=>{
 const page=await open(width);try{
  const original='LAN соединяет устройства рядом. WAN объединяет удалённые сети.\n'.repeat(5)+'<img src=x onerror="window.injected=1">';
  await page.evaluate(text=>{state.messages=[{role:'assistant',id:700,content:text,highlights:[{start:0,end:3,kind:'definition'}]}];renderMessages();},original);
  const text=page.locator('.smart-text-content');assert.equal(await text.textContent(),original);
  assert.equal(await page.locator('.content-surface, .surface-toolbar, #surfaceEditor, [data-surface-action]').count(),0);
  assert.equal(await page.evaluate(()=>typeof IRUContentSurface),'undefined');
  assert.equal(await text.locator('mark').count(),0);assert.equal(await text.locator('img').count(),0);
  assert.equal(await text.evaluate(el=>getComputedStyle(el).userSelect),'text');
  await text.evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);});
  await page.context().grantPermissions(['clipboard-read','clipboard-write']);await page.keyboard.press('Control+c');
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText().then(t=>t.replace(/\r\n/g,'\n'))),original);
  await page.evaluate(()=>renderMessages());assert.equal(await page.locator('[data-surface-action], #surfaceEditor').count(),0);
  assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});

test('dock PLAN revision is exact, authenticated and never re-executes on refresh',async()=>{
 const page=await open();try{
  const posts=[];
  await page.route('**/api/tasks/own-confirm',r=>r.fulfill({json:{task:{task_id:'own-confirm',status:'confirm',plan_review:{revision:'revision-42',steps:[{title:'Создать файл',instruction:'Сохранить результат'}]},commands:[]}}}));
  await page.route('**/api/tasks/own-confirm/review-plan',r=>{posts.push({body:JSON.parse(r.request().postData()),headers:r.request().headers()});r.fulfill({json:{status:'ok'}});});
  await page.locator('#operationsToggle').click();await page.locator('[data-task-id="own-confirm"] [data-operation="details"]').click();
  page.once('dialog',d=>d.accept('Изменить название файла'));
  await page.locator('[data-task-id="own-confirm"] [data-operation="revise"]').click();
  await page.waitForFunction(()=>document.getElementById('operationsItems').getAttribute('aria-busy')==='false');
  assert.deepEqual(posts[0].body,{revision:'revision-42',action:'revise',changes:'Изменить название файла'});
  assert.ok(posts[0].headers['x-token']||posts[0].headers.authorization);
  await page.evaluate(()=>IRUOperations.refresh());assert.equal(posts.length,1);assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});

for(const width of [390,1280])test(`clean working UI preview ${width}`,async()=>{
 const page=await open(width);try{
  await page.evaluate(()=>{state.messages=[{role:'user',id:800,content:'А пока объясни разницу между LAN и WAN.'},{role:'assistant',id:801,content:'LAN — локальная сеть\n\nСоединяет устройства дома или в офисе. Например, ноутбук и принтер через домашний роутер.\n\nWAN — сеть на больших расстояниях\n\nСвязывает отдельные локальные сети между городами и странами. Интернет — самый знакомый пример.\n\nОсновная разница — масштаб и расстояние между устройствами.',highlights:[{start:0,end:20,kind:'definition'}]}];renderMessages();});
  await page.locator('#operationsToggle').click();
  if(process.env.IRU_SCREENSHOT_DIR)await page.screenshot({path:require('node:path').join(process.env.IRU_SCREENSHOT_DIR,`voice-conversation-ui-${width}.png`)});
  assert.equal(await page.locator('.content-surface, .surface-toolbar, #surfaceEditor').count(),0);assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});

test('dock reuses existing journal and preserves its disclosure while polling',async()=>{
 const page=await open();try{
  await page.route('**/api/tasks/own-active',r=>r.fulfill({json:{task:{task_id:'own-active',status:'running',commands:[{tool_name:'execute_cmd',device_id:'Second',status:'success',step_id:'step_1',result:{returncode:0,stdout:'OK: verified fixture'}}],tasks:[],execution_details:'Операция проверена, работа продолжается.'}}}));
  await page.locator('#operationsToggle').click();await page.locator('[data-task-id="own-active"] [data-operation="details"]').click();
  const row=page.locator('.operation-item[data-task-id="own-active"]');
  await row.locator('summary').click();await row.locator('.cmd-log').click();await row.locator('[data-action="toggle-cmd-entry"]').click();
  assert.equal(await row.locator('.cmd-details').isVisible(),true);
  await page.evaluate(()=>IRUOperations.refresh());
  assert.equal(await row.locator('.cmd-log').getAttribute('aria-expanded'),'true');assert.equal(await row.locator('.cmd-details').isVisible(),true);
  assert.equal(await row.getAttribute('data-status'),'running');assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
for(const cd of [{command:'Удалить тестовый файл'},{confirmation_id:'nonce-only'},{confirmation_id:'',command:'Удалить тестовый файл'},{confirmation_id:'nonce',command:'   '}])test(`dock refuses incomplete confirmation ${JSON.stringify(cd)}`,async()=>{
 const page=await open();try{
  const effects=[];page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.startsWith('/api/tasks/'))effects.push(r.url());});
  await page.route('**/api/tasks/own-confirm',r=>r.fulfill({json:{task:{task_id:'own-confirm',status:'confirm',confirm_data:cd,commands:[]}}}));
  await page.locator('#operationsToggle').click();await page.locator('[data-task-id="own-confirm"] [data-operation="details"]').click();
  const row=page.locator('[data-task-id="own-confirm"]');assert.equal(await row.locator('[data-operation="confirm"], [data-operation="deny"]').count(),0);
  assert.ok((await row.locator('.operation-confirmation-invalid').textContent()).includes('Обновите'));
  // Even a stale/forged DOM button cannot reach either execution endpoint.
  await row.evaluate(el=>{const b=document.createElement('button');b.dataset.operation='confirm';b.textContent='stale';el.append(b);b.click();});
  await page.waitForFunction(()=>document.getElementById('operationsItems').getAttribute('aria-busy')==='false');
  assert.deepEqual(effects,[]);assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});

for(const change of ['nonce','command','status'])test(`dock does not approve refreshed ${change} in place of presented command`,async()=>{
 const page=await open();try{
  let current={confirmation_id:'exact-nonce',command:'Удалить тестовый файл'};let status='confirm';const effects=[];
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.startsWith('/api/tasks/'))effects.push(r.url());});
  await page.route('**/api/tasks/own-confirm',r=>r.fulfill({json:{task:{task_id:'own-confirm',status,confirm_data:current,commands:[]}}}));
  await page.locator('#operationsToggle').click();await page.locator('[data-task-id="own-confirm"] [data-operation="details"]').click();
  const button=page.locator('[data-task-id="own-confirm"] [data-operation="confirm"]');await button.waitFor();
  if(change==='nonce')current={...current,confirmation_id:'replacement-nonce'};
  if(change==='command')current={...current,command:'Удалить другой файл'};
  if(change==='status')status='cancelled';
  await button.click();await page.waitForFunction(()=>document.getElementById('operationsItems').getAttribute('aria-busy')==='false');
  assert.deepEqual(effects,[]);assert.ok((await page.locator('#toast').textContent()).includes('изменились'));assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});

for(const width of [360,1280])test(`dock polling preserves selected text, focus and unrelated row DOM at ${width}`,async()=>{
 const page=await open(width);try{
  let changed=false;await page.route('**/api/operations',r=>r.fulfill({json:{operations:operations.map(i=>i.task_id==='own-queued'?{...i,title:changed?'Новое название очереди':i.title,updated_at:Date.now()}:i)}}));
  await page.route('**/api/tasks/own-active',r=>r.fulfill({json:{task:{task_id:'own-active',status:'running',commands:[],tasks:[],execution_details:'Текст журнала для свободного выделения.',elapsed_ms:Date.now()}}}));
  await page.locator('#operationsToggle').click();await page.locator('[data-task-id="own-active"] [data-operation="details"]').click();
  const row=page.locator('.operation-item[data-task-id="own-active"]');await row.locator('summary').click();
  await row.locator('[data-operation="cancel"]').focus();
  await row.locator('details .operation-prose').evaluate(el=>{window.selectedLogNode=el;window.activeDockFocus=document.activeElement;window.observedDockMutations=0;window.dockObserver=new MutationObserver(m=>{observedDockMutations+=m.length;});dockObserver.observe(document.getElementById('operationsItems'),{subtree:true,childList:true,attributes:true});const range=document.createRange();range.selectNodeContents(el);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);});
  // Exercise the actual 2.5s poll as well as explicit refresh; timestamps alone are not content.
  await page.waitForTimeout(2800);await page.evaluate(()=>IRUOperations.refresh());
  assert.equal(await page.evaluate(()=>observedDockMutations),0);
  assert.equal(await page.evaluate(()=>document.activeElement===activeDockFocus),true);
  assert.equal(await page.evaluate(()=>getSelection().toString()),'Текст журнала для свободного выделения.');
  changed=true;await page.evaluate(()=>IRUOperations.refresh());
  assert.equal(await row.locator('details .operation-prose').evaluate(el=>el===selectedLogNode),true);
  assert.equal(await page.evaluate(()=>document.activeElement===activeDockFocus),true);
  assert.equal(await page.evaluate(()=>getSelection().toString()),'Текст журнала для свободного выделения.');
  assert.ok((await page.locator('[data-task-id="own-queued"]').textContent()).includes('Новое название очереди'));assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});


test('dock binds exact multiline command before HTML normalizes CRLF',async()=>{
 const page=await open();try{
  const effects=[];const command='Write-Output "line one"\r\nWrite-Output "line two"';
  await page.route('**/api/tasks/own-confirm',r=>r.fulfill({json:{task:{task_id:'own-confirm',status:'confirm',confirm_data:{confirmation_id:'multiline-nonce',command},commands:[]}}}));
  await page.route('**/api/tasks/own-confirm/command-decision',r=>{effects.push(JSON.parse(r.request().postData()));r.fulfill({json:{status:'ok'}});});
  await page.locator('#operationsToggle').click();await page.locator('[data-task-id="own-confirm"] [data-operation="details"]').click();
  const row=page.locator('[data-task-id="own-confirm"]');assert.equal(await row.locator('.operation-command-preview').textContent(),command.replace(/\r\n/g,'\n'));
  await row.locator('[data-operation="confirm"]').click();await page.waitForFunction(()=>document.getElementById('operationsItems').getAttribute('aria-busy')==='false');
  assert.deepEqual(effects,[{confirmation_id:'multiline-nonce',accepted:true,via_voice:false}]);assert.deepEqual(page.errors,[]);
 }finally{await page.close();}
});
