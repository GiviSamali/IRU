const {test}=require('node:test');
const assert=require('node:assert/strict');
const ui=require('../ui/js/smart-ui.js');
const message=(status='success')=>({role:'assistant',goalId:'g',taskKind:'goal',
  content:'Worker SHA-256 long report',conversationalResponse:'Создал alpha.txt и beta.txt.',
  goalPresentation:{goal_id:'g',status,terminal:true,completed_count:status==='success'?2:1,total_count:2,
    message:'Worker SHA-256 long report',task_ids:['a','b'],result_artifacts:['alpha.txt','beta.txt'].map((name,i)=>({
      name,path:'C:/LIVE/'+name,device_id:'pc',task_id:i?'b':'a',verified:true}))}});
test('terminal Goal renders only conversation, two files, no report or counters',()=>{
  const m=message(),before=JSON.stringify(m),view=ui.adapt(m),html=ui.render(view);
  assert.equal(JSON.stringify(m),before);
  assert.equal(view.blocks.filter(b=>b.type==='file').length,2);
  assert.equal(html.split('Создал alpha.txt и beta.txt.').length-1,1);
  for(const value of ['Worker SHA-256','Поручение','Ход выполнения','2 / 2'])assert.ok(!html.includes(value));
});
for(const status of ['partial','unknown','failed','blocked','cancelled'])test(status+' remains visible',()=>{
  const m=message(status);m.conversationalResponse='Первый файл создан. Второй результат не получен.';
  const html=ui.render(ui.adapt(m));
  assert.ok(html.includes(`data-status="${status}"`) && html.includes('smart-status'));
  assert.ok(html.includes('Второй результат не получен.') && !html.includes('Worker SHA-256'));
});
test('research is neither duplicated nor truncated, markup stays escaped',()=>{
  const m=message();m.conversationalResponse=Array.from({length:100},(_,i)=>`${i+1}. Вывод`).join('\n')+'\n<img src=x>';
  const html=ui.render(ui.adapt(m));
  assert.ok(html.includes('100. Вывод') && html.includes('&lt;img src=x&gt;'));
  assert.ok(!html.includes('Полный текст') && !html.includes('<img'));
});
test('only exact generated file footers disappear; other technical prose stays available',()=>{
  const m=message();m.goalPresentation.result_artifacts[0].sha256='a'.repeat(64);
  m.conversationalResponse='Создал файлы.\nФайл: C:/LIVE/alpha.txt\nC:/LIVE/alpha.txt · SHA-256 '+'a'.repeat(64)+'\nЗапрошенный SHA-256: '+'a'.repeat(64);
  const view=ui.adapt(m),text=view.blocks.find(b=>b.type==='goal').message;
  assert.ok(!text.includes('Файл: C:/LIVE') && !text.includes(' · SHA-256'));
  assert.ok(text.includes('Запрошенный SHA-256:'));
});
for(const status of ['running','queued','waiting_confirmation','waiting_input'])test(status+' keeps progress and attention',()=>{
  const m=message(status);m.goalPresentation.terminal=false;m.goalPresentation.completed_count=0;
  m.goalPresentation.requires_user_action=status.startsWith('waiting');
  if(status==='waiting_confirmation')m.confirmTaskId='turn';
  const view=ui.adapt(m),html=ui.render(view);
  assert.ok(html.includes('Ход выполнения'));
  if(status.startsWith('waiting'))assert.ok(html.includes('Нужен ответ'));
  if(m.confirmTaskId)assert.ok(view.blocks.some(b=>b.type==='action'));
});
