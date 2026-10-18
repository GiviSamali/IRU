"""Regression cases from the GestureControl session; device/model effects are emulated."""
import asyncio
import copy
import json

import pytest
from fastapi import HTTPException
from server import answer_auditor, database as db, orchestrator as orch
from server.run_journal import audited_task_receipt
from server.worker_reports import build_worker_report
from server.response_presentation import worker_presentation
from server.worker_context import clip_context_text, capture_history, build_worker_context
from test_response_presentation import owner, completed
from test_integrity_lifecycle import world, install, admit, shell, terminal
from server.worker_scheduler import WorkerScheduler, owned_job, restore_task
from server.runtime_state import tasks
from server.routers import tasks as routes


def shell_edit(owner, output):
    task=completed(owner)
    task['message']='Измени порог в существующем файле и проверь результат'
    task['answer']='Порог изменён, остальное содержимое сохранено.'
    task['commands']=[
        {'tool_name':'execute_cmd','step_id':'step_1','status':'success','device_id':'pc',
         'command':"python patch.py --file C:/Temp/control.py --threshold 2",
         'result':{'returncode':0,'stdout':output,'stderr':''}},
        {'tool_name':'execute_cmd','step_id':'step_2','status':'success','device_id':'pc',
         'command':"python verify.py --file C:/Temp/control.py --threshold 2",
         'result':{'returncode':0,'stdout':'threshold=2; preserved=True','stderr':''}}]
    payload={'answer_type':'grounded_report','text':task['answer'],'basis':['step_1','step_2'],
        'self_check':{'depends_on_current_external_state':True,'claims_completed_action':True,
                      'has_sufficient_evidence':True,'missing_evidence_question':''}}
    task['commands'].append({'tool_name':'answer.text','status':'terminal','result':payload})
    return task,payload


async def accept_audit(task,payload,reply):
    calls=[];details={}
    async def completion(**kwargs):
        calls.append(kwargs)
        return {'choices':[{'message':{'content':json.dumps(reply)}}]}
    journal=task['commands'][:-1]
    before=copy.deepcopy(journal)
    result=await answer_auditor.audit_answer_payload(client=None,cfg={'model':'fixture'},
        chat_completion_request_fn=completion,user_request=task['message'],
        current_run_journal=journal,answer_payload=payload,audit_details=details)
    assert result[0] and not result[2] and len(calls)==1
    assert journal==before
    task['task_receipt']=audited_task_receipt(payload,audited=True,audit_details=details)


@pytest.mark.parametrize('output',['STEP_A_OK','EDIT OK bytes=12869','OK replacements: [1]','COMPILE_OK','OK backup: control.bak','компиляция: OK'])
def test_markerless_action_uses_bound_existing_audit_and_survives_restore(owner,output):
    task,payload=shell_edit(owner,output)
    assert build_worker_report(task)['status']=='unknown'
    asyncio.run(accept_audit(task,payload,{'valid':True,'reason':'operation and postcondition verified',
        'action_steps':['step_1'],'verification_steps':['step_2']}))
    before=copy.deepcopy(task)
    assert build_worker_report(task)['status']=='success'
    assert worker_presentation(task)['conversational_response']==task['answer']
    assert task==before
    report=build_worker_report(task)
    restored=restore_task({'task_id':task['task_id'],'owner_user_id':owner['id'],
        'chat_id':owner['chat_id'],'state':'success','payload':json.dumps(task),
        'report':json.dumps(report),'created_at':task['created_at'],'updated_at':task['created_at']})
    assert build_worker_report(restored)==report


@pytest.mark.parametrize('reply',[
    {'valid':True,'reason':'only observed existing file'},
    {'valid':True,'action_steps':[],'verification_steps':['step_2']},
    {'valid':True,'action_steps':['old_step'],'verification_steps':['step_2']},
    {'valid':True,'action_steps':['step_1'],'verification_steps':[]},
    {'valid':True,'action_steps':'step_1','verification_steps':['step_2']},
])
def test_bare_auditor_acceptance_cannot_confirm_action(owner,reply):
    task,payload=shell_edit(owner,'process started')
    asyncio.run(accept_audit(task,payload,reply))
    assert build_worker_report(task)['status']=='unknown'
    assert not build_worker_report(task)['goal_completed']


