"""P0-02: fake agent, real confirm route/transport; no OS effects or paid LLM."""
import asyncio
import json
import time
from dataclasses import dataclass
import pytest
from fastapi import HTTPException
from server import database as db, task_runtime as runtime
from server.runtime_state import devices,tasks,TASK_TTL,request_task_cancel
from server.routers import tasks as routes
from server.command_confirmation import command_confirmation,confirmed_command_outcome


class FakeAgent:
    def __init__(self,key,result):
        self.key=key;self.result=result;self.calls=[];self.transport_error=None
    async def send_text(self,wire):
        payload=json.loads(wire)['payload'];self.calls.append(payload)
        pending=devices[self.key]['pending'].pop(payload['id'])
        if self.transport_error:
            pending.set_exception(RuntimeError(self.transport_error))
        else:pending.set_result(self.result)


@dataclass
class Scenario:
    client: object
    headers: dict
    user: dict
    task: dict
    confirmation: dict
    agent: FakeAgent
    key: str
    tid: str='p0-confirm'


@pytest.fixture
def scenario(client,monkeypatch):
    user=db.create_user('p0-confirm-user');chat=db.create_chat(user['id'])
    db.upsert_device_profile('pc',user['id'],{'os':'Windows','hostname':'pc','username':'tester'})
    headers={'X-Token':user['token']};key=f"{user['id']}:pc"
    agent=FakeAgent(key,{'returncode':0,'stdout':'OK: effect_verified','stderr':''})
    devices[key]={'user_id':user['id'],'ws':agent,'info':{'os':'Windows'},'pending':{}}
    data=command_confirmation({'command':'Remove-Item demo.txt','device_id':'pc',
                               'params':{'command':'Remove-Item demo.txt'},'chat_id':chat['id'],'user_id':user['id']})
    task={'task_id':'p0-confirm','user_id':user['id'],'chat_id':chat['id'],'message':'Удалить файл, затем создать отчёт',
          'device_ids':['pc'],'status':'confirm','modes':{},'created_at':time.time(),'confirm_data':data,'commands':[],'tasks':[]}
    tasks['p0-confirm']=task
    # Use the production transport with the fake socket; this also exercises ownership.
    monkeypatch.setattr(routes,'send_command_to_agent',runtime.send_command_to_agent)
    async def no_llm(*a,**kw):pytest.fail('Confirmation must not start a new LLM/controller loop')
    monkeypatch.setattr(runtime,'process_nl_command',no_llm)
    monkeypatch.setattr(routes,'run_nl_task',no_llm)
    return Scenario(client,headers,user,task,data,agent,key)


def approve(env):
    response=env.client.post(f'/api/tasks/{env.tid}/confirm',headers=env.headers)
    assert response.status_code==200,response.text


def finished(env):
    for _ in range(100):
        response=env.client.get(f'/api/tasks/{env.tid}',headers=env.headers)
        assert response.status_code==200
        task=response.json()['task']
        if task['status'] not in {'confirm','running','pending','cancelling'}:return task
        time.sleep(.01)
    raise AssertionError('Confirmed operation failed to terminate')


@pytest.mark.parametrize('code',[0,'0'])
def test_verified_command_success_is_not_success_of_the_whole_goal(scenario,code):
    scenario.agent.result={'returncode':code,'stdout':'OK: deleted_and_verified','stderr':''}
    approve(scenario);task=finished(scenario)
    assert len(scenario.agent.calls)==1
    assert task['status']=='blocked' and task['task_receipt']['task_status']=='partial'
    assert task['task_receipt']['command_outcome']=='success'
    assert task['task_receipt']['goal_completed'] is False
    assert task['commands'][-2]['tool_name']=='execute_cmd' and task['commands'][-2]['status']=='success'
    assert task['commands'][-1]['result']['answer_type']=='partial_report'
    assert task['commands'][-1]['result']['basis']==[task['commands'][-2]['step_id']]
    assert 'Продолжение исходной задачи недоступно' in task['answer']
    assert 'завершение всей задачи не подтверждено' in task['answer']
    assert task['answer']!='Выполнено.'
    # The report creation mentioned in the original goal was not dispatched.
    assert [call['action'] for call in scenario.agent.calls]==['execute_cmd']


@pytest.mark.parametrize('result',[
    {'returncode':7,'stdout':'','stderr':'PRIVATE_STDERR'},
    {'error':'PRIVATE_ERROR'},
    {'status':'failed','returncode':0,'stdout':'OK: optimistic'},
    {'status':'error'},
    {'completion_state':'failed','returncode':0,'stdout':'OK: optimistic'},
    {'returncode':0,'stdout':'NO: effect_missing'},
])
def test_failed_result_never_becomes_done(scenario,result):
    scenario.agent.result=result;approve(scenario);task=finished(scenario)
    assert task['status']=='failed' and task['task_receipt']['command_outcome']=='failed'
    assert task['task_receipt']['goal_completed'] is False
    assert task['commands'][-1]['result']['answer_type']=='error_report'
    assert task['answer']!='Выполнено.'
    assert len(scenario.agent.calls)==1


