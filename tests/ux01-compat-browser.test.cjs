// Reproduces UI wiring defects on both the baseline and new workspace.
const {test,before,after}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require('playwright'),{createServer}=require('./helpers/smart-ui-fixtures.cjs');
let browser,server,origin;
before(async()=>{({server}=createServer());await new Promise(r=>server.listen(0,'127.0.0.1',r));origin=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'msedge',headless:true});});
after(async()=>{await browser?.close();if(server)await new Promise(r=>server.close(r));});
async function open(){const page=await browser.newPage({viewport:{width:1200,height:800}});await page.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());await page.addInitScript(()=>localStorage.setItem('iru_token','smart-ui-fixture'));await page.goto(origin,{waitUntil:'networkidle'});await page.locator('#appRoot.active').waitFor();return page;}
test('real Stop button click sends one existing Goal cancellation request',async()=>{
 const p=await open(),writes=[];try{
  await p.route('**/api/goals/g/cancel*',r=>{writes.push(r.request().method());return r.fulfill({json:{status:'ok'}});});
  await p.evaluate(()=>{state.currentChatId=1;state.messages=[{role:'assistant',_taskId:'turn',taskKind:'goal',goalId:'g',loading:true,content:'Выполняю'}];state.pendingTasks=[{task_id:'turn',kind:'goal',goalId:'g',chatId:1,msgIndex:0}];renderMessages();updateStopButton();});
  await Promise.all([p.waitForResponse(r=>r.url().includes('/api/goals/g/cancel'),{timeout:1500}),p.locator('#btnStopTask').click()]);
  assert.deepEqual(writes,['POST']);
 }finally{await p.close();}
});
test('late history of previous chat cannot replace the current conversation',async()=>{
 const p=await open();let release;const gate=new Promise(r=>release=r);try{
  await p.route('**/api/chats/1/messages',async r=>{await gate;await r.fulfill({json:{status:'ok',messages:[{role:'assistant',content:'Старый разговор'}]}});});
  await p.route('**/api/chats/2/messages',r=>r.fulfill({json:{status:'ok',messages:[{role:'assistant',content:'Текущий разговор'}]}}));
  await p.evaluate(()=>{window.oldRead=openChat(1);});await p.evaluate(()=>openChat(2));release();await p.evaluate(()=>window.oldRead);
  assert.deepEqual(await p.evaluate(()=>({chat:state.currentChatId,text:state.messages[0]?.content})),{chat:2,text:'Текущий разговор'});
 }finally{release();await p.close();}
});
