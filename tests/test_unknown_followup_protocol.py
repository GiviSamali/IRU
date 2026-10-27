"""Local regressions for the 88714b9d transport/protocol/follow-up failure."""
import asyncio

import pytest

from server import orchestrator as orch
from server.worker_scheduler import WorkerScheduler, owned_job, restore_task
from server.runtime_state import tasks
from test_orchestrator_worker import owners
from test_goal_parallel_scheduler import template, until
from test_tool_only_protocol import _run_case, _message, _tool_call
from test_diagnostic_task_followup import cmd, decide_as
from test_v3_goal_coverage_and_speech import provider


def test_unknown_tool_result_precedes_correction_and_mutation_stays_blocked(owners):
    captured=[]; dispatched=[]
    async def send(device,action,params):
        dispatched.append(action)
        return {'status':'unknown','error':'confirmed_transport_outcome_unknown'}
    result=_run_case([
        _message(tool_calls=[_tool_call('scan','execute_cmd',{'command':'Get-ChildItem notes -Recurse'})]),
        _message(tool_calls=[_tool_call('write','write_content',{'path':'C:/map.html','content':'unsafe'})]),
        _message(tool_calls=[_tool_call('finish','answer_text',{
            'result':'Команда осталась без результата; карта не создана.',
            'unresolved':['HTML-файл не создан.']})]),
    ],send_command_fn=send,captured=captured)
    messages=captured[-1]['messages']
    pending=set()
    for message in messages:
        if pending:
            assert message['role']=='tool', 'A correction interrupted an open tool exchange'
            assert message['tool_call_id'] in pending
            pending.remove(message['tool_call_id'])
        elif message.get('tool_calls'):
            pending={call['id'] for call in message['tool_calls']}
    assert dispatched==['execute_cmd']
    assert any((e.get('result') or {}).get('error')=='mutation_blocked_after_unknown' for e in result['commands'])


def test_restored_recovery_worker_can_observe_but_cannot_write(owners):
    tid='restored-recovery'; dispatched=[]
    tasks[tid]={'task_id':tid,'_action_outcome_unknown':True}
    async def send(device,action,params):
        dispatched.append(action)
        return {'status':'success','entries':[]}
    try:
        result=_run_case([
            _message(tool_calls=[_tool_call('write','write_content',{'path':'C:/map.html','content':'unsafe'})]),
            _message(tool_calls=[_tool_call('observe','list_dir',{'path':'C:/'})]),
            _message(tool_calls=[_tool_call('finish','answer_text',{
                'result':'Сверка каталога выполнена, карта не создана.',
                'unresolved':['Создание HTML не выполнено.']})]),
        ],poll_task_id=tid,send_command_fn=send)
        assert dispatched==['list_dir']
        assert any((e.get('result') or {}).get('error')=='mutation_blocked_after_unknown' for e in result['commands'])
    finally:
        tasks.pop(tid,None)


def test_explicit_unknown_followup_is_observation_only_and_survives_restart(owners,monkeypatch):
    owner=owners[0]; seen=[]
    async def execute(task):
        seen.append(dict(task))
        task.update(status='unknown',answer='HTML не создан.',commands=[{
            'step_id':'step_1','tool_name':'execute_cmd','tool_type':'control','status':'unknown',
            'result':{'status':'unknown','error':'confirmed_transport_outcome_unknown'}}])
    async def scenario():
        scheduler=WorkerScheduler(execute=execute)
        first=await scheduler.submit_goal(template(owner),[{'key':'map','objective':'Создать карту',
            'artifacts':['HTML карты'],'depends_on':[]}],request_key='first')
        old=first['tasks'][0]['task_id']
        await until(lambda:owned_job(old,owner['id'])['state']=='unknown')
        choice=orch.Decision(intent='delegate',scope='server',objective='Продолжить карту',
            goal_id=first['goal_id'],goal_revision=1,assignments=[orch.Assignment(
                key='retry',objective='Создать карту',artifacts=['HTML карты'],replaces_source_task_id=old)])
        decide_as(monkeypatch,choice)
        async def delegate(choice,request_key):
            admitted=await scheduler.submit_goal(template(owner),[a.model_dump() for a in choice.assignments],
                request_key=request_key,goal_id=choice.goal_id,expected_revision=choice.goal_revision)
            return admitted['tasks'][0]
        response=await orch.run_turn(cmd('Продолжи карту'),owner,owner['chat_id'],delegate)
        new=response['worker_task_id']; assert new and new!=old
        await until(lambda:owned_job(new,owner['id'])['state']=='unknown')
        restored=restore_task(owned_job(new,owner['id']))
        assert restored['_action_outcome_unknown'] is True
        assert restored['assigned_artifacts']==['HTML карты']
        assert restored['proposed_objective']=='Создать карту'
        assert not restored['worker_report']['goal_completed']
        assert 'сверку' in response['answer']
        # An additional follow-up cannot launder the recovery restriction away.
        next_attempt=await scheduler.submit_goal(template(owner),[{'key':'again','objective':'Создать карту',
            'artifacts':['HTML карты'],'replaces_source_task_id':new}],request_key='again',
            goal_id=first['goal_id'],expected_revision=2)
        assert next_attempt['tasks'][0]['_action_outcome_unknown']
        await until(lambda:owned_job(next_attempt['tasks'][0]['task_id'],owner['id'])['state']=='unknown')
    asyncio.run(scenario())


@pytest.mark.parametrize('reference',['task','goal'])
def test_status_followup_reuses_goal_speech_cache(owners,monkeypatch,reference):
    owner=owners[0]
    async def execute(task):
        task.update(status='unknown',answer='Карта не создана.',commands=[{
            'step_id':'step_1','tool_name':'execute_cmd','status':'unknown',
            'result':{'status':'unknown','error':'confirmed_transport_outcome_unknown'}}])
    calls=provider(monkeypatch,text='Карта пока не создана: результат проверки не получен.')
    async def scenario():
        scheduler=WorkerScheduler(execute=execute)
        admitted=await scheduler.submit_goal(template(owner),[{'key':'map','objective':'Создать карту',
            'artifacts':['HTML карты']}],request_key='speech-map')
        tid=admitted['tasks'][0]['task_id']
        await until(lambda:owned_job(tid,owner['id'])['state']=='unknown')
        choice=orch.Decision(intent='task_status',**({'goal_id':admitted['goal_id']} if reference=='goal'
            else {'task_id':tid,'reference':'explicit'}))
        decide_as(monkeypatch,choice)
        async def forbidden(*args,**kwargs):pytest.fail('Status must not execute a new Worker')
        first=await orch.run_turn(cmd('Как там карта?'),owner,owner['chat_id'],forbidden)
        second=await orch.run_turn(cmd('Что с картой?'),owner,owner['chat_id'],forbidden)
        assert first['answer']==second['answer']=='Карта пока не создана: результат проверки не получен.'
        assert len(calls)==1
        assert owned_job(tid,owner['id'])['state']=='unknown'
    asyncio.run(scenario())
