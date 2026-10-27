const {test}=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../ui/js/smart-ui.js');
const blocks=m=>ui.adapt({role:'assistant',...m}).blocks;
const task=m=>blocks(m).find(b=>b.type==='task');
const canonical=(m,status)=>({...m,workerReport:{schema_version:1,status,goal_completed:status==='success',artifacts:[],target_device_ids:[]}});
test('registry contains exactly four blocks, adapter is pure and keeps original text',()=>{
  assert.deepEqual(Object.keys(ui.REGISTRY),['text','task','file','action']);
  const m={role:'assistant',content:'Первая строка\n\nПоследняя <img src=x onerror=alert(1)>',commands:[]};
  const before=JSON.stringify(m),view=ui.adapt(m);
  assert.equal(view.blocks[0].text,m.content);assert.equal(JSON.stringify(m),before);
  const html=ui.render(view);assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<img'));
  assert.deepEqual(blocks({content:'Я создал report.docx. /api/download/123'}).map(b=>b.type),['text']);
});
test('status table is explicit and never reads prose or missing errors as success',()=>{
  for(const [source,expected] of Object.entries({running:'running',done:'unknown',completed_with_recovery:'unknown',partial:'partial',blocked:'blocked',failed:'failed',cancelled:'cancelled',confirm:'waiting'})) assert.equal(task({taskStatus:source}).status,expected);
  assert.equal(task({commands:[{action:'execute_cmd',result:{}}],content:'Выполнено ✓'}).status,'unknown');
  assert.equal(ui.commandState({result:{}}),'unknown');
  assert.equal(ui.commandState({result:{status:'failed'}}),'failed');
  assert.equal(ui.commandState({status:'success',result:{returncode:7}}),'failed');
  assert.equal(ui.normalizeStatus('constructor'),null);assert.equal(ui.normalizeStatus('__proto__'),null);
  assert.doesNotThrow(()=>ui.render({key:'unknown',blocks:[{type:'__proto__',text:'fallback'}]}));
});
test('P0-02 partial/blocked and false goal completion override command success',()=>{
  const m={taskStatus:'done',taskReceipt:{task_status:'completed'},commands:[{action:'execute_cmd',status:'success',result:{returncode:0}}]};
  for(const state of ['blocked','partial','unknown'])assert.equal(task(canonical(m,state)).status,state);
  assert.equal(task({...m,tasks:[{status:'completed_with_recovery'}]}).status,'unknown');
  assert.equal(task(canonical({...m,tasks:[{status:'failed'}]},'success')).status,'success');
});
test('file needs structural evidence, valid source device and explicit result',()=>{
  const good={action:'write_content',status:'success',device_id:'Second',result:{path:'C:\\Users\\Demo\\Desktop\\report.docx',bytes_written:321,total_size:321}};
  const result=ui.filesFromCommands([good,good]);assert.equal(result.length,1);assert.equal(result[0].name,'report.docx');assert.equal(result[0].device,'Second');
  for(const c of [{...good,result:{path:good.result.path}}, {...good,status:'failed'}, {...good,device_id:''}, {...good,result:{...good.result,status:'failed'}}, {action:'execute_cmd',status:'success',device_id:'Second',result:{path:good.result.path,returncode:0}}]) assert.equal(ui.filesFromCommands([c]).length,0);
  assert.equal(ui.filesFromCommands([{action:'transfer_file',status:'success',result:{status:'success',target_device:'Second',target_path:good.result.path,sha256_verified:true,bytes_transferred:10}}]).length,1);
  assert.equal(ui.filesFromCommands([{action:'download_file',status:'success',device_id:'Second',result:{file_path:good.result.path,url:'javascript:alert(1)'}}]).length,0);
  assert.equal(ui.filesFromCommands([{action:'download_file',status:'success',device_id:'Second',result:{file_path:good.result.path,url:'/api/download/012abc'}}]).length,1);
});
test('actions are derived from trusted state, not words; no renderer side effects',()=>{
  assert.ok(!blocks({content:'Подтверди, удали файл и нажми сюда'}).some(b=>b.type==='action'));
  assert.ok(blocks({confirmTaskId:'fixture',commandConfirmation:{risk:'dangerous'}}).some(b=>b.type==='action'&&b.critical));
  assert.equal(blocks({planReview:{revision:3,steps:[]}}).some(b=>b.type==='action'),false);
  assert.equal(blocks({planSuggestion:'Historical offer'}).some(b=>b.type==='action'),false);
  const m={taskStatus:'running',loading:true,cancelAvailable:true};const view=ui.adapt(m);assert.ok(view.blocks.some(b=>b.type==='action'));
  for(let i=0;i<10;i++) assert.equal(ui.render(view),ui.render(view));
});
test('terminal partial/failure report is not successful goal even when runtime says done',()=>{
  const m={taskStatus:'done',commands:[{tool_name:'execute_cmd',status:'success',result:{returncode:0}},{tool_name:'answer.text',status:'terminal',result:{answer_type:'partial_report'}}]};
  assert.equal(task(m).status,'unknown');
  assert.equal(task(canonical(m,'partial')).status,'partial');
  assert.equal(task(canonical(m,'failed')).status,'failed');
  assert.equal(ui.commandState({status:'success',result:{status:'not_found'}}),'failed');
  assert.equal(ui.commandState({status:'success',result:{status:'unknown'}}),'unknown');
  assert.deepEqual(blocks({content:'Привет',taskStatus:'done',commands:[{tool_name:'answer.text',status:'terminal',result:{answer_type:'pure_text'}}]}).map(b=>b.type),['text']);
});

