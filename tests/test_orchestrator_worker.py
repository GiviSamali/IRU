import asyncio
import json
import time
import uuid
from types import SimpleNamespace

import pytest
from server import database as db, orchestrator as orch
from server.runtime_state import tasks, devices
from server.worker_scheduler import WorkerScheduler, list_jobs, owned_job, init_worker_storage
from server.worker_reports import build_worker_report
from server.routers import tasks as routes


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/'ow.sqlite')
    db.init_db();init_worker_storage()
    a=db.create_user('A');b=db.create_user('B')
    a['chat_id']=db.create_chat(a['id'],'A')['id'];b['chat_id']=db.create_chat(b['id'],'B')['id']
    for owner in (a,b):devices[f"{owner['id']}:pc"]={'user_id':owner['id'],'info':{'hostname':'pc','os':'Windows'},'ws':object()}
    return a,b


def task(owner, goal='goal'):
    return {'task_id':str(uuid.uuid4()),'user_id':owner['id'],'chat_id':owner['chat_id'],'message':goal,
        'device_ids':[f"{owner['id']}:pc"],'status':'running','created_at':time.time(),'modes':{},'kind':'worker'}


def success(t):
    t.update(status='done',answer='verified',commands=[{'tool_name':'write_content','step_id':'step_1','status':'success',
        'device_id':'pc','result':{'path':r'C:\Users\Demo\Desktop\report.txt','bytes_written':2}}],
        tasks=[],task_receipt={'task_status':'completed','final_verification_status':'verified'})


async def wait_until(predicate):
    for _ in range(200):
        if predicate():return
        await asyncio.sleep(.01)
    raise AssertionError('bounded wait exceeded')


def test_atomic_single_slot_fifo_queue_limits_cancel_and_idempotency(owners):
    a,b=owners
    async def scenario():
        gate=asyncio.Event();started=[];active=0;maximum=0
        async def execute(t):
            nonlocal active,maximum
            active+=1;maximum=max(maximum,active);started.append(t['message'])
            await gate.wait();success(t);active-=1
        scheduler=WorkerScheduler(execute)
        first=await scheduler.submit(task(a,'A'),request_key='same')
        again=await scheduler.submit(task(a,'A'),request_key='same')
        assert again['task_id']==first['task_id']
        with pytest.raises(ValueError):await scheduler.submit(task(a,'changed'),request_key='same')
        remaining=await asyncio.gather(*(scheduler.submit(task(a,str(i))) for i in range(4)))
        await asyncio.sleep(0);assert started==['A'] and maximum==1
        assert all(t['status']=='queued' for t in remaining)
        with pytest.raises(ValueError,match='queue_full'):await scheduler.submit(task(a,'overflow'))
        await scheduler.cancel(remaining[1]['task_id'],a['id'])
        gate.set();await scheduler.runners[a['id']]
        assert started==['A','0','2','3'] and maximum==1
        assert owned_job(remaining[1]['task_id'],a['id'])['state']=='cancelled'
        assert owned_job(first['task_id'],b['id']) is None
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_users_do_not_share_slot_and_foreign_chat_is_rejected(owners):
    a,b=owners
    async def scenario():
        gate=asyncio.Event();started=[]
        async def execute(t):started.append(t['user_id']);await gate.wait();success(t)
        scheduler=WorkerScheduler(execute)
        await scheduler.submit(task(a));await scheduler.submit(task(b));await asyncio.sleep(0)
        assert sorted(started)==sorted([a['id'],b['id']])
        wrong=task(a);wrong['chat_id']=b['chat_id']
        with pytest.raises(ValueError,match='chat_not_owned'):await scheduler.submit(wrong)
        gate.set();await asyncio.gather(*scheduler.runners.values());await scheduler.shutdown()
    asyncio.run(scenario())


def test_confirmation_retains_slot_then_failure_releases_next(owners):
    a,b=owners
    async def scenario():
        started=[]
        async def execute(t):
            started.append(t['message'])
            if t['message']=='A':t['status']='confirm'
            else:raise RuntimeError('fake failure')
        scheduler=WorkerScheduler(execute)
        first=await scheduler.submit(task(a,'A'));second=await scheduler.submit(task(a,'B'))
        await wait_until(lambda:first['status']=='confirm')
        await asyncio.sleep(.15);assert started==['A']
        first['status']='cancelled';await scheduler.runners[a['id']]
        assert started==['A','B'] and owned_job(second['task_id'],a['id'])['state']=='failed'
        await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('status',['partial','blocked','failed','cancelled','unknown'])
