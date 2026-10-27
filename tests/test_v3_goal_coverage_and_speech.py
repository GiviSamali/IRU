"""Two bounded fixes: existing goal coverage and existing Orchestrator speech.

Only fake providers, synthetic receipts and temporary SQLite are used.
"""
import asyncio
import copy
import json
import time

import pytest

from server import database as db, orchestrator as orch, goal_completion as speech
from server.run_journal import validate_answer_text_payload, append_answer_step
from server.worker_reports import build_worker_report, coverage_binding, coverage_request, result_binding, saved_worker_report
from server.goal_presentation import goal_presentation
from server.runtime_state import tasks
from test_orchestrator_worker import owners
from test_orchestrator_worker import task as admitted_task
from server.worker_scheduler import WorkerScheduler, owned_job, restore_task
from server.routers import tasks as routes
from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_tools import NON_PIPELINE_TOOLS
from test_tool_only_protocol import _run_case, _message, _tool_call


REQUEST='Прочитай A.txt, B.txt и C.txt целиком и сравни их содержимое.'


def task_with_read(coverage=None, outcome='success', count=1):
    steps=[{'step_id':f'step_{i+1}','tool_name':'read_file','target_device_id':'pc',
        'result':{'status':outcome,'path':f'C:/Synthetic/{name}.txt','content':'same' if name in 'AB' else 'different'}}
        for i,name in enumerate('ABC'[:count])]
    text='Прочитан только A; B и C не прочитаны, сравнение не выполнено.' if count==1 else 'A и B равны; C отличается.'
    t={'task_id':'coverage','status':'done','device_ids':['pc'],'execution_scope':'analysis',
       'original_request':REQUEST,'proposed_objective':REQUEST,'answer':text,'commands':steps}
    payload=validate_answer_text_payload({'result':text,'unresolved':[]},steps)
    if coverage:
        payload['_coverage']={'coverage':coverage,'evidence_refs':[s['step_id'] for s in steps],
            'reason':'B и C не прочитаны; обязательное сравнение отсутствует.' if count==1 else 'Все источники и сравнение представлены.',
            'binding':coverage_binding(coverage_request(t),steps,text)}
    append_answer_step(steps,'answer_text',payload)
    return t


@pytest.mark.parametrize('coverage',[None,'partial','unknown'])
def test_successful_read_and_empty_unresolved_cannot_complete_missing_comparison(coverage):
    t=task_with_read(coverage)
    report=build_worker_report(t)
    assert report['status']=='partial' and report['goal_completed'] is False


def test_complete_goal_coverage_is_bound_to_actual_request_and_sources():
    t=task_with_read('complete',count=3)
    assert build_worker_report(t)['goal_completed']
    t['original_request']='Прочитай также D.txt и включи его в сравнение.'
    assert not build_worker_report(t)['goal_completed']


@pytest.mark.parametrize('outcome',['unknown','failed'])
def test_positive_language_assessment_cannot_override_negative_execution(outcome):
    assert not build_worker_report(task_with_read('complete',outcome))['goal_completed']


def test_old_bound_false_success_is_rechecked_without_model_call():
    t=task_with_read()
    old={**build_worker_report(t),'status':'success','goal_completed':True,'error_code':None}
    t.update(worker_report=old,worker_report_binding=result_binding(t,old))
    assert saved_worker_report(t)['status']=='partial'


def test_real_controller_uses_existing_coverage_once_for_empty_unresolved(monkeypatch):
    monkeypatch.setattr(db,'get_device_profile',lambda *a,**k:None)
    tid='abc-current';t={'task_id':tid,'execution_scope':'analysis','original_request':REQUEST,'device_ids':['device-1']}
    tasks[tid]=t;calls=[];sent=[]
    async def send(device,action,params):
        sent.append(params);return {'returncode':0,'stdout':'Only A content','stderr':''}
    try:
        result=_run_case([
            _message(tool_calls=[_tool_call('read','execute_cmd',{'command':'Get-Content A.txt','artifact_paths':[]})]),
            _message(tool_calls=[_tool_call('finish','answer_text',{'result':'Прочитан A; сравнение не выполнено.','unresolved':[]})]),
            _message(content=json.dumps({'coverage':'partial','evidence_refs':['step_1'],'reason':'B and C not read; comparison missing'}),finish_reason='stop')],
            poll_task_id=tid,user_message=REQUEST,send_command_fn=send,captured=calls,
            cfg={'model':'mock','answer_auditor_enabled':True})
        assert len(sent)==1 and len(calls)==3 and calls[-1]['phase']=='result_coverage'
        material=json.loads(calls[-1]['messages'][-1]['content'])
        assert material['request']==REQUEST and material['remaining']==[]
        t.update(status='done',commands=result['commands'],answer=result['answer'])
        assert not build_worker_report(t)['goal_completed']
    finally:tasks.pop(tid,None)


