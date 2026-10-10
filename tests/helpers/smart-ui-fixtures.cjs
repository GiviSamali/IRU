/* Synthetic fixtures only. Shared by browser regression and the local UI demo. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const dir=path.resolve(__dirname,'../../ui');
const write={action:'write_content',tool_name:'write_content',status:'success',device_id:'Second',step_id:'step_1',command:'[tool] write_content',result:{path:'C:\\Users\\Demo\\Desktop\\report.docx',bytes_written:1280,total_size:1280}};
const messages=[
 {id:1,role:'user',content:'Подготовь отчёт и покажи состояние работы.'},
 {id:2,role:'assistant',_taskId:'demo-running',loading:true,currentStatus:'writing_file',taskStatus:'running',taskTitle:'Подготовка отчёта',liveTasks:[{id:1,goal:'Собрать материалы',status:'running',steps:[{idx:1,title:'Изучить источник',status:'done',result:'Источник прочитан.'},{idx:2,title:'Подготовить документ',status:'running'}]}]},
 {id:3,role:'assistant',_taskId:'demo-completed',content:'Отчёт подготовлен.\nФайл доступен на Second.\n\n'+('В отчёте сохранены исходные данные и выводы.\n'.repeat(9)),taskStatus:'done',taskReceipt:{task_status:'completed'},commands:[write],tasks:[{id:2,goal:'Подготовить отчёт',status:'completed',steps:[{idx:1,title:'Создать документ',status:'done',result:'Документ сохранён.',verification:'Проверен результат записи.'}]}]},
 {id:4,role:'assistant',_taskId:'demo-partial',content:'Команда выполнена. Продолжение исходной задачи недоступно.',taskStatus:'blocked',taskReceipt:{task_status:'partial',command_outcome:'success',goal_completed:false},commands:[{action:'execute_cmd',tool_name:'execute_cmd',step_id:'step_1',status:'success',device_id:'Second',result:{returncode:0,stdout:'OK: fixture_action_verified'}}]},
 {id:5,role:'assistant',_taskId:'demo-confirm',confirmTaskId:'demo-confirm',taskStatus:'confirm',content:'Удаление требует подтверждения.\nЭто демонстрация: реальные файлы не затрагиваются.',commandConfirmation:{confirmation_id:'demo-confirmation',risk:'dangerous',voice_allowed:false,command:'Удалить демонстрационный файл'},commands:[]},
];
const clone=value=>JSON.parse(JSON.stringify(value));
function taskReply(id,status='blocked') {
 return {task_id:id,chat_id:1,message:'Демонстрационная задача',device_ids:['Second'],status,answer:status==='cancelled'?'Демонстрационная задача отменена.':'Команда проверена; исходная цель не завершена.',tasks:[],commands:[],task_receipt:status==='cancelled'?{task_status:'cancelled'}:{task_status:'partial',goal_completed:false,command_outcome:'success'}};
}
function apiData(url,method,decisions) {
 const user={id:2,name:'Demo',is_admin:false,data_consent:true,plan:'pro',limits:{}};
 if(url==='/api/auth'||url==='/api/user_info')return {status:'ok',user};
 if(url==='/api/chats')return {status:'ok',chats:[{id:1,title:'Smart UI — демонстрация'}]};
 if(url==='/api/chats/1/messages')return {status:'ok',messages:clone(messages)};
 if(url==='/api/devices')return {status:'ok',devices:{Second:{online:true,info:{hostname:'Second',os:'Windows',username:'Demo'}}}};
 if(url==='/api/voice/config')return {available:true};
 if(url==='/api/terms_status')return {status:'ok',accepted:true};
 if(url==='/api/memory/stats')return {status:'ok',memory_stats:{facts:0,commands:0,facts_list:[]}};
 if(url==='/api/memory/facts')return {status:'ok',facts:[]};
 if(url==='/api/download_request')return {status:'error',error:'Это mock: бинарный файл не загружается.'};
 const match=url.match(/^\/api\/tasks\/([^/]+)(?:\/(.+))?$/);
 if(match){if(method==='POST'){decisions.set(match[1],match[2]==='cancel'?'cancelled':'blocked');return {status:'ok'};}return {status:'ok',task:taskReply(match[1],decisions.get(match[1])||'running')};}
 return {status:'ok',facts:[],devices:{},passport:{},memory_stats:{facts:0,commands:0,facts_list:[]}};
}
function createServer({demo=false}={}) {
 const requests=[],decisions=new Map();
 const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname.startsWith('/api/')){
   let body='';req.on('data',chunk=>{body+=chunk;if(body.length>8192)req.destroy();});
   req.on('end',()=>{requests.push({path:url.pathname,method:req.method,body});if(requests.length>4096)requests.shift();res.setHeader('Content-Type','application/json; charset=utf-8');const data=apiData(url.pathname,req.method,decisions);if(demo&&url.pathname==='/api/voice/config')data.available=false;res.end(JSON.stringify(data));});return;
  }
  const pathname=decodeURIComponent(url.pathname.replace(/^\/static\//,'/'));
  const file=path.resolve(dir,'.'+(pathname==='/'?'/index.html':pathname));
  if(!file.startsWith(dir+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  let content=fs.readFileSync(file);
  if(demo&&file.endsWith('index.html'))content=content.toString().replace('<script src="js/core.js"','<script>localStorage.setItem("iru_token","smart-ui-fixture");</script>\n<script src="js/core.js"');
  const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.ico':'image/x-icon','.png':'image/png'};
  res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');res.end(content);
 });
 return {server,requests};
}
module.exports={messages,write,clone,createServer};