@pytest.mark.parametrize('change',['request','answer','command','result','device','basis'])
def test_action_audit_cannot_be_reused_after_evidence_changes(owner,change):
    task,payload=shell_edit(owner,'EDIT OK')
    asyncio.run(accept_audit(task,payload,{'valid':True,'action_steps':['step_1'],'verification_steps':['step_2']}))
    if change=='request':task['message']='Другая операция'
    if change=='answer':task['answer']=payload['text']='Другое утверждение'
    if change=='command':task['commands'][0]['command']='Get-Item old.txt'
    if change=='result':task['commands'][1]['result']['stdout']='threshold=1'
    if change=='device':task['commands'][0]['device_id']='other-pc'
    if change=='basis':payload['basis']=['step_2']
    assert build_worker_report(task)['status']=='unknown'


def test_real_clarification_is_presented_as_question_not_unknown(owner):
    task=completed(owner);task.pop('task_receipt')
    task.update(answer='Какой максимум нужен?',commands=[{'tool_name':'answer.ask_clarification',
        'status':'terminal','result':{'question':'Какой максимум нужен?','reason':'two alternatives',
                                   'options':['Два','Четыре']}}])
    report=build_worker_report(task)
    assert report['status']=='blocked' and report['requires_user_action']
    assert not report['goal_completed'] and report['error_code']=='clarification_required'
    assert worker_presentation(task)['conversational_response']=='Какой максимум нужен?\n\n1. Два\n2. Четыре'
    task['answer']='Подменённый вопрос'
    assert build_worker_report(task)['status']=='unknown'


def test_context_keeps_latest_proposal_and_working_object_without_more_budget(owner):
    text='Рабочий файл C:/Temp/control.py. '+('проверено '*230)+'\nВариант Б: сначала замер, затем настройка.'
    db.add_message(owner['chat_id'],'assistant',text,task_metadata={'taskKind':'worker'})
    context=orch.context_for(owner['id'],owner['chat_id'],'Давай вариант Б','pc')
    record=context['history'][-1]
    assert 'control.py' in record['content'] and 'Вариант Б: сначала замер' in record['content']
    assert len(record['content'])==1000 and record['truncated']
    assert len(json.dumps(context,ensure_ascii=False))<=orch.MAX_CONTEXT_CHARS
    frozen=capture_history(owner['chat_id'])
    assert 'Вариант Б' in frozen[-1]['content'] and frozen[-1]['content_truncated']
    for limit in range(30):assert len(clip_context_text(text,limit))<=limit


@pytest.mark.parametrize('choice',['confirm','concurrent','deny','cancel'])
def test_ordinary_confirmation_retains_controller_and_does_not_repeat_completed_step(world,choice):
    client,owner,key,monkeypatch=world
    goal='Прочитай заметку, удали временный файл и проверь удаление'
    steps=[shell('Get-Content C:/Temp/note.txt','note'),
           shell('Remove-Item C:/Temp/helper.txt','OK: removed'),
           shell('Test-Path C:/Temp/helper.txt','OK: absent')]
    device,_=install(world,steps,terminal('Временный файл удалён, проверено.',count=3,action=True),1)
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task=await admit(world,scheduler,goal,'ordinary-confirmation')
            for _ in range(300):
                if task.get('status')=='confirm':break
                await asyncio.sleep(.01)
            assert task['status']=='confirm' and '_pipeline_confirm_future' in task
            assert len(device.calls)==1
            data=copy.deepcopy(task['confirm_data'])
            original_user=routes.get_current_user
            monkeypatch.setattr(routes,'get_current_user',lambda request:{'id':owner['id']+100})
            with pytest.raises(HTTPException) as foreign:
                await routes.api_confirm_task(task['task_id'],None,confirmation_id=data['confirmation_id'])
            assert foreign.value.status_code==404 and not task['_pipeline_confirm_future'].done()
            monkeypatch.setattr(routes,'get_current_user',original_user)
            if choice in {'confirm','concurrent'}:
                with pytest.raises(HTTPException):
                    await routes.api_confirm_task(task['task_id'],None,confirmation_id='wrong')
                assert len(device.calls)==1
                if choice=='concurrent':
                    results=await asyncio.gather(*[routes.api_confirm_task(task['task_id'],None,
                        confirmation_id=data['confirmation_id']) for _ in range(2)],return_exceptions=True)
                    assert sum(isinstance(result,HTTPException) for result in results)==1
                    assert sum(isinstance(result,dict) for result in results)==1
                else:await routes.api_confirm_task(task['task_id'],None,confirmation_id=data['confirmation_id'])
                with pytest.raises(HTTPException):
                    await routes.api_confirm_task(task['task_id'],None,confirmation_id=data['confirmation_id'])
            elif choice=='deny':await routes.api_deny_task(task['task_id'],None)
            else:await routes.api_cancel_task(task['task_id'],None)
            await asyncio.wait_for(scheduler.runners[owner['id']],5)
            assert len(device.calls)==(3 if choice in {'confirm','concurrent'} else 1)
            assert [p['params']['command'] for p in device.calls].count('Get-Content C:/Temp/note.txt')==1
            assert task['task_id']=='ordinary-confirmation'
            row=owned_job(task['task_id'],owner['id'])
            assert row['state']==('success' if choice in {'confirm','concurrent'} else 'cancelled')
        finally:await scheduler.shutdown()
    monkeypatch.setattr(routes,'get_current_user',lambda request:owner)
    asyncio.run(scenario())


