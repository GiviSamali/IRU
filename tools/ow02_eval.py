"""Reproducible OW-02 probes. Default: offline mocks. --live: primary LLM only, never agents."""
import argparse
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import sqlite3
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
SCENARIOS=[
 ('conversation','Стоит ли использовать Python для такой задачи?',{'conversation'},None),
 ('file','Создай текстовый файл на рабочем столе',{'delegate'},'simple'),
 ('presentation','Подготовь презентацию, документ и таблицу по статье',{'delegate'},'plan'),
 ('known_reference','Передай этот файл с givi на Second',{'delegate'},'simple'),
 ('missing_reference','Передай этот файл на Second',{'clarify'},None),
 ('status','Как там презентация?',{'task_status'},None),
 ('parallel_dialogue','Объясни разницу между TCP и UDP',{'conversation'},None),
 ('queue','А ещё подготовь таблицу расходов',{'delegate'},None),
 ('multi_device','На givi открой браузер, на Second открой приложение',{'delegate'},None),
 ('incomplete','Какой из этих вариантов лучше?',{'conversation','clarify'},None),
]


def offline(owner, chat):
 from test_tool_only_protocol import _run_case,_message,_tool_call,_answer_call
 from server import database as db, orchestrator as orch
 from server.routers import tasks as routes
 from server.worker_scheduler import WorkerScheduler,init_worker_storage
 from server.runtime_state import devices
 init_worker_storage();sent=[];captured=[]
 async def send(device,action,args):
  sent.append(action);return {'status':'ok','path':args.get('path'),'bytes_written':1,'summary':'OK: file_written'}
 began=time.perf_counter()
 with contextlib.redirect_stdout(io.StringIO()):
  result=_run_case([_message(tool_calls=[_tool_call('one','write_content',{'path':'C:/Temp/one.txt','content':'1'})]),
   _message(tool_calls=[_tool_call('two','write_content',{'path':'C:/Temp/two.txt','content':'2'})]),
   _message(tool_calls=[_answer_call('answer','Both written',answer_type='grounded_report',basis=['step_1','step_2'])])],
   user_message='Создай два отдельных файла',send_command_fn=send,captured=captured)
 measurement={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'mode':'controlled_mock_after',
  'two_files':{'tool_calls':len(sent),'llm_calls':len(captured),'elapsed_ms':round((time.perf_counter()-began)*1000,2),'terminal':result['commands'][-1]['tool_name']}}
 db.add_message(chat,'user','Используй имя согласованное-имя.txt');db.add_message(chat,'assistant','Уточнение принято.')
 db.add_user_fact(str(owner['id']),'Предпочитает краткое оформление','preference')
 devices[f"{owner['id']}:pc"]={'user_id':owner['id'],'info':{'hostname':'pc'},'ws':object()}
 async def execute(t):t['status']='unknown'
 scheduler=WorkerScheduler(execute);routes.scheduler=scheduler
 async def sample():
  t=await routes.submit_worker(owner,chat,'Создай этот файл',[f"{owner['id']}:pc"],{},objective='Generated objective')
  await scheduler.runners[owner['id']];body=json.dumps(t['worker_context'],ensure_ascii=False)
  measurement['worker_context']={'chars':len(body),'retains_human_referent':'согласованное-имя.txt' in body}
  c=orch.context_for(owner['id'],chat,'Создай этот файл','pc')
  measurement['orchestrator_context']={'chars':len(json.dumps(c,ensure_ascii=False)),'general_preference_count':len(c['facts'])}
  await scheduler.shutdown()
 asyncio.run(sample())
 return measurement