def test_report_negative_states_and_unverified_outcome(owners,status):
    a,b=owners;t=task(a);success(t);t['status']=status
    report=build_worker_report(t)
    assert report['status']==status and report['goal_completed'] is False
    assert report['schema_version']==1 and report['worker_id']=='worker-1'
    t.update(status='done',task_receipt={'task_status':'completed','goal_completed':True},commands=[{'tool_name':'execute_cmd','status':'success','step_id':'step_1','result':{'returncode':0}}])
    assert build_worker_report(t)['status']=='unknown'
    t['commands'][0]['result']['status']='unknown'
    assert not build_worker_report(t)['goal_completed']


def test_restart_explicitly_terminates_unstarted_and_uncertain_jobs_without_replay(owners):
    a,b=owners
    async def scenario():
        gate=asyncio.Event();calls=[]
        async def execute(t):calls.append(t['task_id']);await gate.wait()
        scheduler=WorkerScheduler(execute)
        first=await scheduler.submit(task(a));second=await scheduler.submit(task(a))
        await asyncio.sleep(0);await scheduler.shutdown()
        restarted=WorkerScheduler(execute)
        await restarted.recover()
        assert owned_job(first['task_id'],a['id'])['state']=='unknown'
        assert owned_job(second['task_id'],a['id'])['state']=='cancelled'
        assert calls==[first['task_id']] and not restarted.runners
        assert db.get_messages(a['chat_id'])[-1]['taskStatus']=='cancelled'
    asyncio.run(scenario())


def test_orchestrator_malformed_decision_does_not_delegate_and_turn_is_idempotent(owners,monkeypatch):
    a,b=owners;delegated=[]
    async def bad(*args,**kwargs):raise ValueError('malformed')
    monkeypatch.setattr(orch,'decide',bad)
    async def delegate(*args,**kwargs):delegated.append(1)
    cmd=SimpleNamespace(request_id='one',message='hello',device_id='pc',modes={},broadcast=False)
    async def scenario():
        first=await orch.run_turn(cmd,a,a['chat_id'],delegate)
        second=await orch.run_turn(cmd,a,a['chat_id'],delegate)
        assert first==second and not delegated and len(db.get_messages(a['chat_id']))==2
    asyncio.run(scenario())


def test_orchestrator_context_is_bounded_owner_scoped_and_data_only(owners):
    a,b=owners
    db.add_user_fact(str(a['id']),'Руслан любит компактные ответы','preference')
    db.add_user_fact(str(b['id']),'PRIVATE_OTHER_OWNER','preference')
    db.add_message(a['chat_id'],'user','x'*50000)
    context=orch.context_for(a['id'],a['chat_id'],'Руслан','pc')
    text=json.dumps(context,ensure_ascii=False)
    assert len(text)<=orch.MAX_CONTEXT_CHARS and 'PRIVATE_OTHER_OWNER' not in text
    assert len(context['facts'])==1 and all(d['device_id']=='pc' for d in context['devices'])
    assert 'execute_cmd' not in json.dumps(orch.TOOL) and orch.TOOL['function']['name']=='orchestrator_decision'
    with pytest.raises(Exception):orch.Decision.model_validate({'intent':'delegate','user_id':b['id'],'verified':True})