test('malformed identifiers, return codes and Unicode metadata fail safely',()=>{
  assert.doesNotThrow(()=>ui.render(ui.adapt({id:{toString:'invalid'},role:'assistant',content:'Оригинал'})));
  assert.doesNotThrow(()=>ui.render(ui.adapt({_taskId:'\ud800',role:'assistant',content:'Оригинал'})));
  assert.equal(ui.commandState({status:'success',result:{returncode:'7'}}),'blocked');
  assert.equal(ui.filesFromCommands([{action:'write_content',status:'success',device_id:'Second',result:{path:'\ud800',bytes_written:1}}]).length,0);
});

test('terminal protocol/budget failures cannot become success from runtime done',()=>{
  for(const [name,state] of [['tool_only_protocol','failed'],['answer_auditor','failed'],['budget_guard','blocked']]) {
    const m={taskStatus:'done',commands:[{tool_name:name,status:state}]};
    assert.equal(task(m).status,'unknown');
    assert.equal(task(canonical(m,state)).status,state);
    assert.equal(task(canonical(m,'success')).status,'success');
  }
});

test('real get_file_link normalized and legacy contracts restore File without trusting prose',()=>{
  const command={action:'get_file_link',device_id:'Second',result:{url:'/api/download/012abc',file_path:'C:\\Users\\Demo\\Desktop\\linked.docx'}};
  assert.equal(ui.filesFromCommands([command]).length,1);
  assert.equal(ui.filesFromCommands([{...command,tool_name:'get_file_link',status:'success'}])[0].name,'linked.docx');
  const files=blocks({content:'Ссылка /api/download/012abc',commands:[command]}).filter(b=>b.type==='file');
  assert.equal(files.length,1);assert.equal(files[0].device,'Second');
  const html=ui.render(ui.adapt({role:'assistant',commands:[command]}));
  assert.ok(html.includes('data-action="download-message-file"'));assert.ok(!html.includes('href="/api/download/012abc"'));
  assert.equal(blocks({content:'Скачай linked.docx /api/download/012abc'}).filter(b=>b.type==='file').length,0);
  for(const invalid of [{...command,action:'unrelated_tool'}, {...command,status:'failed'}, {...command,status:'pending'}, {...command,result:{...command.result,error:'link_failed'}}, {...command,device_id:''}, {...command,result:{url:'javascript:alert(1)',file_path:command.result.file_path}}, {...command,result:{url:'https://other.invalid/api/download/012abc',file_path:command.result.file_path}}, {...command,result:{url:'/api/download/012abc'}}]) assert.equal(ui.filesFromCommands([invalid]).length,0);
});
test('done and weak receipt cannot override failed/partial/blocked nested steps',()=>{
  for(const state of ['failed','partial','blocked']) {
    const m={taskStatus:'done',tasks:[{status:'done',steps:[{status:state}]}],taskReceipt:{task_status:'completed'}};
    assert.equal(task(m).status,'unknown');
    assert.equal(task(canonical(m,state)).status,state);
  }
  assert.equal(task(canonical({taskStatus:'done'},'waiting_input')).status,'waiting');
});
test('sufficient final receipt preserves recovered success while negative final outcome remains authoritative',()=>{
  const m={taskStatus:'done',tasks:[{status:'done',steps:[{status:'failed'}]}],taskReceipt:{task_status:'completed',goal_completed:true,final_verification_status:'verified'}};
  assert.equal(task(m).status,'unknown');
  assert.equal(task(canonical(m,'success')).status,'success');
  for(const state of ['partial','failed','blocked','unknown'])assert.equal(task(canonical(m,state)).status,state);
});

