"""P0-03 read-only mock baseline. No IRU source edits; no live network/device calls."""
import ast,string,argparse,asyncio,contextlib,contextvars,functools,hashlib,inspect,io,json,logging,math,os,re,statistics,subprocess,sys,tempfile,time
from collections import Counter,defaultdict
from pathlib import Path

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
os.environ['IRU_ANSWER_AUDITOR_ENABLED']='1'
import httpx
from server import controller as ctl, controller_pipeline as plan, controller_non_pipeline as normal
from server import database as db, task_runtime as runtime, controller_shared as shared, device_context as device_ctx
from server import voice, answer_auditor, controller_onboarding, run_journal, python_toolchain
from server.runtime_state import tasks,devices
from server.routers import tasks as task_routes

CFG={'model':'deepseek-v4-flash','model_reasoner':'deepseek-v4-pro','base_url':'https://fixture.invalid','api_key':'fixture-only','max_tokens':4096}
CURRENT=None
PHASE=contextvars.ContextVar('baseline_phase',default=None)
ROUTE=contextvars.ContextVar('baseline_route',default=None)
FIXED_PARTS=set()
# Extract only source-defined instruction literals; never persist prompt contents.
for module in (ctl,plan,normal,run_journal,answer_auditor,voice,controller_onboarding):
    for name,value in vars(module).items():
        if name.isupper() and isinstance(value,str) and len(value)>=30:
            try:pieces=[literal for literal,field,fmt,conversion in string.Formatter().parse(value)]
            except ValueError:pieces=[value]
            FIXED_PARTS.update(part for part in pieces if len(part)>=30)
for function in (plan.pipeline_plan_prompt,plan.pipeline_worker_prompt,plan.pipeline_summary_prompt):
    tree=ast.parse(inspect.getsource(function))
    for node in ast.walk(tree):
        if isinstance(node,ast.JoinedStr):
            FIXED_PARTS.update(item.value for item in node.values if isinstance(item,ast.Constant) and isinstance(item.value,str) and len(item.value)>=30)
for module in (normal,run_journal,plan,answer_auditor,voice):
    tree=ast.parse(inspect.getsource(module))
    FIXED_PARTS.update(node.value for node in ast.walk(tree) if isinstance(node,ast.Constant) and isinstance(node.value,str) and len(node.value)>=30)
CLOCK=time.perf_counter_ns

def ms(start):return (CLOCK()-start)/1_000_000

def wire(value):return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')

def count_text(value):
    if isinstance(value,str):return len(value),len(value.encode('utf-8'))
    return len(wire(value).decode('utf-8')),len(wire(value))

class Discard:
    def write(self,s):return len(s)
    def flush(self):pass

