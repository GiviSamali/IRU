/* Replays values produced by the connected Python lifecycle, not hand-authored
 * status fixtures. Device/model/TTS effects are emulated; production UI is real. */
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const {createServer}=require('./helpers/smart-ui-fixtures.cjs');
let browser,server,origin,records,scratch;
before(async()=>{
 scratch=fs.mkdtempSync(path.join(os.tmpdir(),'iru-p0-connected-'));
 const exported=path.join(scratch,'api-snapshots');
 const python=process.env.IRU_TEST_PYTHON||(process.platform==='win32'?'python.exe':'python3');
 const run=spawnSync(python,['-m','pytest','-q','tests/test_integrity_lifecycle.py','tests/test_integrity_review.py','-k','not operations_read','--tb=short','--basetemp='+path.join(scratch,'pytest')],{
  cwd:path.resolve(__dirname,'..'),env:{...process.env,IRU_P0_LIFECYCLE_EXPORT:exported,PYTHONIOENCODING:'utf-8'},encoding:'utf-8',timeout:120000,
 });
 assert.equal(run.status,0,run.stdout+'\n'+run.stderr);
 records=[...Array.from({length:20},(_,i)=>i+1),121,122].map(number=>JSON.parse(fs.readFileSync(path.join(exported,`case-${String(number).padStart(2,'0')}.json`),'utf-8')));
 ({server}=createServer());await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin=`http://127.0.0.1:${server.address().port}`;
 browser=await chromium.launch({channel:'msedge',headless:true});
});
after(async()=>{await browser?.close();if(server)await new Promise(resolve=>server.close(resolve));
 if(scratch){const resolved=path.resolve(scratch);assert.ok(resolved.startsWith(path.resolve(os.tmpdir())+path.sep));assert.ok(path.basename(resolved).startsWith('iru-p0-connected-'));fs.rmSync(resolved,{recursive:true,force:true});}
});
for(const number of [...Array.from({length:20},(_,i)=>i+1),121,122])test(`P0 connected scenario ${number}: server → Chat / Dock → F5 / chat switch`,async()=>{
 const record=records.find(item=>item.number===number),page=await browser.newPage({viewport:{width:1024,height:900}});
 let snapshot=record,reloaded=false;const errors=[],mutations=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
 await page.route('**/api/voice/config',r=>r.fulfill({json:record.voice_config}));
 await page.route('**/api/chats/1/messages',r=>r.fulfill({json:snapshot.history||record.history}));
 await page.route('**/api/operations',r=>r.fulfill({json:snapshot.operations||record.operations}));
 await page.route('**/api/tasks/**',r=>{
  const url=new URL(r.request().url());
  if(r.request().method()==='POST'){mutations.push(url.pathname);return r.fulfill({status:409,json:{detail:'read-only lifecycle replay'}});}
  const id=url.pathname.split('/')[3];
  const api=snapshot.api?.task_id===id?snapshot.api:record.api;
  return r.fulfill({json:{status:'ok',task:reloaded&&id===record.api.task_id?record.restored:api}});
 });
 await page.addInitScript(()=>{localStorage.setItem('iru_token','smart-ui-fixture');class SR{start(){queueMicrotask(()=>this.onstart?.());}abort(){this.onend?.();}stop(){this.onend?.();}}window.SpeechRecognition=window.webkitSpeechRecognition=SR;});
 try{
  await page.goto(origin,{waitUntil:'networkidle'});await page.locator('#appRoot.active').waitFor();
  for(const phase of record.phases||[]){
   snapshot=phase;
   await page.evaluate(history=>{state.currentChatId=1;state.messages=history.messages;renderMessages();return IRUOperations.refresh();},phase.history);
   if(await page.locator('#operationsToggle').getAttribute('aria-expanded')!=='true')await page.locator('#operationsToggle').click();
   const row=page.locator(`[data-task-id="${phase.api.task_id}"]`);
   const expected=phase.api.worker_report.status;
   const label=await page.evaluate(status=>status==='queued'?'В очереди':status==='waiting_confirmation'?'Нужно подтверждение':IRUSmartUI.LABELS[IRUSmartUI.normalizeStatus(status)],expected);
   assert.equal(await row.locator('.smart-status').textContent(),label);
   if(number===20){assert.deepEqual(phase.rejected_statuses,[422,409,409]);assert.equal(record.calls.length,0);}
  }
  snapshot=record;
  for(let cycle=0;cycle<2;cycle++){
   if(cycle){reloaded=true;await page.reload({waitUntil:'networkidle'});}
   await page.evaluate(history=>{state.currentChatId=1;state.messages=history.messages;renderMessages();return IRUOperations.refresh();},record.history);
   const key=`smart-${record.api.chat_id}-${encodeURIComponent(record.api.task_id)}`;
   const message=page.locator(`[data-message-key="${key}"]`);
   assert.equal(await message.count(),1);
   assert.equal(await message.locator('.smart-text-content').textContent(),record.api.conversational_response);
   assert.ok(!(await message.textContent()).includes('ИРУ завершила задачу без текстового ответа'));
   if(record.api.worker_report){
    if(await page.locator('#operationsToggle').getAttribute('aria-expanded')!=='true')await page.locator('#operationsToggle').click();
    const row=page.locator(`[data-task-id="${record.api.task_id}"]`);
    const label=await page.evaluate(status=>IRUSmartUI.LABELS[status],record.expected);
    assert.equal(await row.locator('.smart-status').textContent(),label);
    await row.locator('[data-operation="details"]').click();
    assert.equal(await row.locator('.operation-prose').first().textContent(),record.api.conversational_response);
    await page.evaluate(()=>{state.currentChatId=2;state.messages=[];renderMessages();});
    assert.equal(await row.locator('.smart-status').textContent(),label);
   }else{assert.equal(await page.locator('.operation-item').count(),0);}
  }
  if(record.voice_failure){
   await page.route('**/api/voice/tasks/*/speech',r=>r.fulfill({status:record.voice_failure.status,json:record.voice_failure.body}));
   assert.equal(await page.evaluate(id=>apiFetch('/api/voice/tasks/'+id+'/speech',{method:'POST',headers:authHeaders()}).then(r=>r.status),record.api.task_id),502);
   assert.equal(record.api.answer,record.restored.answer);
  }
  assert.ok(record.spoken.length);assert.deepEqual(errors,[]);assert.deepEqual(mutations,[]);
 }finally{await page.close();}
});