const simpleSuccess={workerReport:{schema_version:1,status:'success',goal_completed:true,artifacts:[],target_device_ids:[]},taskStatus:'done',taskElapsedMs:2000,taskReceipt:{task_status:'completed',goal_completed:true},tasks:[],commands:[{tool_name:'app.open_url',step_id:'step_1',status:'success',result:{status:'opened_verified'}}]};
test('unknown is distinct from waiting and unverified done never claims success',()=>{
 for(const status of ['unknown','nonsense','done']) assert.equal(task({taskStatus:status}).status,'unknown');
 for(const status of ['pending','waiting','queued','confirm']) assert.equal(task({taskStatus:status}).status,'waiting');
 assert.equal(task({commands:[{tool_name:'app.launch',status:'success',result:{status:'unknown'}}]}).status,'unknown');
 assert.equal(task({commands:[{tool_name:'app.launch',status:'failed'}]}).status,'unknown');
 assert.ok(ui.render(ui.adapt({role:'assistant',taskStatus:'unknown'})).includes('Результат не подтверждён'));
 assert.equal(task({taskStatus:'done',commands:[{tool_name:'app.open_url',status:'success'}]}).status,'unknown');
});
test('one proved successful short ordinary operation renders a compact result with optional journal',()=>{
 assert.equal(task(simpleSuccess).compact,true);
 const html=ui.render(ui.adapt({role:'assistant',...simpleSuccess}),{taskDetails:()=>'<p>journal</p>'});
 assert.ok(html.includes('compact-result'));assert.ok(html.includes('Подробности'));assert.ok(html.includes('journal'));
 assert.ok(!html.includes('smart-task-title'));
});
test('PLAN, multiple operations, long tasks, recovery and confirmation retain detailed Task',()=>{
 for(const m of [
  {...simpleSuccess,taskMode:'plan'},
  {...simpleSuccess,tasks:[{status:'done',steps:[{status:'done'}]}]},
  {...simpleSuccess,commands:[...simpleSuccess.commands,...simpleSuccess.commands]},
  {...simpleSuccess,taskElapsedMs:30000},
  {...simpleSuccess,confirmTaskId:'confirmation'},
  {...simpleSuccess,planReview:{revision:1}},
  {...simpleSuccess,taskReceipt:{task_status:'completed',goal_completed:true,answer_source:'pipeline_step_report'}},
  {...simpleSuccess,commands:[{tool_name:'execute_cmd',status:'failed'},...simpleSuccess.commands]},
 ])assert.equal(task(m).compact,false);
 for(const status of ['partial','blocked','failed','unknown','running']){
  const m=canonical({...simpleSuccess,taskStatus:status,taskReceipt:{task_status:status}},status);
  assert.equal(task(m).status,status);assert.equal(task(m).compact,false);
 }
});
test('completion needs structured evidence, no inference from model prose',()=>{
  const command={tool_name:'app.open_url',step_id:'step_1',status:'success',result:{status:'opened_verified'}};
  const terminal={tool_name:'answer.text',status:'terminal',result:{answer_type:'grounded_report',basis:['step_1'],self_check:{has_sufficient_evidence:true}}};
  const m={taskStatus:'done',taskElapsedMs:2000,commands:[command,terminal]};
  assert.equal(task(m).status,'unknown');
  assert.equal(task(canonical(m,'success')).compact,true);
  for(const result of [{answer_type:'grounded_report'},{...terminal.result,basis:['old_step']},{...terminal.result,self_check:{has_sufficient_evidence:false}}]) {
    const changed={...m,commands:[command,{...terminal,result}]};
    assert.equal(task(changed).status,'unknown');
    assert.equal(task(canonical(changed,'partial')).status,'partial');
  }
});

