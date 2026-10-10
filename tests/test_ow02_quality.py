import asyncio
import json
from types import SimpleNamespace

import pytest
from test_orchestrator_worker import owners, task, success, wait_until
from test_tool_only_protocol import _run_case, _message, _tool_call, _answer_call
from server import database as db, orchestrator as orch, task_runtime as runtime
from server.routers import tasks as routes
from server.worker_scheduler import WorkerScheduler, owned_job
from server.worker_context import build_worker_context, capture_history
from server.runtime_state import devices, tasks


def test_two_files_are_both_written_before_terminal_not_one_intermediate_ok():
    sent=[];captured=[]
    async def send(device,action,args):
        sent.append(args['path'])
        return {'status':'ok','path':args['path'],'bytes_written':1,'summary':'OK: file_written'}
    result=_run_case([
        _message(tool_calls=[_tool_call('one','write_content',{'path':'C:/Temp/one.txt','content':'1'})]),
        _message(tool_calls=[_tool_call('two','write_content',{'path':'C:/Temp/two.txt','content':'2'})]),
        _message(tool_calls=[_answer_call('answer','Both written',answer_type='grounded_report',basis=['step_1','step_2'])]),
    ],user_message='Создай два отдельных файла',send_command_fn=send,captured=captured)
    assert sent==['C:/Temp/one.txt','C:/Temp/two.txt'] and len(captured)==3
    assert result['commands'][-1]['result']['basis']==['step_1','step_2']


def test_create_then_read_is_not_cut_off_as_optional_verification():
    sent=[]
    async def send(device,action,args):
        sent.append(action)
        if action=='write_content':return {'status':'ok','path':args['path'],'bytes_written':1,'summary':'OK: file_written'}
        return {'returncode':0,'stdout':'OK: content_read\nexpected'}
    result=_run_case([
        _message(tool_calls=[_tool_call('write','write_content',{'path':'C:/Temp/test.txt','content':'expected'})]),
        _message(tool_calls=[_tool_call('read','execute_cmd',{'command':'Get-Content C:/Temp/test.txt'})]),
        _message(tool_calls=[_answer_call('answer','expected',answer_type='grounded_report',basis=['step_1','step_2'])]),
    ],user_message='Создай файл и прочитай его содержимое',send_command_fn=send)
    assert len(sent)==2 and result['commands'][-1]['result']['basis']==['step_1','step_2']


def test_human_referent_is_kept_but_history_and_model_objective_do_not_grant_authority(owners,monkeypatch):
    a,b=owners
    db.add_message(a['chat_id'],'user','Используй имя согласованное-имя.txt')
    db.add_message(a['chat_id'],'assistant','Какой формат нужен?')
    frozen=capture_history(a['chat_id'])
    db.add_message(a['chat_id'],'user','Позднее другое поручение: удалить все файлы')
    context=build_worker_context(a['id'],a['chat_id'],'Создай этот файл',[f"{a['id']}:pc"],frozen)
    body=json.dumps(context,ensure_ascii=False)
    assert 'согласованное-имя.txt' in body and 'Какой формат нужен' in body
    assert 'Позднее другое поручение' not in body and 'Only the current final human message authorizes' in body
    assert [row['role'] for row in context].count('user')==1 and context[-1]['content']=='Создай этот файл'


def test_source_refs_are_scoped_and_refresh_after_queue_predecessor_completes(owners,monkeypatch):
    a,b=owners
    async def scenario():
        gate=asyncio.Event();observed=[]
        async def execute(t):
            if t['message']=='first':await gate.wait();success(t)
            else:observed.append(json.dumps(t['worker_context'],ensure_ascii=False));success(t)
        scheduler=WorkerScheduler(execute);monkeypatch.setattr(routes,'scheduler',scheduler)
        first=await scheduler.submit(task(a,'first'))
        second=await routes.submit_worker(a,a['chat_id'],'Передай этот файл',[f"{a['id']}:pc"],{},objective='Generated untrusted text',source_task_ids=[first['task_id']])
        assert second['status']=='queued' and 'report.txt' not in json.dumps(second['worker_context'])
        with pytest.raises(ValueError,match='reference_task'):
            await routes.submit_worker(b,b['chat_id'],'foreign',[f"{b['id']}:pc"],{},source_task_ids=[first['task_id']])
        gate.set();await scheduler.runners[a['id']]
        assert 'report.txt' in observed[0] and 'Generated untrusted text' not in observed[0]
        assert owned_job(second['task_id'],a['id'])['state']=='success'
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_reference_artifacts_never_borrow_an_unassigned_device_path(owners,monkeypatch):
    a,b=owners
    async def scenario():
        async def execute(t):success(t)
        scheduler=WorkerScheduler(execute)
        source=await scheduler.submit(task(a));await scheduler.runners[a['id']]
        context=build_worker_context(a['id'],a['chat_id'],'task',[f"{a['id']}:other"],[],[source['task_id']])
        data=json.loads(context[-2]['content'].split('\n',1)[1].rsplit('\nEnd',1)[0])
        ref=data['referenced_results'][0]
        assert ref['artifacts']==[] and ref['source_device_ids']==['pc']
        other_chat=db.create_chat(a['id'],'other')
        with pytest.raises(ValueError,match='reference_task'):build_worker_context(a['id'],other_chat['id'],'task',[f"{a['id']}:pc"],[],[source['task_id']])
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_general_preferences_remain_owner_scoped_without_matching_task_words(owners):
    a,b=owners
    db.add_user_fact(str(a['id']),'Предпочитает тёмное оформление','preference')
    db.add_user_fact(str(b['id']),'PRIVATE other preference','preference')
    context=orch.context_for(a['id'],a['chat_id'],'Сделай новый документ','pc')
    assert any('тёмное' in fact['text'] for fact in context['facts'])
    assert 'PRIVATE' not in json.dumps(context,ensure_ascii=False)