@pytest.mark.parametrize('result',[
    None,{},[],{'returncode':0,'stdout':''},
    {'status':'unknown','returncode':0,'stdout':'OK: optimistic'},
    {'status':'unknown','error':'PRIVATE_TIMEOUT'},
    {'status':'started','returncode':0,'stdout':'OK: optimistic'},
    {'status':'success','returncode':0},
    {'terminal_sufficient':True},
    {'returncode':False,'stdout':'OK: optimistic'},
    {'returncode':True,'stdout':'OK: optimistic'},
    {'returncode':0.0,'stdout':'OK: optimistic'},
    {'status':[],'returncode':0,'stdout':'OK: optimistic'},
    {'returncode':0,'completion_state':'partial_success','stdout':'OK: optimistic'},
    {'returncode':0,'stdout':'OK: launch_requested long_running'},
])
def test_unknown_or_launch_only_result_never_claims_proven_success(scenario,result):
    scenario.agent.result=result;approve(scenario);task=finished(scenario)
    assert task['status']=='blocked' and task['task_receipt']['command_outcome']=='unknown'
    assert task['commands'][-2]['status']=='unknown'
    assert task['commands'][-1]['result']['self_check']['claims_completed_action'] is False
    assert 'не подтверждён' in task['answer'] and 'автоматически её не повторяю' in task['answer']
    assert len(scenario.agent.calls)==1


def test_duplicate_confirmation_cannot_execute_twice(scenario):
    approve(scenario)
    assert scenario.client.post(f'/api/tasks/{scenario.tid}/confirm',headers=scenario.headers).status_code==400
    task=finished(scenario)
    assert len(scenario.agent.calls)==1
    assert scenario.client.post(f'/api/tasks/{scenario.tid}/command-decision',headers=scenario.headers,
        json={'confirmation_id':scenario.confirmation['confirmation_id'],'accepted':True}).status_code==409
    assert task['task_receipt']['goal_completed'] is False


def test_simultaneous_approvals_are_one_shot(scenario,monkeypatch):
    monkeypatch.setattr(routes,'get_current_user',lambda request:scenario.user)
    async def run():
        replies=await asyncio.gather(routes.api_confirm_task(scenario.tid,None),routes.api_confirm_task(scenario.tid,None),return_exceptions=True)
        assert sum(isinstance(r,dict) for r in replies)==1
        assert any(isinstance(r,HTTPException) and r.status_code==400 for r in replies)
        for _ in range(10):
            if scenario.task['status']!='running':break
            await asyncio.sleep(0)
    asyncio.run(run())
    assert len(scenario.agent.calls)==1


def test_cancel_before_approval_prevents_execution(scenario):
    response=scenario.client.post(f'/api/tasks/{scenario.tid}/cancel',headers=scenario.headers)
    assert response.status_code==200
    assert scenario.client.post(f'/api/tasks/{scenario.tid}/confirm',headers=scenario.headers).status_code==400
    assert not scenario.agent.calls and scenario.task['status']=='cancelled'


def test_cancel_after_approval_before_dispatch_prevents_execution(scenario,monkeypatch):
    monkeypatch.setattr(routes,'get_current_user',lambda request:scenario.user)
    async def run():
        await routes.api_confirm_task(scenario.tid,None)
        await routes.api_cancel_task(scenario.tid,None)
        await asyncio.sleep(0);await asyncio.sleep(0)
    asyncio.run(run())
    assert not scenario.agent.calls and scenario.task['status']=='cancelled'


def test_denied_command_cannot_be_approved_later(scenario):
    assert scenario.client.post(f'/api/tasks/{scenario.tid}/deny',headers=scenario.headers).status_code==200
    assert scenario.client.post(f'/api/tasks/{scenario.tid}/confirm',headers=scenario.headers).status_code==400
    assert not scenario.agent.calls


@pytest.mark.parametrize('expired,wrong_id',[(True,False),(False,True)])
def test_stale_confirmation_never_dispatches(scenario,expired,wrong_id):
    if expired:scenario.task['created_at']=time.time()-TASK_TTL-1
    response=scenario.client.post(f'/api/tasks/{scenario.tid}/command-decision',headers=scenario.headers,
        json={'confirmation_id':'old-id' if wrong_id else scenario.confirmation['confirmation_id'],'accepted':True})
    assert response.status_code==409 and not scenario.agent.calls