class Run:
    def __init__(self,label,config,enabled=True):
        self.label=label;self.config=config;self.enabled=enabled;self.calls=[];self.spans=defaultdict(list);self.ops=Counter();self.rows=Counter()
        self.facts_bytes=0;self.facts_rows=0;self.parts=defaultdict(set);self.history=set();self.depth=0;self.main_no=0;self.phase_no=Counter();self.tool_calls=0
        self.stage='accepted';self.prompt_sizes=[];self.outcome=None;self.facts_by_stage=Counter();self.fact_bytes_by_stage=Counter();self.stages=defaultdict(Counter);self.builder_bytes=Counter();self.builder_peak_chars=Counter();self.poll_bytes=[]
    def span(self,label,elapsed):
        if self.enabled:self.spans[label].append(elapsed)
    def component(self,label,value):
        if self.enabled and isinstance(value,str) and value:self.parts[label].add(value)
    def context_sizes(self,request):
        # Only aggregate numeric sizes escape this process. Components match exact builder outputs.
        groups=Counter();message_bytes=0
        for message in request.get('messages',[]):
            content=message.get('content') or ''
            if not isinstance(content,str):content=json.dumps(content,ensure_ascii=False,separators=(',',':'))
            body_bytes=len(content.encode('utf-8'));message_bytes+=body_bytes
            if message.get('role')=='tool':groups['tool_results_bytes']+=body_bytes;continue
            if (message.get('role'),content) in self.history:groups['history_bytes']+=body_bytes;continue
            if message.get('role')!='system':
                journal_bytes=0;answer_bytes=0
                try:
                    obj=json.loads(content)
                    if isinstance(obj,dict) and 'current_run_journal' in obj:
                        journal_bytes=len(json.dumps(obj['current_run_journal'],ensure_ascii=False).encode('utf-8'))
                        if 'answer_payload' in obj:answer_bytes=len(json.dumps(obj['answer_payload'],ensure_ascii=False).encode('utf-8'))
                except (ValueError,TypeError):pass
                groups['journal_bytes']+=journal_bytes;groups['answer_payload_bytes']+=answer_bytes
                groups['current_turn_and_corrections_bytes']+=body_bytes-journal_bytes-answer_bytes;continue
            # Longest non-overlapping exact chunks avoid nested/double counting.
            matches=[]
            for label,chunks in self.parts.items():
                for chunk in chunks:
                    start=0
                    while True:
                        pos=content.find(chunk,start)
                        if pos<0:break
                        matches.append((pos,pos+len(chunk),label));start=pos+len(chunk)
            taken=[]
            for a,b,label in sorted(matches,key=lambda row:row[1]-row[0],reverse=True):
                if not any(a<d and b>c for c,d in taken):taken.append((a,b));groups[label+'_bytes']+=len(content[a:b].encode('utf-8'))
            fixed_matches=[]
            for chunk in FIXED_PARTS:
                start=0
                while True:
                    pos=content.find(chunk,start)
                    if pos<0:break
                    fixed_matches.append((pos,pos+len(chunk)));start=pos+len(chunk)
            for a,b in sorted(fixed_matches,key=lambda row:row[1]-row[0],reverse=True):
                if not any(a<d and b>c for c,d in taken):
                    taken.append((a,b));groups['fixed_instructions_bytes']+=len(content[a:b].encode('utf-8'))
            residual=body_bytes-sum(len(content[a:b].encode('utf-8')) for a,b in taken)
            groups['system_residual_bytes']+=residual
        schema_bytes=len(wire(request.get('tools',[]))) if 'tools' in request else 0
        total=len(wire(request));groups['schemas_bytes']=schema_bytes
        groups['json_metadata_overhead_bytes']=total-message_bytes-schema_bytes
        assert groups['json_metadata_overhead_bytes']>=0
        assert sum(groups.values())==total
        return dict(groups),total

class Cursor:
    def __init__(self,inner,kind):self.inner=inner;self.kind=kind
    def __getattr__(self,name):return getattr(self.inner,name)
    def fetchall(self):
        start=CLOCK();rows=self.inner.fetchall()
        if CURRENT and CURRENT.enabled:CURRENT.span('sql_fetch',ms(start));CURRENT.rows[self.kind]+=len(rows)
        return rows
    def fetchone(self):
        start=CLOCK();row=self.inner.fetchone()
        if CURRENT and CURRENT.enabled:CURRENT.span('sql_fetch',ms(start));CURRENT.rows[self.kind]+=int(row is not None)
        return row
    def __iter__(self):return iter(self.inner)

class Connection:
    def __init__(self,inner):self.inner=inner
    def __getattr__(self,name):return getattr(self.inner,name)
    def execute(self,sql,*args,**kwargs):
        kind=next((name for name in ('llm_usage_events','device_profiles','device_memory','user_memory','messages','steps','tasks','users') if re.search(r'\b'+name+r'\b',sql)), 'other')
        action=sql.lstrip().split(None,1)[0].lower();key=action+'_'+kind
        start=CLOCK();cursor=self.inner.execute(sql,*args,**kwargs)
        if CURRENT and CURRENT.enabled:CURRENT.ops['sql.'+key]+=1;CURRENT.span('sql_execute',ms(start))
        return Cursor(cursor,key)

ORIGINAL_DB=db.get_db
@contextlib.contextmanager
def measured_db():
    with ORIGINAL_DB() as conn:yield Connection(conn)