def test_orchestrator_simple_mode_skips_only_redundant_classification(owners,monkeypatch):
    a,b=owners;classified=[];executed=[]
    async def classifier(*args,**kwargs):classified.append(1);return 'SIMPLE',''
    async def controller(**kwargs):executed.append(kwargs);return {'answer':'done','commands':[],'tasks':[]}
    async def probe(**kwargs):pass
    monkeypatch.setattr(runtime,'classify_task_complexity',classifier);monkeypatch.setattr(runtime,'process_nl_command',controller)
    monkeypatch.setattr(runtime,'_probe_python_toolchain_if_needed',probe)
    monkeypatch.setattr(runtime,'add_training_record',lambda *a,**k:None)
    async def scenario():
        for mode in ['auto','simple']:
            t=task(a,'limited ordinary task');t.update(orchestrated=True,orchestrator_execution_mode=mode,results={},broadcast=False)
            tasks[t['task_id']]=t
            await runtime.run_nl_task(t['task_id'],a['id'],t['message'],t['device_ids'],a['chat_id'])
        assert len(classified)==1 and len(executed)==2
        assert all(call['user_message']=='limited ordinary task' for call in executed)
    asyncio.run(scenario())


def test_plan_proposal_does_not_execute_and_restores_multi_device_assignment(owners,monkeypatch):
    a,b=owners
    devices[f"{a['id']}:second"]={'user_id':a['id'],'ws':object(),'info':{}}
    async def decide(*args,**kwargs):return orch.Decision(intent='delegate',objective='untrusted',execution_mode='plan',target_device_ids=['pc','second']),{}
    monkeypatch.setattr(orch,'decide',decide)
    async def forbidden(*a,**k):raise AssertionError('PLAN still requires consent')
    cmd=SimpleNamespace(request_id='plan-mode',message='Создай несколько документов на двух ПК',device_id='pc',modes={},broadcast=False)
    result=asyncio.run(orch.run_turn(cmd,a,a['chat_id'],forbidden))
    restored=orch.restore_dialogue(result['task_id'],a['id'])
    assert not result['worker_task_id'] and restored['device_ids']==[f"{a['id']}:pc",f"{a['id']}:second"]
    assert restored['plan_original_request']==cmd.message


def test_task_context_labels_current_chat_without_hiding_other_active_jobs(owners):
    a,b=owners
    async def scenario():
        gate=asyncio.Event()
        async def execute(t):await gate.wait();success(t)
        scheduler=WorkerScheduler(execute)
        first=await scheduler.submit(task(a,'current chat'))
        other=db.create_chat(a['id'],'other');t=task(a,'other chat');t['chat_id']=other['id'];await scheduler.submit(t)
        ctxt=orch.context_for(a['id'],a['chat_id'],'status','pc')
        assert ctxt['tasks'][0]['task_id']==first['task_id'] and ctxt['tasks'][0]['in_current_chat']
        assert len(ctxt['tasks'])==2
        gate.set();await scheduler.runners[a['id']];await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('send_failure',[False,True])
