"""One executor verdict; report/Goal never reassess Assignment semantics."""
import pytest

from server.run_journal import validate_answer_text_payload, ProtocolValidationError, append_answer_step
from server.worker_reports import build_worker_report
from server.tool_completion import confirmed_window_effect
from test_tool_only_protocol import _run_case, _message, _tool_call
from test_orchestrator_worker import owners


def operation(status='success', **result):
    return {'task_id':'t','step_id':'step_1','tool_name':'app.launch','tool_type':'typed',
        'target_device_id':'pc','result':{'status':status,**result}}


@pytest.mark.parametrize('status',['success','partial','failed','unknown'])
def test_report_projects_explicit_verdict_without_rechecking_assignment(status):
    steps=[operation(os_open_accepted=True,launch_state='accepted',requested_object={'type':'file','path':'C:/a.pptx'})]
    payload=validate_answer_text_payload({'status':status,'evidence_refs':['step_1'],
        'result':'OS accepted opening request','unresolved':[]},steps,require_status=True)
    append_answer_step(steps,'answer_text',payload,target_device_id='pc')
    report=build_worker_report({'task_id':'t','status':'done','device_ids':['pc'],
        'commands':steps,'window_effects':['open'],'open_objects':[{'type':'file','path':'C:/a.pptx','device_id':'pc'}]})
    assert report['status']==status
    assert report['goal_completed']==(status=='success')
    assert report['evidence_refs']==['step_1']


@pytest.mark.parametrize('payload',[
    {'result':'done'},
    {'status':'success','evidence_refs':['missing'],'result':'done'},
    {'status':'success','evidence_refs':['step_1'],'result':'done','unresolved':['not finished']},
])
def test_bad_terminal_is_rejected_inside_executor(payload):
    with pytest.raises(ProtocolValidationError):
        validate_answer_text_payload(payload,[operation()],require_status=True)


def test_unknown_mutation_cannot_become_success():
    with pytest.raises(ProtocolValidationError,match='mutation outcome'):
        validate_answer_text_payload({'status':'success','evidence_refs':['step_1'],'result':'done'},
            [operation()],require_status=True,task={'task_id':'t','_action_outcome_unknown':True})


def test_a_real_error_is_a_valid_source_for_a_completed_diagnostic():
    payload=validate_answer_text_payload({'status':'success','evidence_refs':['step_1'],
        'result':'The opening failed because the OS refused the request.'},
        [operation('failed',error='OS refused')],require_status=True)
    assert payload['status']=='success'
    # A tool outcome is not the verdict on the user's diagnostic Assignment.


def test_success_cannot_discard_all_instrumental_sources():
    with pytest.raises(ProtocolValidationError,match='instrumental sources'):
        validate_answer_text_payload({'status':'success','evidence_refs':[],'result':'done'},
            [operation()],require_status=True)


def test_os_acceptance_is_open_not_document_observation():
    receipt=operation(os_open_accepted=True,launch_state='accepted',
        requested_object={'type':'file','path':'C:/a.pptx'},object_open_verified=False,observed_object=None)
    assert confirmed_window_effect(receipt)=='open'
    assert confirmed_window_effect({**receipt,'tool_name':'execute_cmd',
        'result':{**receipt['result'],'returncode':0}}) is None
    assert confirmed_window_effect(operation('unknown',**{k:v for k,v in receipt['result'].items() if k!='status'})) is None


def test_loop_can_correct_terminal_without_extra_observation_or_assessor():
    captured=[]
    replies=[_message(tool_calls=[_tool_call('read','execute_cmd',{'command':'read'})]),
        _message(tool_calls=[_tool_call('invalid','answer_text',{'status':'success','evidence_refs':['missing'],'result':'observed'})]),
        _message(tool_calls=[_tool_call('valid','answer_text',{'status':'success','evidence_refs':['step_1'],'result':'observed'})])]
    result=_run_case(replies,captured=captured)
    assert result['commands'][-1]['result']['status']=='success'
    assert len(captured)==3
    assert len([e for e in result['commands'] if e.get('tool_name')=='execute_cmd'])==1
    assert 'terminal_contract_condition' in str(captured[-1]['messages'])


def test_unknown_transport_guard_wins_over_persisted_worker_success():
    steps=[operation()]
    append_answer_step(steps,'answer_text',validate_answer_text_payload(
        {'status':'success','evidence_refs':['step_1'],'result':'done'},steps),target_device_id='pc')
    assert build_worker_report({'task_id':'t','status':'done','device_ids':['pc'],
        'commands':steps,'_action_outcome_unknown':True})['status']=='unknown'