def test_30_second_worker_allows_three_independent_orchestrator_answers(owners,monkeypatch):
    a,b=owners;metrics=[]
    async def decide(message,context,**kwargs):
        await asyncio.sleep(.01)
        return orch.Decision(intent='conversation',answer='Answer '+message),{'entity':'orchestrator','llm_calls':1,'snapshot_calls':0,'usage':{'prompt_tokens':100,'completion_tokens':10}}
    monkeypatch.setattr(orch,'decide',decide)
    async def scenario():
        calls=[];started=asyncio.Event()
        async def execute(t):calls.append(t['task_id']);started.set();await asyncio.sleep(30);success(t)
        scheduler=WorkerScheduler(execute);worker=await scheduler.submit(task(a));await started.wait()
        for index in range(3):
            cmd=SimpleNamespace(request_id='q'+str(index),message='question '+str(index),device_id='pc',modes={},broadcast=False)
            began=time.monotonic()
            response=await orch.run_turn(cmd,a,a['chat_id'],lambda *args:None)
            metrics.append(time.monotonic()-began)
            assert response['answer'].startswith('Answer') and worker['status']=='running'
        assert max(metrics)<2 and calls==[worker['task_id']]
        await scheduler.runners[a['id']]
        assert owned_job(worker['task_id'],a['id'])['state']=='success'
        assert len(db.get_messages(a['chat_id']))==7 # one Worker row plus three dialogue pairs
        print('OW30 measured dialogue seconds:',[round(v,4) for v in metrics])
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_real_intake_delegation_conversation_status_and_ownership(owners,monkeypatch):
    a,b=owners
    async def scenario():
        gate=asyncio.Event();executed=[]
        async def execute(t):executed.append(t);await gate.wait();success(t)
        scheduler=WorkerScheduler(execute)
        monkeypatch.setattr(routes,'scheduler',scheduler);monkeypatch.setattr(orch,'scheduler',scheduler)
        monkeypatch.setattr(routes,'get_current_user',lambda request:a)
        request=SimpleNamespace(client=SimpleNamespace(host='ow-test'))
        choice=orch.Decision(intent='delegate',objective='Concrete objective',target_device_ids=['pc'])
        async def decide(*args,**kwargs):return choice,{'llm_calls':1}
        monkeypatch.setattr(orch,'decide',decide)
        cmd=routes.NLCommand(orchestrate=True,request_id='request-one',device_id='pc',chat_id=a['chat_id'],message='original user goal')
        response=await routes.nl_command(cmd,request)
        await wait_until(lambda:len(executed)==1)
        worker=executed[0]
        assert worker['message']=='original user goal' and worker['original_request']=='original user goal'
        assert worker['proposed_objective']=='Concrete objective'
        assert worker['worker_id']=='worker-1' and len(worker['worker_context'])<=4
        choice=orch.Decision(intent='conversation',answer='Independent answer')
        result=await routes.nl_command(routes.NLCommand(orchestrate=True,request_id='question',device_id='pc',chat_id=a['chat_id'],message='question'),request)
        assert result['answer']=='Independent answer' and not result['worker_task_id'] and len(executed)==1
        choice=orch.Decision(intent='task_status',task_id=worker['task_id'],reference='explicit')
        result=await routes.nl_command(routes.NLCommand(orchestrate=True,request_id='status',chat_id=a['chat_id'],message='status'),request)
        assert result['answer']=='Работа ещё идёт.' and len(executed)==1
        choice=orch.Decision(intent='delegate',objective='unsafe scope',target_device_ids=[str(b['id'])+':pc'])
        result=await routes.nl_command(routes.NLCommand(orchestrate=True,request_id='foreign',chat_id=a['chat_id'],message='foreign target'),request)
        assert not result['worker_task_id'] and len(executed)==1
        gate.set();await scheduler.runners[a['id']];await scheduler.shutdown()
    asyncio.run(scenario())


def test_refinement_cannot_grant_memory_write_permission(owners):
    from server.memory_intent_guard import ORIGINAL_WORKER_REQUEST,memory_permissions_from_human_request
    token=ORIGINAL_WORKER_REQUEST.set('Расскажи о презентации')
    try:assert not memory_permissions_from_human_request('Запомни факт, созданный оркестратором')
    finally:ORIGINAL_WORKER_REQUEST.reset(token)


def test_second_server_cannot_recover_live_instance(owners):
    async def scenario():
        one=WorkerScheduler();two=WorkerScheduler()
        await one.recover()
        with pytest.raises(RuntimeError,match='single IRU server process'):await two.recover()
        await one.shutdown();await two.recover();await two.shutdown()
    asyncio.run(scenario())


