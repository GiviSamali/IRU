const {test}=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../ui/js/smart-ui.js');
const blocks=m=>ui.adapt({role:'assistant',...m}).blocks;
const task=m=>blocks(m).find(b=>b.type==='task');
test('registry contains exactly four blocks, adapter is pure and keeps original text',()=>{
  assert.deepEqual(Object.keys(ui.REGISTRY),['text','task','file','action']);
  const m={role:'assistant',content:'Первая строка\n\nПоследняя <img src=x onerror=alert(1)>',commands:[]};
  const before=JSON.stringify(m),view=ui.adapt(m);
  assert.equal(view.blocks[0].text,m.content);assert.equal(JSON.stringify(m),before);
  const html=ui.render(view);assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<img'));
  assert.deepEqual(blocks({content:'Я создал report.docx. /api/download/123'}).map(b=>b.type),['text']);
});
test('status table is explicit and never reads prose or missing errors as success',()=>{
  for(const [source,expected] of Object.entries({running:'running',done:'success',completed_with_recovery:'success',partial:'partial',blocked:'blocked',failed:'failed',cancelled:'cancelled',confirm:'waiting'})) assert.equal(task({taskStatus:source}).status,expected);
  assert.equal(task({commands:[{action:'execute_cmd',result:{}}],content:'Выполнено ✓'}).status,'waiting');
  assert.equal(ui.commandState({result:{}}),'waiting');
  assert.equal(ui.commandState({result:{status:'failed'}}),'failed');
  assert.equal(ui.commandState({status:'success',result:{returncode:7}}),'failed');
  assert.equal(ui.normalizeStatus('constructor'),null);assert.equal(ui.normalizeStatus('__proto__'),null);
  assert.doesNotThrow(()=>ui.render({key:'unknown',blocks:[{type:'__proto__',text:'fallback'}]}));
});
test('P0-02 partial/blocked and false goal completion override command success',()=>{
  const m={taskStatus:'blocked',taskReceipt:{task_status:'partial',command_outcome:'success',goal_completed:false},commands:[{action:'execute_cmd',status:'success',result:{returncode:0}}]};
  assert.equal(task(m).status,'blocked');assert.ok(task(m).summary.includes('не завершена'));
  assert.equal(task({...m,taskStatus:'done'}).status,'partial');
  assert.equal(task({taskStatus:'done',commands:[{status:'failed'}],tasks:[{status:'completed_with_recovery'}]}).status,'success');
  assert.equal(task({tasks:[{status:'done'},{status:'blocked'}]}).status,'blocked');
  assert.equal(task({tasks:[{status:'done'},{}]}).status,'waiting');
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
  assert.ok(blocks({planReview:{revision:3,steps:[]}}).some(b=>b.type==='action'));
  const m={taskStatus:'running',loading:true,cancelAvailable:true};const view=ui.adapt(m);assert.ok(view.blocks.some(b=>b.type==='action'));
  for(let i=0;i<10;i++) assert.equal(ui.render(view),ui.render(view));
});
test('terminal partial/failure report is not successful goal even when runtime says done',()=>{
  const m={taskStatus:'done',commands:[{tool_name:'execute_cmd',status:'success',result:{returncode:0}},{tool_name:'answer.text',status:'terminal',result:{answer_type:'partial_report'}}]};
  assert.equal(task(m).status,'partial');
  assert.equal(task({...m,commands:[{tool_name:'answer.report_failure',status:'terminal',result:{}}]}).status,'failed');
  assert.equal(ui.commandState({status:'success',result:{status:'not_found'}}),'failed');
  assert.equal(ui.commandState({status:'success',result:{status:'unknown'}}),'blocked');
  assert.deepEqual(blocks({content:'Привет',taskStatus:'done',commands:[{tool_name:'answer.text',status:'terminal',result:{answer_type:'pure_text'}}]}).map(b=>b.type),['text']);
});

test('malformed identifiers, return codes and Unicode metadata fail safely',()=>{
  assert.doesNotThrow(()=>ui.render(ui.adapt({id:{toString:'invalid'},role:'assistant',content:'Оригинал'})));
  assert.doesNotThrow(()=>ui.render(ui.adapt({_taskId:'\ud800',role:'assistant',content:'Оригинал'})));
  assert.equal(ui.commandState({status:'success',result:{returncode:'7'}}),'blocked');
  assert.equal(ui.filesFromCommands([{action:'write_content',status:'success',device_id:'Second',result:{path:'\ud800',bytes_written:1}}]).length,0);
});

test('terminal protocol/budget failures cannot become success from runtime done',()=>{
  for (const [name,status] of [['tool_only_protocol','failed'],['answer_auditor','failed'],['budget_guard','blocked']]) {
    assert.equal(task({taskStatus:'done',commands:[{tool_name:name,status}]}).status,status);
  }
  // An explicit completed receipt or validated final answer overrides old recovered failures.
  assert.equal(task({taskStatus:'done',taskReceipt:{task_status:'completed_with_recovery',final_verification_status:'verified'},commands:[{tool_name:'tool_only_protocol',status:'failed'}]}).status,'success');
  assert.equal(task({taskStatus:'done',commands:[{tool_name:'tool_only_protocol',status:'failed'},{tool_name:'answer.text',status:'terminal',result:{answer_type:'grounded_report'}}]}).status,'success');
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
  for(const state of ['failed','partial','blocked']){
    for(const tasks of [[{status:state}],[{status:'done',steps:[{status:state}]}],[{status:'done',steps:[{status:'done'},{status:state}]}]]){
      assert.equal(task({taskStatus:'done',tasks}).status,state);
      assert.equal(task({taskStatus:'done',taskReceipt:{task_status:'completed'},tasks}).status,state);
      assert.equal(task({taskStatus:'done',taskReceipt:{task_status:'completed',final_verification_status:'unverified'},tasks}).status,state);
    }
  }
  assert.equal(task({taskStatus:'done',tasks:[{status:'done',steps:[{status:'pending'}]}]}).status,'waiting');
  assert.equal(task({taskStatus:'done',tasks:[{status:'done',steps:[{status:'running'}]}]}).status,'running');
});
test('sufficient final receipt preserves recovered success while negative final outcome remains authoritative',()=>{
  const tasks=[{status:'done',steps:[{status:'failed'}]}];
  assert.equal(task({taskStatus:'done',tasks,taskReceipt:{task_status:'completed_with_recovery',final_verification_status:'verified'}}).status,'success');
  assert.equal(task({taskStatus:'done',tasks,taskReceipt:{task_status:'completed',goal_completed:true}}).status,'success');
  assert.equal(task({taskStatus:'done',tasks,taskReceipt:{task_status:'completed',final_verification_status:'verified',goal_completed:false}}).status,'partial');
  assert.equal(task({taskStatus:'done',tasks,taskReceipt:{task_status:'completed',final_verification_status:'failed'}}).status,'failed');
  assert.equal(task({taskStatus:'blocked',tasks,taskReceipt:{task_status:'completed',final_verification_status:'verified'}}).status,'blocked');
});