def test_tool_error_is_repaired_before_terminal_with_no_second_evaluator():
    sent=[];captured=[]
    async def send(device,action,args):
        sent.append(args['command'])
        return {'returncode':1,'stderr':'bad argument','stdout':''} if len(sent)==1 else {'returncode':0,'stdout':'actual result','stderr':''}
    result=_run_case([
        _message(tool_calls=[_tool_call('bad','execute_cmd',{'command':'bad'})]),
        _message(tool_calls=[_tool_call('fix','execute_cmd',{'command':'correct'})]),
        _message(tool_calls=[_tool_call('done','answer_text',{'status':'success','evidence_refs':['step_2'],'result':'actual result'})]),
    ],send_command_fn=send,captured=captured)
    assert sent==['bad','correct']
    assert len(captured)==3
    assert 'bad argument' in str(captured[1]['messages'])
    assert result['commands'][-1]['result']['status']=='success'


def test_simple_open_needs_one_action_and_terminal_only():
    sent=[];captured=[]
    async def send(device,action,args):
        sent.append(action)
        return {'status':'success','completion_state':'success','os_open_accepted':True,
            'launch_state':'accepted','requested_object':{'type':'file','path':'C:/a.pptx'},
            'object_open_verified':False,'observed_object':None,'pid':None,'window':None,'next_actions':[]}
    result=_run_case([
        _message(tool_calls=[_tool_call('open','app_launch',{'command':'C:/a.pptx'})]),
        _message(tool_calls=[_tool_call('done','answer_text',{'status':'success','evidence_refs':['step_1'],
            'result':'Requested opening a.pptx in the default application.'})]),
    ],send_command_fn=send,captured=captured)
    assert sent==['app.launch']
    assert len(captured)==2
    assert [e['tool_name'] for e in result['commands']]==['app.launch','answer.text']


def test_foreign_evidence_cannot_support_terminal():
    with pytest.raises(ProtocolValidationError,match='another task'):
        validate_answer_text_payload({'status':'success','evidence_refs':['step_1'],'result':'done'},
            [operation()],require_status=True,task={'task_id':'different'})


def test_group_aggregates_worker_statuses_without_reassessing_receipts(owners):
    import asyncio
    from server.worker_scheduler import WorkerScheduler, goal_view
    from test_orchestrator_worker import task as task_template
    owner,_=owners
    async def execute(task):
        steps=[{**operation(), 'task_id':task['task_id']}]
        status='failed' if task['proposed_objective']=='send' else 'success'
        append_answer_step(steps,'answer_text',validate_answer_text_payload(
            {'status':status,'evidence_refs':['step_1'],'result':task['proposed_objective']},steps),target_device_id='pc')
        task.update(status='done',commands=steps,answer=task['proposed_objective'])
    async def scenario():
        scheduler=WorkerScheduler(execute)
        admitted=await scheduler.submit_goal(task_template(owner,'research, create, send'),
            [{'key':key,'objective':key,'artifacts':[],'depends_on':[]} for key in ['research','create','send']],request_key='executor-group')
        await scheduler.runners[owner['id']]
        g=goal_view(admitted['goal_id'],owner['id'],owner['chat_id'])
        assert [m['status'] for m in g['tasks']]==['success','success','failed']
        assert g['status']=='partial'
        assert not g['goal_completed']
    asyncio.run(scenario())


def test_multi_tool_batch_still_needs_only_one_followup():
    captured=[]
    result=_run_case([
        _message(tool_calls=[_tool_call('a','execute_cmd',{'command':'read A'}),
                            _tool_call('b','execute_cmd',{'command':'read B'})]),
        _message(tool_calls=[_tool_call('done','answer_text',{'status':'success',
            'evidence_refs':['step_1','step_2'],'result':'Compared A and B'})]),
    ],captured=captured)
    assert len(captured)==2
    assert len([e for e in result['commands'] if e.get('tool_name')=='execute_cmd'])==2
    assert result['commands'][-1]['result']['basis']==['step_1','step_2']


def test_real_negative_window_query_can_complete_an_answer():
    async def send(*args):return {'status':'not_found','matches':[]}
    result=_run_case([
        _message(tool_calls=[_tool_call('find','window_find',{'title_contains':'missing'})]),
        _message(tool_calls=[_tool_call('done','answer_text',{'status':'success',
            'evidence_refs':['step_1'],'result':'No matching window was found'})]),
    ],send_command_fn=send,user_message='Is that window present?')
    assert result['commands'][-1]['result']['status']=='success'


def test_report_success_does_not_turn_os_acceptance_into_window_verification():
    from server.response_presentation import worker_presentation
    steps=[operation(os_open_accepted=True,launch_state='accepted',requested_object={'type':'file','path':'C:/a.pptx'})]
    payload=validate_answer_text_payload({'status':'success','evidence_refs':['step_1'],
        'result':'Requested OS opening; loading was not checked.'},steps,require_status=True)
    append_answer_step(steps,'answer_text',payload,target_device_id='pc')
    task={'task_id':'t','status':'done','device_ids':['pc'],'commands':steps,'window_effects':['open']}
    assert worker_presentation(task)['conversational_response']==payload['text']