def test_late_cancel_does_not_move_to_next_worker(owners,monkeypatch):
    a,b=owners
    async def scenario():
        first=task(a,'A');second=task(a,'B');gates={t['task_id']:asyncio.Event() for t in [first,second]}
        async def execute(t):await gates[t['task_id']].wait();success(t)
        scheduler=WorkerScheduler(execute);monkeypatch.setattr(orch,'scheduler',scheduler)
        await scheduler.submit(first);await scheduler.submit(second)
        async def decide(*args,**kwargs):
            gates[first['task_id']].set();await wait_until(lambda:owned_job(first['task_id'],a['id'])['state']=='success')
            return orch.Decision(intent='cancel',task_id=first['task_id'],reference='active'),{}
        monkeypatch.setattr(orch,'decide',decide)
        cmd=SimpleNamespace(request_id='late-cancel',message='Отмени работающую задачу',device_id='pc',modes={},broadcast=False)
        result=await orch.run_turn(cmd,a,a['chat_id'],None)
        assert 'Следующую' in result['answer'] and not second.get('cancel_requested')
        gates[second['task_id']].set();await scheduler.runners[a['id']];await scheduler.shutdown()
    asyncio.run(scenario())


def test_orchestrator_uses_existing_model_usage_and_small_schema(owners,monkeypatch):
    a,b=owners
    from server import controller
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'choices':[{'finish_reason':'stop','message':{'tool_calls':[{'function':{'name':'orchestrator_decision','arguments':json.dumps({'intent':'conversation','answer':'hello'})}}]}}],
            'usage':{'prompt_tokens':120,'completion_tokens':15,'total_tokens':135}}
    sent=[]
    class Client:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):sent.append(kwargs['json']);return Response()
    monkeypatch.setattr(orch.httpx,'AsyncClient',Client)
    monkeypatch.setattr(orch,'load_llm_config',lambda:{'model':'mock-model','base_url':'https://example.invalid','api_key':'fake-test-key'})
    async def scenario():
        decision,metrics=await orch.decide('hello',orch.context_for(a['id'],a['chat_id'],'hello','pc'),user_id=a['id'],chat_id=a['chat_id'],task_id='usage-orch')
        assert decision.intent=='conversation' and len(sent)==1 and metrics['snapshot_calls']==0
        assert sent[0]['model']=='mock-model' and sent[0]['max_tokens']==1200
        assert [tool['function']['name'] for tool in sent[0]['tools']]==['orchestrator_decision']
        with db.get_db() as c:row=c.execute("SELECT phase,prompt_tokens,completion_tokens,metadata FROM llm_usage_events WHERE poll_task_id='usage-orch'").fetchone()
        assert row['phase']=='orchestrator' and row['prompt_tokens']==120 and row['completion_tokens']==15
        assert json.loads(row['metadata'])['entity']=='orchestrator' and 'latency_ms' in json.loads(row['metadata'])
    asyncio.run(scenario())