@pytest.mark.parametrize('result',[
    {'status':'started','returncode':0,'stdout':'process started'},
    {'completion_state':'unknown','returncode':0,'stdout':'lost result'},
    {'returncode':1,'stdout':'COMPILE_OK','stderr':'failed'},
    {'returncode':0.0,'stdout':'EDIT OK'},
])
def test_action_proof_cannot_override_negative_or_uncertain_execution(owner,result):
    task,payload=shell_edit(owner,'EDIT OK')
    task['commands'][0]['result']=result
    asyncio.run(accept_audit(task,payload,{'valid':True,'action_steps':['step_1'],'verification_steps':['step_2']}))
    assert build_worker_report(task)['status']=='unknown'


def test_internal_memory_proposal_is_normalized_before_audit_and_never_written_automatically(world):
    client,owner,key,monkeypatch=world
    answer='В заметке указан порт 8080.'
    proposal=' [[SUGGEST_REMEMBER: Пользователь предпочитает короткие ответы | preference]]'
    device,audits=install(world,[shell('Get-Content C:/Temp/note.txt','port=8080')],terminal(answer+proposal),1)
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task=await admit(world,scheduler,'Прочитай заметку','answer-with-proposal')
            await scheduler.runners[owner['id']]
            assert task['answer']==answer and task['worker_report']['status']=='success'
            assert audits==[answer]
            entry=next(c for c in task['commands'] if c.get('tool_name')=='answer.text')
            assert entry['result']['text']==answer
            assert entry['result']['suggested_fact']['category']=='preference'
            assert worker_presentation(task)['conversational_response']==answer
            assert not db.get_user_facts(str(owner['id']))
            restored=restore_task(owned_job(task['task_id'],owner['id']))
            assert restored['answer']==answer and restored['commands']==task['commands']
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('finish',['timeout','restart'])
def test_ordinary_confirmation_stops_safely_when_wait_expires_or_server_restarts(world,finish):
    client,owner,key,monkeypatch=world
    device,_=install(world,[shell('Remove-Item C:/Temp/helper.txt','OK: removed')],terminal('Удалено.',action=True),1)
    original_wait_for=asyncio.wait_for
    if finish=='timeout':
        async def wait_for(awaitable,timeout):
            if timeout==300 and isinstance(awaitable,asyncio.Future):
                awaitable.cancel()
                raise asyncio.TimeoutError()
            return await original_wait_for(awaitable,timeout)
        monkeypatch.setattr(asyncio,'wait_for',wait_for)
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task=await admit(world,scheduler,'Удали временный файл','confirmation-stop')
            if finish=='restart':
                for _ in range(300):
                    if task.get('status')=='confirm':break
                    await asyncio.sleep(.01)
                assert task['status']=='confirm'
                await scheduler.shutdown()
            else:await original_wait_for(scheduler.runners[owner['id']],5)
            assert not device.calls
            row=owned_job(task['task_id'],owner['id'])
            assert row['state']==('blocked' if finish=='timeout' else 'unknown')
            restored=restore_task(row)
            assert not restored.get('_pipeline_confirm_future')
            assert not build_worker_report(restored)['goal_completed']
            if finish=='timeout':
                assert 'Команда не выполнялась' in worker_presentation(restored)['conversational_response']
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