db.get_db=measured_db

def replace_aliases(original,replacement):
    for module in tuple(sys.modules.values()):
        if module and getattr(module,'__name__','').startswith('server'):
            for key,value in tuple(vars(module).items()):
                if value is original:setattr(module,key,replacement)

COMPONENTS={
 'build_memory_block':'memory','build_devices_block':'device','build_device_profile_block':'device',
 'build_target_device_block':'device','build_python_toolchain_block':'device','format_minimal_llm_context_block':'device',
 'format_recent_artifact_context_block':'artifact','format_conversation_context_block':'history','data_only_context':None}
ROOTS={'_build_runtime_context','_build_route_kwargs','build_pipeline_shared_context','build_pipeline_worker_context',
       'build_minimal_llm_context','build_memory_block','build_chat_messages','build_recent_artifact_context',
       'format_recent_artifact_context_block','format_conversation_context_block','_build_non_pipeline_system_prompt'}
WATCH=[(db,'get_messages','history_retrieval_ms'),(db,'get_device_profile','profile_lookup_ms'),
       (db,'get_memory_stats','memory_retrieval_ms'),(db,'get_recent_commands','command_memory_retrieval_ms'),
       (db,'get_user_facts','user_fact_retrieval_ms'),(db,'add_message','result_persistence_ms'),
       (db,'add_llm_usage_event','usage_persistence_ms'),(ctl,'_build_runtime_context','context_builder_ms'),
       (ctl,'_build_route_kwargs','context_builder_ms'),(ctl,'_build_non_pipeline_system_prompt','context_builder_ms'),
       (device_ctx,'build_minimal_llm_context','manifest_ms'),(shared,'build_memory_block','memory_block_ms'),
       (shared,'build_chat_messages','history_format_ms'),(shared,'build_recent_artifact_context','artifact_builder_ms'),
       (shared,'format_recent_artifact_context_block','artifact_format_ms'),(plan,'build_pipeline_shared_context','pipeline_context_ms'),
       (plan,'build_pipeline_worker_context','pipeline_context_ms'),(plan,'format_conversation_context_block','history_format_ms')]
# Watch other formatted outputs even when they are not top-level context roots.
for name in ('build_devices_block','build_device_profile_block','data_only_context'):
    WATCH.append((shared,name,'context_component_ms'))
from server import path_scope
WATCH.append((path_scope,'build_target_device_block','context_component_ms'))
WATCH.append((python_toolchain,'build_python_toolchain_block','context_component_ms'))
WATCH.append((device_ctx,'format_minimal_llm_context_block','context_component_ms'))
for module,name,metric in WATCH:
    original=getattr(module,name)
    def factory(original,name,metric):
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            run=CURRENT;start=CLOCK();outer=bool(run and name in ROOTS and run.depth==0)
            if run and name in ROOTS:run.depth+=1
            try:result=original(*args,**kwargs)
            finally:
                elapsed=ms(start)
                if run:
                    run.span(metric,elapsed);run.ops[name]+=1;run.stages[run.stage][name]+=1
                    if name=='add_message':run.span(run.stage+'_message_persistence_ms',elapsed)
                    if name in ROOTS:
                        run.depth-=1
                        if outer:run.span('context_preparation_ms',elapsed)
            if run:
                if run.enabled and isinstance(result,(str,dict,list)):
                    try:
                        chars,amount=count_text(result);run.builder_bytes[name]+=amount;run.builder_peak_chars[name]=max(run.builder_peak_chars[name],chars)
                    except (TypeError,ValueError):pass
                label=COMPONENTS.get(name)
                if name=='data_only_context' and args:
                    label=('handoff' if args[0] in ('previous_step_summary','previous_step_handoff') else
                           'device' if args[0] in ('device_inventory','device_profile','device_context','target_device') else
                           'memory' if args[0]=='memory' else 'artifact' if args[0]=='recent_artifacts' else None)
                if label:run.component(label,result)
                if name=='build_chat_messages':
                    run.history.update((row.get('role'),row.get('content')) for row in result)
                if name=='get_memory_stats' and run.enabled:
                    facts=result.get('facts_list') or [];run.facts_rows+=len(facts)
                    amount=sum(len(str(f.get('text') or '').encode('utf-8')) for f in facts)
                    run.facts_bytes+=amount;run.facts_by_stage[run.stage]+=len(facts);run.fact_bytes_by_stage[run.stage]+=amount
            return result
        return wrapped
    replace_aliases(original,factory(original,name,metric))