def test_unknown_mutation_is_not_replayed_even_when_model_requests_it():
    sent=[];captured=[]
    async def send(device,action,args):
        sent.append(action)
        return {'status':'unknown','error':'confirmed_transport_outcome_unknown'}
    result=_run_case([
        _message(tool_calls=[_tool_call('write','execute_cmd',{'command':'Set-Content C:/x.txt hello'})]),
        _message(tool_calls=[_tool_call('retry','execute_cmd',{'command':'Set-Content C:/x.txt hello'})]),
        _message(tool_calls=[_tool_call('done','answer_text',{'status':'unknown','evidence_refs':['step_1'],
            'result':'The action may have happened; it was not repeated.'})]),
    ],send_command_fn=send,captured=captured)
    assert sent==['execute_cmd']
    assert len(captured)==3
    assert result['commands'][-1]['result']['status']=='unknown'


def test_evidence_linking_is_automatic_for_normal_terminal():
    captured=[]
    result=_run_case([
        _message(tool_calls=[_tool_call('read','execute_cmd',{'command':'read'})]),
        _message(tool_calls=[_tool_call('done','answer_text',{'status':'success','result':'Read the requested source'})]),
    ],captured=captured)
    assert len(captured)==2
    assert result['commands'][-1]['result']['basis']==['step_1']


def test_goal_cannot_treat_missing_report_as_a_successful_worker(owners):
    import json,time
    from server import database as db
    from server.worker_scheduler import goal_view
    owner,_=owners
    with db.get_db() as c:
        c.execute('INSERT INTO worker_goals VALUES(?,?,?,?,?,?)',('integrity',owner['id'],owner['chat_id'],'request',1,time.time()))
        c.execute('INSERT INTO worker_jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('missing-report',owner['id'],owner['chat_id'],'success',json.dumps({'task_id':'missing-report'}),None,None,time.time(),time.time(),None))
        c.execute('INSERT INTO worker_goal_tasks VALUES(?,?,?)',('missing-report','integrity',1))
    g=goal_view('integrity',owner['id'],owner['chat_id'])
    assert g['tasks'][0]['status']=='unknown'
    assert g['status']=='unknown' and not g['goal_completed']


def test_native_integrity_failure_cannot_be_overridden_by_old_terminal():
    steps=[operation()]
    append_answer_step(steps,'answer_text',validate_answer_text_payload(
        {'status':'success','result':'done'},steps),target_device_id='pc')
    report=build_worker_report({'task_id':'t','status':'unknown','device_ids':['pc'],
        'commands':steps,'worker_error_code':'immutable_result_conflict'})
    assert report['status']=='unknown' and report['error_code']=='immutable_result_conflict'


def test_model_must_receive_new_action_results_before_its_final_verdict():
    sent=[];captured=[]
    async def send(device,action,args):
        sent.append(action)
        return {'returncode':0,'stdout':'ACTUAL RESULT','stderr':''}
    result=_run_case([
        _message(tool_calls=[_tool_call('action','execute_cmd',{'command':'read'}),
            _tool_call('premature','answer_text',{'status':'success','result':'Speculative result'})]),
        _message(tool_calls=[_tool_call('informed','answer_text',{'status':'success','result':'ACTUAL RESULT'})]),
    ],send_command_fn=send,captured=captured)
    assert len(captured)==2 and sent==['execute_cmd']
    assert 'ACTUAL RESULT' in str(captured[1]['messages'])
    assert 'have not been seen' in str(captured[1]['messages'])
    assert result['commands'][-1]['result']['text']=='ACTUAL RESULT'


def test_legacy_done_is_not_promoted_but_bound_historical_report_is_preserved():
    from server.worker_reports import result_binding,saved_worker_report
    task={'task_id':'legacy','status':'done','device_ids':['pc'],'commands':[
        {'tool_name':'answer.text','status':'terminal','result':{'text':'Old text','_result_contract':3}}]}
    report=build_worker_report(task)
    assert report['status']=='unknown' and report['error_code']=='legacy_terminal_status_missing'
    historical={**report,'status':'success','goal_completed':True,'error_code':None}
    task.update(worker_report=historical,worker_report_binding=result_binding(task,historical))
    assert saved_worker_report(task)==historical


def test_receipt_tampering_after_terminal_is_integrity_failure_not_reassessment():
    steps=[operation()]
    append_answer_step(steps,'answer_text',validate_answer_text_payload(
        {'status':'success','result':'done'},steps),target_device_id='pc')
    task={'task_id':'t','status':'done','device_ids':['pc'],'commands':steps}
    assert build_worker_report(task)['status']=='success'
    steps[0]['result']['status']='failed'
    report=build_worker_report(task)
    assert report['status']=='unknown'
    assert report['error_code']=='worker_evidence_binding_mismatch'