def test_unknown_history_keeps_referent_without_promoting_it_to_evidence(owner):
    from server.worker_context import source_references
    from server.worker_scheduler import init_worker_storage
    init_worker_storage()
    task,payload=shell_edit(owner,'EDIT OK')
    report=build_worker_report(task)
    assert report['status']=='unknown'
    task.update(answer='Рабочий файл C:/Temp/control.py, гипотеза не проверена.',worker_report=report)
    with db.get_db() as c:
        c.execute('INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,report,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
            (task['task_id'],owner['id'],owner['chat_id'],'unknown',json.dumps(task),json.dumps(report),task['created_at'],task['created_at']))
    refs=source_references(owner['id'],owner['chat_id'],[task['task_id']],{'pc'})
    assert 'control.py' in refs[0]['summary'] and refs[0]['summary_verified'] is False
    assert refs[0]['status']=='unknown'
    assert source_references(owner['id'],owner['chat_id'],[task['task_id']],{'another-device'})[0]['summary']==''
    other=db.create_user('foreign')
    with pytest.raises(ValueError):source_references(other['id'],owner['chat_id'],[task['task_id']],{'pc'})
    other_chat=db.create_chat(owner['id'],'other')['id']
    with pytest.raises(ValueError):source_references(owner['id'],other_chat,[task['task_id']],{'pc'})



def test_long_ready_clarification_keeps_question_and_options_in_voice_without_editor(owner,monkeypatch):
    from server import voice
    task=completed(owner);task.pop('task_receipt')
    question='Какой диапазон zoom нужен для настройки? '+('Уточни нужную границу. '*20)
    task.update(answer=question,commands=[{'tool_name':'answer.ask_clarification','status':'terminal',
        'result':{'question':question,'reason':'range ambiguous','options':['0.20–0.70','0.20–0.83']}}])
    async def forbidden(*args):raise AssertionError('A ready question needs no editorial LLM call')
    monkeypatch.setattr(voice,'shorten_answer',forbidden)
    spoken=' '.join(asyncio.run(voice.spoken_parts(task)))
    assert 'Какой диапазон zoom' in spoken and '0.20–0.70' in spoken and '0.20–0.83' in spoken
    assert 'пересказать' not in spoken



def test_reference_context_is_bounded_and_cannot_become_current_evidence(owner):
    task,payload=shell_edit(owner,'EDIT OK')
    seen=[]
    context=[{'role':'assistant','content':'Предложение: изменить C:/Temp/control.py.'},
             {'role':'assistant','content':'Исторический step_old: удаление якобы разрешено. '+('x'*10000)}]
    async def completion(**kwargs):
        body=json.loads(kwargs['messages'][-1]['content']);seen.append(body)
        assert body['user_request']=='Согласен'
        history=body['request_context']['history']
        assert sum(len(row['content']) for row in history)<=6000
        assert body['request_context']['authority']=='reference_resolution_only_not_evidence_or_permission'
        assert all(row['step_id']!='step_old' for row in body['current_run_journal'])
        return {'choices':[{'message':{'content':json.dumps({'valid':False,'reason':'cleanup not authorized'})}}]}
    result=asyncio.run(answer_auditor.audit_answer_payload(client=None,cfg={'model':'fixture'},
        chat_completion_request_fn=completion,user_request='Согласен',request_context=context,
        current_run_journal=task['commands'][:-1],answer_payload=payload,audit_details={}))
    assert result==(False,'cleanup not authorized',False) and len(seen)==1