def provider_context(owner, chat, scene):
 from server import orchestrator as orch
 context=orch.context_for(owner['id'],chat,'eval','givi')
 context['devices']=[{'device_id':d,'name':d,'platform':'Windows','online':True,'availability':'synthetic_eval_metadata'} for d in ['givi','Second']]
 context['selected_device']='givi';context['history']=[];context['tasks']=[]
 if scene=='queue':context['history']=[{'role':'user','content':'Расходы за сентябрь: жильё 10000, еда 5000.'}]
 if scene in {'known_reference','status','queue','parallel_dialogue'}:
  done=scene=='known_reference'
  context['tasks']=[{'task_id':'eval-task','chat_id':chat,'in_current_chat':True,'objective':'Создать презентацию',
    'status':'success' if done else 'running','device_ids':['givi'],'report':{'schema_version':1,'status':'success','goal_completed':True,
      'artifacts':[{'path':'C:/Users/Eval/Desktop/report.txt','device_id':'givi','verified':True}]}}]
 return context


async def live(owner,chat,args,cfg):
 from server import orchestrator as orch, database as db
 from server.llm_usage import estimate_deepseek_cost_usd
 rows=[];spent=0.0
 for name,text,expected,mode in SCENARIOS[:args.max_calls]:
  context=provider_context(owner,chat,name)
  upper_input=len(json.dumps([orch.SYSTEM,context,text,orch.TOOL],ensure_ascii=True).encode())+2048
  reserve=estimate_deepseek_cost_usd(cfg.get('model'),{'cache_miss_tokens':upper_input,'completion_tokens':1200},cfg)
  if spent+reserve>args.max_cost_usd:break
  began=time.perf_counter();poll='ow02-live-'+name
  try:
   decision,metrics=await orch.decide(text,context,user_id=owner['id'],chat_id=chat,task_id=poll)
   row={'scenario':name,'intent':decision.intent,'execution_mode':decision.execution_mode,
        'target_device_ids':decision.target_device_ids,'source_task_ids':decision.source_task_ids,
        'routing_matches':decision.intent in expected,'mode_matches':mode is None or decision.execution_mode==mode,
        'devices_valid':all(d in {'givi','Second'} for d in decision.target_device_ids),
        'reference_matches':name!='known_reference' or (decision.source_task_ids==['eval-task'] and set(decision.target_device_ids)=={'givi','Second'})}
  except Exception as exc:row={'scenario':name,'error_type':type(exc).__name__}
  summary=db.get_llm_usage_summary_for_poll_task(owner['id'],poll)
  spent+=summary.get('estimated_cost_usd') or 0
  row.update(latency_ms=round((time.perf_counter()-began)*1000,2),usage=summary)
  rows.append(row)
 return {'mode':'real_provider_synthetic_context','actual_devices':False,'paid_requests_explicitly_requested':True,
         'model':cfg.get('model'),'max_logical_calls':args.max_calls,'configured_price_budget_usd':args.max_cost_usd,
         'estimated_cost_usd':spent,'results':rows}



