"""Critical regressions from the RAG continuation incident; no LIVE effects."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from server import database as db, controller, controller_onboarding as onboarding, orchestrator as orch
from server.routers import tasks as routes
from server.runtime_state import tasks, devices
from server.worker_scheduler import WorkerScheduler, owned_job, goal_view
from test_goal_parallel_scheduler import owner, template, complete


def test_server_worker_keeps_its_assignment_after_strict_prompt(monkeypatch):
    tasks['research']={'task_id':'research','goal_id':'goal','proposed_objective':'Research RAG only; siblings create documents'}
    captured=[]
    async def completion(**kwargs):
        captured.append(kwargs)
        if len(captured)==1:
            return {'choices':[{'message':{'tool_calls':[{'id':'search','function':{
                'name':'web_search','arguments':json.dumps({'query':'RAG research'})}}]}}]}
        payload={'answer_type':'partial_report','text':'Search failed; no sources yet','basis':['step_1'],
            'self_check':{'depends_on_current_external_state':True,'claims_completed_action':False,
                'has_sufficient_evidence':False,'missing_evidence_question':'Sources missing'}}
        return {'choices':[{'message':{'tool_calls':[{'id':'answer','function':{
            'name':'answer_text','arguments':json.dumps(payload)}}]}}]}
    async def search(*args):return {'error':'controlled HTTP 429'}
    monkeypatch.setattr(onboarding,'run_web_search',search)
    monkeypatch.setattr(controller,'_chat_completion_request',completion)
    asyncio.run(onboarding.process_onboarding_message('Prepare the complete package',
        usage_context={'worker_execution':True,'poll_task_id':'research'},
        load_llm_config_fn=lambda:{'model':'mock','api_key':'test','answer_auditor_enabled':False},
        current_datetime_msk_fn=lambda:'2026-10-21'))
    prompt=captured[0]['messages'][0]['content']
    assert 'Research RAG only; siblings create documents' in prompt
    assert 'Report and verify completion of this assigned result only' in prompt
    assert len(captured)==2


@pytest.mark.parametrize('mixed,implicit',[(False,False),(True,False),(True,True)])
def test_server_scope_device_hint_does_not_lose_valid_intake(owner,monkeypatch,mixed,implicit):
    key=f"{owner['id']}:pc"
    devices[key]={'user_id':owner['id'],'ws':object(),'info':{'os':'Windows'}}
    monkeypatch.setattr(routes,'get_current_user',lambda request:owner)
    monkeypatch.setattr(routes,'check_rate_limit',lambda key:True)
    monkeypatch.setattr(routes,'check_ip_rate_limit',lambda key:True)
    captured=[]
    async def submit(t,assignments,**kwargs):
        captured.append((t,assignments))
        return {'goal_id':'goal','tasks':[{'task_id':'accepted','status':'queued'}]}
    monkeypatch.setattr(routes,'scheduler',SimpleNamespace(submit_goal=submit))
    async def turn(cmd,user,chat,delegate):
        assignments=[orch.Assignment(key='research',objective='Research RAG',target_device_ids=None if implicit else [])]
        if mixed:assignments.append(orch.Assignment(key='doc',objective='Create document',target_device_ids=['pc'],depends_on=['research']))
        return await delegate(orch.Decision(intent='delegate',scope='server',target_device_ids=['pc'],assignments=assignments),'request')
    monkeypatch.setattr(routes,'run_turn',turn)
    cmd=routes.NLCommand(message='Continue the package',device_id='pc',chat_id=owner['chat_id'])
    if implicit:
        result=asyncio.run(routes.nl_command(cmd,SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'))))
        assert result['status']=='error'
        assert captured==[]
        return
    asyncio.run(routes.nl_command(cmd,SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'))))
    assert captured[0][0]['device_ids']==([key] if mixed else [])
    assert captured[0][1][0]['target_device_ids']==[]
    if mixed:assert captured[0][1][1]['target_device_ids']==[key]


async def blocked_goal(owner):
    async def execute(task):task.update(status='partial',answer='Research is incomplete',commands=[])
    scheduler=WorkerScheduler(execute)
    admitted=await scheduler.submit_goal(template(owner),[
        {'key':'research','objective':'Research RAG'},
        {'key':'doc','objective':'Create Word','depends_on':['research']}],request_key='initial')
    await scheduler.runners[owner['id']]
    return scheduler,admitted,admitted['tasks'][0]['task_id'],admitted['tasks'][1]['task_id']


def test_terminal_partial_dependency_is_rejected_atomically(owner):
    async def scenario():
        scheduler,goal,source,consumer=await blocked_goal(owner)
        with pytest.raises(ValueError,match='dependency_source_not_successful'):
            await scheduler.submit_goal(template(owner),[{'key':'bad','objective':'Continue research',
                'required_source_task_ids':[source]}],request_key='bad',goal_id=goal['goal_id'],expected_revision=1)
        assert goal_view(goal['goal_id'],owner['id'],owner['chat_id'])['revision']==1
        with db.get_db() as c:assert c.execute('select count(*) from worker_jobs').fetchone()[0]==2
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_replacement_research_unblocks_existing_consumer_once_and_keeps_history(owner):
    async def scenario():
        scheduler,goal,source,consumer=await blocked_goal(owner)
        before=owned_job(source,owner['id'])
        observed=[]
        async def execute(task):
            observed.append((task['task_id'],copy.deepcopy(task.get('pinned_dependency_results') or [])))
            complete(task)
        scheduler.execute=execute
        assignments=[{'key':'repair','objective':'Complete missing RAG research','replaces_source_task_id':source}]
        added=await scheduler.submit_goal(template(owner),assignments,request_key='repair',goal_id=goal['goal_id'],expected_revision=1)
        retry=await scheduler.submit_goal(template(owner),assignments,request_key='repair',goal_id=goal['goal_id'],expected_revision=1)
        replacement=added['tasks'][0]['task_id']
        assert retry['tasks'][0]['task_id']==replacement
        assert not goal_view(goal['goal_id'],owner['id'],owner['chat_id'])['goal_completed']
        await scheduler.runners[owner['id']]
        assert [tid for tid,_ in observed]==[replacement,consumer]
        assert observed[0][1]==[]
        assert observed[1][1][0]['data']['task_id']==replacement
        assert owned_job(source,owner['id'])==before
        view=goal_view(goal['goal_id'],owner['id'],owner['chat_id'])
        assert view['goal_completed'] and view['revision']==2
        historical=next(item for item in view['tasks'] if item['task_id']==source)
        assert historical['status']=='partial' and historical['superseded_by']==replacement
        assert historical['required_for_goal'] is False
        with db.get_db() as c:
            assert c.execute('select revision from worker_goal_tasks where task_id=?',(consumer,)).fetchone()[0]==2
            old=json.loads(c.execute('select previous_payload from worker_task_revisions where task_id=?',(consumer,)).fetchone()[0])
            assert old['dependency_sources_before']==[source]
        await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('hazard',['stale_revision','dispatched_zero','unknown_source','foreign_goal','dependent_repair','device_repair'])
def test_unsafe_dependency_replacement_rolls_back(owner,hazard):
    async def scenario():
        scheduler,goal,source,consumer=await blocked_goal(owner)
        revision=1
        assignment={'key':'repair','objective':'Complete missing facts','replaces_source_task_id':source}
        if hazard=='stale_revision':revision=2
        with db.get_db() as c:
            if hazard=='dispatched_zero':
                payload=json.loads(c.execute('select payload from worker_jobs where task_id=?',(consumer,)).fetchone()[0])
                payload['dispatch_started_at']=0
                c.execute('update worker_jobs set payload=? where task_id=?',(json.dumps(payload),consumer))
            if hazard=='unknown_source':c.execute("update worker_jobs set state='unknown' where task_id=?",(source,))
            if hazard=='foreign_goal':assignment['replaces_source_task_id']='not-in-this-goal'
        if hazard=='dependent_repair':assignment['required_source_task_ids']=[consumer]
        if hazard=='device_repair':assignment['target_device_ids']=[f"{owner['id']}:pc"]
        with pytest.raises(ValueError):
            await scheduler.submit_goal(template(owner,device=hazard=='device_repair'),[assignment],
                request_key='unsafe',goal_id=goal['goal_id'],expected_revision=revision)
        with db.get_db() as c:
            assert c.execute('select count(*) from worker_jobs').fetchone()[0]==2
            assert c.execute('select source_task_id from worker_dependencies where task_id=?',(consumer,)).fetchone()[0]==source
            assert c.execute('select revision from worker_goals where goal_id=?',(goal['goal_id'],)).fetchone()[0]==1
            assert c.execute('select count(*) from worker_task_revisions').fetchone()[0]==0
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_direct_deliverables_reach_scheduler_without_preparation(owner,monkeypatch):
    key=f"{owner['id']}:pc"
    devices[key]={'user_id':owner['id'],'ws':object(),'info':{'os':'Windows'}}
    monkeypatch.setattr(routes,'get_current_user',lambda request:owner)
    monkeypatch.setattr(routes,'check_rate_limit',lambda key:True)
    monkeypatch.setattr(routes,'check_ip_rate_limit',lambda key:True)
    submitted=[]
    async def submit(template,assignments,**kwargs):
        submitted.append(assignments)
        return {'goal_id':'goal','tasks':[{'task_id':'accepted','status':'queued'}]}
    monkeypatch.setattr(routes,'scheduler',SimpleNamespace(submit_goal=submit))
    async def turn(cmd,user,chat,delegate):
        decision=orch.Decision(intent='delegate',scope='device',target_device_ids=['pc'],
            objective='Make independent documents',
            assignments=[
                orch.Assignment(key='word',objective='Write Word',artifacts=['report.docx']),
                orch.Assignment(key='slides',objective='Make slides',artifacts=['slides.pptx'])])
        return await delegate(decision,'direct-delegation')
    monkeypatch.setattr(routes,'run_turn',turn)
    cmd=routes.NLCommand(message='Make a Word document and slides',device_id='pc',chat_id=owner['chat_id'])
    asyncio.run(routes.nl_command(cmd,SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'))))
    assert len(submitted)==1
    assert [row['key'] for row in submitted[0]]==['word','slides']
    assert [row['depends_on'] for row in submitted[0]]==[[],[]]
    assert [row['artifacts'] for row in submitted[0]]==[['report.docx'],['slides.pptx']]
    assert all('stage' not in row and 'reconnaissance_contract' not in row for row in submitted[0])
