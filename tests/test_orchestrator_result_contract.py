"""Synthetic historical cases. No production, real device or provider calls."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from server import database as db, orchestrator as orch
from server.run_journal import validate_answer_text_payload, append_answer_step
from server.worker_reports import build_worker_report, coverage_binding, coverage_request
from server.worker_scheduler import WorkerScheduler, owned_job, restore_task, goal_view
from server.runtime_state import tasks
from server.goal_presentation import goal_presentation
from server.routers import tasks as task_routes
from test_orchestrator_worker import owners, task
from test_tool_only_protocol import _run_case, _message, _tool_call


def entry(tool, result, number=1):
    return {'step_id': f'step_{number}', 'tool_name': tool, 'target_device_id': 'pc', 'result': result}


def finish(steps, result='Получен результат.', *, scope='analysis', effects=(), unresolved=(), recommendations=()):
    payload=validate_answer_text_payload({'result':result,'unresolved':list(unresolved),'remaining':list(recommendations)},steps)
    append_answer_step(steps,'answer_text',payload,target_device_id='pc')
    return {'task_id':'synthetic','status':'done','device_ids':['pc'],'execution_scope':scope,
        'window_effects':list(effects),'commands':steps,'answer':payload['text']}


def opened():
    return {'status':'opened_verified','completion_state':'success','window_found':True,
        'window_title':'Яндекс — Comet','url':'https://ya.ru','window':{'pid':123,'title':'Яндекс — Comet'}}


def complete_coverage_fixture(t):
    """Represent the existing successful goal-coverage observation in pure fixtures."""
    journal=t['commands'][:-1]
    t['commands'][-1]['result']['_coverage']={'coverage':'complete',
        'evidence_refs':[s['step_id'] for s in journal],
        'binding':coverage_binding(coverage_request(t),journal,t['answer'])}


def test_bcf16d6e_verified_open_is_spoken_by_orchestrator():
    t=finish([entry('app.open_url',opened())],scope='window',effects=['open'],result='worker material')
    report=build_worker_report(t)
    assert report['goal_completed'] and report['status']=='success'
    human=orch.present_worker_result(t,report)
    assert 'Открытие подтверждено' in human['conversational_response']
    assert 'https://ya.ru' in human['conversational_response']
    assert 'worker material' not in human['conversational_response']
    assert 'success' in human['execution_details']


def test_b1daebcb_optional_recommendation_is_not_an_unfinished_requirement():
    t=finish([entry('execute_cmd',{'returncode':0,'stdout':'fileA.txt\nfolderB','stderr':''})],
        result='На рабочем столе файл fileA.txt и папка folderB. Проверен верхний уровень.',
        recommendations=['При необходимости открыть папку folderB'])
    complete_coverage_fixture(t)
    report=build_worker_report(t)
    assert report['goal_completed']
    assert 'fileA.txt' in orch.present_worker_result(t,report)['conversational_response']
    assert report['evidence_refs']


def test_aa2baba8_pending_close_cannot_be_spoken_as_closed():
    t=finish([entry('window.control',{'status':'pending','completion_state':'pending','action':'close',
        'summary':'Запрос закрытия отправлен. Окно остаётся открытым.'})],
        scope='window',effects=['close'],result='Таблица закрыта.')
    report=build_worker_report(t)
    human=orch.present_worker_result(t,report)['conversational_response']
    assert not report['goal_completed'] and report['status']=='partial'
    assert 'Окно остаётся открытым' in human and 'Таблица закрыта' not in human


def test_3801d76f_initial_failure_does_not_erase_verified_open():
    steps=[entry('execute_cmd',{'returncode':1,'stdout':'','stderr':'first attempt failed'}),
        entry('app.launch',{**opened(),'window_title':'agent_errors.xlsx — WPS'},2),
        entry('window.list',{'status':'success','windows':[{'title':'agent_errors.xlsx — WPS'}]},3)]
    t=finish(steps,scope='window',effects=['open'])
    report=build_worker_report(t)
    assert report['goal_completed'] and report['error_code'] is None
    assert 'agent_errors.xlsx' in orch.present_worker_result(t,report)['conversational_response']


def test_f7477289_synthetic_rollback_is_the_final_file_state():
    # User reconstruction only, not an original journal or real historical path.
    path='C:/Synthetic/atlas.js';original=b'size=0.42';smaller=b'size=0.21'
    digest=lambda data:hashlib.sha256(data).hexdigest()
    steps=[entry('write_content',{'status':'ok','verified':True,'committed':True,'path':path,
        'file_sha256':digest(after),'previous_sha256':digest(before),'total_size':len(after)},n)
        for n,before,after in [(4,original,smaller),(18,smaller,original),(19,original,smaller),(20,smaller,original)]]
    t=finish(steps,scope='general',result='Размер уменьшен до 0.21.');t['assigned_artifacts']=[path]
    report=build_worker_report(t)
    assert not report['goal_completed'] and report['error_code']=='required_file_change_reverted'
    assert 'уменьшен до 0.21' not in orch.present_worker_result(t,report)['conversational_response']


def test_unknown_transport_cannot_be_success_or_trigger_an_automatic_retry(monkeypatch):
    monkeypatch.setattr(db,'get_device_profile',lambda *a,**k:None)
    sent=[]
    async def send(*args):
        sent.append(args)
        return {'status':'unknown','error':'confirmed_transport_outcome_unknown','dispatched':True}
    result=_run_case([_message(tool_calls=[_tool_call('lost','execute_cmd',{'command':'mutate','artifact_paths':[]})])],
        send_command_fn=send,max_iterations=3)
    assert len(sent)==1 and not build_worker_report({'task_id':'lost','status':'done','device_ids':['device-1'],
        'commands':result['commands'],'answer':result['answer']})['goal_completed']


def test_useful_partial_keeps_findings_and_substantive_unresolved():
    t=finish([entry('execute_cmd',{'returncode':0,'stdout':'service=active; resets=15','stderr':''})],
        result='Сервис работает. Обнаружено 15 переподключений.',unresolved=['Причина переподключений не установлена'])
    report=build_worker_report(t);human=orch.present_worker_result(t,report)['conversational_response']
    assert report['status']=='partial' and not report['goal_completed']
    assert '15 переподключений' in human and 'Причина переподключений' in human
    assert all(code not in human for code in ['Partial','Unknown','0/1'])


def test_unproved_effect_claim_is_not_the_orchestrator_reply():
    t=finish([entry('execute_cmd',{'returncode':0,'stdout':'helper observation','stderr':''})],
        scope='general',result='Файл создан и полностью проверен.')
    report=build_worker_report(t)
    human=orch.present_worker_result(t,report)['conversational_response']
    assert not report['goal_completed']
    assert 'Файл создан' not in human and 'helper observation' in human


def test_compact_controller_uses_existing_goal_coverage_once_without_repair(monkeypatch):
    monkeypatch.setattr(db,'get_device_profile',lambda *a,**k:None)
    tasks['compact']={'task_id':'compact','execution_scope':'analysis','original_request':'Посмотри рабочий стол'}
    calls=[]
    try:
        result=_run_case([
            _message(tool_calls=[_tool_call('read','execute_cmd',{'command':'Get-ChildItem','artifact_paths':[]})]),
            _message(tool_calls=[_tool_call('result','answer_text',{'result':'Получен список файлов','unresolved':[],
                'remaining':['Можно открыть папки']})]),
            _message(content=json.dumps({'coverage':'complete','evidence_refs':['step_1'],'reason':'Listing obtained'}),finish_reason='stop')],poll_task_id='compact',captured=calls,
            cfg={'model':'mock','answer_auditor_enabled':True})
        assert len(calls)==3 and all(call['tools'] for call in calls[:2])
        assert calls[-1]['tools'] is None and calls[-1]['phase']=='result_coverage'
        assert result['commands'][-1]['result']['_result_contract']==3
    finally:tasks.pop('compact',None)


def test_open_controller_uses_unchanged_agent_api_and_no_narration_call(monkeypatch):
    monkeypatch.setattr(db,'get_device_profile',lambda *a,**k:None)
    tasks['open-contract']={'task_id':'open-contract','execution_scope':'window','window_effects':['open']}
    calls=[];sent=[]
    async def send(device,action,params):
        sent.append((action,params))
        return opened()
    try:
        result=_run_case([
            _message(tool_calls=[_tool_call('open','app_open_url',{'url':'https://ya.ru'})]),
            _message(tool_calls=[_tool_call('finish','answer_text',{'result':'Opened','unresolved':[]})])],
            poll_task_id='open-contract',captured=calls,send_command_fn=send,
            cfg={'model':'mock','answer_auditor_enabled':True})
        assert sent==[('app.open_url',{'url':'https://ya.ru'})]
        assert len(calls)<=2 and all(call['tools'] for call in calls)
        t={**tasks['open-contract'],'status':'done','device_ids':['device-1'],'commands':result['commands'],'answer':result['answer']}
        assert build_worker_report(t)['goal_completed']
    finally:tasks.pop('open-contract',None)


def test_goal_assignment_result_and_followup_use_existing_durable_path(owners,monkeypatch):
    owner,_=owners;received=[];dialogue=[]
    async def execute(t):
        received.append(t)
        result=finish([entry('execute_cmd',{'returncode':0,'stdout':'server active; reconnects=15','stderr':''})],
            result='Сервер работает. В журнале 15 переподключений.',recommendations=['Можно отдельно исследовать причину'])
        t.update(status='done',answer=result['answer'],commands=result['commands'])
        complete_coverage_fixture(t)
    async def scenario():
        scheduler=WorkerScheduler(execute)
        async def decide(message,context,**kwargs):
            dialogue.append((message,context))
            if len(dialogue)==1:
                return orch.Decision(intent='delegate',objective='Осмотреть сервер',execution_scope='analysis',
                    target_device_ids=['pc'],assignments=[orch.Assignment(key='inspect',objective='Проверить состояние сервера',
                    scope='Текущее состояние и последние ошибки',constraints=['Только чтение'],execution_scope='analysis')]),{'llm_calls':1}
            jobs=context['tasks'];source=next(j for j in jobs if j['task_id']==received[0]['task_id'])
            assert source['report']['status']=='success' and '15 переподключений' in source['report']['summary']
            return orch.Decision(intent='task_status',task_id=source['task_id'],reference='explicit',
                answer='Сервер работает; нашлось 15 переподключений, причина не исследована.'),{'llm_calls':1}
        monkeypatch.setattr(orch,'decide',decide)
        monkeypatch.setattr(orch,'scheduler',scheduler)
        async def delegate(choice,request_key):
            template=task(owner,'Как дела с сервером?');template['context_history']=[]
            admitted=await scheduler.submit_goal(template,[a.model_dump() for a in choice.assignments],request_key=request_key)
            return {**admitted['tasks'][0],'goal_task_ids':[t['task_id'] for t in admitted['tasks']]}
        try:
            cmd=lambda message,key:SimpleNamespace(message=message,request_id=key,device_id='pc',device_ids=[],modes={},broadcast=False)
            first=await orch.run_turn(cmd('Как дела с сервером?','first'),owner,owner['chat_id'],delegate)
            await scheduler.runners[owner['id']]
            saved=restore_task(owned_job(first['worker_task_id'],owner['id']))
            assert saved['assignment_scope']=='Текущее состояние и последние ошибки'
            assert saved['assignment_constraints']==['Только чтение']
            assert 'Только чтение' in json.dumps(received[0]['worker_context'],ensure_ascii=False)
            assert saved['worker_report']['goal_completed']
            goal=goal_view(first['goal_id'],owner['id'],owner['chat_id'])
            human=goal_presentation(goal)['message']
            assert '15 переподключений' in human and '1 из 1' not in human
            # A Goal member owns no chat message: the single user-visible result
            # is the Orchestrator message of the Goal.
            assert saved['history_message_id'] is None
            with db.get_db() as c:
                row=(c.execute('select content from messages where id=?',(saved['history_message_id'],)).fetchone()
                     if saved['history_message_id'] else None)
            assert row is None
            monkeypatch.setattr(task_routes,'get_current_user',lambda request:owner)
            root_reply=(await task_routes.api_get_task(first['task_id'],None))['task']
            assert root_reply['goal_presentation']['status']=='success'
            assert '15 переподключений' in root_reply['answer']
            tasks.pop(received[0]['task_id'],None)  # Next turn must read the durable result.
            second=await orch.run_turn(cmd('И что там?','followup'),owner,owner['chat_id'],delegate)
            assert '15 переподключений' in second['answer']
            assert len(received)==1 and len(dialogue)==2
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


def test_legacy_api_answer_is_orchestrator_reply_not_internal_worker_material(owners,monkeypatch):
    owner,_=owners
    t=task(owner);tid=t['task_id']
    t.update(finish([entry('app.open_url',opened())],scope='window',effects=['open'],result='internal Worker material'))
    t.update(task_id=tid,worker_id='worker-1')
    tasks[tid]=t
    monkeypatch.setattr(task_routes,'get_current_user',lambda request:owner)
    try:
        response=asyncio.run(task_routes.api_get_task(tid,None))['task']
        assert response['answer']==response['conversational_response']
        assert 'Открытие подтверждено' in response['answer'] and 'internal Worker material' not in response['answer']
        assert t['answer']=='internal Worker material'
    finally:tasks.pop(tid,None)


def test_existing_file_coverage_call_receives_optional_advice_separately():
    from server.answer_auditor import assess_result_coverage
    calls=[]
    steps=[entry('office_docx',{'status':'success','action':'inspect','items':[{'text':'requested content'}]})]
    payload=validate_answer_text_payload({'result':'Read-back matches','unresolved':[],
        'remaining':['Можно добавить иллюстрации']},steps)
    async def completion(**kwargs):
        calls.append(kwargs)
        material=json.loads(kwargs['messages'][-1]['content'])
        assert material['remaining']==[] and material['optional_recommendations']==['Можно добавить иллюстрации']
        return {'choices':[{'message':{'content':json.dumps({'coverage':'complete','evidence_refs':['step_1']})}}]}
    result=asyncio.run(assess_result_coverage(client=None,cfg={'model':'mock','answer_auditor_enabled':True},
        chat_completion_request_fn=completion,user_request='Create document',current_run_journal=steps,answer_payload=payload))
    assert result['coverage']=='complete' and len(calls)==1 and calls[0]['tools'] is None


def test_flat_server_imports_render_compact_result_without_provider(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    source='''
from orchestrator import present_worker_result
from run_journal import validate_answer_text_payload, append_answer_step
from worker_reports import build_worker_report
steps=[{'step_id':'step_1','tool_name':'window.control','target_device_id':'pc','result':{'status':'success','completion_state':'success','action':'minimize','summary':'Window minimized'}}]
payload=validate_answer_text_payload({'result':'Findings','unresolved':[]},steps)
append_answer_step(steps,'answer_text',payload)
task={'task_id':'flat','status':'done','device_ids':['pc'],'execution_scope':'window','window_effects':['minimize'],'commands':steps,'answer':'Findings'}
report=build_worker_report(task)
assert report['goal_completed'] and report['evidence_refs']==['step_1']
assert present_worker_result(task,report)['conversational_response']=='Window minimized'
print('FLAT_REPLY_OK')
'''
    env={**os.environ,'IRU_DB_PATH':str(tmp_path/'flat.sqlite'),'PYTHONIOENCODING':'utf-8'}
    result=subprocess.run([sys.executable,'-c',source],cwd=Path(__file__).parents[1]/'server',
        env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode==0,result.stderr
    assert 'FLAT_REPLY_OK' in result.stdout


def test_goal_preserves_existing_file_content_verification_scope():
    artifact={'path':'C:/report.docx','device_id':'pc','verified':True,'content_verified':False,
        'verification_scope':'file_identity_and_format'}
    goal={'goal_id':'g','status':'partial','terminal':True,'goal_completed':False,'tasks':[
        {'task_id':'t','status':'partial','expected_artifacts':['report.docx'],
         'report':{'summary':'Содержание не подтверждено','artifacts':[artifact]}}]}
    projected=goal_presentation(goal)
    assert projected['result_artifacts'][0]['content_verified'] is False
    assert projected['result_artifacts'][0]['verification_scope']=='file_identity_and_format'


def test_server_only_worker_uses_existing_coverage_with_compact_contract(monkeypatch):
    from server import controller, controller_onboarding as onboarding, answer_auditor
    calls=[]
    async def completion(**kwargs):
        calls.append(kwargs)
        if kwargs['tools'] is None:
            return _message(content=json.dumps({'coverage':'complete','evidence_refs':['step_1'],'reason':'Requested source found'}),finish_reason='stop')
        assert kwargs['tools'] and 'answer_text(result=' in kwargs['messages'][0]['content']
        return (_message(tool_calls=[_tool_call('search','web_search',{'query':'service information'})])
            if len(calls)==1 else _message(tool_calls=[_tool_call('finish','answer_text',
                {'result':'В источнике описано состояние сервиса.','unresolved':[],'remaining':['Можно расширить поиск']})]))
    async def search(*args,**kwargs):
        return {'status':'success','results':[{'title':'Service','url':'https://example.test','snippet':'service active'}]}
    monkeypatch.setattr(controller,'_chat_completion_request',completion)
    monkeypatch.setattr(onboarding,'run_web_search',search)
    tasks['server-compact']={'task_id':'server-compact','execution_scope':'analysis','device_ids':[],
        'original_request':'Проверь сведения о сервисе'}
    try:
        result=asyncio.run(onboarding.process_onboarding_message('Проверь сведения о сервисе',
            usage_context={'poll_task_id':'server-compact','worker_execution':True},
            load_llm_config_fn=lambda:{'model':'mock','answer_auditor_enabled':True},
            current_datetime_msk_fn=lambda:'2026-10-27 MSK'))
        assert len(calls)==3 and result['commands'][-1]['result']['_result_contract']==3
        t={**tasks['server-compact'],'status':'done','commands':result['commands'],'answer':result['answer']}
        assert build_worker_report(t)['goal_completed']
    finally:tasks.pop('server-compact',None)