def goal(owner,status='partial',scope='analysis'):
    return {'goal_id':'speech-goal','revision':1,'owner_user_id':owner['id'],'chat_id':owner['chat_id'],
        'original_request':REQUEST,'status':status,'terminal':True,'goal_completed':status=='success',
        'tasks':[{'task_id':'member','status':status,'execution_scope':scope,'objective':REQUEST,
            'report':{'status':status,'goal_completed':status=='success','summary':'Прочитан только A.',
                'evidence_refs':['step_1'],'artifacts':[]},'result':{'result_id':'r'} if status=='success' else None,
            'coverage_observation':{'coverage':'partial','reason':'B и C не прочитаны; сравнение не выполнено.'}}]}


def origin(owner,g):
    orch.init_turn_storage()
    with db.get_db() as c:
        mid=c.execute('INSERT INTO messages(chat_id,role,content,task_metadata,created_at) VALUES(?,?,?,?,?)',
            (owner['chat_id'],'assistant','',json.dumps({'goalId':g['goal_id'],'resultFirst':True}),time.time())).lastrowid
        c.execute('INSERT INTO orchestrator_turns VALUES(?,?,?,?,?,?,?,?)',
            (owner['id'],'speech-request','f','turn',owner['chat_id'],mid,'{}',time.time()))
    return mid


def provider(monkeypatch,text='Сравнение пока не выполнено: прочитан лишь A. Не хватает B и C.',error=None):
    calls=[]
    monkeypatch.setattr(speech,'load_llm_config',lambda:{'model':'deepseek-v4-flash',
        'deepseek_pricing_usd_per_1m':{'deepseek-v4-flash':{'input_cache_hit':1,'input_cache_miss':2,'output':4}}})
    async def complete(*args,**kwargs):
        calls.append((args,kwargs))
        await asyncio.sleep(.01)
        if error:raise error
        return {'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150},
            'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{
                'name':'goal_completion_reply','arguments':json.dumps({'answer':text,'spoken_response':text})}}]}}]}
    monkeypatch.setattr(speech,'_chat_completion_request',complete)
    return calls


def test_partial_gets_real_orchestrator_model_reply_once_and_persisted_metrics(owners,monkeypatch):
    owner,_=owners;g=goal(owner);mid=origin(owner,g);calls=provider(monkeypatch);before=copy.deepcopy(g)
    async def scenario():
        projected=goal_presentation(g)
        first,second=await asyncio.gather(speech.present_completed_goal(g,projected,owner['id'],owner['chat_id']),
            speech.present_completed_goal(g,projected,owner['id'],owner['chat_id']))
        assert len(calls)==1 and first['message']==second['message']
        assert first['message']!=projected['message'] and 'Не хватает B и C' in first['message']
        assert first['status']=='partial' and not first['goal_completed'] and g==before
        assert first['reply_source']=='orchestrator_llm'
        assert first['reply_metrics']['usage']['total_tokens']==150
        assert first['reply_metrics']['estimated_cost_usd']==pytest.approx(.0004)
        assert first['reply_metrics']['latency_ms']>=5
        again=await speech.present_completed_goal(g,projected,owner['id'],owner['chat_id'])
        assert len(calls)==1 and again['reply_request_llm_calls']==0
        assert again['reply_metrics']==first['reply_metrics']
        with db.get_db() as c:row=c.execute('SELECT content FROM messages WHERE id=?',(mid,)).fetchone()
        assert row['content']==first['message']
        body=json.loads(calls[0][0][3][-1]['content'])['untrusted_evidence']
        assert body['original_request']==REQUEST and body['verified_goal_status']=='partial'
        assert body['worker_outcomes'][0]['coverage_observation']['coverage']=='partial'
        assert [tool['function']['name'] for tool in calls[0][1]['tools']]==['goal_completion_reply']
    asyncio.run(scenario())


