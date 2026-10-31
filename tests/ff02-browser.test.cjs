// Local fixture HTTP server and headless Edge only; no Agent or real API.
const {test,before,after}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {createServer}=require('./helpers/smart-ui-fixtures.cjs');
let browser,server,origin;
before(async()=>{
  ({server}=createServer());await new Promise(r=>server.listen(0,'127.0.0.1',r));
  origin=`http://127.0.0.1:${server.address().port}`;
  browser=await chromium.launch({channel:'msedge',headless:true});
});
after(async()=>{await browser?.close();if(server)await new Promise(r=>server.close(r));});
for(const width of [360,1280])test(`poll and restored history show one answer and two files at ${width}px`,async()=>{
  const page=await browser.newPage({viewport:{width,height:900}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const answer='Создал alpha.txt и beta.txt с указанным содержимым. Оба файла готовы.';
  const goal={goal_id:'ff02',status:'success',terminal:true,goal_completed:true,
    completed_count:2,total_count:2,message:'Internal Worker SHA-256 CRLF report',task_ids:['a','b'],
    result_artifacts:['alpha.txt','beta.txt'].map((name,i)=>({path:'C:/LIVE/'+name,name,device_id:'Second',task_id:i?'b':'a',verified:true}))};
  const message={role:'assistant',_taskId:'turn',taskKind:'goal',taskStatus:'success',goalId:'ff02',
    resultFirst:true,content:answer,conversationalResponse:answer,goalPresentation:goal};
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await page.route('**/api/chats/1/messages',route=>route.fulfill({json:{status:'ok',messages:[message]}}));
  await page.route('**/api/tasks/turn',route=>route.fulfill({json:{status:'ok',task:{task_id:'turn',chat_id:1,
    status:'done',kind:'goal',goal_id:'ff02',result_first:true,goal_presentation:goal,
    conversational_response:answer,answer,commands:[],tasks:[]}}}));
  await page.addInitScript(()=>localStorage.setItem('iru_token','smart-ui-fixture'));
  try{
    await page.goto(origin,{waitUntil:'networkidle'});
    await page.evaluate(()=>{state.currentChatId=1;state.messages=[{role:'assistant',_taskId:'turn',loading:true,content:''}];renderMessages();pollTask('turn',0);});
    await page.locator('[data-block-type="file"]').first().waitFor();
    async function check(){
      const text=await page.locator('#chatMessages').innerText();
      assert.equal(text.split(answer).length-1,1);
      for(const hidden of ['Internal Worker','SHA-256','Ход выполнения','Поручение','2 / 2'])assert.ok(!text.includes(hidden),hidden);
      assert.equal(await page.locator('[data-block-type="file"]').count(),2);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    }
    await check();await page.reload({waitUntil:'networkidle'});
    await page.evaluate(()=>openChat(1));await check();assert.deepEqual(errors,[]);
  }finally{await page.close();}
});
