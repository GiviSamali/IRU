"""Connected MVP: real controllers, scheduler, SQLite and native-window adapter logic.
Only Windows APIs, LLM and TTS are emulated; no user windows are modified.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from server import task_runtime as runtime, orchestrator as orch
from server.runtime_state import devices, tasks
from server.worker_scheduler import WorkerScheduler, owned_job, restore_task
from server.routers import tasks as routes
from test_integrity_lifecycle import world, admit, install, terminal
from test_window_control import FakeAdapter, window, wc
from test_orchestrator_worker import success, used_commands


class WindowAgent:
    def __init__(self, key):
        self.key=key
        self.adapter=FakeAdapter([window(101,42,'First — Notepad','notepad.exe'),
                                  window(202,43,'Second — Notepad','notepad.exe')])
        self.control=wc.WindowControl(self.adapter)
        self.calls=[]
        self.lose_action_result=False
        self.wrong_receipt=False

    async def send_text(self, wire):
        payload=json.loads(wire)['payload']
        assert payload['action']=='window.control'
        self.calls.append(dict(payload['params']))
        result=self.control.run(**payload['params'])
        if self.wrong_receipt and payload['params']['action']=='activate' and payload['params'].get('window_id'):
            result['window']['window_id']='0'*32
        future=devices[self.key]['pending'].pop(payload['id'])
        if self.lose_action_result and payload['params']['action']=='activate' and payload['params'].get('window_id'):
            future.set_exception(RuntimeError('response lost after action'))
        else:
            future.set_result(result)


async def ready(world, scheduler):
    _,owner,key,monkeypatch=world
    args={'action':'activate','target':'Блокнот'}
    install(world,[('window_control',args,{})],terminal('unused'),201)
    agent=WindowAgent(key)
    devices[key]={'user_id':owner['id'],'ws':agent,'info':{'hostname':'pc','os':'Windows'},'pending':{}}
    monkeypatch.setattr(orch,'scheduler',scheduler)
    monkeypatch.setattr(routes,'scheduler',scheduler)
    task=await admit(world,scheduler,'Переключись на Блокнот','window-input')
    for _ in range(200):
        job=owned_job(task['task_id'],owner['id'])
        if job['state']=='waiting_input':
            return task,agent
        await asyncio.sleep(.01)
    raise AssertionError((task,agent.calls))


async def reply(world, task, request_id='answer-choice', **changes):
    _,owner,_,_=world
    cmd=SimpleNamespace(message='Второе',request_id=request_id,device_id='pc',modes={},broadcast=False,
                        input_task_id=task['task_id'],input_question_id=task['input_required']['question_id'])
    for key,value in changes.items():setattr(cmd,key,value)
    async def no_new_worker(*args,**kwargs):raise AssertionError('No new Worker admission')
    return await orch.run_turn(cmd,owner,owner['chat_id'],no_new_worker)


def test_connected_question_answer_continues_same_worker_and_voice(world):
    client,owner,_,_=world
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task,agent=await ready(world,scheduler)
            event=task['input_required']; original=task['original_request']
            assert event['type']=='input_required' and event['phase']=='waiting'
            assert event['presented_by']=='orchestrator'
            assert 118 <= event['expires_at']-time.time() <= 120
            assert agent.adapter.calls==[]
            headers={'X-Token':owner['token']}
            shown=client.get('/api/tasks/'+task['task_id'],headers=headers).json()['task']
            assert shown['presentation_status']=='waiting_input'
            assert shown['conversational_response']==event['question']
            assert shown['input_required']['question_id']==event['question_id']
            history=client.get(f"/api/chats/{owner['chat_id']}/messages",headers=headers).json()['messages']
            saved=next(row for row in history if row.get('_taskId')==task['task_id'])
            assert saved['inputRequired']['question_id']==event['question_id']
            assert client.post('/api/voice/tasks/'+task['task_id']+'/speech?question='+event['question_id'],headers=headers).status_code==200
            quota=used_commands(owner)
            refused=client.post('/nl_command',headers=headers,json={
                'orchestrate':False,'device_id':'pc','chat_id':owner['chat_id'],'message':'Второе',
                'input_task_id':task['task_id'],'input_question_id':event['question_id'],
            })
            assert refused.status_code==422 and used_commands(owner)==quota
            assert task['input_required']['phase']=='waiting' and agent.adapter.calls==[]
            result=client.post('/nl_command',headers=headers,json={
                'orchestrate':True,'device_id':'pc','chat_id':owner['chat_id'],'message':'Второе',
                'request_id':'answer-choice','input_task_id':task['task_id'],'input_question_id':event['question_id'],
            }).json()
            assert used_commands(owner)==quota
            assert result['status']=='ok' and result['worker_task_id']==task['task_id']
            assert result['answer']=='Продолжаю исходную задачу.'
            await scheduler.runners[owner['id']]
            assert task['original_request']==original and task['input_required']['phase']=='answered'
            assert agent.adapter.calls==[(202,'activate',None)]
            assert [item['action'] for item in agent.calls]==['activate','find','activate']
            assert task['worker_report']['status']=='success'
            restored=restore_task(owned_job(task['task_id'],owner['id']))
            assert restored['commands']==task['commands'] and restored['input_required']==task['input_required']
            assert client.post('/api/voice/tasks/'+task['task_id']+'/speech?question='+event['question_id'],headers=headers).status_code==409
            assert await reply(world,task)==result  # retry of the same HTTP request is cached
            assert (await reply(world,task,'late-reply'))['worker_task_id'] is None
            assert agent.adapter.calls==[(202,'activate',None)]
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('problem',['invalid_choice','wrong_question','foreign_user','foreign_chat','cancel','timeout','monotonic_timeout','stale','stale_identity','lost_result','wrong_receipt','restart','two_answers'])
def test_input_boundary_never_replays_or_selects_another_window(world,problem):
    _,owner,_,_=world
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task,agent=await ready(world,scheduler)
            event=task['input_required']
            if problem in {'invalid_choice','wrong_question','foreign_user','foreign_chat'}:
                user=owner['id']+1 if problem=='foreign_user' else owner['id']
                chat=owner['chat_id']+1 if problem=='foreign_chat' else owner['chat_id']
                qid='0'*32 if problem=='wrong_question' else event['question_id']
                with pytest.raises(ValueError):
                    await scheduler.answer_window_input(user,chat,task['task_id'],qid,'3' if problem=='invalid_choice' else 'Второе')
                assert agent.adapter.calls==[] and event['phase']=='waiting'
                await scheduler.cancel(task['task_id'],owner['id'])
            elif problem=='cancel':
                await scheduler.cancel(task['task_id'],owner['id'])
            elif problem=='timeout':
                event['expires_at']=time.time()-1
            elif problem=='monotonic_timeout':
                task['_window_input_deadline']=time.monotonic()-1
            elif problem=='restart':
                await scheduler.shutdown()
                assert task['status']=='unknown'
                assert restore_task(owned_job(task['task_id'],owner['id']))['input_required']['question_id']==event['question_id']
                with pytest.raises(ValueError):
                    await scheduler.answer_window_input(owner['id'],owner['chat_id'],task['task_id'],event['question_id'],'2')
                return
            else:
                if problem=='stale':agent.adapter.rows.pop()
                if problem=='stale_identity':agent.adapter.rows[1]['class_name']='DifferentWindow'
                if problem=='lost_result':agent.lose_action_result=True
                if problem=='wrong_receipt':agent.wrong_receipt=True
                if problem=='two_answers':
                    results=await asyncio.gather(*(scheduler.answer_window_input(owner['id'],owner['chat_id'],task['task_id'],event['question_id'],'2') for _ in range(2)),return_exceptions=True)
                    assert sum(isinstance(value,dict) for value in results)==1
                    assert sum(isinstance(value,ValueError) for value in results)==1
                else:await reply(world,task)
            await scheduler.runners[owner['id']]
            expected=1 if problem in {'lost_result','wrong_receipt','two_answers'} else 0
            assert len(agent.adapter.calls)==expected
            if expected:assert agent.adapter.calls==[(202,'activate',None)]
            if problem in {'stale','stale_identity','lost_result','wrong_receipt'}:assert task['status']=='unknown'
            if problem in {'timeout','monotonic_timeout'}:assert task['worker_error_code']=='input_expired'
            with pytest.raises(ValueError):
                await scheduler.answer_window_input(owner['id'],owner['chat_id'],task['task_id'],event['question_id'],'2')
            assert len(agent.adapter.calls)==expected
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('resolve',['answer','timeout','cancel'])
def test_independent_task_queues_and_advances_without_new_reply_quota(world,resolve):
    _,owner,key,_=world
    executed=[]
    async def execute(task):
        executed.append(task['task_id'])
        if task['task_id']=='window-input':
            await runtime.run_nl_task(task['task_id'],task['user_id'],task['message'],task['device_ids'],task['chat_id'])
        else:success(task)
    async def scenario():
        scheduler=WorkerScheduler(execute)
        try:
            first,agent=await ready(world,scheduler)
            second=await scheduler.submit({'task_id':'independent','user_id':owner['id'],'chat_id':owner['chat_id'],
                'message':'Другой независимый запрос','device_ids':[key],'modes':{},'status':'running','kind':'worker','created_at':time.time()})
            assert second['status']=='queued' and executed==['window-input']
            before=owned_job(first['task_id'],owner['id'])['created_at']
            quota=used_commands(owner)
            if resolve=='answer':await reply(world,first)
            elif resolve=='timeout':first['input_required']['expires_at']=time.time()-1
            else:await scheduler.cancel(first['task_id'],owner['id'])
            await scheduler.runners[owner['id']]
            assert used_commands(owner)==quota
            assert executed==['window-input','independent']
            assert owned_job(first['task_id'],owner['id'])['created_at']==before
            assert owned_job(second['task_id'],owner['id'])['state']=='success'
            assert len(agent.adapter.calls)==(1 if resolve=='answer' else 0)
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


def test_input_descriptor_rejects_completed_or_unknown_prior_side_effects():
    from server.window_policy import window_input_required
    result={'status':'ambiguous','candidates':[
        {'window_id':'a'*32,'pid':42,'process_name':'notepad.exe','class_name':'App'},
        {'window_id':'b'*32,'pid':43,'process_name':'notepad.exe','class_name':'App'},
    ]}
    latest={'tool_name':'window.control','step_id':'step_2','result':result}
    for status in ('success','unknown'):
        previous={'tool_name':'window.control','step_id':'step_1','result':{'status':status,'action':'activate'}}
        assert window_input_required({'action':'activate'},result,[previous,latest],'pc') is None
    assert window_input_required({'action':'close'},result,[latest],'pc') is None


def test_switch_wording_is_one_original_window_action_not_a_generated_scope():
    from server.window_policy import ordinary_window_request, window_action_sequence
    assert ordinary_window_request('ИРУ, переключись на Блокнот')
    assert window_action_sequence('ИРУ, переключись на Блокнот')==['activate']
    assert not ordinary_window_request('Переключись на Блокнот и удали файлы')


def test_restart_recovers_saved_question_as_unknown_without_execution(world):
    _,owner,_,monkeypatch=world
    async def scenario():
        scheduler=WorkerScheduler()
        recovered=WorkerScheduler()
        try:
            task,agent=await ready(world,scheduler)
            saved=owned_job(task['task_id'],owner['id'])
            qid=task['input_required']['question_id']
            await scheduler.shutdown()
            # Restore the last durable snapshot as after an abrupt process loss.
            from server import database as db
            with db.get_db() as connection:
                connection.execute("UPDATE worker_jobs SET state=?,payload=?,report=? WHERE task_id=?",
                                   ('waiting_input',saved['payload'],saved['report'],task['task_id']))
            tasks.pop(task['task_id'])
            monkeypatch.setattr(recovered,'acquire_instance',lambda:True)
            await recovered.recover()
            restored=restore_task(owned_job(task['task_id'],owner['id']))
            assert restored['status']=='unknown'
            assert restored['input_required']['question_id']==qid
            assert restored['input_required']['phase']=='interrupted'
            assert len(restored['input_required']['options'])==2
            with pytest.raises(ValueError):
                await recovered.answer_window_input(owner['id'],owner['chat_id'],task['task_id'],qid,'2')
            assert agent.adapter.calls==[]
        finally:
            await scheduler.shutdown();await recovered.shutdown()
    asyncio.run(scenario())
