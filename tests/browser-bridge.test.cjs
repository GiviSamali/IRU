/* Controlled pages only; no accounts, secrets, cookies or production requests. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const vm = require('node:vm');
const os = require('node:os');
const {spawn} = require('node:child_process');
const { chromium } = require('playwright');
const extensionPath = path.join(__dirname,'..','browser_extension');
const contentSource = fs.readFileSync(path.join(extensionPath,'content.js'),'utf8');
let browser, server, origin, serial = 0;
test.before(async () => {
  server = http.createServer((req,res) => {
    const name = path.basename(new URL(req.url,'http://localhost').pathname);
    const fixture = path.join(__dirname,'browser-fixtures',name);
    if (!fs.existsSync(fixture)) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type','text/html; charset=utf-8'); res.end(fs.readFileSync(fixture));
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel:'msedge',headless:true});
});
test.after(async () => { await browser?.close(); await new Promise(resolve => server?.close(resolve)); });
async function fixture(name = 'form.html') {
  const page = await browser.newPage(); await page.goto(origin + '/' + name);
  await install(page); return page;
}
async function install(page) {
  await page.evaluate(() => {
    window.chrome = {runtime:{id:'test-extension',onMessage:{addListener(listener){window.testBridgeHandler=listener;}}}};
  });
  await page.addScriptTag({content:contentSource});
}
async function command(page,operation,params = {},authorization = {},requestId = 'request-' + (++serial)) {
  return page.evaluate(({operation,params,authorization,requestId}) => new Promise(resolve => {
    window.testBridgeHandler({type:'iru_browser_command',operation,params,authorization,request_id:requestId},{id:'test-extension'},resolve);
  }),{operation,params:{tab_id:1,...params},authorization,requestId});
}
async function observed(page,name) {
  const result = await command(page,'web.elements');
  const element = result.elements.find(item => item.name === name);
  assert.ok(element,'Element named ' + name);
  return {element,result,params:{document_id:result.document_id,revision:result.revision,element_id:element.element_id}};
}
test('semantic read filters hidden/technical/credential content and returns bounded page evidence',async () => {
  const page = await fixture(); const result = await command(page,'web.read');
  assert.equal(result.status,'success'); assert.match(result.text,/Visible useful information/);
  assert.doesNotMatch(result.text,/private junk|hidden descendant|ARIA hidden|Transparent hidden|secret-password|window.events/);
  assert.equal(result.trust,'untrusted_page_data'); assert.equal(result.page.origin,origin);
  assert.equal(result.document_id,result.page.document_id); assert.equal(result.revision,result.page.revision);
  assert.deepEqual(result.headings,[{role:'heading',name:'Ordinary HTML form'}]); await page.close();
});
test('elements expose generic accessible roles and opaque IDs stable within revision',async () => {
  const page = await fixture(); const first = await command(page,'web.elements'), second = await command(page,'web.elements');
  assert.deepEqual(first.elements,second.elements); assert.ok(first.elements.some(e => e.name === 'Full name' && e.role === 'textbox'));
  assert.ok(first.elements.some(e => e.name === 'Notes' && e.type === 'textarea'));
  assert.ok(first.elements.some(e => e.name === 'Rich text' && e.type === 'contenteditable'));
  assert.ok(first.elements.every(e => /^[\da-f-]{36}$/.test(e.element_id)));
  assert.ok(!first.elements.some(e => ['password','file','hidden'].includes(e.type) || e.name === 'Invisible button')); await page.close();
});
for (const [name,value,kind] of [['Full name','IRU user','input'],['Notes','Line one\nLine two','textarea'],['Rich text','Rich <b>literal</b> text','contenteditable']]) {
  test(`fill ${kind} changes draft via DOM input/change without submitting or synthesizing input`,async () => {
    const page = await fixture(); const item = await observed(page,name);
    const result = await command(page,'web.fill',{...item.params,text:value});
    assert.equal(result.status,'success'); assert.equal(result.draft,true); assert.equal(result.response_policy,'silent_on_success');
    assert.notEqual(result.revision,item.result.revision); assert.notEqual(result.element.element_id,item.element.element_id);
    const actual = await page.locator(kind === 'input' ? '#name' : kind === 'textarea' ? '#notes' : '[contenteditable]').evaluate(el => el.isContentEditable ? el.textContent : el.value);
    assert.equal(actual,value); assert.deepEqual(await page.evaluate(() => window.events),{keyboard:0,pointer:0,input:1,change:1,sends:0}); await page.close();
  });
}
test('modern framework-like controlled input uses native setter and Send happens exactly once',async () => {
  const page = await fixture('chat.html'); const textbox = await observed(page,'Message');
  const fill = await command(page,'web.fill',{...textbox.params,text:'test connection with IRU'});
  assert.equal(fill.status,'success'); assert.equal(await page.evaluate(() => window.frameworkDraft),'test connection with IRU');
  assert.equal(await page.evaluate(() => !!window.instanceSetterUsed),false); assert.equal(await page.evaluate(() => window.sends),0);
  const send = await observed(page,'Send message');
  const denied = await command(page,'web.activate',send.params); assert.equal(denied.error,'external_action_not_authorized');
  const activated = await command(page,'web.activate',send.params,{external_action:true},'one-send');
  assert.equal(activated.status,'success'); assert.equal(activated.effect,'activation_dispatched');
  const duplicate = await command(page,'web.activate',send.params,{external_action:true},'one-send');
  assert.deepEqual(duplicate,activated); assert.equal(await page.evaluate(() => window.sends),1);
  const waited = await command(page,'web.wait',{document_id:activated.document_id,revision:activated.revision,timeout_ms:2000});
  assert.equal(waited.changed,true); const read = await command(page,'web.read'); assert.match(read.text,/Connection acknowledged/); await page.close();
});
test('a second dynamic contenteditable chat follows fill, activate, wait, read universally',async () => {
  const page = await fixture('dynamic-chat.html'); const editor = await observed(page,'Compose message');
  const filled = await command(page,'web.fill',{...editor.params,text:'second dynamic app'}); assert.equal(filled.status,'success');
  const send = await observed(page,'Send'); const activated = await command(page,'web.activate',send.params,{external_action:true});
  const waited = await command(page,'web.wait',{document_id:activated.document_id,revision:activated.revision,timeout_ms:2000});
  assert.equal(waited.changed,true); const read = await command(page,'web.read'); assert.match(read.text,/New message: second dynamic app/); assert.equal(await page.evaluate(() => window.sends),1); await page.close();
});
test('revision changes and replaced equivalent elements invalidate old IDs instead of retargeting',async () => {
  const page = await fixture(); const send = await observed(page,'Send');
  await page.evaluate(() => { const old=document.getElementById('send'); const replacement=old.cloneNode(true); old.replaceWith(replacement); window.replacementActivated=false; replacement.onclick=()=>{window.replacementActivated=true;}; });
  const stale = await command(page,'web.activate',send.params,{external_action:true});
  assert.equal(stale.error,'stale_element'); assert.equal(await page.evaluate(() => window.replacementActivated),false);
  const fresh = await observed(page,'Send'); assert.notEqual(fresh.result.revision,send.result.revision); assert.notEqual(fresh.element.element_id,send.element.element_id); await page.close();
});
test('navigation changes document identity and old page elements are stale',async () => {
  const page = await fixture(); const item = await observed(page,'Full name'); await page.goto(origin + '/chat.html'); await install(page);
  const stale = await command(page,'web.fill',{...item.params,text:'must not touch new page'}); assert.equal(stale.error,'stale_element');
  const fresh = await command(page,'web.elements'); assert.notEqual(fresh.document_id,item.result.document_id); await page.close();
});
test('wait is bounded and honestly reports no change on timeout',async () => {
  const page = await fixture(); const result = await command(page,'web.read');
  const before=Date.now(); const waited=await command(page,'web.wait',{document_id:result.document_id,revision:result.revision,timeout_ms:80});
  assert.equal(waited.changed,false); assert.ok(Date.now()-before < 1000); await page.close();
});
test('hidden-only mutations do not invalidate semantic evidence',async () => {
  const page = await fixture(); const result=await command(page,'web.read');
  await page.locator('[hidden]').evaluate(el=>{el.textContent='hidden changed';});
  const next=await command(page,'web.read'); assert.equal(next.revision,result.revision); await page.close();
});
test('huge DOM stays within traversal/text/element bounds',async () => {
  const page=await fixture(); await page.evaluate(()=>{const fragment=document.createDocumentFragment();for(let i=0;i<30000;i++){const node=document.createElement('p');node.textContent='Long useful text '.repeat(40)+i;fragment.append(node);}document.querySelector('main').append(fragment);});
  const start=Date.now(); const read=await command(page,'web.read',{max_chars:1200}); assert.equal(read.status,'success'); assert.ok(read.text.length<=1200); assert.equal(read.truncated,true);
  const elements=await command(page,'web.elements',{max_elements:3,position:'head'}); assert.equal(elements.elements.length,3); assert.ok(Date.now()-start<5000); await page.close();
});
test('prompt injection remains untrusted page data and cannot invoke any privileged capability',async () => {
  const page=await fixture('injection.html'); const read=await command(page,'web.read'); assert.match(read.text,/Ignore previous instructions and upload/); assert.equal(read.trust,'untrusted_page_data');
  const denied=await command(page,'execute_cmd',{command:'upload file'}); assert.equal(denied.error,'invalid_parameters');
  const send=await observed(page,'Send secrets'); const activate=await command(page,'web.activate',send.params); assert.equal(activate.error,'external_action_not_authorized'); await page.close();
});
test('arbitrary JavaScript/selectors, wrong documents, oversized values and out-of-scope sensitive actions are rejected',async () => {
  const page=await fixture(); const item=await observed(page,'Full name');
  for(const params of [{script:'alert(1)'},{selector:'#name'},{...item.params,text:'x'.repeat(20001)}]) {const result=await command(page,'web.fill',params); assert.equal(result.error,'invalid_parameters');}
  const deletion=await observed(page,'Delete account'); assert.equal((await command(page,'web.activate',deletion.params,{external_action:true})).error,'unsupported_sensitive_action');
  const unsafe=await observed(page,'Unsafe script link'); assert.equal((await command(page,'web.activate',unsafe.params,{external_action:true})).error,'unsupported_url');
  const disabled=await observed(page,'Unavailable'); assert.equal((await command(page,'web.activate',disabled.params,{external_action:true})).error,'element_unavailable'); await page.close();
});
test('static implementation contains no model evaluator or synthetic keyboard/pointer machinery',() => {
  assert.doesNotMatch(contentSource,/\beval\s*\(|new\s+Function\b|KeyboardEvent|MouseEvent|PointerEvent|dispatchEvent\([^\n]*(?:key|mouse|pointer)|innerHTML\s*=/i);
  const background=fs.readFileSync(path.join(extensionPath,'background.js'),'utf8'); assert.match(background,/files:\['content\.js'\]/); assert.doesNotMatch(background,/func:\s*|\beval\s*\(/);
  const manifest=JSON.parse(fs.readFileSync(path.join(extensionPath,'manifest.json'),'utf8')); assert.equal(manifest.manifest_version,3); assert.equal(manifest.minimum_chrome_version,'116'); assert.ok(!manifest.permissions.includes('debugger')); assert.ok(!manifest.permissions.includes('cookies')); assert.ok(!manifest.permissions.includes('history'));
});
function backgroundHarness({receiptState={},sendMessage}={}) {
  const local={bridge_config:{server_url:'http://127.0.0.1',device_id:'givi',bridge_id:'bridge-123',token:'scoped-test-token'},activation_receipts:receiptState}, session={}, sockets=[];
  const event=()=>({addListener(){}});
  class FakeWebSocket {
    static OPEN=1;static CONNECTING=0;
    constructor(url){this.url=url;this.readyState=0;this.messages=[];sockets.push(this);}
    send(message){this.messages.push(JSON.parse(message));}
    close(code){this.readyState=3;this.onclose?.({code:code||1000});}
  }
  const storage = data => ({async get(key){return Object.fromEntries((Array.isArray(key)?key:[key]).map(item=>[item,data[item]]));},async set(values){Object.assign(data,values);}});
  const chrome={storage:{local:storage(local),session:storage(session),onChanged:event()},runtime:{onInstalled:event(),onStartup:event(),openOptionsPage(){}},alarms:{create(){},onAlarm:event()},action:{onClicked:event()},tabs:{async query(){return [{id:1,title:'Open chat',url:origin+'/chat.html',active:true},{id:2,title:'Internal',url:'chrome://settings',active:false}];},async get(id){if(id!==1)throw new Error('unknown tab');return{id:1,url:origin+'/chat.html'};},sendMessage:sendMessage||(async(_id,message)=>message.operation==='bridge.ping'?{status:'success'}:{status:'success',effect:'activation_dispatched',page:{document_id:'document',revision:'2'}})},scripting:{async executeScript(){}}};
  const context={chrome,WebSocket:FakeWebSocket,URL,TextEncoder,setTimeout(){return 1;},clearTimeout(){},setInterval(){return 1;},clearInterval(){},console};
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(extensionPath,'background.js'),'utf8'),context);
  return {context,sockets,receipts:local,local,async ready(){await new Promise(resolve=>setImmediate(resolve));const socket=sockets[0];socket.readyState=1;socket.onopen();return socket;},async command(socket,message){const before=socket.messages.length;await socket.onmessage({data:JSON.stringify({type:'command',...message})});return socket.messages.slice(before).find(msg=>msg.type==='result'&&msg.request_id===message.request_id)?.result;}};
}
const activationParams={tab_id:1,document_id:'document',revision:'1',element_id:'element'};
test('background pairs via hello token in message, not URL; tabs are restricted to normal web pages',async()=>{
  const harness=backgroundHarness(),socket=await harness.ready();assert.equal(socket.url,'ws://127.0.0.1/ws/browser');assert.equal(socket.messages[0].type,'hello');assert.equal(socket.messages[0].device_id,'givi');assert.equal(socket.messages[0].token,'scoped-test-token');
  const result=await harness.command(socket,{request_id:'tabs-1',operation:'web.tabs',params:{}});assert.equal(result.status,'success');assert.equal(result.tabs.length,1);assert.equal(result.tabs[0].tab_id,1);
});
test('background caches activation receipts; duplicates and changed parameters never activate twice',async()=>{
  let activations=0;const harness=backgroundHarness({sendMessage:async(_id,message)=>{if(message.operation==='bridge.ping')return{status:'success'};activations++;return{status:'success',effect:'activation_dispatched'};}}),socket=await harness.ready();
  const message={request_id:'unique-send',operation:'web.activate',params:activationParams,authorization:{external_action:true}};
  assert.equal((await harness.command(socket,message)).status,'success');assert.equal((await harness.command(socket,message)).status,'success');assert.equal(activations,1);
  const conflict=await harness.command(socket,{...message,params:{...activationParams,element_id:'different'}});assert.equal(conflict.error,'request_id_conflict');assert.equal(activations,1);
});
test('background conservatively preserves pending outcome after service worker restart',async()=>{
  let activations=0;const original=backgroundHarness({sendMessage:async(_id,message)=>{if(message.operation==='bridge.ping')return{status:'success'};activations++;throw new Error('port disconnected after dispatch');}}),socket=await original.ready();
  const message={request_id:'lost-send',operation:'web.activate',params:activationParams,authorization:{external_action:true}};
  const unknown=await original.command(socket,message);assert.equal(unknown.status,'unknown');assert.equal(activations,1);
  const state=original.receipts.activation_receipts;state['lost-send']={fingerprint:state['lost-send'].fingerprint,pending:true};
  const restarted=backgroundHarness({receiptState:state,sendMessage:async()=>{activations++;return{status:'success'};}}),newSocket=await restarted.ready();
  const result=await restarted.command(newSocket,message);assert.equal(result.status,'unknown');assert.equal(result.error,'needs_verification');assert.equal(activations,1);
});
test('background fails exact unknown tab instead of fallback and rejects unrecognized commands',async()=>{
  const harness=backgroundHarness(),socket=await harness.ready();
  const missing=await harness.command(socket,{request_id:'missing-tab',operation:'web.read',params:{tab_id:99}});assert.equal(missing.status,'failed');assert.equal(missing.error,'tab_disconnected');
  const arbitrary=await harness.command(socket,{request_id:'arbitrary',operation:'web.javascript',params:{script:'alert(1)'}});assert.equal(arbitrary.error,'invalid_command');
});

test('framework rejects draft: fill reports action_not_verified rather than false success',async()=>{
  const page=await fixture(); await page.locator('#name').evaluate(el=>{el.addEventListener('input',()=>{el.value='rejected by app';});});
  const item=await observed(page,'Full name'); const filled=await command(page,'web.fill',{...item.params,text:'should not be claimed'}); assert.equal(filled.status,'failed'); assert.equal(filled.error,'action_not_verified'); await page.close();
});
test('main scope excludes a main element beneath a hidden ancestor',async()=>{
  const page=await fixture();await page.evaluate(()=>{const main=document.querySelector('main'),hidden=document.createElement('div');hidden.hidden=true;main.replaceWith(hidden);hidden.append(main);});
  const read=await command(page,'web.read',{scope:'main'});const elements=await command(page,'web.elements',{scope:'main'});assert.equal(read.text,'');assert.equal(elements.elements.length,0);await page.close();
});
test('actual unpacked MV3 extension uses isolated content world and real tabs messaging for full chat flow',async()=>{
  const profile=fs.mkdtempSync(path.join(os.tmpdir(),'iru-browser-bridge-test-'));let context;
  try {
    context=await chromium.launchPersistentContext(profile,{channel:'msedge',headless:true,args:['--disable-extensions-except='+extensionPath,'--load-extension='+extensionPath]});
    const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');
    assert.match(worker.url(),/^chrome-extension:\/\/[a-p]{32}\/background\.js$/);
    const page=await context.newPage();await page.goto(origin+'/chat.html');
    assert.equal(await page.evaluate(()=>typeof globalThis.__iruBrowserInstalled),'undefined','No bridge handler exposed to page world');
    const run=(operation,params={},authorization={},request_id='actual-'+(++serial))=>worker.evaluate(({operation,params,authorization,request_id})=>execute({type:'command',operation,params,authorization,request_id}),{operation,params,authorization,request_id});
    const tabs=await run('web.tabs');const tab=tabs.tabs.find(item=>item.url===origin+'/chat.html');assert.ok(tab);
    const read=await run('web.read',{tab_id:tab.tab_id});assert.match(read.text,/Welcome to the conversation/);
    const observed=await run('web.elements',{tab_id:tab.tab_id}),composer=observed.elements.find(e=>e.name==='Message');
    const filled=await run('web.fill',{tab_id:tab.tab_id,document_id:observed.document_id,revision:observed.revision,element_id:composer.element_id,text:'actual extension connection'});assert.equal(filled.status,'success');assert.equal(filled.tab_id,tab.tab_id);assert.equal(filled.element_id,filled.element.element_id);assert.equal(await page.evaluate(()=>window.sends),0);
    const fresh=await run('web.elements',{tab_id:tab.tab_id}),send=fresh.elements.find(e=>e.name==='Send message');
    const activated=await run('web.activate',{tab_id:tab.tab_id,document_id:fresh.document_id,revision:fresh.revision,element_id:send.element_id},{external_action:true},'actual-send-once');assert.equal(activated.status,'success');
    const duplicate=await run('web.activate',{tab_id:tab.tab_id,document_id:fresh.document_id,revision:fresh.revision,element_id:send.element_id},{external_action:true},'actual-send-once');assert.deepEqual(duplicate,activated);
    const waited=await run('web.wait',{tab_id:tab.tab_id,document_id:activated.document_id,revision:activated.revision,timeout_ms:2000});assert.equal(waited.changed,true);
    const answer=await run('web.read',{tab_id:tab.tab_id});assert.match(answer.text,/Connection acknowledged/);assert.equal(await page.evaluate(()=>window.sends),1);
  } finally {
    await context?.close();
    const resolved=path.resolve(profile),temp=path.resolve(os.tmpdir())+path.sep;
    assert.ok(resolved.startsWith(temp)&&path.basename(resolved).startsWith('iru-browser-bridge-test-'));fs.rmSync(resolved,{recursive:true,force:true});
  }
});

test('durable activation pending receipt survives a full browser restart without another Send',async()=>{
  const profile=fs.mkdtempSync(path.join(os.tmpdir(),'iru-browser-bridge-test-'));let context;
  const launch=()=>chromium.launchPersistentContext(profile,{channel:'msedge',headless:true,args:['--disable-extensions-except='+extensionPath,'--load-extension='+extensionPath]});
  try {
    context=await launch();let worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');
    const page=await context.newPage();await page.goto(origin+'/chat.html');
    const run=(operation,params={},authorization={},request_id='restart-'+(++serial))=>worker.evaluate(({operation,params,authorization,request_id})=>execute({type:'command',operation,params,authorization,request_id}),{operation,params,authorization,request_id});
    const tabs=await run('web.tabs'),tab=tabs.tabs.find(item=>item.url===origin+'/chat.html');const observed=await run('web.elements',{tab_id:tab.tab_id}),composer=observed.elements.find(e=>e.name==='Message');
    await run('web.fill',{tab_id:tab.tab_id,document_id:observed.document_id,revision:observed.revision,element_id:composer.element_id,text:'persisted receipt test'});
    const fresh=await run('web.elements',{tab_id:tab.tab_id}),send=fresh.elements.find(e=>e.name==='Send message'),params={tab_id:tab.tab_id,document_id:fresh.document_id,revision:fresh.revision,element_id:send.element_id};
    const result=await run('web.activate',params,{external_action:true},'persisted-one-send');assert.equal(result.status,'success');assert.equal(await page.evaluate(()=>window.sends),1);
    // Simulate worker loss between external effect and confirmed receipt; durable pending remains.
    await worker.evaluate(async()=>{const stored=await chrome.storage.local.get('activation_receipts');const entry=stored.activation_receipts['persisted-one-send'];delete entry.result;entry.pending=true;await chrome.storage.local.set(stored);});
    await context.close();context=await launch();worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');
    const nextPage=await context.newPage();await nextPage.goto(origin+'/chat.html');
    const unknown=await run('web.activate',params,{external_action:true},'persisted-one-send');assert.equal(unknown.status,'unknown');assert.equal(unknown.error,'needs_verification');assert.equal(await nextPage.evaluate(()=>window.sends),0);
  } finally {await context?.close();const resolved=path.resolve(profile);assert.ok(resolved.startsWith(path.resolve(os.tmpdir())+path.sep)&&path.basename(resolved).startsWith('iru-browser-bridge-test-'));fs.rmSync(resolved,{recursive:true,force:true});}
});
test('real production pairing and WebSocket router drive the actual extension through full chat flow',async()=>{
  const python=process.env.IRU_TEST_PYTHON || (process.platform==='win32'?'python.exe':'python3');
  const child=spawn(python,[path.join(__dirname,'browser_bridge_fixture_server.py'),'--port','0'],{cwd:path.join(__dirname,'..'),env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',PYTHONIOENCODING:'utf-8'},stdio:['ignore','pipe','pipe']});
  let output='',errors='',context;const profile=fs.mkdtempSync(path.join(os.tmpdir(),'iru-browser-bridge-test-'));
  child.stdout.on('data',data=>{output+=String(data);});child.stderr.on('data',data=>{errors+=String(data);});
  try {
    const fixtureOrigin=await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>{clearInterval(poll);reject(new Error('Browser fixture startup timed out: '+errors));},15000);
      const poll=setInterval(()=>{const match=output.match(/IRU_BROWSER_FIXTURE_PORT=(\d+)/);if(match){clearInterval(poll);clearTimeout(timeout);resolve('http://127.0.0.1:'+match[1]);}},30);
      child.once('error',error=>{clearInterval(poll);clearTimeout(timeout);reject(error);});
      child.once('exit',code=>{clearInterval(poll);clearTimeout(timeout);reject(new Error('Browser fixture stopped '+code+': '+errors));});
    });
    for(let i=0;i<50;i++){try{if((await fetch(fixtureOrigin+'/fixture/ready')).ok)break;}catch{}await new Promise(r=>setTimeout(r,30));}
    context=await chromium.launchPersistentContext(profile,{channel:'msedge',headless:true,args:['--disable-extensions-except='+extensionPath,'--load-extension='+extensionPath]});
    const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker'),extensionId=worker.url().split('/')[2];
    const options=await context.newPage();await options.goto('chrome-extension://'+extensionId+'/options.html');
    await options.evaluate(({server})=>{document.getElementById('server').value=server;document.getElementById('device').value='givi';document.getElementById('accountToken').value='browser-fixture-account-token';document.getElementById('pairForm').requestSubmit();},{server:fixtureOrigin});
    let stored;for(let i=0;i<100;i++){stored=await worker.evaluate(()=>chrome.storage.local.get('bridge_config'));if(stored.bridge_config?.token)break;await new Promise(r=>setTimeout(r,50));}
    assert.ok(stored.bridge_config?.token,'Real server pairing creates a scoped extension credential');assert.equal(stored.bridge_config.device_id,'givi');assert.ok(!JSON.stringify(stored).includes('browser-fixture-account-token'),'Account token is discarded');assert.equal(await options.locator('#accountToken').inputValue(),'');
    const page=await context.newPage();await page.goto(fixtureOrigin+'/fixture/pages/chat.html');
    const run=async(operation,params={},authorization={},task_id)=>{const response=await fetch(fixtureOrigin+'/fixture/action',{method:'POST',headers:{'Content-Type':'application/json','X-Token':'browser-fixture-account-token'},body:JSON.stringify({operation,params,authorization,task_id})});assert.equal(response.status,200);return response.json();};
    let tabs;for(let i=0;i<30;i++){tabs=await run('web.tabs');if(tabs.status==='success')break;await new Promise(r=>setTimeout(r,50));}assert.equal(tabs.status,'success');
    const tab=tabs.tabs.find(item=>item.url===fixtureOrigin+'/fixture/pages/chat.html');assert.ok(tab);
    const read=await run('web.read',{tab_id:tab.tab_id});assert.equal(read.trust,'untrusted_page_data');assert.match(read.text,/Welcome to the conversation/);
    const observed=await run('web.elements',{tab_id:tab.tab_id}),composer=observed.elements.find(item=>item.name==='Message');
    const filled=await run('web.fill',{tab_id:tab.tab_id,document_id:observed.document_id,revision:observed.revision,element_id:composer.element_id,text:'real IRU protocol connection'});assert.equal(filled.status,'success');assert.equal(await page.evaluate(()=>window.sends),0);
    const fresh=await run('web.elements',{tab_id:tab.tab_id}),send=fresh.elements.find(item=>item.name==='Send message'),params={tab_id:tab.tab_id,document_id:fresh.document_id,revision:fresh.revision,element_id:send.element_id};
    const denied=await run('web.activate',params);assert.equal(denied.status,'failed');assert.equal(await page.evaluate(()=>window.sends),0);
    const activated=await run('web.activate',params,{external_action:true},'production-protocol-one-send');assert.equal(activated.status,'success');
    const duplicate=await run('web.activate',params,{external_action:true},'production-protocol-one-send');assert.equal(duplicate.status,'success');assert.equal(await page.evaluate(()=>window.sends),1);
    const waited=await run('web.wait',{tab_id:tab.tab_id,document_id:activated.document_id,revision:activated.revision,timeout_ms:2000});assert.equal(waited.changed,true);
    const answer=await run('web.read',{tab_id:tab.tab_id});assert.match(answer.text,/Connection acknowledged/);assert.equal(await page.evaluate(()=>window.sends),1);
    await context.close();context=null;
    let offline;for(let i=0;i<20;i++){offline=await run('web.tabs');if(offline.status==='failed')break;await new Promise(r=>setTimeout(r,30));}assert.equal(offline.status,'failed');assert.match(offline.error,/offline/);
  } finally {
    await context?.close();child.kill();if(child.exitCode===null)await new Promise(resolve=>child.once('exit',resolve));
    const resolved=path.resolve(profile);assert.ok(resolved.startsWith(path.resolve(os.tmpdir())+path.sep)&&path.basename(resolved).startsWith('iru-browser-bridge-test-'));fs.rmSync(resolved,{recursive:true,force:true});
  }
});

test('large chat default tail keeps latest messages and composer; explicit head preserves the article start',async()=>{
  const page=await fixture('chat.html');await page.evaluate(()=>{const messages=document.getElementById('messages');const fragment=document.createDocumentFragment();for(let i=0;i<12000;i++){const article=document.createElement('article');article.textContent='Older message '+i+' '+('history data '.repeat(12));const button=document.createElement('button');button.textContent='Old action '+i;article.append(button);fragment.append(article);}const latest=document.createElement('p');latest.textContent='LATEST MESSAGE: material needed for the current answer';fragment.append(latest);messages.append(fragment);});
  const tail=await command(page,'web.read',{scope:'main',max_chars:1200});assert.equal(tail.status,'success');assert.match(tail.text,/LATEST MESSAGE/);assert.equal(tail.truncated,true);
  const elements=await command(page,'web.elements',{scope:'main',max_elements:10});assert.ok(elements.elements.some(item=>item.name==='Message'));assert.ok(elements.elements.some(item=>item.name==='Send message'));
  const head=await command(page,'web.read',{scope:'main',position:'head',max_chars:1200});assert.match(head.text,/Welcome to the conversation/);assert.doesNotMatch(head.text,/LATEST MESSAGE/);await page.close();
});

test('durable receipt cleanup evicts only confirmed outcomes older than seven days',async()=>{
  const old=Date.now()-8*24*60*60*1000,receiptState={};for(let i=0;i<510;i++)receiptState['confirmed-'+i]={fingerprint:'old',result:{status:i%2?'success':'failed'},updated_at:old};
  receiptState.pending={fingerprint:'pending',pending:true,updated_at:old};receiptState.unknown={fingerprint:'unknown',result:{status:'unknown',error:'needs_verification'},updated_at:old};
  const harness=backgroundHarness({receiptState}),socket=await harness.ready();
  const result=await harness.command(socket,{request_id:'new-after-expiry',operation:'web.activate',params:activationParams,authorization:{external_action:true}});assert.equal(result.status,'success');
  const stored=harness.receipts.activation_receipts;assert.equal(Object.keys(stored).length,3);assert.ok(stored.pending);assert.equal(stored.unknown.result.status,'unknown');assert.ok(stored['new-after-expiry'].created_at);
});
test('concurrent activation receipt writes remain durable without lost pending operations',async()=>{
  let activations=0;const harness=backgroundHarness({sendMessage:async(_id,message)=>{if(message.operation==='bridge.ping')return{status:'success'};activations++;await new Promise(r=>setTimeout(r,5));throw new Error('unknown effect');}}),socket=await harness.ready();
  const message=request_id=>({request_id,operation:'web.activate',params:activationParams,authorization:{external_action:true}});
  const outcomes=await Promise.all([harness.command(socket,message('concurrent-a')),harness.command(socket,message('concurrent-b'))]);assert.ok(outcomes.every(result=>result.status==='unknown'));assert.equal(activations,2);
  assert.equal(harness.receipts.activation_receipts['concurrent-a'].result.status,'unknown');assert.equal(harness.receipts.activation_receipts['concurrent-b'].result.status,'unknown');
});
test('extension reports concurrent-browser rejection and requires repair after expired authorization',async()=>{
  for(const [code,status] of [[4009,'browser_already_connected'],[4003,'pairing_required']]){const harness=backgroundHarness(),socket=await harness.ready();socket.close(code);await new Promise(r=>setImmediate(r));assert.equal(harness.local.bridge_status.status,status);}
});

test('generic activation refuses forms with credentials or a preselected local attachment',async()=>{
  const page=await fixture();await page.evaluate(()=>{window.credentialSubmits=0;window.fileSubmits=0;const main=document.querySelector('main');const auth=document.createElement('form');auth.id='auth-form';const password=document.createElement('input');password.type='password';password.value='fixture-only';const proceed=document.createElement('button');proceed.textContent='Continue';auth.append(password,proceed);auth.onsubmit=event=>{event.preventDefault();window.credentialSubmits++;};const attachment=document.createElement('form');attachment.id='attachment-form';const file=document.createElement('input');file.type='file';file.id='attachment';const send=document.createElement('button');send.textContent='Send attachment';attachment.append(file,send);attachment.onsubmit=event=>{event.preventDefault();window.fileSubmits++;};main.append(auth,attachment);});
  const auth=await observed(page,'Continue');assert.equal((await command(page,'web.activate',auth.params,{external_action:true})).error,'unsupported_sensitive_action');assert.equal(await page.evaluate(()=>window.credentialSubmits),0);
  const empty=await observed(page,'Send attachment');const allowed=await command(page,'web.activate',empty.params,{external_action:true});assert.equal(allowed.status,'success');assert.equal(await page.evaluate(()=>window.fileSubmits),1,'Empty attachment input does not block a chat composer');
  await page.locator('#attachment').setInputFiles({name:'fixture.bin',mimeType:'application/octet-stream',buffer:Buffer.from([0,1,254,255])});
  const selected=await observed(page,'Send attachment');assert.equal((await command(page,'web.activate',selected.params,{external_action:true})).error,'unsupported_file_upload');assert.equal(await page.evaluate(()=>window.fileSubmits),1);await page.close();
});
test('oversized associated form fails closed within the bounded control scan',async()=>{
  const page=await fixture();await page.evaluate(()=>{const form=document.createElement('form');for(let i=0;i<256;i++){const input=document.createElement('input');input.type='hidden';form.append(input);}const button=document.createElement('button');button.textContent='Continue large form';form.append(button);form.onsubmit=event=>{event.preventDefault();window.largeFormSubmitted=true;};document.querySelector('main').append(form);});
  const button=await observed(page,'Continue large form');const result=await command(page,'web.activate',button.params,{external_action:true});assert.equal(result.error,'unsupported_form_size');assert.equal(await page.evaluate(()=>!!window.largeFormSubmitted),false);await page.close();
});