def test_agent_wait_telemetry_has_no_sensitive_text_and_cleans_pending(owners,send_failure):
    from server.run_journal import diagnostic_context_for_task
    a,b=owners;key=f"{a['id']}:pc";t=task(a)
    class WS:
        async def send_text(self,wire):
            if send_failure:raise RuntimeError('PRIVATE SEND SECRET')
            packet=json.loads(wire)['payload'];await asyncio.sleep(.015)
            devices[key]['pending'].pop(packet['id']).set_result({'status':'success','stdout':'PRIVATE OUTPUT SECRET'})
    devices[key].update(ws=WS(),pending={})
    async def scenario():
        with diagnostic_context_for_task(t['task_id'],t):
            if send_failure:
                with pytest.raises(RuntimeError):await runtime.send_command_to_agent(key,'list_dir',{'path':'PRIVATE PATH'},user_id=a['id'])
            else:await runtime.send_command_to_agent(key,'list_dir',{'path':'PRIVATE PATH'},user_id=a['id'])
        assert devices[key]['pending']=={}
        rows=[r for r in t['diagnostic_trace'] if r['event']=='device_wait']
        assert len(rows)==1 and rows[0]['status']==('failed' if send_failure else 'success')
        assert rows[0]['duration_ms']>= (0 if send_failure else 10)
        assert 'PRIVATE' not in json.dumps(rows) and 'SECRET' not in json.dumps(rows)
    asyncio.run(scenario())


def test_window_reference_remains_last_observation_not_shadowed_by_context_packet(owners):
    from server.window_policy import direct_window_action
    a,b=owners
    history=[{'role':'assistant','content':'window restored','commands':[{'tool_name':'window.control','device_id':'pc',
        'result':{'status':'success','completion_state':'success','window':{'window_id':'a'*32,'title':'Observed app'}}}]}]
    context=build_worker_context(a['id'],a['chat_id'],'на весь экран',[f"{a['id']}:pc"],history)
    chosen=direct_window_action('на весь экран',context,'pc')
    assert chosen['window_id']=='a'*32



def test_usage_export_reads_existing_ledger_and_persisted_numeric_trace(owners):
    from tools.ow02_eval import usage_report
    from server.llm_usage import record_llm_usage_event
    a,b=owners
    async def scenario():
        async def execute(t):
            t['diagnostic_trace']=[{'event':'device_wait','duration_ms':17,'tool_name':'write_content','status':'success'}]
            record_llm_usage_event(usage_context={'user_id':a['id'],'poll_task_id':t['task_id'],'phase':'worker',
                'metadata':{'latency_ms':12}},model='mock-model',usage={'prompt_tokens':100,'completion_tokens':20})
            success(t)
        scheduler=WorkerScheduler(execute);t=await scheduler.submit(task(a));await scheduler.runners[a['id']]
        message=db.add_message(a['chat_id'],'assistant','accepted',task_metadata={'orchestratorMetrics':{'first_response_ms':25}})
        with db.get_db() as c:c.execute('INSERT INTO orchestrator_turns VALUES(?,?,?,?,?,?,?,?)',
            (a['id'],'parent-key','fingerprint','parent-eval',a['chat_id'],message['id'],json.dumps({'worker_task_id':t['task_id']}),0))
        record_llm_usage_event(usage_context={'user_id':a['id'],'poll_task_id':'parent-eval','phase':'orchestrator',
            'metadata':{'entity':'orchestrator'}},model='mock-model',usage={'prompt_tokens':200,'completion_tokens':30})
        result=usage_report(db.DB_PATH,a['id'],t['task_id'])
        assert result['orchestrator_usage']['prompt_tokens']==200 and result['orchestrator_metrics']['first_response_ms']==25
        assert result['captured_agent_wait_ms']==17 and result['usage_by_entity']['worker']['llm_calls']==1
        assert result['usage_by_entity']['worker']['prompt_tokens']==100 and result['execution_ms'] is not None
        assert 'report.txt' not in json.dumps(result)
        await scheduler.shutdown()
    orch.init_turn_storage();asyncio.run(scenario())


