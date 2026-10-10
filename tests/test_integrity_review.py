"""Independent review regressions; emulated execution, real SQLite and scheduler."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest
from server import database as db, task_runtime as runtime
from server.runtime_state import tasks
from server.worker_scheduler import WorkerScheduler, owned_job, restore_task
from server.worker_reports import build_worker_report
from server.response_presentation import worker_presentation
from test_orchestrator_worker import owners, task, success, wait_until
from test_integrity_lifecycle import world


def test_missing_history_before_finalization_keeps_result_and_advances_queue(owners):
    owner,_=owners
    async def scenario():
        gate=asyncio.Event(); started=[]
        async def execute(t):
            started.append(t['message'])
            if t['message']=='first': await gate.wait()
            success(t)
        scheduler=WorkerScheduler(execute)
        try:
            first=await scheduler.submit(task(owner,'first'))
            second=await scheduler.submit(task(owner,'second'))
            await wait_until(lambda:started==['first'])
            with db.get_db() as c:
                c.execute('DELETE FROM messages WHERE id=?',(first['history_message_id'],))
            gate.set()
            await scheduler.runners[owner['id']]
            assert started==['first','second']
            for t in (first,second):
                job=owned_job(t['task_id'],owner['id'])
                assert job['state']=='success'
                restored=restore_task(job)
                assert restored['answer']==t['answer']
                assert restored['commands']==t['commands']
                assert restored['worker_report']['status']=='success'
            messages=db.get_messages(owner['chat_id'])
            assert len([m for m in messages if m.get('_taskId')==first['task_id']])==1
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_history_sql_error_is_local_and_does_not_reclassify_execution(owners):
    owner,_=owners
    async def scenario():
        gate=asyncio.Event(); started=[]
        async def execute(t):
            started.append(t['message']); await gate.wait(); success(t)
        scheduler=WorkerScheduler(execute)
        try:
            first=await scheduler.submit(task(owner,'first'))
            second=await scheduler.submit(task(owner,'second'))
            with db.get_db() as c:
                c.execute("CREATE TRIGGER reject_history BEFORE UPDATE ON messages BEGIN SELECT RAISE(ABORT,'history unavailable'); END")
            gate.set(); await scheduler.runners[owner['id']]
            assert started==['first','second']
            for t in (first,second):
                job=owned_job(t['task_id'],owner['id'])
                assert job['state']=='success'
                restored=restore_task(job)
                assert restored['commands']==t['commands'] and restored['answer']==t['answer']
                assert any(e.get('event')=='history_persistence_failed' for e in restored['diagnostic_trace'])
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


def test_report_column_failure_keeps_final_snapshot_and_releases_slot(owners):
    owner,_=owners
    async def scenario():
        gate=asyncio.Event(); started=[]
        async def execute(t):
            started.append(t['message']); await gate.wait(); success(t)
        scheduler=WorkerScheduler(execute)
        try:
            first=await scheduler.submit(task(owner,'first'))
            second=await scheduler.submit(task(owner,'second'))
            with db.get_db() as c:
                c.execute("CREATE TRIGGER reject_report BEFORE UPDATE OF report ON worker_jobs BEGIN SELECT RAISE(ABORT,'report column unavailable'); END")
            gate.set(); await scheduler.runners[owner['id']]
            assert started==['first','second']
            for t in (first,second):
                job=owned_job(t['task_id'],owner['id'])
                assert job['state']=='success'
                restored=restore_task(job)
                assert restored['worker_report']==t['worker_report']
                assert restored['commands']==t['commands'] and restored['task_receipt']==t['task_receipt']
                assert any(e.get('event')=='report_persistence_failed' for e in restored['diagnostic_trace'])
                message=next(m for m in db.get_messages(owner['chat_id']) if m.get('_taskId')==t['task_id'])
                assert message['workerReport']==restored['worker_report']
                assert message['content']==worker_presentation(restored)['conversational_response']
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('foreign', [False,True])
def test_deleted_or_foreign_chat_is_never_recreated_or_modified(owners,foreign):
    owner,other=owners
    async def scenario():
        gate=asyncio.Event()
        async def execute(t):await gate.wait();success(t)
        scheduler=WorkerScheduler(execute)
        try:
            first=await scheduler.submit(task(owner))
            if foreign:
                row=db.add_message(other['chat_id'],'assistant','foreign text')
                first['history_message_id']=row['id']
            else:assert db.delete_chat(owner['chat_id'],owner['id'])
            gate.set();await scheduler.runners[owner['id']]
            assert owned_job(first['task_id'],owner['id'])['state']=='success'
            if foreign:assert db.get_messages(other['chat_id'])[0]['content']=='foreign text'
            else:assert db.get_chat(owner['chat_id'],owner['id']) is None
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


def payload(text,kind='grounded_report',basis=None):
    return {'answer_type':kind,'text':text,'basis':basis or ['step_1'],
            'self_check':{'depends_on_current_external_state':True,'claims_completed_action':False,
                          'has_sufficient_evidence':True,'missing_evidence_question':''}}


@pytest.mark.parametrize('text,result,expected',[
 ('Ссылка: https://storage.yandexcloud.net/agent-files/fake.txt',{'returncode':0,'stdout':'IRU'},'Ссылка не была сформирована системой.'),
 ('Я запомнила ваш выбор.',{'returncode':0,'stdout':'IRU'},'Я не менял память'),
 ('Других устройств в сети не обнаружено.',{'returncode':0,'stdout':'IRU'},'Других подключённых к ИРУ'),
 ('Готово, файл создан.',{'returncode':5,'stdout':'','stderr':'Access denied'},'Команда завершилась с ошибкой'),
])
def test_runtime_applies_content_guard_to_structurally_valid_terminal(monkeypatch,text,result,expected):
    key='1:pc';tid='trust-review'
    runtime.tasks[tid]={'task_id':tid,'user_id':1,'chat_id':1,'message':'Проверь папку','original_request':'Проверь папку',
        'device_ids':[key],'status':'running','results':{},'modes':{},'orchestrated':True,
        'orchestrator_execution_mode':'simple','created_at':time.time(),'worker_id':'worker-1'}
    runtime.devices[key]={'user_id':1,'info':{'hostname':'pc','os':'Windows'},'pending':{}}
    commands=[{'tool_name':'execute_cmd','step_id':'step_1','status':'failed' if result['returncode'] else 'success','result':result},
              {'tool_name':'answer.text','status':'terminal','result':payload(text)}]
    async def process(**kw):return {'answer':text,'commands':copy.deepcopy(commands),'tasks':[]}
    async def probe(**kw):return None
    monkeypatch.setattr(runtime,'process_nl_command',process)
    monkeypatch.setattr(runtime,'_probe_python_toolchain_if_needed',probe)
    monkeypatch.setattr(runtime,'get_messages',lambda *a,**kw:[])
    monkeypatch.setattr(runtime,'get_device_profile',lambda *a,**kw:None)
    saved=[]
    monkeypatch.setattr(runtime,'add_message',lambda *a,**kw:saved.append(a))
    asyncio.run(runtime.run_nl_task(tid,1,'Проверь папку',[key],1))
    t=runtime.tasks[tid]
    assert expected in t['answer']
    assert t['commands']==commands  # Original text and evidence retained.
    report=build_worker_report(t)
    assert report['goal_completed'] is False
    assert expected in worker_presentation(t,report)['conversational_response']
    assert expected in saved[-1][2]


def test_runtime_history_failure_preserves_answer_and_evidence(monkeypatch):
    key='1:pc';tid='history-review';text='**Папка:** `IRU` существует.'
    runtime.tasks[tid]={'task_id':tid,'user_id':1,'chat_id':1,'message':'Проверь папку',
        'device_ids':[key],'status':'running','results':{},'modes':{},'orchestrated':True,
        'orchestrator_execution_mode':'simple','created_at':time.time(),'worker_id':'worker-1'}
    runtime.devices[key]={'user_id':1,'info':{'hostname':'pc','os':'Windows'},'pending':{}}
    commands=[{'tool_name':'execute_cmd','step_id':'step_1','status':'success','result':{'returncode':0,'stdout':'IRU'}},
              {'tool_name':'answer.text','status':'terminal','result':payload(text)}]
    async def process(**kw):return {'answer':text,'commands':copy.deepcopy(commands),'tasks':[]}
    async def probe(**kw):return None
    monkeypatch.setattr(runtime,'process_nl_command',process)
    monkeypatch.setattr(runtime,'_probe_python_toolchain_if_needed',probe)
    monkeypatch.setattr(runtime,'get_messages',lambda *a,**kw:[])
    monkeypatch.setattr(runtime,'get_device_profile',lambda *a,**kw:None)
    def missing(*a,**kw):raise ValueError('message_identity_mismatch')
    monkeypatch.setattr(runtime,'add_message',missing)
    asyncio.run(runtime.run_nl_task(tid,1,'Проверь папку',[key],1))
    t=runtime.tasks[tid]
    assert t['answer']==text and t['commands']==commands
    assert t['status']=='done'


def test_model_informational_flag_cannot_confirm_original_action_goal(owners):
    owner,_=owners;t=task(owner,'Создай новый файл')
    text='Файл существует.'
    t.update(status='done',answer=text,commands=[
        {'tool_name':'execute_cmd','step_id':'step_1','status':'success','result':{'returncode':0,'stdout':'existing.txt'}},
        {'tool_name':'answer.text','status':'terminal','result':payload(text)}])
    assert build_worker_report(t)['status']=='unknown'
    assert not build_worker_report(t)['goal_completed']
    t['task_receipt']={'answer_source':'audited_terminal','task_status':'completed','goal_completed':True,'final_verification_status':'verified'}
    t['commands'][-1]['result']['self_check']['claims_completed_action']=True
    t['commands'].insert(0,{'tool_name':'execute_cmd','step_id':'step_0','status':'success','result':{'returncode':0,'stdout':'OK: unrelated'}})
    assert build_worker_report(t)['status']=='unknown'


@pytest.mark.parametrize('kind',['partial_report','error_report'])
def test_false_completion_disguised_as_negative_report_still_rejected(kind):
    from server.controller_trust import enforce_trusted_answer
    text='Готово, файл создан.'
    answer=payload(text,kind);answer['self_check']['has_sufficient_evidence']=False
    journal=[{'tool_name':'execute_cmd','step_id':'step_1','status':'failed',
              'result':{'returncode':5,'stderr':'Access denied','stdout':''}},
             {'tool_name':'answer.text','status':'terminal','result':answer}]
    assert enforce_trusted_answer(text,journal)!=text


def test_action_goal_with_wrong_information_label_rejected_through_full_lifecycle(world):
    from test_integrity_lifecycle import install, admit, capture, shell, terminal
    from test_tool_only_protocol import _message, _tool_call, _completion_fn
    from server import controller_non_pipeline as controller
    from server.answer_auditor import audit_answer_payload
    client,owner,key,monkeypatch=world
    goal='Создай новый файл C:/Temp/new.txt и проверь создание'
    steps=[shell('Get-Item C:/Temp/new.txt','new.txt')]
    wrong=terminal('Файл существует.',action=False)
    partial=terminal('Подтверждено существование файла. Создание нового файла не доказано.',kind='partial_report',sufficient=False)
    device,_=install(world,steps,partial,121)
    monkeypatch.setattr(controller,'audit_answer_payload',audit_answer_payload)
    captured=[]
    responses=[_message(tool_calls=[_tool_call('read','execute_cmd',steps[0][1])]),
               _message(tool_calls=[wrong]),
               _message(content='{"valid":false,"reason":"The action goal is not proved; the worker informational label does not change it"}',finish_reason='stop'),
               _message(tool_calls=[partial]),
               _message(content='{"valid":true,"reason":"Honest partial observations; no proof of creation"}',finish_reason='stop')]
    async def process(**kw):
        return await controller.process_non_pipeline_command(
            user_message=kw['user_message'],device_id=kw['device_id'],device_info=kw['device_info'],
            send_command_fn=kw['send_command_fn'],get_file_link_fn=kw['get_file_link_fn'],chat_history=kw['chat_history'],
            user_id=None,chat_id=None,modes=kw['modes'],poll_task_id=kw['poll_task_id'],
            cfg={'model':'controlled-fixture','max_tokens':512},system_msg='system',machine_guid=None,mem_user_id=None,
            non_pipeline_tools=[],max_iterations=8,pick_model_fn=lambda *a:'controlled-fixture',
            chat_completion_request_fn=_completion_fn(responses,captured))
    monkeypatch.setattr(runtime,'process_nl_command',process)
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            t=await admit(world,scheduler,goal,'wrong-action-label')
            await scheduler.runners[owner['id']]
            assert t['task_receipt']['goal_completed'] is False
            assert t['answer']=='Подтверждено существование файла. Создание нового файла не доказано.'
            assert len(device.calls)==1
            await capture(world,t,121,'partial',device)
            audits=[c for c in captured if c.get('tools') is None]
            assert len(audits)==2
            assert json.loads(audits[0]['messages'][1]['content'])['user_request']==goal
            assert 'not the worker' in audits[0]['messages'][0]['content']
            assert not any(c.get('result',{}).get('text')=='Файл существует.' for c in t['commands'])
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('count',[100,1000])
def test_operations_read_with_large_completed_history(owners,monkeypatch,count):
    import statistics
    import tracemalloc
    from server.routers import tasks as routes
    owner,_=owners;now=time.time()
    template=task(owner);success(template);report=build_worker_report(template)
    template.update(status='success',worker_report=report,worker_id='worker-1')
    template['commands'][0]['result']['stdout']='x'*(128*1024)
    rows=[]
    for i in range(count):
        tid=f'load-{i}'
        item={**template,'task_id':tid,'worker_report':{**report,'task_id':tid}}
        rows.append((tid,owner['id'],owner['chat_id'],'success',json.dumps(item),json.dumps(item['worker_report']),now+i,now+i))
    with db.get_db() as c:
        c.executemany('INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,report,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',rows)
    from server.worker_scheduler import init_worker_storage
    init_worker_storage()
    with db.get_db() as c:
        for ordering in ("created_at DESC", "CASE WHEN state IN ('running','waiting_confirmation') THEN 0 WHEN state='queued' THEN 1 ELSE 2 END, CASE WHEN state='queued' THEN created_at ELSE -created_at END, rowid"):
            plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN SELECT * FROM worker_jobs WHERE owner_user_id=? ORDER BY '+ordering+' LIMIT 20',(owner['id'],))]
            assert any('USING INDEX worker_owner_' in step for step in plan),plan
            assert not any('TEMP B-TREE' in step for step in plan),plan
    monkeypatch.setattr(routes,'get_current_user',lambda request:owner)
    tracemalloc.start();timings=[]
    for _ in range(5):
        start=time.perf_counter()
        result=asyncio.run(routes.api_operations(SimpleNamespace()))
        timings.append((time.perf_counter()-start)*1000)
        assert len(result['operations'])==20
        assert result['operations'][0]['task_id']==f'load-{count-1}'
        assert all(item['status']=='success' for item in result['operations'])
    peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
    print(f'OPERATIONS_LOAD jobs={count} payload_bytes={len(rows[0][4])*count} median_ms={statistics.median(timings):.2f} max_ms={max(timings):.2f} peak_python_bytes={peak}')


def test_history_deleted_during_real_worker_keeps_all_presentations_and_queue(world):
    from test_integrity_lifecycle import install, admit, capture, shell, terminal
    from server import controller_non_pipeline as controller
    client,owner,key,monkeypatch=world
    goal='Проверь папку на рабочем столе'
    steps=[shell('Get-ChildItem C:/Users/Owner/Desktop','IRU')]
    device,_=install(world,steps,terminal('**Папка:** `IRU` существует.'),122)
    deleted=[]
    async def audit(**kw):
        with db.get_db() as c:tid=c.execute("SELECT task_id FROM worker_jobs WHERE owner_user_id=? AND state='running'",(owner['id'],)).fetchone()[0]
        t=tasks[tid]
        with db.get_db() as c:c.execute('DELETE FROM messages WHERE id=?',(t['history_message_id'],))
        deleted.append(t['history_message_id'])
        return True,'controlled supported observation',False
    monkeypatch.setattr(controller,'audit_answer_payload',audit)
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            first=await admit(world,scheduler,goal,'history-deleted-live')
            # Keep a second real queued job; finalization must advance it.
            second=await scheduler.submit({'task_id':'after-history-deletion','user_id':owner['id'],'chat_id':owner['chat_id'],
                'message':goal,'original_request':goal,'device_ids':[key],'modes':{},'kind':'worker',
                'orchestrated':True,'orchestrator_execution_mode':'simple','created_at':time.time(),'status':'running','results':{}})
            device.steps.extend(copy.deepcopy(steps))
            await scheduler.runners[owner['id']]
            assert len(deleted)==2 and len(device.calls)==2
            assert owned_job(first['task_id'],owner['id'])['state']=='success'
            assert owned_job(second['task_id'],owner['id'])['state']=='success'
            assert first['commands'][-1]['result']['text']==first['answer']=='**Папка:** `IRU` существует.'
            # Capture one real dispatch per job, with the same shared transport.
            one=SimpleNamespace(calls=device.calls[:1])
            await capture(world,first,122,'success',one)
            assert any(e['event']=='history_persistence_failed' for e in first['diagnostic_trace'])
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


def test_cached_success_cannot_bypass_current_goal_evidence_contract(owners):
    from server.response_presentation import normalized_worker_report
    owner,_=owners;t=task(owner,'Создай новый файл');success(t)
    stale=build_worker_report(t)
    text='Файл существует.'
    t.update(answer=text,commands=[{'tool_name':'execute_cmd','step_id':'step_1','status':'success','result':{'returncode':0,'stdout':'existing.txt'}},
        {'tool_name':'answer.text','status':'terminal','result':payload(text)}],task_receipt=None)
    assert build_worker_report(t)['status']=='unknown'
    assert normalized_worker_report(t,stale)['status']=='unknown'