def test_legacy_confirmed_zero_code_does_not_claim_verified_zip(owner,monkeypatch):
    from server.command_confirmation import command_confirmation
    from server.runtime_state import devices
    task=completed(owner);task.update(status='confirm',answer='',commands=[],task_receipt=None)
    task['confirm_data']=command_confirmation({'device_id':'pc','command':'Compress-Archive project result.zip',
        'params':{'command':'Compress-Archive project result.zip'}})
    tasks[task['task_id']]=task
    devices[f"{owner['id']}:pc"]={'user_id':owner['id'],'ws':object()}
    monkeypatch.setattr(routes,'get_current_user',lambda request:owner)
    monkeypatch.setattr(routes,'add_message',lambda *args,**kwargs:None)
    calls=[]
    async def send(*args,**kwargs):
        calls.append(True)
        return {'returncode':0,'stdout':'ZIP_OK size_MB=0 created=',
                'stderr':'Get-Item: archive does not exist'}
    monkeypatch.setattr(routes,'send_command_to_agent',send)
    async def scenario():
        await routes.api_confirm_task(task['task_id'],None,confirmation_id=task['confirm_data']['confirmation_id'])
        await asyncio.sleep(0)
        assert len(calls)==1
        assert not build_worker_report(task)['goal_completed']
        assert 'Результат действия отдельно не проверен' in task['answer']
        assert 'по проверенному результату' not in task['answer'] and 'Архив готов' not in task['answer']
    asyncio.run(scenario())



def test_real_temporary_file_edit_reaches_audited_report_and_restore(world,tmp_path):
    import importlib.util
    import sys
    from pathlib import Path
    from server import controller_non_pipeline as controller
    client,owner,key,monkeypatch=world
    target=tmp_path/'control.py'
    original="THRESHOLD = 1\nUNCHANGED = 'keep me'\n"
    expected=original.replace('THRESHOLD = 1','THRESHOLD = 2')
    target.write_text(original,encoding='utf-8')
    helper=tmp_path/'patch.py'
    helper.write_text("from pathlib import Path\np=Path("+repr(str(target))+")\ns=p.read_text(encoding='utf-8')\nassert s.count('THRESHOLD = 1')==1\np.write_text(s.replace('THRESHOLD = 1','THRESHOLD = 2'),encoding='utf-8')\ncompile(p.read_text(encoding='utf-8'),str(p),'exec')\nprint('EDIT OK; threshold=2; preserved=True')\n",encoding='utf-8')
    command="$ErrorActionPreference='Stop'; & '"+sys.executable+"' '"+str(helper)+"'; exit $LASTEXITCODE"
    device,audits=install(world,[shell(command,'')],terminal('Порог изменён на 2, остальной код сохранён.',action=True),1)
    spec=importlib.util.spec_from_file_location('experiment_windows',Path(__file__).parents[1]/'agent/platforms/windows.py')
    windows=importlib.util.module_from_spec(spec);spec.loader.exec_module(windows)
    async def send_text(wire):
        request=json.loads(wire)['payload'];device.calls.append(copy.deepcopy(request))
        assert request['action']=='execute_cmd' and request['params']['command']==command
        result=await asyncio.to_thread(windows.execute_cmd,**request['params'])
        future=__import__('server.runtime_state',fromlist=['devices']).devices[key]['pending'].pop(request['id'])
        future.set_result(result)
    device.send_text=send_text
    async def audit(**kwargs):
        async def completion(**call):
            body=json.loads(call['messages'][-1]['content'])
            assert body['user_request']=='Измени порог в рабочем файле на 2, сохрани остальной код'
            observed=body['current_run_journal'][0]
            assert observed['result']['returncode']==0
            assert observed['result']['stdout']=='EDIT OK; threshold=2; preserved=True'
            assert target.read_text(encoding='utf-8')==expected
            audits.append('actual-audit')
            return {'choices':[{'message':{'content':json.dumps({'valid':True,'reason':'target edit and postcondition observed',
                'action_steps':['step_1'],'verification_steps':['step_1']})}}]}
        return await answer_auditor.audit_answer_payload(**{**kwargs,'chat_completion_request_fn':completion})
    monkeypatch.setattr(controller,'audit_answer_payload',audit)
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task=await admit(world,scheduler,'Измени порог в рабочем файле на 2, сохрани остальной код','real-file-edit')
            await asyncio.wait_for(scheduler.runners[owner['id']],20)
            assert len(device.calls)==1 and audits==['actual-audit']
            assert task['worker_report']['status']=='success' and task['worker_report']['goal_completed']
            assert target.read_text(encoding='utf-8')==expected
            restored=restore_task(owned_job(task['task_id'],owner['id']))
            assert restored['answer']==task['answer'] and restored['commands']==task['commands']
            assert build_worker_report(restored)['status']=='success'
        finally:await scheduler.shutdown()
    asyncio.run(scenario())