test('a stale terminal or unknown duration cannot compact a later/unmeasured operation',()=>{
 const action={tool_name:'app.open_url',step_id:'step_1',status:'success'};
 const terminal={tool_name:'answer.text',status:'terminal',result:{answer_type:'grounded_report',basis:['step_1'],self_check:{has_sufficient_evidence:true}}};
 assert.equal(task(canonical({taskStatus:'done',taskElapsedMs:1000,commands:[action,terminal,{tool_name:'execute_cmd',status:'failed'}]},'failed')).status,'failed');
 assert.equal(task({...simpleSuccess,taskElapsedMs:undefined}).compact,false);
});

test('conversation without operations stays text, but an explicit negative result is never hidden',()=>{
 const m={taskStatus:'done',taskMode:'conversation',content:'Привет',commands:[]};
 assert.deepEqual(blocks(m).map(b=>b.type),['text']);
 for(const status of ['failed','partial','blocked','unknown']) assert.equal(task({...m,taskStatus:status}).status,status);
 assert.equal(task({...m,loading:true}).status,'running');
});


test('DIALOG conversational text and raw execution details remain separate without a fifth block',()=>{
 const raw='10 slide structures. C:\\private\\build.py <script>bad()</script>';
 const m={workerReport:{schema_version:1,status:'success',goal_completed:true,artifacts:[],target_device_ids:[]},content:raw,conversationalResponse:'Презентация готова.',executionDetails:raw,taskStatus:'success',
  taskReceipt:{task_status:'completed',goal_completed:true,final_verification_status:'verified'},taskElapsedMs:1000,
  commands:[{tool_name:'write_content',status:'success',device_id:'pc',result:{path:'C:\\Desktop\\result.pptx',bytes_written:500}}]};
 const view=ui.adapt({role:'assistant',...m});
 assert.equal(view.blocks.find(b=>b.type==='text').text,'Презентация готова.');
 assert.equal(view.blocks.find(b=>b.type==='task').executionDetails,raw);
 assert.equal(view.blocks.find(b=>b.type==='task').compact,true);
 assert.deepEqual(Object.keys(ui.REGISTRY),['text','task','file','action']);
 for(const status of ['unknown','partial','failed','blocked']){
  const state=task(canonical({...m,taskStatus:status,taskReceipt:{task_status:status,goal_completed:false}},status));
  assert.equal(state.status,status);assert.equal(state.compact,false);
 }
});

test('DIALOG file cards accept verified owned Worker artifacts, never report prose or invented download URLs',()=>{
 const report={schema_version:1,target_device_ids:['pc'],artifacts:[{path:'C:\\Desktop\\result.pptx',device_id:'pc',verified:true}]};
 const good=blocks({content:'Result',workerReport:report}).filter(b=>b.type==='file');
 assert.equal(good.length,1);assert.equal(good[0].name,'result.pptx');
 for(const artifact of [{path:report.artifacts[0].path,device_id:'other',verified:true},{path:report.artifacts[0].path,device_id:'pc',verified:false},{path:'',device_id:'pc',verified:true}])
  assert.equal(blocks({workerReport:{...report,artifacts:[artifact]}}).filter(b=>b.type==='file').length,0);
 assert.equal(blocks({content:'Presentation ready at C:\\Desktop\\fake.pptx'}).filter(b=>b.type==='file').length,0);
});


test('retired highlights are ignored and all response text remains escaped',()=>{
 const source='<script>alert(1)</script> result';
 const view=ui.adapt({role:'assistant',content:source,highlights:[{start:0,end:8,kind:'warning'}]});
 const rendered=ui.render(view);
 assert.ok(!rendered.includes('<script>')&&!rendered.includes('<mark'));
 assert.ok(rendered.includes('&lt;script&gt;'));
 assert.equal(ui.highlightedText,undefined);
 assert.ok(!Object.hasOwn(view.blocks[0],'highlights'));
});