ORIGINAL_PROBE=runtime._probe_python_toolchain_if_needed
@functools.wraps(ORIGINAL_PROBE)
async def probe(*args,**kwargs):
    start=CLOCK()
    try:return await ORIGINAL_PROBE(*args,**kwargs)
    finally:
        if CURRENT:CURRENT.ops['python_probe_checks']+=1;CURRENT.span('python_probe_elapsed_ms',ms(start))
replace_aliases(ORIGINAL_PROBE,probe)

ORIGINAL_COMPLETION=ctl._chat_completion_request
@functools.wraps(ORIGINAL_COMPLETION)
async def completion(*args,**kwargs):
    run=CURRENT;phase=kwargs.get('phase') or (kwargs.get('usage_context') or {}).get('phase') or 'unspecified'
    token=PHASE.set(phase);route_token=ROUTE.set((kwargs.get('usage_context') or {}).get('route'));start=CLOCK()
    try:return await ORIGINAL_COMPLETION(*args,**kwargs)
    finally:
        if run:run.span('llm_request_total_ms',ms(start))
        PHASE.reset(token);ROUTE.reset(route_token)
replace_aliases(ORIGINAL_COMPLETION,completion)
ORIGINAL_CLASSIFY=ctl.classify_task_complexity
@functools.wraps(ORIGINAL_CLASSIFY)
async def classify(*args,**kwargs):
    token=PHASE.set('classification');start=CLOCK()
    try:return await ORIGINAL_CLASSIFY(*args,**kwargs)
    finally:
        if CURRENT:CURRENT.span('classification_elapsed_ms',ms(start))
        PHASE.reset(token)
replace_aliases(ORIGINAL_CLASSIFY,classify)
for module,name,metric in ((answer_auditor,'audit_answer_payload','auditor_elapsed_ms'),(voice,'shorten_answer','voice_brief_elapsed_ms'),(voice,'synthesize','tts_elapsed_ms')):
    orig=getattr(module,name)
    def factory(orig,metric):
        @functools.wraps(orig)
        async def wrapped(*args,**kwargs):
            start=CLOCK()
            try:return await orig(*args,**kwargs)
            finally:
                if CURRENT:CURRENT.span(metric,ms(start))
        return wrapped
    replace_aliases(orig,factory(orig,metric))

ORIGINAL_CLIENT=httpx.AsyncClient
ORIGINAL_POST=ORIGINAL_CLIENT.post
async def measured_post(self,url,*args,**kwargs):
    start=CLOCK()
    try:return await ORIGINAL_POST(self,url,*args,**kwargs)
    finally:
        if CURRENT:CURRENT.span('llm_http_elapsed_ms' if str(url).endswith('/chat/completions') else 'tts_http_elapsed_ms',ms(start))
ORIGINAL_CLIENT.post=measured_post

