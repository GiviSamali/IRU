import asyncio
import copy
import json

import httpx
import pytest
from test_tool_only_protocol import _run_case, _message, _tool_call, _answer_call
from test_orchestrator_worker import owners
from server import controller, database as db
from server.llm_usage import LLM_ENTITY
from server.run_journal import audited_task_receipt, make_run_step, append_tool_step
from server.tool_completion import tool_result_terminal_sufficient
from server.tool_repeat_guard import repeated_command_observation
from server.worker_reports import build_worker_report


def worker_task(result):
    return {'task_id':'test-task','status':'done','device_ids':['device-1'],
        'answer':result['answer'],'commands':result['commands'],'task_receipt':result.get('task_receipt')}


def test_repeated_observation_warns_but_does_not_skip_execution_or_end_goal():
    calls=[]
    class Captures(list):
        def append(self, value):super().append(copy.deepcopy(value['messages']))
    captured=Captures()
    async def send(device, action, args):
        calls.append(args['command'])
        return {'returncode':0,'stdout':'tail' if args['command']=='inspect' else 'changed and verified','stderr':''}
    result=_run_case([
        _message(tool_calls=[_tool_call('a','execute_cmd',{'command':'inspect'})]),
        _message(tool_calls=[_tool_call('b','execute_cmd',{'command':'inspect'})]),
        _message(tool_calls=[_tool_call('c','execute_cmd',{'command':'transform'})]),
        _message(tool_calls=[_answer_call('d','Change verified',answer_type='grounded_report',basis=['step_3'])]),
        _message(content='{"valid":true,"reason":"mock verification"}',finish_reason='stop')],
        user_message='Change the requested part only',send_command_fn=send,captured=captured,
        cfg={'model':'mock-model','answer_auditor_enabled':True},max_iterations=4)
    assert calls==['inspect','inspect','transform']
    assert any('repeat 2 times' in str(m.get('content')) for m in captured[2])
    assert build_worker_report(worker_task(result))['status']=='success'


@pytest.mark.parametrize('audited',[True,False])
def test_repair_receipt_preserves_audited_goal_without_trusting_model_flags(audited):
    responses=[_message(tool_calls=[_tool_call('a','write_content',{'path':'C:/Temp/output.txt','content':'ok'})]),
        _message(tool_calls=[_answer_call('b','Written',answer_type='grounded_report',basis=['step_1'])])]
    if audited:responses.append(_message(content='{"valid":true,"reason":"mock write evidence"}',finish_reason='stop'))
    async def send(*args):return {'path':'C:/Temp/output.txt','bytes_written':2}
    result=_run_case(responses,user_message='Create a file',send_command_fn=send,
        cfg={'model':'mock-model','answer_auditor_enabled':audited},max_iterations=1)
    report=build_worker_report(worker_task(result))
    assert report['status']==('success' if audited else 'unknown')
    assert bool(result.get('task_receipt')) is audited


def test_native_failure_cannot_be_overridden_by_terminal_hint():
    for result in ({'status':'failed','error':'failure','terminal_sufficient':True},
            {'status':'success','sha256_verified':False,'terminal_sufficient':True},
            {'status':'unknown','terminal_sufficient':True}):
        assert not tool_result_terminal_sufficient({'tool_name':'transfer_file','result':result})


def test_empty_or_changed_observations_are_not_misclassified_as_repeats():
    journal=[]
    for command,output in [('write',''),('write',''),('read','one'),('read','two')]:
        append_tool_step(journal,make_run_step(journal=journal,tool_name='execute_cmd',command=command,
            target_device_id='device-1',result={'returncode':0,'stdout':output,'stderr':''}))
        assert repeated_command_observation(journal) is None