test('WorkerReport outcome remains authoritative over legacy nested step snapshots',()=>{
 const report={schema_version:1,status:'success',goal_completed:true,evidence_refs:['step_1'],artifacts:[],target_device_ids:['pc']};
 const message={taskStatus:'done',workerReport:report,content:'Observed IRU',tasks:[{status:'failed',steps:[{status:'failed'}]}]};
 assert.equal(task(message).status,'success');
 for(const status of ['unknown','partial','failed','blocked','cancelled'])assert.equal(task({...message,workerReport:{...report,status,goal_completed:false}}).status,status);
});


test('assistant formatting uses only escaped text and fixed tags',()=>{
  const source='# Result\n\n**Important** <img src=x onerror=alert(1)>\n<script>alert(2)</script>';
  const block={key:'format',text:source,long:false};
  const html=ui.REGISTRY.text(block,{formatAssistantText:true});
  assert.ok(html.includes('<h1>Result</h1>'));
  assert.ok(html.includes('<strong>Important</strong>'));
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!html.includes('<img') && !html.includes('<script'));
  assert.equal(block.text,source);
  assert.ok(ui.REGISTRY.text(block,{}).includes('# Result'));
  assert.ok(!ui.REGISTRY.text(block,{}).includes('<h1>'));
});

test('code and incomplete model markup remain safe and readable',()=>{
  const html=ui.REGISTRY.text({key:'code',long:false,text:'# Heading\n\n`**literal**`\n\n```html\n<b onclick="bad()">**literal**</b>'},{formatAssistantText:true});
  assert.ok(html.includes('`**literal**`'));
  assert.ok(html.includes('<pre><code>&lt;b onclick=&quot;bad()&quot;&gt;**literal**&lt;/b&gt;</code></pre>'));
  assert.ok(!html.includes('<b onclick'));
});

test('canonical artifacts suppress stale write cards and keep server outcome visible',()=>{
 const command={tool_name:'write_content',status:'success',device_id:'pc',result:{path:'C:/result.txt',bytes_written:3}};
 const m=canonical({commands:[command]},'unknown');
 m.workerReport.target_device_ids=['pc'];m.workerReport.artifacts=[{path:'C:/result.txt',device_id:'pc',verified:false}];
 assert.equal(task(m).status,'unknown');assert.equal(blocks(m).filter(b=>b.type==='file').length,0);
 assert.equal(task(canonical({},'partial')).status,'partial');
});

test('partial WorkerReport overrides stale Done and scopes file verification to actual checks',()=>{
 const report={schema_version:1,status:'partial',goal_completed:false,summary:'Содержание не подтверждено.',
   target_device_ids:['pc'],artifacts:[{path:'C:/report.docx',device_id:'pc',verified:true,
     verification_scope:'file_identity_and_format',content_verified:false}]};
 const view=ui.adapt({role:'assistant',content:'Готово. Всё проверено.',workerReport:report});
 assert.equal(view.blocks.find(b=>b.type==='text').text,report.summary);
 const html=ui.render(view);
 assert.ok(!html.includes('Готово'));
 assert.ok(html.includes('полнота содержания не проверена'));
 const checked=ui.render(ui.adapt({role:'assistant',workerReport:{...report,status:'success',goal_completed:true,
   artifacts:[{...report.artifacts[0],content_verified:true}]}}));
 assert.ok(checked.includes('Файл и полнота содержания подтверждены'));
});

test('Orchestrator reply is visible while technical Goal counters stay inside collapsed details',()=>{
 const goal={goal_id:'g',status:'partial',completed_count:0,total_count:1,
   requires_user_action:false,message:'Окно остаётся открытым.',task_ids:['t'],artifacts:[]};
 const html=ui.render(ui.adapt({role:'assistant',goalId:'g',taskKind:'goal',content:goal.message,goalPresentation:goal}));
 assert.ok(html.includes('Окно остаётся открытым.'));
 const details=html.indexOf('<details');
 assert.ok(details>0 && html.indexOf('0 / 1')>details);
 assert.ok(!html.slice(0,details).includes('Частично выполнено'));
 goal.result_artifacts=[{path:'C:/report.docx',device_id:'pc',task_id:'t',verified:true,content_verified:true}];
 const checked=ui.render(ui.adapt({role:'assistant',goalId:'g',taskKind:'goal',content:goal.message,goalPresentation:goal}));
 assert.ok(checked.includes('Файл и полнота содержания подтверждены'));
});