def usage_report(path,owner,task_id):
 """Read aggregate metrics only from a local DB. No prompts, paths or credentials."""
 with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as c:
  c.row_factory=sqlite3.Row
  events=c.execute('SELECT phase,metadata,prompt_tokens,completion_tokens,estimated_cost_usd,request_ok FROM llm_usage_events WHERE user_id=? AND poll_task_id=?',(owner,task_id)).fetchall()
  roles={}
  for event in events:
   meta=json.loads(event['metadata'] or '{}');key=meta.get('entity') or 'unlabelled'
   r=roles.setdefault(key,{'llm_calls':0,'prompt_tokens':0,'completion_tokens':0,'llm_latency_ms':0,'estimated_cost_usd':0.0})
   r['llm_calls']+=1;r['prompt_tokens']+=event['prompt_tokens'];r['completion_tokens']+=event['completion_tokens']
   r['llm_latency_ms']+=meta.get('latency_ms') or 0;r['estimated_cost_usd']+=event['estimated_cost_usd'] or 0
  job=c.execute('SELECT j.created_at,j.updated_at,m.task_metadata FROM worker_jobs j LEFT JOIN messages m ON m.id=j.message_id WHERE j.owner_user_id=? AND j.task_id=?',(owner,task_id)).fetchone()
  result={'task_id':task_id,'usage_by_entity':roles}
  if job:
   meta=json.loads(job['task_metadata'] or '{}');trace=meta.get('diagnosticTrace') or []
   result.update(execution_ms=meta.get('taskElapsedMs'),admission_to_start_ms=int(max(0,(meta.get('workerStartedAt') or job['created_at'])-job['created_at'])*1000),
      captured_agent_wait_ms=sum(e.get('duration_ms') or 0 for e in trace if e.get('event')=='device_wait'),
      tool_results=sum(e.get('event')=='tool_result' and not str(e.get('tool_name','')).startswith('answer.') for e in trace))
   parent=c.execute("SELECT t.task_id,m.task_metadata FROM orchestrator_turns t JOIN messages m ON m.id=t.message_id WHERE t.owner_user_id=? AND json_extract(t.response,'$.worker_task_id')=?",(owner,task_id)).fetchone()
   if parent:
    result['orchestrator_metrics']=json.loads(parent['task_metadata'] or '{}').get('orchestratorMetrics')
    total=c.execute('SELECT COUNT(*) AS llm_calls, COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, COALESCE(SUM(completion_tokens),0) AS completion_tokens, COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd FROM llm_usage_events WHERE user_id=? AND poll_task_id=?',(owner,parent['task_id'])).fetchone()
    result['orchestrator_usage']=dict(total)
  return result

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--live',action='store_true',help='Explicit opt-in to paid primary LLM calls; no Worker/agent execution')
 parser.add_argument('--estimate',action='store_true',help='Show scenarios and a configured-price envelope without API calls')
 parser.add_argument('--max-calls',type=int,default=3,choices=range(1,11))
 parser.add_argument('--max-cost-usd',type=float,default=.02)
 parser.add_argument('--output',type=Path)
 parser.add_argument('--usage-task-id')
 parser.add_argument('--user-id',type=int)
 parser.add_argument('--database',type=Path)
 args=parser.parse_args()
 if args.max_cost_usd<=0:parser.error('cost budget must be positive')
 if args.usage_task_id:
  if args.user_id is None or args.database is None:parser.error('usage needs --database and --user-id')
  print(json.dumps(usage_report(args.database,args.user_id,args.usage_task_id),ensure_ascii=False,indent=2));return
 with tempfile.TemporaryDirectory(prefix='iru-ow02-eval-') as temp:
  os.environ['IRU_DB_PATH']=str(Path(temp)/'eval.db')
  from server import database as db
  with contextlib.redirect_stdout(io.StringIO()):
   db.init_db();owner=db.create_user('evaluation');chat=db.create_chat(owner['id'],'OW-02')['id']
  if args.live or args.estimate:
   from server.controller import load_llm_config
   from server.llm_usage import estimate_deepseek_cost_usd
   try:cfg=load_llm_config()
   except Exception:cfg={'model':'deepseek-v4-flash'}
   estimates=[]
   for name,text,expected,mode in SCENARIOS[:args.max_calls]:
    from server import orchestrator as orch
    upper=len(json.dumps([orch.SYSTEM,provider_context(owner,chat,name),text,orch.TOOL],ensure_ascii=True).encode())+2048
    estimates.append({'scenario':name,'expected_intents':sorted(expected),'expected_mode':mode,
      'input_token_upper_estimate':upper,'output_token_limit':1200,
      'configured_price_upper_usd':estimate_deepseek_cost_usd(cfg.get('model'),{'cache_miss_tokens':upper,'completion_tokens':1200},cfg)})
   report={'mode':'estimate_only','model':cfg.get('model'),'price_source':'current repo/config rates, not a billing guarantee',
     'estimated_upper_usd':sum(r['configured_price_upper_usd'] for r in estimates),'scenarios':estimates}
   if args.live:
    if not cfg.get('api_key') or not cfg.get('base_url'):parser.error('Configure IRU provider credentials first; no API calls made')
    report=asyncio.run(live(owner,chat,args,cfg))
  else:report=offline(owner,chat)
  rendered=json.dumps(report,ensure_ascii=False,indent=2)
  if args.output:args.output.write_text(rendered,encoding='utf-8')
  print(rendered)


if __name__=='__main__':main()