def test_simple_window_confirmation_does_not_call_model(owners,monkeypatch):
    owner,_=owners;g=goal(owner,'success','window');origin(owner,g)
    g['original_request']=g['tasks'][0]['objective']='Открой Яндекс'
    g['tasks'][0]['report']['summary']='Открытие подтверждено.'
    monkeypatch.setattr(speech,'_narrate',lambda *a:pytest.fail('Simple action must use template'))
    result=asyncio.run(speech.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id']))
    assert result['reply_source']=='template' and result['reply_metrics']['llm_calls']==0


@pytest.mark.parametrize('mode',['contradiction','provider_error'])
def test_narration_failure_keeps_report_and_caches_safe_fallback(owners,monkeypatch,mode):
    owner,_=owners;g=goal(owner);origin(owner,g)
    calls=provider(monkeypatch,text='Готово. Всё выполнено.',error=TimeoutError() if mode=='provider_error' else None)
    async def scenario():
        projected=goal_presentation(g)
        first=await speech.present_completed_goal(g,projected,owner['id'],owner['chat_id'])
        again=await speech.present_completed_goal(g,projected,owner['id'],owner['chat_id'])
        assert len(calls)==1 and first['reply_source']=='template_fallback'
        assert first['message']==projected['message']==again['message']
        assert first['status']=='partial' and not first['goal_completed']
        if mode=='contradiction':assert first['reply_metrics']['estimated_cost_usd']>0
        else:assert first['reply_metrics']['estimated_cost_usd'] is None
    asyncio.run(scenario())


def test_successful_content_result_gets_model_reply_not_worker_echo(owners,monkeypatch):
    owner,_=owners;g=goal(owner,'success');origin(owner,g)
    g['tasks'][0]['report']['summary']='A и B совпадают, C отличается.'
    g['tasks'][0]['coverage_observation']={'coverage':'complete','reason':'Прочитаны и сравнены все три файла.'}
    calls=provider(monkeypatch,text='Сравнение закончено: A и B одинаковы, а C содержит отличия.')
    reply=asyncio.run(speech.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id']))
    assert reply['reply_source']=='orchestrator_llm' and len(calls)==1
    assert reply['message']!=g['tasks'][0]['report']['summary']
    assert reply['status']=='success' and reply['goal_completed']


def test_language_model_cannot_return_a_new_technical_status(owners,monkeypatch):
    owner,_=owners;g=goal(owner);origin(owner,g);before=copy.deepcopy(g)
    calls=provider(monkeypatch);base=speech._chat_completion_request
    async def injected(*args,**kwargs):
        data=await base(*args,**kwargs)
        fn=data['choices'][0]['message']['tool_calls'][0]['function']
        fn['arguments']=json.dumps({**json.loads(fn['arguments']),'goal_completed':True})
        return data
    monkeypatch.setattr(speech,'_chat_completion_request',injected)
    reply=asyncio.run(speech.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id']))
    assert reply['reply_source']=='template_fallback' and len(calls)==1
    assert not reply['goal_completed'] and reply['status']=='partial' and g==before


def test_old_positive_narration_is_not_reused_after_report_changes(owners,monkeypatch):
    owner,_=owners;g=goal(owner,'success');mid=origin(owner,g)
    calls=provider(monkeypatch,text='Сравнение выполнено, результаты получены.')
    async def scenario():
        first=await speech.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id'])
        changed=goal(owner,'partial');projected=goal_presentation(changed)
        second=await speech.present_completed_goal(changed,projected,owner['id'],owner['chat_id'])
        assert second['reply_source']=='template_stale_cache' and len(calls)==1
        assert second['message']==projected['message'] and second['message']!=first['message']
        with db.get_db() as c:row=c.execute('select content from messages where id=?',(mid,)).fetchone()
        assert row['content']==second['message']
    asyncio.run(scenario())


def test_optional_cost_calculation_cannot_erase_valid_model_reply(owners,monkeypatch):
    from server import llm_usage
    owner,_=owners;g=goal(owner);origin(owner,g);calls=provider(monkeypatch)
    def broken(*args,**kwargs):raise ValueError('test pricing')
    monkeypatch.setattr(llm_usage,'estimate_deepseek_cost_usd',broken)
    reply=asyncio.run(speech.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id']))
    assert reply['reply_source']=='orchestrator_llm' and len(calls)==1
    assert reply['reply_metrics']['usage']['total_tokens']==150
    assert reply['reply_metrics']['estimated_cost_usd'] is None


def test_request_worker_checked_report_orchestrator_model_and_cached_api(owners,monkeypatch):
    from types import SimpleNamespace
    owner,_=owners;worker_calls=[];dispatches=[];executed=[]
    narration_calls=provider(monkeypatch)
    async def execute(t):
        executed.append(t)
        async def complete(**kwargs):
            worker_calls.append(kwargs)
            if kwargs['tools'] is None:
                assert kwargs['phase']=='result_coverage'
                return _message(content=json.dumps({'coverage':'partial','evidence_refs':['step_1'],
                    'reason':'B и C не прочитаны; обязательное сравнение отсутствует.'}),finish_reason='stop')
            if len(worker_calls)==1:
                return _message(tool_calls=[_tool_call('read','execute_cmd',{'command':'Get-Content A.txt','artifact_paths':[]})])
            return _message(tool_calls=[_tool_call('finish','answer_text',
                {'result':'Прочитаны все файлы, сравнение полностью готово.','unresolved':[]})])
        async def send(device,action,params):
            dispatches.append((action,params));return {'returncode':0,'stdout':'A content only','stderr':''}
        result=await process_non_pipeline_command(user_message=t['message'],device_id='pc',device_info={'hostname':'pc','os':'Windows'},
            send_command_fn=send,get_file_link_fn=lambda *a:'',chat_history=t.get('worker_context') or [],user_id=owner['id'],
            chat_id=owner['chat_id'],modes={},poll_task_id=t['task_id'],cfg={'model':'mock','answer_auditor_enabled':True},
            system_msg='Worker',machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,
            max_iterations=4,pick_model_fn=lambda *a:'mock',chat_completion_request_fn=complete)
        t.update(status='done',answer=result['answer'],commands=result['commands'])
    async def scenario():
        scheduler=WorkerScheduler(execute)
        async def decide(*args,**kwargs):
            return orch.Decision(intent='delegate',objective=REQUEST,target_device_ids=['pc'],execution_scope='analysis',
                assignments=[orch.Assignment(key='compare',objective=REQUEST,scope='Все три файла и обязательное сравнение',
                    execution_scope='analysis',constraints=['Только чтение'])]),{'llm_calls':1}
        async def delegate(choice,request_key):
            template=admitted_task(owner,REQUEST);template['context_history']=[]
            admitted=await scheduler.submit_goal(template,[a.model_dump() for a in choice.assignments],request_key=request_key)
            return {**admitted['tasks'][0],'goal_task_ids':[t['task_id'] for t in admitted['tasks']]}
        monkeypatch.setattr(orch,'decide',decide);monkeypatch.setattr(orch,'scheduler',scheduler)
        monkeypatch.setattr(routes,'get_current_user',lambda request:owner)
        try:
            cmd=SimpleNamespace(message=REQUEST,request_id='e2e',device_id='pc',device_ids=[],modes={},broadcast=False)
            turn=await orch.run_turn(cmd,owner,owner['chat_id'],delegate)
            await scheduler.runners[owner['id']]
            report=executed[0]['worker_report']
            assert report['status']=='partial' and not report['goal_completed']
            assert len(dispatches)==1 and len(worker_calls)==3 and not narration_calls
            first=(await routes.api_get_task(turn['task_id'],None))['task']
            assert first['goal_presentation']['reply_source']=='orchestrator_llm'
            assert 'Не хватает B и C' in first['answer'] and 'полностью готово' not in first['answer']
            again=(await routes.api_get_task(turn['task_id'],None))['task']
            direct=await routes.api_goal(turn['goal_id'],owner['chat_id'],None)
            assert first['answer']==again['answer']==direct['presentation']['message']
            assert len(narration_calls)==1 and len(executed)==1
            restored=restore_task(owned_job(executed[0]['task_id'],owner['id']))
            assert restored['worker_report']['status']=='partial'
            assert restored['answer']=='Прочитаны все файлы, сравнение полностью готово.'
        finally:await scheduler.shutdown()
    asyncio.run(scenario())