def tool(name,args):return {'id':'fixture-call','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}
def answer(basis=None,long=False):
    grounded=bool(basis)
    text=('Синтетический подробный ответ. '*20 if long else 'Синтетический ответ.')
    return tool('answer_text',{'answer_type':'grounded_report' if grounded else 'pure_text','text':text,'basis':basis or [],
        'self_check':{'depends_on_current_external_state':grounded,'claims_completed_action':grounded,
        'has_sufficient_evidence':True,'missing_evidence_question':''}})

def response(message,finish='tool_calls'):return {'choices':[{'finish_reason':finish,'message':message}]}

def provider(request):
    run=CURRENT
    assert run is not None,'Provider request outside measurement'
    if request.url.host=='tts.api.cloud.yandex.net':return httpx.Response(200,content=b'OggS-synthetic-fixture')
    assert request.url.host=='fixture.invalid' and request.url.path=='/chat/completions','Live network forbidden'
    payload=json.loads(request.content);assert len(wire(payload))==len(request.content);phase=PHASE.get() or 'unspecified';run.phase_no[phase]+=1;index=run.phase_no[phase]
    category='classification' if phase=='classification' else 'auditor' if 'auditor' in phase else 'other' if ('voice' in phase or 'repair' in phase) else 'main'
    if run.enabled:
        account_start=CLOCK();groups,total=run.context_sizes(payload)
        run.calls.append({'model':payload['model'],'route':ROUTE.get() or ('classification' if phase=='classification' else 'unknown'),'phase':phase,'category':category,'message_count':len(payload.get('messages',[])),
            'schema_count':len(payload.get('tools') or []),'context':groups,'serialized_input_bytes':total,'serialized_input_chars':len(request.content.decode('utf-8')),
            'prompt_tokens':None,'completion_tokens':None,'cached_tokens':None,'provider_usage':'unknown',
            'ttft_ms':'not_measured','thinking':payload.get('thinking'),'reasoning_effort':payload.get('reasoning_effort')})
        run.span('payload_measurement_overhead_ms',ms(account_start))
    if phase=='classification':return httpx.Response(200,json=response({'content':'SIMPLE'},'stop'))
    if 'auditor' in phase:
        valid=not (run.config.get('error_mode')=='auditor_correction' and index==1)
        return httpx.Response(200,json=response({'content':json.dumps({'valid':valid,'reason':'fixture'})},'stop'))
    if phase=='voice_brief':return httpx.Response(200,json=response({'content':'Краткий синтетический ответ.'},'stop'))
    if phase.startswith('pipeline.plan'):
        steps=[{'title':f'Fixture step {i}','instruction':f'Execute fixture stage {i}','device_id':'pc1','success_criteria':'verified fixture outcome'} for i in range(1,4)]
        return httpx.Response(200,json=response({'content':json.dumps({'goal':'Fixture plan','steps':steps})},'stop'))
    if '.step_' in phase:
        if phase.endswith('.iteration.1'):call=tool('execute_cmd',{'command':'Write-Output fixture_stage'})
        else:call=answer(['step_1'])
    elif phase=='pipeline.final':
        obj=json.loads(payload['messages'][-1]['content']);journal=obj.get('current_run_journal') or []
        basis=[next(row['step_id'] for row in reversed(journal) if not row['tool_name'].startswith('answer.'))]
        call=answer(basis)
    else:
        run.main_no+=1
        if run.config.get('error_mode')=='repair' and 'repair' not in phase:
            return httpx.Response(200,json=response({'content':'Fixture raw content'},'stop'))
        if run.config.get('error_mode')=='correction' and run.main_no==1:
            return httpx.Response(200,json=response({'content':'Fixture raw content'},'stop'))
        if run.config.get('error_mode')=='tool_correction' and run.main_no==1:call=tool('execute_cmd',{})
        elif run.config.get('error_mode')=='tool_correction' and run.main_no==2:call=tool('execute_cmd',{'command':'New-Item -ItemType Directory Test; Write-Output corrected_fixture'})
        elif run.config.get('action') and run.main_no==1:call=tool('execute_cmd',{'command':'New-Item -ItemType Directory Test; Write-Output fixture'})
        elif run.config.get('error_mode')=='recovery' and run.main_no==2:call=tool('execute_cmd',{'command':'New-Item -ItemType Directory Test; Write-Output corrected_fixture'})
        else:
            basis=['step_2'] if run.config.get('error_mode') in ('recovery','tool_correction') else ['step_1'] if run.config.get('action') else []
            call=answer(basis,long=run.config.get('long_answer',False))
    return httpx.Response(200,json=response({'content':'','tool_calls':[call]}))

def fake_client(*args,**kwargs):
    kwargs['transport']=httpx.MockTransport(provider)
    return ORIGINAL_CLIENT(*args,**kwargs)
httpx.AsyncClient=fake_client
ctl.load_llm_config=lambda:dict(CFG)
os.environ['YANDEX_API_KEY']='fixture-only';os.environ['YANDEX_FOLDER_ID']='fixture-folder'

async def fake_agent(device,action,params,**kwargs):
    run=CURRENT;start=CLOCK();run.tool_calls+=1
    try:
        assert action in ('execute_cmd','device.refresh_state','device.prepare_runtime'), 'Unexpected fixture action'
        if run.config.get('error_mode')=='recovery' and run.tool_calls==1:
            return {'returncode':1,'stdout':'','stderr':'Fixture failure'}
        # Intentionally no OK marker: the original worker must choose its terminal answer.
        return {'returncode':0,'stdout':'FIXTURE_VERIFIED=1','stderr':''}
    finally:run.span('tool_execution_ms',ms(start))
runtime.send_command_to_agent=fake_agent
# Only account auth is mocked for automatic, local fixture PLAN approval/polling.
USER=None
task_routes.get_current_user=lambda request:USER

SCENARIOS=[('S1',{'request':'Привет'}),('S2',{'request':'Объясни, что такое рекурсия'}),
 ('S3',{'request':'Создай папку Test','action':True}),('S4',{'request':'Продолжи работу с предыдущим файлом','history':8,'artifact':True})]
SCENARIOS += [(f'S5_{n}',{'request':'Объясни, что такое рекурсия','history':n}) for n in (50,500,5000)]
SCENARIOS += [(f'S6_{n}',{'request':'Привет','devices':n}) for n in (1,5,20)]
SCENARIOS += [('S7',{'request':'Выполни многошаговую задачу','pipeline':True}),('S8_short',{'request':'Привет','voice':True}),
 ('S8_long',{'request':'Объясни, что такое рекурсия','voice':True,'long_answer':True}),
 ('S9_correction',{'request':'Привет','error_mode':'correction'}),('S9_repair',{'request':'Привет','error_mode':'repair'}),
 ('S9_recovery',{'request':'Создай папку Test','action':True,'error_mode':'recovery'}),
 ('S9_tool_correction',{'request':'Создай папку Test','action':True,'error_mode':'tool_correction'}),
 ('S9_auditor_correction',{'request':'Привет','error_mode':'auditor_correction'})]
SCENARIOS += [(f'S10_{n}x{size}',{'request':'Привет','facts':n,'fact_chars':size}) for n,size in ((100,100),(1000,100),(1000,2000))]

def seed(config):
    global USER
    tasks.clear();devices.clear();python_toolchain._RECEIPT_CACHE.clear()
    db.init_db();USER=db.create_user('baseline-fixture');chat=db.create_chat(USER['id'])
    for i in range(1,config.get('devices',1)+1):
        did=f'pc{i}';info={'device_id':did,'hostname':did,'os':'Windows','username':'Fixture','machine_guid':f'fixture-guid-{i}'}
        db.upsert_device_profile(did,USER['id'],info)
        devices[f'{USER["id"]}:{did}']={'user_id':USER['id'],'ws':object(),'info':info,'pending':{},'short_device_id':did}
    history=config.get('history',0)
    with db.get_db() as conn:
        for i in range(history):
            role='user' if i%2==0 else 'assistant';content=f'SYNTHETIC_HISTORY_{i} '+('Тестовый контекст. '*12)
            commands=None
            if config.get('artifact') and i==history-1:
                commands=json.dumps([{'action':'write_content','target_device_id':'pc1','result':{'path':r'C:\Users\Fixture\Desktop\fixture.txt','summary':'OK: file_written'}}])
            conn.execute('INSERT INTO messages(chat_id,role,content,commands,created_at) VALUES(?,?,?,?,?)',(chat['id'],role,content,commands,1000+i))
        for i in range(config.get('facts',0)):
            text=f'FIXTURE_FACT_{i} '+('x'*config.get('fact_chars',100))
            conn.execute('INSERT INTO user_memory(user_id,fact_text,category,created_at) VALUES(?,?,?,?)',(str(USER['id']),text,'general',f'{i:08d}'))
    return chat['id']

async def one(label,config,chat_id,rep,enabled=True):
    global CURRENT
    tasks.clear();python_toolchain._RECEIPT_CACHE.clear()
    # Clear runtime-only fields which would otherwise turn repeats into different fixtures.
    for dev in devices.values():dev.pop('activation_context_markers',None)
    run=Run(label,config,enabled);CURRENT=run;tid=f'baseline-{label}-{rep}'
    start=CLOCK();run.stage='accepted'
    db.add_message(chat_id,'user',config['request'])
    key=f'{USER["id"]}:pc1'
    task={'task_id':tid,'user_id':USER['id'],'chat_id':chat_id,'message':config['request'],'device_ids':['pc1'],
        'status':'running','created_at':time.time(),'modes':{'pipeline':bool(config.get('pipeline'))},'results':{},'commands':[]}
    tasks[tid]=task;run.stage='runtime'
    execution=asyncio.create_task(runtime.run_nl_task(tid,USER['id'],config['request'],[key],chat_id))
    while not execution.done():
        if task.get('plan_review'):
            await task_routes.api_review_plan(tid,task_routes.PlanReviewBody(revision=task['plan_review']['revision'],action='approve'),None)
        await asyncio.sleep(0)
    await execution
    elapsed=ms(start);run.spans['request_elapsed_ms'].append(elapsed)
    run.outcome=task.get('status')
    assert task.get('status') in ('done','completed_with_recovery'), (label,task.get('status'),task.get('task_receipt'))
    assert run.main_no or any('worker' in phase for phase in run.phase_no), 'Runtime did not reach worker'
    assert any(row.get('tool_name')=='answer.text' for row in task.get('commands',[])), 'No validated terminal fixture answer'
    # Actual existing polling endpoint; no added network request.
    run.stage='poll';poll_start=CLOCK()
    for _ in range(5):
        polled=await task_routes.api_get_task(tid,None)
        if enabled:run.poll_bytes.append(len(wire(polled)))
    run.span('polling_total_ms',ms(poll_start));run.stage='post_result'
    if config.get('voice'):
        parts=await voice.spoken_parts(task)
        brief_count=sum(call['phase']=='voice_brief' for call in run.calls)
        await voice.spoken_parts(task)
        assert sum(call['phase']=='voice_brief' for call in run.calls)==brief_count
        for part in parts:await voice.synthesize(part)
    CURRENT=None
    return run


def summarize(label,runs,config):
    calls=[call for run in runs for call in run.calls]
    call_kinds=Counter(call['category'] for call in calls)
    first=[run.calls for run in runs][0]
    spans=defaultdict(list)
    for run in runs:
        for key,values in run.spans.items():spans[key].append(sum(values))
    latency={}
    for key,values in spans.items():
        ordered=sorted(values)
        latency[key]={'p50_ms':statistics.median(values),'p95_ms':ordered[math.ceil(.95*len(ordered))-1] if len(ordered)>=20 else None,'n':len(values),'boundary':'mock/local; per-run aggregate'}
    keys=set().union(*(run.ops for run in runs))
    rows=set().union(*(run.rows for run in runs))
    sizes=Counter()
    for call in first:sizes.update(call['context'])
    return {'scenario':label,'samples':len(runs),'fixture':{k:v for k,v in config.items() if k!='request'},
      'calls_per_request':{kind:call_kinds[kind]/len(runs) for kind in ('classification','main','auditor','other')},
      'llm_call_count_per_request':len(calls)/len(runs),'tool_call_count_per_request':statistics.mean(r.tool_calls for r in runs),
      'calls_first_sample':first,'cumulative_input_bytes_first_sample':sum(c['serialized_input_bytes'] for c in first),
      'peak_call_input_bytes':max((c['serialized_input_bytes'] for c in calls),default=0),'components_first_sample_sum_bytes':dict(sizes),
      'provider_input_tokens':None,'provider_completion_tokens':None,'cached_tokens':None,'estimated_cost_usd':None,
      'latency':latency,'operations_per_request':{key:statistics.mean(r.ops[key] for r in runs) for key in sorted(keys)},
      'rows_per_request':{key:statistics.mean(r.rows[key] for r in runs) for key in sorted(rows)},
      'fact_rows_read_per_request':statistics.mean(r.facts_rows for r in runs),
      'operations_by_stage_first_sample':{stage:dict(counts) for stage,counts in runs[0].stages.items()},
      'builder_output_bytes_first_sample':dict(runs[0].builder_bytes),
      'builder_peak_output_chars_first_sample':dict(runs[0].builder_peak_chars),'poll_response_bytes_first_sample':runs[0].poll_bytes,
      'fact_rows_by_stage_first_sample':dict(runs[0].facts_by_stage),'fact_text_bytes_by_stage_first_sample':dict(runs[0].fact_bytes_by_stage),
      'fact_text_bytes_read_per_request':statistics.mean(r.facts_bytes for r in runs),
      'ttft_ms':'not_measured','submit_to_ui_result_ms':'not_measured','submit_to_first_audio_ms':'not_measured',
      'outcomes':dict(Counter(r.outcome for r in runs))}

async def main(args):
    global CURRENT
    results=[];calibration=[]
    scenarios=SCENARIOS if not args.scenario else [row for row in SCENARIOS if row[0] in args.scenario]
    for label,config in scenarios:
        with tempfile.TemporaryDirectory(prefix='iru-p0-03-db-') as temp:
            db.DB_PATH=Path(temp)/'fixture.sqlite3';CURRENT=None
            with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):chat_id=seed(config)
            with db.get_db() as conn:baseline_max=conn.execute('SELECT COALESCE(MAX(id),0) FROM messages').fetchone()[0]
            runs=[]
            for rep in range(args.samples+1):
                with db.get_db() as conn:
                    conn.execute('DELETE FROM messages WHERE id>?',(baseline_max,))
                    conn.execute('DELETE FROM device_memory')
                    conn.execute('DELETE FROM tasks')
                    conn.execute('DELETE FROM llm_usage_events')
                with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):run=await one(label,config,chat_id,rep)
                if rep:runs.append(run)
            results.append(summarize(label,runs,config))
            if args.calibrate and label in ('S1','S3','S7','S10_1000x2000'):
                measured=[];unrecorded=[]
                for rep in range(30):
                    for enabled in ((True,False) if rep%2==0 else (False,True)):
                        with db.get_db() as conn:
                            conn.execute('DELETE FROM messages WHERE id>?',(baseline_max,));conn.execute('DELETE FROM device_memory');conn.execute('DELETE FROM tasks');conn.execute('DELETE FROM llm_usage_events')
                        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):cal=await one(label,config,chat_id,900+rep,enabled)
                        (measured if enabled else unrecorded).append(sum(cal.spans['request_elapsed_ms']))
                calibration.append({'scenario':label,'n_pairs':30,'recorded_p50_ms':statistics.median(measured),'unrecorded_p50_ms':statistics.median(unrecorded),
                    'boundary':'same wrappers and MockTransport; numeric recording on/off, not pristine production'})
        print(f'{label}: measured {args.samples} mock/local runs',flush=True)
    output={'head':subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
            'environment':{'python':sys.version.split()[0],'platform':sys.platform,'auditor':'enabled','network':'httpx.MockTransport only','devices':'fake',
              'history_limit':50,'mock_added_latency_ms':0,'timings':'instrumented mock/local, not production','warmup_runs_per_fixture':1},
            'scenarios':results,'measurement_calibration':calibration,'provider_usage':'unknown; mock responses intentionally omit usage','fixed_context_method':'exact source instruction literals; unmatched system residual reported separately',
            'source_changed':bool(subprocess.check_output(['git','-C',str(REPO),'status','--porcelain','--untracked-files=no'],text=True).strip())}
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Numeric baseline saved',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--samples',type=int,default=30);parser.add_argument('--output',required=True);parser.add_argument('--scenario',action='append');parser.add_argument('--calibrate',action='store_true')
    asyncio.run(main(parser.parse_args()))