def test_thinking_request_preserves_reasoning_and_omits_unsupported_tool_choice(owners):
    posts=[]
    class Client:
        async def post(self,url,**kwargs):
            posts.append(copy.deepcopy(kwargs['json']))
            return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':'ok'}}]})
    messages=[{'role':'assistant','content':'historical data'},
        {'role':'assistant','content':'','reasoning_content':'actual reasoning'}, {'role':'user','content':'Do the task'}]
    saved=copy.deepcopy(messages)
    token=LLM_ENTITY.set('worker')
    try:
        asyncio.run(controller._chat_completion_request(client=Client(),cfg={'model':'deepseek-v4-flash',
            'base_url':'https://example.test','api_key':'test','max_tokens':4096},model='deepseek-v4-flash',
            messages=messages,tools=[{'type':'function','function':{'name':'answer_text','parameters':{'type':'object'}}}],
            tool_choice='required',phase='non_pipeline.iteration.1'))
    finally:LLM_ENTITY.reset(token)
    assert len(posts)==1 and posts[0]['thinking']['type']=='enabled' and posts[0]['reasoning_effort']=='low'
    assert 'tool_choice' not in posts[0] and 'temperature' not in posts[0]
    assert posts[0]['messages'][0]['reasoning_content']=='' and posts[0]['messages'][1]['reasoning_content']=='actual reasoning'
    assert messages==saved
    assert controller._thinking_request_fields({'model_reasoner':'deepseek-v4-pro'},'deepseek-v4-pro',phase='answer_repair.auditor')['thinking']['type']=='disabled'


def test_provider_failure_preserves_observed_journal_and_artifact(monkeypatch):
    calls=0
    async def completion(**kwargs):
        nonlocal calls
        calls+=1
        if calls==1:return _message(tool_calls=[_tool_call('a','write_content',{'path':'C:/Temp/kept.txt','content':'ok'})])
        raise httpx.ConnectError('mock failure')
    monkeypatch.setattr('test_tool_only_protocol._completion_fn',lambda *args,**kwargs:completion)
    async def send(*args):return {'path':'C:/Temp/kept.txt','bytes_written':2}
    result=_run_case([],send_command_fn=send)
    assert result['commands'][0]['result']['path']=='C:/Temp/kept.txt'
    assert result['task_receipt']['goal_completed'] is False and result['task_receipt']['task_status']=='partial'
    assert build_worker_report(worker_task(result))['artifacts'][0]['path']=='C:/Temp/kept.txt'


def test_stale_pipeline_confirmation_cannot_approve_next_command(owners,monkeypatch):
    from server.routers import tasks as routes
    from server.runtime_state import tasks
    from server.command_confirmation import command_confirmation
    a,_=owners
    monkeypatch.setattr(routes,'get_current_user',lambda request:a)
    async def scenario():
        task={'task_id':'nonce-task','user_id':a['id'],'status':'confirm','modes':{'pipeline':True}}
        task['confirm_data']=command_confirmation({'command':'first','params':{'command':'first'}})
        first=task['confirm_data']['confirmation_id'];task['_pipeline_confirm_future']=asyncio.get_running_loop().create_future()
        tasks[task['task_id']]=task
        with pytest.raises(Exception) as error:await routes.api_confirm_task(task['task_id'],None)
        assert getattr(error.value,'status_code',None)==409 and not task['_pipeline_confirm_future'].done()
        await routes.api_command_decision(task['task_id'],routes.CommandDecisionBody(confirmation_id=first,accepted=True),None)
        assert task['_pipeline_confirm_future'].result() is True
        task['status']='confirm';task['confirm_data']=command_confirmation({'command':'second','params':{'command':'second'}})
        task['_pipeline_confirm_future']=asyncio.get_running_loop().create_future()
        with pytest.raises(Exception) as error:
            await routes.api_command_decision(task['task_id'],routes.CommandDecisionBody(confirmation_id=first,accepted=True),None)
        assert getattr(error.value,'status_code',None)==409 and not task['_pipeline_confirm_future'].done()
    asyncio.run(scenario())