def test_server_worker_search_has_current_journal_and_validated_terminal(owners,monkeypatch):
    from server import controller_onboarding as onboarding
    from server.controller_trust import has_grounded_terminal_answer
    a,b=owners
    answer={'answer_type':'grounded_report','text':'Forecast based on tool data','basis':['step_1'],
        'self_check':{'depends_on_current_external_state':True,'claims_completed_action':False,'has_sufficient_evidence':True,'missing_evidence_question':''}}
    messages=[{'tool_calls':[{'id':'search','function':{'name':'web_search','arguments':json.dumps({'query':'weather'})}}]},
        {'tool_calls':[{'id':'answer','function':{'name':'answer_text','arguments':json.dumps(answer)}}]}]
    requested=[]
    class Response:
        def __init__(self,data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    class Client:
        def __init__(self,*a,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,url,**kwargs):
            requested.append(kwargs['json'])
            return Response({'choices':[{'message':messages.pop(0)}],'usage':{'prompt_tokens':40,'completion_tokens':20}})
    async def search(*a,**kw):return {'status':'success','results':[{'title':'Forecast','url':'https://example.invalid','snippet':'tool evidence'}]}
    monkeypatch.setattr(onboarding.httpx,'AsyncClient',Client);monkeypatch.setattr(onboarding,'run_web_search',search)
    result=asyncio.run(onboarding.process_onboarding_message('weather',usage_context={'user_id':a['id'],'worker_execution':True},
        load_llm_config_fn=lambda:{'model':'mock-model','api_key':'fake-test','base_url':'https://example.invalid'},current_datetime_msk_fn=lambda:'2026-10-09'))
    assert has_grounded_terminal_answer(result['answer'],result['commands'])
    assert len(requested)==2 and len(requested[0]['messages'][0]['content'])<10000
    assert set(t['function']['name'] for t in requested[0]['tools']) <= {'web_search','remember_fact','forget_fact','memory_list_facts','memory_get_stats','answer_text'}
    t=task(a);t.update(status='done',answer=result['answer'],commands=result['commands'],device_ids=[])
    assert build_worker_report(t)['status']=='success'


def test_free_busy_delegation_latency_and_worker_role_usage(owners, monkeypatch):
    from server.llm_usage import record_llm_usage_event
    a,b=owners
    async def scenario():
        gate=asyncio.Event();executed=[];metrics={};llm_calls=[]
        async def execute(t):
            executed.append(t['task_id'])
            record_llm_usage_event(usage_context={'user_id':a['id'],'poll_task_id':t['task_id'],'phase':'fake_worker'},
                model='mock-model',usage={'prompt_tokens':40,'completion_tokens':20},cfg={})
            await gate.wait();success(t)
        worker_scheduler=WorkerScheduler(execute);monkeypatch.setattr(orch,'scheduler',worker_scheduler)
        async def decide(message,*args,**kwargs):
            await asyncio.sleep(.01);llm_calls.append(message)
            return (orch.Decision(intent='delegate',objective='one task',target_device_ids=['pc']) if message=='delegate'
                else orch.Decision(intent='conversation',answer='answer')),{'llm_calls':1,'snapshot_calls':0}
        monkeypatch.setattr(orch,'decide',decide)
        async def delegate(choice,request_key):return await worker_scheduler.submit(task(a,choice.objective),request_key=request_key)
        for name in ('free','delegate','busy'):
            cmd=SimpleNamespace(request_id=name,message=name,device_id='pc',modes={},broadcast=False)
            began=time.monotonic();response=await orch.run_turn(cmd,a,a['chat_id'],delegate)
            metrics[name]=round((time.monotonic()-began)*1000,2)
            if name=='free':assert not executed and response['worker_task_id'] is None
            if name=='delegate':await wait_until(lambda:len(executed)==1)
            if name=='busy':assert response['worker_task_id'] is None and len(executed)==1
        assert len(llm_calls)==3 and max(metrics.values())<2000
        with db.get_db() as c:row=c.execute("SELECT prompt_tokens,completion_tokens,metadata FROM llm_usage_events WHERE poll_task_id=?",(executed[0],)).fetchone()
        assert row['prompt_tokens']==40 and row['completion_tokens']==20 and json.loads(row['metadata'])['entity']=='worker'
        gate.set();await worker_scheduler.runners[a['id']];await worker_scheduler.shutdown()
        print('OW mock latency milliseconds:',json.dumps(metrics))
    asyncio.run(scenario())


def test_restored_success_is_terminal_and_runtime_ttl_keeps_only_active_worker(owners):
    from server.runtime_state import cleanup_old_tasks, TASK_TTL, TERMINAL_TASK_STATUSES
    a,b=owners
    assert 'success' in TERMINAL_TASK_STATUSES
    finished=task(a);waiting=task(a)
    for t in (finished,waiting):t.update(worker_id='worker-1',created_at=time.time()-TASK_TTL-1);tasks[t['task_id']]=t
    finished['status']='success';waiting['status']='queued'
    cleanup_old_tasks()
    assert finished['task_id'] not in tasks and waiting['task_id'] in tasks


def set_used(owner, used, date=None):
    import datetime
    with db.get_db() as c:
        c.execute('UPDATE users SET daily_commands_count=?,daily_commands_date=? WHERE id=?',
            (used,date or datetime.date.today().isoformat(),owner['id']))


def used_commands(owner):
    return db.check_daily_command_limit(owner['id'])['used']


def test_conversation_status_and_retry_do_not_consume_exhausted_command_quota(owners,monkeypatch):
    a,b=owners
    async def scenario():
        async def execute(t):success(t)
        local=WorkerScheduler(execute);monkeypatch.setattr(routes,'scheduler',local);monkeypatch.setattr(orch,'scheduler',local)
        job=await local.submit(task(a));await local.runners[a['id']];set_used(a,30)
        monkeypatch.setattr(routes,'get_current_user',lambda request:a)
        monkeypatch.setattr(routes,'check_rate_limit',lambda owner:True)
        monkeypatch.setattr(routes,'check_ip_rate_limit',lambda ip:True)
        selected=orch.Decision(intent='conversation',answer='Как дела? Хорошо.')
        decisions=[]
        async def decide(*args,**kwargs):decisions.append(1);return selected,{}
        monkeypatch.setattr(orch,'decide',decide)
        request=SimpleNamespace(client=SimpleNamespace(host='quota-test'))
        cmd=routes.NLCommand(orchestrate=True,request_id='talk',message='Как дела?',chat_id=a['chat_id'])
        one=await routes.nl_command(cmd,request);two=await routes.nl_command(cmd,request)
        assert one==two and one['answer']=='Как дела? Хорошо.' and len(decisions)==1 and used_commands(a)==30
        selected=orch.Decision(intent='task_status',task_id=job['task_id'],reference='explicit')
        answer=await routes.nl_command(routes.NLCommand(orchestrate=True,request_id='status-free',message='Что мы делали?',chat_id=a['chat_id']),request)
        assert answer['answer']=='Файл готов.' and used_commands(a)==30
        assert owned_job(job['task_id'],a['id'])['state']=='success'
        selected=orch.Decision(intent='delegate',objective='Посмотри файлы',target_device_ids=['pc'])
        rejected=await routes.nl_command(routes.NLCommand(orchestrate=True,request_id='over-quota',message='Посмотри файлы',chat_id=a['chat_id']),request)
        assert rejected['worker_task_id'] is None and 'Дневной лимит' in rejected['answer'] and used_commands(a)==30
        assert len(list_jobs(a['id']))==1
        # Independent spam guard still runs before even a cached response.
        monkeypatch.setattr(routes,'check_rate_limit',lambda owner:False)
        assert (await routes.nl_command(cmd,request))['status']=='error'
        await local.shutdown()
    asyncio.run(scenario())


def test_admission_charges_once_rejects_without_charge_and_resets_day(owners,monkeypatch):
    a,b=owners
    async def scenario():
        gate=asyncio.Event()
        async def execute(t):await gate.wait();success(t)
        local=WorkerScheduler(execute);monkeypatch.setattr(routes,'scheduler',local)
        set_used(a,29)
        first=await routes.submit_worker(a,a['chat_id'],'Посмотри файлы',[f"{a['id']}:pc"],{},request_key='once',objective='Read')
        repeated=await routes.submit_worker(a,a['chat_id'],'Посмотри файлы',[f"{a['id']}:pc"],{},request_key='once',objective='Read')
        assert first['task_id']==repeated['task_id'] and used_commands(a)==30
        with pytest.raises(ValueError,match='daily_command_limit_exceeded'):
            await routes.submit_worker(a,a['chat_id'],'new',[f"{a['id']}:pc"],{},request_key='new')
        with pytest.raises(ValueError,match='device_not_owned'):
            await routes.submit_worker(a,a['chat_id'],'bad',[f"{b['id']}:pc"],{},request_key='bad')
        assert used_commands(a)==30 and len(list_jobs(a['id']))==1
        # A previous day is reset atomically by admission, not by a chat turn.
        set_used(a,30,'2000-01-01')
        await local.submit(task(a,'next day'));assert used_commands(a)==1
        for i in range(3):await local.submit(task(a,str(i)))
        before=used_commands(a)
        with pytest.raises(ValueError,match='worker_queue_full'):await local.submit(task(a,'overflow'))
        assert used_commands(a)==before
        gate.set();await local.runners[a['id']];await local.shutdown()
    asyncio.run(scenario())


def test_quota_last_slot_is_atomic_and_admission_rollback_does_not_charge(owners):
    a,b=owners
    async def scenario():
        gate=asyncio.Event()
        async def execute(t):await gate.wait();success(t)
        local=WorkerScheduler(execute);set_used(a,29)
        results=await asyncio.gather(local.submit(task(a,'one')),local.submit(task(a,'two')),return_exceptions=True)
        assert sum(isinstance(result,dict) for result in results)==1 and used_commands(a)==30
        set_used(b,0)
        with db.get_db() as c:
            c.execute("CREATE TRIGGER reject_worker BEFORE INSERT ON worker_jobs WHEN NEW.owner_user_id="+str(b['id'])+" BEGIN SELECT RAISE(ABORT,'test admission rollback'); END")
        with pytest.raises(Exception,match='test admission rollback'):await local.submit(task(b))
        assert used_commands(b)==0 and not list_jobs(b['id'])
        gate.set();await local.runners[a['id']];await local.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('modes',[{}, {'pipeline':True}])
def test_generated_scope_never_becomes_worker_human_request_or_instructions(owners,monkeypatch,modes):
    from server import task_runtime as runtime
    a,b=owners
    original='Посмотри содержимое папки'
    expansion='Приведи папку в порядок, удалив ненужные файлы'
    observed=[];actions=[]
    async def controller(**kwargs):
        observed.append(kwargs)
        action='delete_file' if expansion in kwargs['user_message'] else 'list_dir'
        await kwargs['send_command_fn']('pc',action,{'path':'<desktop>'})
        return {'answer':'folder inspected','commands':[],'tasks':[]}
    async def transport(device,action,params,**kwargs):actions.append(action);return {'entries':[]}
    async def probe(**kwargs):pass
    async def classify(*args,**kwargs):return 'SIMPLE',''
    monkeypatch.setattr(runtime,'process_nl_command',controller);monkeypatch.setattr(runtime,'send_command_to_agent',transport)
    monkeypatch.setattr(runtime,'_probe_python_toolchain_if_needed',probe);monkeypatch.setattr(runtime,'classify_task_complexity',classify)
    monkeypatch.setattr(runtime,'get_device_profile',lambda *args,**kwargs:None)
    monkeypatch.setattr(runtime,'add_training_record',lambda *args,**kwargs:None)
    async def scenario():
        async def execute(t):
            # Even an obsolete/corrupt caller passing objective hits the runtime original boundary.
            await runtime.run_nl_task(t['task_id'],a['id'],expansion,t['device_ids'],a['chat_id'])
        local=WorkerScheduler(execute);monkeypatch.setattr(routes,'scheduler',local)
        t=await routes.submit_worker(a,a['chat_id'],original,[f"{a['id']}:pc"],modes,objective=expansion,context_summary=expansion)
        await local.runners[a['id']]
        assert t['message']==original and t['proposed_objective']==expansion
        assert len(observed)==1 and observed[0]['user_message']==original
        assert expansion not in json.dumps(observed[0]['chat_history'],ensure_ascii=False)
        assert actions==['list_dir']
        await local.shutdown()
    asyncio.run(scenario())


def test_plan_proposal_keeps_original_human_goal_not_generated_scope(owners,monkeypatch):
    a,b=owners
    async def decide(*args,**kwargs):
        return orch.Decision(intent='delegate',objective='Удалить ненужные файлы',target_device_ids=['pc']),{}
    monkeypatch.setattr(orch,'decide',decide)
    async def forbidden(*args,**kwargs):raise AssertionError('PLAN proposal cannot admit execution')
    cmd=SimpleNamespace(request_id='plan-original',message='Посмотри содержимое папки',device_id='pc',modes={'pipeline':True},broadcast=False)
    result=asyncio.run(orch.run_turn(cmd,a,a['chat_id'],forbidden))
    offer=tasks[result['task_id']]
    assert offer['plan_original_request']==cmd.message and offer['proposed_objective']=='Удалить ненужные файлы'
    assert not result['worker_task_id'] and used_commands(a)==0


@pytest.mark.parametrize('with_jobs',[False,True])
def test_context_hard_cap_with_oversized_devices_and_no_tasks(owners,monkeypatch,with_jobs):
    a,b=owners
    for i in range(16):
        devices[f"{a['id']}:pc{i}"]={'user_id':a['id'],'ws':object(),'info':{'hostname':'X'*20000,'os':'W'*20000},
            'activation_summary':{'capabilities_summary':['C'*20000]*8},'last_seen':'T'*20000}
        db.upsert_device_profile(f'pc{i}',a['id'],{'hostname':'R'*20000,'os':'S'*20000})
    for i in range(12):db.add_message(a['chat_id'],'user','H'*2000)
    if with_jobs:
        init_worker_storage()
        t=task(a);t['message']='goal'
        with db.get_db() as c:
            c.execute('INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,report,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                (t['task_id'],a['id'],a['chat_id'],'unknown',json.dumps(t),json.dumps({'summary':'R'*50000}),time.time(),time.time()))
    monkeypatch.setattr(orch,'MAX_CONTEXT_CHARS',700)
    result=orch.context_for(a['id'],a['chat_id'],'question','pc')
    assert len(json.dumps(result,ensure_ascii=False))<=700 and result['context_truncated'] is True
    assert all(len(d.get('name',''))<=80 for d in result['devices'])