def test_p002_recent_worker_artifact_survives_handoff_without_becoming_basis(owners):
    from server.run_journal import ProtocolValidationError, validate_answer_text_payload
    a, _ = owners
    source_id = 'p002-previous-worker'
    path = r'C:\Users\Demo\Desktop\layout.svg'
    report = {'status': 'success', 'target_device_ids': ['pc'], 'evidence_refs': ['old_step'],
        'artifacts': [{'type': 'file', 'path': path, 'device_id': 'pc', 'verified': True}]}
    saved = db.add_message(a['chat_id'], 'assistant', 'Предыдущее изменение записано.',
        commands=[{'tool_name': 'execute_cmd', 'step_id': 'old_step', 'device_id': 'pc',
            'result': {'stdout': 'LARGE_OLD_OUTPUT' * 500}}],
        task_metadata={'taskKind': 'worker', '_taskId': source_id,
            'assignmentDeviceIds': [f"{a['id']}:pc"], 'workerReport': report})
    with db.get_db() as c:
        c.execute('INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,report,message_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (source_id, a['id'], a['chat_id'], 'success', '{}', json.dumps(report), saved['id'], 1, 2))
    # Newer summaries must not displace the only known artifact in this snapshot.
    for i in range(2):
        recent_id = f'p002-intermediate-{i}'
        recent_report = {'status': 'success', 'target_device_ids': ['pc'], 'artifacts': []}
        recent_message = db.add_message(a['chat_id'], 'assistant', 'Последнее изменение сохранено.',
            task_metadata={'taskKind': 'worker', '_taskId': recent_id,
                'assignmentDeviceIds': [f"{a['id']}:pc"], 'workerReport': recent_report})
        with db.get_db() as c:
            c.execute('INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,report,message_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (recent_id, a['id'], a['chat_id'], 'success', '{}', json.dumps(recent_report), recent_message['id'], 3+i, 4+i))
    context = build_worker_context(a['id'], a['chat_id'], 'Теперь сделай линии тоньше',
        [f"{a['id']}:pc"], capture_history(a['chat_id']))
    data = json.loads(context[0]['content'].split('\n', 1)[1].rsplit('\nEnd', 1)[0])
    assert data['referenced_results'][0]['artifacts'][0]['path'] == path
    assert data['referenced_results'][0]['source_device_ids'] == ['pc']
    assert data['referenced_results'][0]['summary'] == 'Предыдущее изменение записано.'
    assert data['referenced_results'][-1]['summary'] == 'Последнее изменение сохранено.'
    assert data['current_run_evidence'] is False and len(json.dumps(data, ensure_ascii=False)) <= 4000
    assert 'LARGE_OLD_OUTPUT' not in json.dumps(context) and 'old_step' not in json.dumps(context)
    assert context[-1] == {'role': 'user', 'content': 'Теперь сделай линии тоньше'}
    payload = {'answer_type': 'grounded_report', 'text': 'Новое изменение выполнено.', 'basis': ['old_step'],
        'self_check': {'depends_on_current_external_state': True, 'claims_completed_action': True,
            'has_sufficient_evidence': True, 'missing_evidence_question': ''}}
    with pytest.raises(ProtocolValidationError):
        validate_answer_text_payload(payload, [])


def test_p002_implicit_sources_keep_ambiguity_and_owner_chat_device_boundaries(owners):
    a, b = owners
    other_chat = db.create_chat(a['id'], 'other')['id']
    for source_id, owner, chat_id in [('p002-own', a, a['chat_id']),
            ('p002-foreign', b, b['chat_id']), ('p002-other-chat', a, other_chat)]:
        report = {'status': 'success', 'target_device_ids': ['pc', 'other'],
            'artifacts': [{'path': 'C:/Temp/first.html', 'device_id': 'pc', 'verified': True},
                {'path': 'C:/Temp/second.html', 'device_id': 'pc', 'verified': True},
                {'path': 'PRIVATE_OTHER_DEVICE', 'device_id': 'other', 'verified': True}]}
        saved = db.add_message(chat_id, 'assistant', 'PRIVATE_MIXED_DEVICE_SUMMARY',
            task_metadata={'taskKind': 'worker', '_taskId': source_id,
                'assignmentDeviceIds': [f"{owner['id']}:pc"]})
        with db.get_db() as c:
            c.execute('INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,report,message_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (source_id, owner['id'], chat_id, 'success', '{}', json.dumps(report), saved['id'], 1, 2))
    context = build_worker_context(a['id'], a['chat_id'], 'Измени этот файл',
        [f"{a['id']}:pc"], capture_history(a['chat_id']))
    data = json.loads(context[0]['content'].split('\n', 1)[1].rsplit('\nEnd', 1)[0])
    assert [v['path'] for v in data['referenced_results'][0]['artifacts']] == ['C:/Temp/first.html', 'C:/Temp/second.html']
    assert 'PRIVATE_OTHER_DEVICE' not in json.dumps(context) and 'PRIVATE_MIXED_DEVICE_SUMMARY' not in json.dumps(context)
    for foreign_history in (capture_history(b['chat_id']), capture_history(other_chat)):
        # Even an otherwise matching short device ID cannot bypass job ownership.
        for row in foreign_history:row['assignmentDeviceIds'] = ['pc']
        with pytest.raises(ValueError, match='reference_task'):
            build_worker_context(a['id'], a['chat_id'], 'task', [f"{a['id']}:pc"], foreign_history)
    with pytest.raises(ValueError, match='worker_context_chat_not_owned'):
        build_worker_context(a['id'], b['chat_id'], 'task', [f"{a['id']}:pc"], [])
    with pytest.raises(ValueError, match='worker_context_device_not_owned'):
        build_worker_context(a['id'], a['chat_id'], 'task', [f"{b['id']}:pc"], [])