@pytest.mark.parametrize('change',['offline','unknown','foreign'])
def test_device_isolation_and_disconnect_have_no_fallback(scenario,change):
    if change=='offline':devices[scenario.key]['ws']=None
    elif change=='unknown':scenario.confirmation['device_id']='unknown'
    else:
        scenario.confirmation['device_id']='999:pc'
        devices['999:pc']={'user_id':999,'ws':scenario.agent,'info':{'os':'Windows'},'pending':{}}
    approve(scenario);task=finished(scenario)
    assert not scenario.agent.calls
    assert task['status']=='failed' and task['task_receipt']['command_outcome']=='failed'
    assert 'не выполнялась' in task['answer']


def test_transport_error_after_dispatch_is_unknown_and_never_retried(scenario):
    scenario.agent.transport_error='PRIVATE_TRANSPORT_ERROR SECRET_TOKEN'
    approve(scenario);task=finished(scenario)
    assert task['status']=='blocked' and task['task_receipt']['command_outcome']=='unknown'
    assert len(scenario.agent.calls)==1
    assert 'SECRET_TOKEN' not in json.dumps(task,ensure_ascii=False)


def test_other_user_cannot_confirm_task(scenario):
    other=db.create_user('other-confirm-user')
    response=scenario.client.post(f'/api/tasks/{scenario.tid}/confirm',headers={'X-Token':other['token']})
    assert response.status_code==404 and not scenario.agent.calls


def test_approved_parameters_are_snapshotted(scenario,monkeypatch):
    monkeypatch.setattr(routes,'get_current_user',lambda request:scenario.user)
    async def run():
        await routes.api_confirm_task(scenario.tid,None)
        scenario.confirmation['params']['command']='Remove-Item changed.txt'
        for _ in range(10):
            if scenario.task['status']!='running':break
            await asyncio.sleep(0)
    asyncio.run(run())
    assert scenario.agent.calls[0]['params']['command']=='Remove-Item demo.txt'


def test_removed_task_cannot_execute_from_a_stale_callback(scenario,monkeypatch):
    monkeypatch.setattr(routes,'get_current_user',lambda request:scenario.user)
    async def run():
        await routes.api_confirm_task(scenario.tid,None)
        tasks.pop(scenario.tid)
        await asyncio.sleep(0)
    asyncio.run(run())
    assert not scenario.agent.calls


def test_sensitive_command_and_output_do_not_enter_confirmation_journal(scenario,caplog,capsys):
    secret='PRIVATE_COMMAND_CREDENTIAL'
    scenario.confirmation['command']=f"Remove-Item demo.txt; $value='{secret}'"
    scenario.confirmation['params']['command']=scenario.confirmation['command']
    scenario.agent.result={'returncode':0,'stdout':'OK: verified\nPRIVATE_STDOUT_SECRET','stderr':'PRIVATE_STDERR_SECRET'}
    approve(scenario);task=finished(scenario)
    stored=db.get_messages(scenario.task['chat_id'])
    material=json.dumps({'commands':task['commands'],'receipt':task['task_receipt'],'stored':stored},ensure_ascii=False)
    logs=caplog.text+capsys.readouterr().out
    for value in (secret,'PRIVATE_STDOUT_SECRET','PRIVATE_STDERR_SECRET'):
        assert value not in material and value not in logs


def test_preview_only_confirmation_is_not_an_executable_command(scenario):
    scenario.confirmation['params']={'action':'execute_cmd','command_preview':'Remove-Item demo.txt'}
    assert scenario.client.post(f'/api/tasks/{scenario.tid}/confirm',headers=scenario.headers).status_code==409
    assert not scenario.agent.calls


def test_cancel_after_dispatch_does_not_get_overwritten_by_completion(scenario):
    original=scenario.agent.send_text
    async def cancel_during_delivery(wire):
        request_task_cancel(scenario.tid,scenario.user['id'])
        await original(wire)
    scenario.agent.send_text=cancel_during_delivery
    approve(scenario);task=finished(scenario)
    assert len(scenario.agent.calls)==1
    assert task['status']=='cancelled'
    assert task['task_receipt']['goal_completed'] is False
    assert task['answer']!='Выполнено.'

@pytest.mark.parametrize('result',[
 {'returncode':False,'stdout':'OK: effect_verified'},
 {'returncode':0.0,'stdout':'OK: effect_verified'},
 {'returncode':0,'stdout':'OK: effect_verified','status':'unknown'},
 {'returncode':0,'stdout':'OK: effect_verified','completion_state':'pending'},
 {'returncode':0,'stdout':'OK: launch_requested'},
])
def test_terminal_sufficiency_agrees_with_uncertain_command_outcome(result):
 from server.tool_completion import execute_cmd_result_is_ok,tool_result_terminal_sufficient
 assert confirmed_command_outcome(result)=='unknown'
 assert not execute_cmd_result_is_ok(result)
 assert not tool_result_terminal_sufficient({'tool_name':'execute_cmd','result':result})
