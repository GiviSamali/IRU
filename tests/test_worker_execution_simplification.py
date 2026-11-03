"""Worker execution contracts: local fake providers and disposable files only."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest

from server import database as db
from server.controller_tools import NON_PIPELINE_TOOLS, worker_tool_inventory
from server.runtime_state import tasks
from server.worker_reports import build_worker_report
from server.api_support import assess_action
from test_tool_only_protocol import _run_case, _message, _tool_call


def answer(text='Done', remaining=None):
    return _message(tool_calls=[_tool_call('answer','answer_text',
        {'result':text,'unresolved':remaining or []})])


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'agent'))
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'worker.sqlite')
    db.init_db()


def test_multiple_calls_one_turn_and_correlated_provider_history():
    seen=[]; sent=[]
    async def send(device, action, args):
        sent.append((device,action,args))
        return {'status':'success','returncode':0,'stdout':args['command'],'stderr':''}
    batch=_message(tool_calls=[_tool_call('a','execute_cmd',{'command':'whoami'}),
                              _tool_call('b','execute_cmd',{'command':'hostname'})])
    batch['choices'][0]['message']['reasoning_content']='independent observations'
    result=_run_case([batch,answer('Observed')],send_command_fn=send,captured=seen)
    assert len(seen)==2 and len(sent)==2
    assert all(request['tool_choice']=='auto' for request in seen)
    history=seen[1]['messages']
    assistant=next(m for m in history if m.get('tool_calls'))
    assert assistant['reasoning_content']=='independent observations'
    assert [c['id'] for c in assistant['tool_calls']]==['a','b']
    assert [m['tool_call_id'] for m in history if m['role']=='tool']==['a','b']
    steps=[c for c in result['commands'] if c.get('tool_name')=='execute_cmd']
    assert len(steps)==2 and all(c['iteration']==1 for c in steps)
    assert len({c['step_id'] for c in steps})==2


@pytest.mark.parametrize('effects',[['open'],[]])
def test_full_inventory_every_turn_and_no_route_completion(effects,monkeypatch):
    tid='inventory'
    monkeypatch.setitem(tasks,tid,{'task_id':tid,'window_effects':effects})
    seen=[]
    result=_run_case([_message(tool_calls=[_tool_call('c','execute_cmd',{'command':'hostname'})]),
                      answer('Observed')],poll_task_id=tid,captured=seen)
    assert len(seen)==2
    expected={t['function']['name'] for t in NON_PIPELINE_TOOLS}
    for request in seen:
        assert {t['function']['name'] for t in request['tools']}==expected
    assert result['commands'][0]['tool_name']=='execute_cmd'


@pytest.mark.parametrize('outcome',[{'status':'unknown','error':'timeout'},
                                  {'status':'unknown','completion_state':'unknown'}])
def test_unknown_in_group_blocks_remaining_mutation_but_allows_observation(outcome):
    sent=[]; seen=[]
    async def send(device, action, args):
        sent.append(action)
        return outcome if action=='execute_cmd' else {'status':'success','entries':[]}
    result=_run_case([_message(tool_calls=[
        _tool_call('a','execute_cmd',{'command':'opaque'}),
        _tool_call('b','write_content',{'path':'C:/x.txt','content':'x'}),
        _tool_call('c','list_dir',{'path':'C:/'})]),answer('Unknown',['outcome unknown'])],
        send_command_fn=send,captured=seen)
    assert sent==['execute_cmd','list_dir']
    assert result['commands'][1]['result']['error']=='mutation_blocked_after_unknown'
    assert {m['tool_call_id'] for m in seen[1]['messages'] if m['role']=='tool'}=={'a','b','c'}


def test_confirmation_epoch_does_not_authorize_rest_of_batch(monkeypatch):
    monkeypatch.setitem(tasks,'confirm',{'task_id':'confirm'})
    sent=[]
    async def send(device, action, args):
        sent.append(action)
        tasks['confirm']['_confirmation_epoch']=1
        return {'status':'success','returncode':0,'stdout':'observed','stderr':''}
    result=_run_case([_message(tool_calls=[_tool_call('a','execute_cmd',{'command':'opaque'}),
        _tool_call('b','execute_cmd',{'command':'other'})]),answer('First only',['second not run'])],
        poll_task_id='confirm',send_command_fn=send)
    assert sent==['execute_cmd']
    assert result['commands'][1]['result']['error']=='batch_stopped_after_confirmation'


def test_cancel_mid_group_records_unexecuted_calls(monkeypatch):
    monkeypatch.setitem(tasks,'cancel',{'task_id':'cancel'})
    sent=[]
    async def send(device, action, args):
        sent.append(action);tasks['cancel']['cancel_requested']=True
        return {'status':'success','returncode':0,'stdout':'one','stderr':''}
    result=_run_case([_message(tool_calls=[_tool_call('a','execute_cmd',{'command':'one'}),
        _tool_call('b','execute_cmd',{'command':'two'}),_tool_call('c','execute_cmd',{'command':'three'})])],
        poll_task_id='cancel',send_command_fn=send)
    assert len(sent)==1 and result['cancelled']
    assert result['commands'][-1]['result']['error']=='batch_not_executed'


def test_duplicate_call_ids_reject_before_any_dispatch():
    sent=[]
    async def send(*args):sent.append(args);return {}
    _run_case([_message(tool_calls=[_tool_call('a','execute_cmd',{'command':'one'}),
        _tool_call('a','execute_cmd',{'command':'two'})]),answer('Nothing run')],send_command_fn=send)
    assert not sent


def test_bad_arguments_do_not_discard_valid_independent_calls():
    sent=[];seen=[]
    async def send(device,action,args):
        sent.append(args['command']);return {'returncode':0,'stdout':'observed','stderr':''}
    bad=_tool_call('bad','execute_cmd');bad['function']['arguments']='{unfinished'
    result=_run_case([_message(tool_calls=[_tool_call('a','execute_cmd',{'command':'whoami'}),bad,
        _tool_call('b','execute_cmd',{'command':'hostname'})]),answer('Two completed')],send_command_fn=send,captured=seen)
    assert sent==['whoami','hostname'] and len(seen)==2
    assert result['commands'][1]['result']['error']=='invalid_tool_arguments_json'
    assert [m['tool_call_id'] for m in seen[1]['messages'] if m.get('role')=='tool']==['a','bad','b']


def test_failure_is_terminal_without_missing_answer(monkeypatch):
    result=_run_case([_message(tool_calls=[_tool_call('fail','answer_report_failure',{
        'message':'Cannot proceed','reason':'unsupported','recoverable':False,'suggested_next_action':'Use compatible device'})])])
    report=build_worker_report({'task_id':'failure','status':'done','device_ids':['device-1'],
        'commands':result['commands'],'answer':result['answer']})
    assert report['status']=='failed' and not report['goal_completed']
    assert report['error_code']!='terminal_answer_missing'
    assert 'Cannot proceed' in report['summary']


def test_write_self_verified_without_readback():
    from test_ff01_worker_execution import FileAgent, PROJECT, controller, call, answer as ffanswer
    agent=FileAgent()
    result=asyncio.run(controller([call('write_content',{'path':PROJECT,'content':'exact'}),ffanswer('Written')],agent.send))
    task={'task_id':'t','status':'done','device_ids':['pc'],'assigned_artifacts':[PROJECT],
          'commands':result['commands'],'answer':result['answer']}
    report=build_worker_report(task)
    assert report['goal_completed'] and report['artifacts'][0]['content_verified']
    assert [a for _,a,_ in agent.sent]==['write_content']


def test_file_preservation_visible_cli_cannot_bypass(monkeypatch):
    from test_ff01_worker_execution import FileAgent, PROJECT
    agent=FileAgent();seen=[]
    monkeypatch.setitem(tasks,'preserve',{'task_id':'preserve','preserve_file_bytes':True})
    result=_run_case([_message(tool_calls=[_tool_call('x','execute_cmd',{'command':'rewrite'})]),
                      answer('Blocked',['not changed'])],poll_task_id='preserve',device_info={'file_replace_protocol':2},
                      send_command_fn=agent.send,captured=seen)
    assert not agent.sent and result['commands'][0]['result']['dispatched'] is False
    schema=next(t for t in seen[0]['tools'] if t['function']['name']=='execute_cmd')
    assert 'BLOCKED' in schema['function']['description']


@pytest.mark.parametrize('command',['python helper.py','powershell -EncodedCommand Zm9v',
    "python -c \"import os;os.remove('x')\"",'echo OK','curl https://example.invalid'])
def test_execution_permission_preserves_exact_action_identity(command):
    a=assess_action('execute_cmd',{'command':command,'artifact_paths':[]})
    b=assess_action('execute_cmd',{'command':command+' changed','artifact_paths':[]})
    assert a['decision']==('CONFIRM' if 'os.remove' in command else 'ALLOW')
    assert a['fingerprint']!=b['fingerprint']


def test_unavailable_capabilities_do_not_disappear():
    tools=worker_tool_inventory(NON_PIPELINE_TOOLS,{})
    assert len(tools)==len(NON_PIPELINE_TOOLS)
    assert 'UNAVAILABLE' in next(t['function']['description'] for t in tools if t['function']['name']=='read_file')


def test_compact_history_can_retrieve_full_own_step(monkeypatch):
    from server import controller_non_pipeline as loop
    async def search(query,max_results):return {'results':[{'url':'https://example.invalid','content':query+'x'*18000}]}
    monkeypatch.setattr(loop,'run_web_search',search)
    seen=[]
    result=_run_case([_message(tool_calls=[_tool_call('a','web_search',{'query':'first'})]),
        _message(tool_calls=[_tool_call('b','web_search',{'query':'second'})]),
        _message(tool_calls=[_tool_call('c','system_get_last_run_summary',{'step_id':'step_1','report_offset':0})]),
        answer('Read')],captured=seen)
    observations=[json.loads(m['content']) for m in seen[2]['messages'] if m.get('role')=='tool']
    assert observations[0]['step_id']=='step_1'
    assert len(observations[0]['result']['results'][0]['content'])>18000
    page=next(c['result'] for c in result['commands'] if c.get('tool_name')=='system.get_last_run_summary')
    assert page['status']=='success' and 0 < page['next_offset'] <= 4000 and 'first' in page['report_text']
    assert len(result['commands'][0]['result']['results'][0]['content'])>18000


@pytest.mark.parametrize('tool,data',[('office_docx',{'elements':[{'type':'paragraph','text':'Actual text'}]}),
    ('office_xlsx',{'sheets':[{'name':'Data','rows':[['Actual text',42]]}]}),
    ('office_pptx',{'slides':[{'title':'Actual text','bullets':['Fact']} ]})])
def test_office_operation_returns_its_own_verified_inspection(tmp_path,tool,data):
    from agent.core.office_tools import office
    path=tmp_path/('result.'+tool.split('_')[1])
    result=office(tool,'create',str(path),data=data,_source_task_id='t')
    assert result.get('status')=='success',result
    assert result['operation_verified'] is True and result['items']
    assert result['next_offset'] is None
    assert result['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()


def test_saved_operation_mismatch_refuses_before_commit(tmp_path,monkeypatch):
    from agent.core import office_tools
    original=office_tools.verify_saved_operation
    def corrupt(tool,intended,reopened):
        reopened.paragraphs[0].text='lost content'
        return original(tool,intended,reopened)
    monkeypatch.setattr(office_tools,'verify_saved_operation',corrupt)
    path=tmp_path/'bad.docx'
    result=office_tools.office('office_docx','create',str(path),data={'elements':[{'type':'paragraph','text':'Keep'}]})
    assert result['status']=='error' and not path.exists() and result['committed'] is False


def test_research_journal_is_required_material_even_if_answer_is_short():
    from server.worker_scheduler import dependency_context
    data={'task_id':'research','answer':'Short findings','report':{'status':'success','goal_completed':True},
          'commands':[{'tool_name':'web_search','result':{'facts':['fact1','fact2'],'sources':['url']}}]}
    material,pending=dependency_context([{'result_id':'r','sha256':'a'*64,'data':data}])
    assert not pending and not material[0]['requires_full_read']
    assert material[0]['research_material'][0]['result']['facts']==['fact1','fact2']
    assert material[0]['historical_material_only'] and not material[0]['current_execution_evidence']


from server.run_journal import append_answer_step, validate_answer_text_payload


def test_cli_open_and_native_open_use_same_observed_object_contract():
    observed={'observed_path':'C:/a.docx','title':'a - Word','process':'winword.exe','class_name':'OpusApp'}
    result={'status':'success','requested_object':{'type':'file','path':'C:/a.docx'},
            'observed_object':observed,'object_open_verified':True,'completion_state':'success'}
    reports=[]
    for tool in ('app.launch','app.verify_launch'):
        journal=[{'step_id':'step_1','tool_name':tool,'target_device_id':'pc','result':result}]
        append_answer_step(journal,'answer_text',validate_answer_text_payload({
            'status':'success','text':'Opened the requested document'},journal))
        reports.append(build_worker_report({'task_id':'open','status':'done','device_ids':['pc'],
            'window_effects':['open'],
            'open_objects':[{'type':'file','path':'C:/a.docx'}],
            'commands':journal}))
    assert all(r['goal_completed'] for r in reports)
    bad=copy.deepcopy(result);bad['object_open_verified']=False
    report=build_worker_report({'task_id':'open','status':'done','device_ids':['pc'],'window_effects':['open'],
        'open_objects':[{'type':'file','path':'C:/a.docx'}],
        'commands':[{'step_id':'step_1','tool_name':'execute_cmd','target_device_id':'pc',
                     'result':{'returncode':0,'stdout':'OK'}}]})
    assert not report['goal_completed']


@pytest.mark.parametrize('method',['office_pptx','execute_cmd'])
@pytest.mark.parametrize('coverage',['complete','partial'])
def test_office_and_cli_receipts_do_not_reassess_worker_content_verdict(tmp_path,monkeypatch,method,coverage):
    from agent.core import actions
    from agent.core.office_tools import office
    path=tmp_path/'result.pptx'
    def create():
        return office('office_pptx','create',str(path),data={'slides':[{'title':'Actual fact'}]},_source_task_id='t')
    if method=='office_pptx':
        result=create()
    else:
        def command(*args,**kwargs):
            assert create()['status']=='success'
            return {'returncode':0,'stdout':'Command finished','stderr':''}
        monkeypatch.setattr(actions.platform_mod,'execute_cmd',command)
        result=actions.execute_cmd('synthetic creation',artifact_paths=[str(path)],_artifact_task_id='t')
    for receipt in result['artifact_receipts']:receipt['device_id']='pc'
    step={'step_id':'step_1','tool_name':method,'status':'success','target_device_id':'pc','result':result}
    from server.run_journal import validate_answer_text_payload,append_answer_step
    journal=[step]
    payload=validate_answer_text_payload({'status':'success' if coverage=='complete' else 'partial',
        'result':'Prepared' if coverage=='complete' else 'Required material missing',
        'unresolved':[] if coverage=='complete' else ['Required material missing']},journal,require_status=True)
    append_answer_step(journal,'answer_text',payload,target_device_id='pc')
    task={'task_id':'t','status':'done','original_request':'Prepare a factual presentation',
        'assigned_artifacts':[str(path)],'device_ids':['pc'],'commands':journal,'answer':'Prepared'}
    report=build_worker_report(task)
    assert report['artifacts'][0]['verified'] is True
    assert report['goal_completed']==(coverage=='complete')
    assert journal[0]['result']==result  # Original native inspection remains in the journal.


def test_cli_zero_and_echo_cannot_certify_nonexistent_artifact(tmp_path,monkeypatch):
    from agent.core import actions
    monkeypatch.setattr(actions.platform_mod,'execute_cmd',lambda *a,**k:{'returncode':0,'stdout':'OK','stderr':''})
    result=actions.execute_cmd('echo OK',artifact_paths=[str(tmp_path/'absent.pptx')],_artifact_task_id='t')
    assert not result['artifact_receipts'] and result['artifact_errors'] and result['status']=='partial'


def test_observation_does_not_launch_and_is_bound_to_actual_object(monkeypatch):
    from agent.core import actions
    calls=[]
    monkeypatch.setattr(actions,'_open_object_kind',lambda path:'file')
    def observe(kind,path,timeout):
        calls.append((kind,path))
        return {'observed_path':path,'title':'file - Word','class_name':'OpusApp','process':'winword.exe'}
    monkeypatch.setattr(actions,'_observe_opened_object',observe)
    monkeypatch.setattr(actions.subprocess,'Popen',lambda *a,**k:pytest.fail('Observation must not launch'))
    result=actions.app_verify_launch(42,object_path='C:/file.docx')
    assert result['object_open_verified'] and calls==[('file','C:/file.docx')]


def test_old_agent_object_observation_is_rejected_before_delivery(monkeypatch):
    from server import task_runtime as runtime
    class Socket:
        async def send_text(self,*args):pytest.fail('Incompatible parameters must not reach Agent')
    monkeypatch.setitem(runtime.devices,'1:pc',{'user_id':1,'info':{},'ws':Socket(),'pending':{}})
    result=asyncio.run(runtime.send_command_to_agent('1:pc','app.verify_launch',
        {'pid':42,'object_path':'C:/file.docx'},user_id=1))
    assert result['status']=='blocked' and result['dispatched'] is False


def test_launch_confirms_known_destructive_commands():
    for command in ('python -c "dangerous()"','powershell -File helper.ps1','cmd /c del x'):
        assert assess_action('app.launch',{'command':command})['decision']==('CONFIRM' if 'del x' in command else 'ALLOW')


def test_research_large_material_has_access_checked_paged_reference():
    from server.worker_scheduler import dependency_context
    data={'task_id':'research','answer':'Short findings','report':{'status':'success','goal_completed':True},
          'commands':[{'tool_name':'web_search','result':{'content':'evidence'*5000}}]}
    material,pending=dependency_context([{'result_id':'r','sha256':'a'*64,'data':data}])
    assert material[0]['requires_full_read'] and 'r' in pending
    assert material[0]['full_material_tool']=='system_get_last_run_summary'


def test_cli_office_receipt_and_stdout_cannot_hide_missing_content_pages():
    path='C:/report.pptx';digest='a'*64
    receipt={'path':path,'sha256':digest,'verified':True,'size_bytes':1000,
        'source_task_id':'t','device_id':'pc','created_or_changed':True,'structure':'office_zip_xml'}
    inspection={'status':'success','path':path,'sha256':digest,'items':[{'text':'first'}],
                'offset':0,'next_offset':1,'total_items':2}
    create={'tool_name':'execute_cmd','step_id':'step_1','target_device_id':'pc','result':{
        'returncode':0,'stdout':'OK: file exists','stderr':'',
        'artifact_receipts':[receipt],'artifact_observations':[inspection]}}
    payload={'text':'Observed content'}
    task={'task_id':'t','status':'done','original_request':'request','device_ids':['pc'],
        'assigned_artifacts':[path],'answer':'Observed content','commands':[
            create,{'tool_name':'answer.text','status':'terminal','result':payload}]}
    report=build_worker_report(task)
    assert not report['goal_completed'] and not report['artifacts'][0]['content_observed']


def test_server_worker_uses_same_batch_and_failure_protocol(monkeypatch):
    from server import controller as controller_module, controller_non_pipeline as loop
    from server.controller_onboarding import process_onboarding_message
    from server.memory_intent_guard import ORIGINAL_WORKER_REQUEST, MEMORY_AUTHORIZATION, bind_memory_authorization
    seen=[]
    async def search(query,max_results):return {'results':[{'url':'https://example.invalid','content':query}]}
    monkeypatch.setattr(loop,'run_web_search',search)
    replies=[_message(tool_calls=[_tool_call('a','web_search',{'query':'first'}),
                                _tool_call('b','web_search',{'query':'second'}),
                                _tool_call('link','get_file_link',{'file_path':'C:/absent.txt'}),
                                _tool_call('device','device_get_passport',{})]),
        _message(tool_calls=[_tool_call('f','answer_report_failure',{'message':'Missing required fact',
            'reason':'insufficient_sources','recoverable':False,'suggested_next_action':''})])]
    async def provider(**kwargs):seen.append(copy.deepcopy(kwargs['messages']));return replies.pop(0)
    monkeypatch.setattr(controller_module,'_chat_completion_request',provider)
    token=ORIGINAL_WORKER_REQUEST.set('Research facts')
    permission=MEMORY_AUTHORIZATION.set(bind_memory_authorization('Research facts',[]))
    try:
        result=asyncio.run(process_onboarding_message(user_message='Research facts',chat_history=[],
            usage_context={'worker_execution':True},load_llm_config_fn=lambda:{'model':'fake'},
            current_datetime_msk_fn=lambda:'2026-11-01'))
    finally:
        MEMORY_AUTHORIZATION.reset(permission);ORIGINAL_WORKER_REQUEST.reset(token)
    assert len(seen)==2
    assert [c['tool_name'] for c in result['commands']]==[
        'web_search','web_search','get_file_link','device.get_passport','answer.report_failure']
    assert all(c['status']=='blocked' for c in result['commands'][2:4])
    assert result['answer']=='Missing required fact'


def test_verified_text_bytes_do_not_override_explicit_missing_content():
    path='C:/short.txt';digest='a'*64
    step={'tool_name':'write_content','step_id':'step_1','target_device_id':'pc',
          'result':{'path':path,'verified':True,'committed':True,'file_sha256':digest,'total_size':5}}
    payload={'_result_contract':3,'text':'Only a heading','unresolved':['The required report body is missing.']}
    report=build_worker_report({'task_id':'t','status':'done','device_ids':['pc'],
        'original_request':'Write the full report','assigned_artifacts':[path],
        'commands':[step,{'tool_name':'answer.text','status':'terminal','result':payload}],'answer':'Only a heading'})
    assert not report['goal_completed'] and report['artifacts'][0]['verified']


def test_substantive_unresolved_is_not_discarded_for_lacking_filename():
    path='C:/x.txt'
    step={'tool_name':'write_content','step_id':'step_1','target_device_id':'pc',
          'result':{'path':path,'committed':True,'verified':True,'file_sha256':'a'*64,'total_size':1}}
    task={'task_id':'t','status':'done','device_ids':['pc'],'assigned_artifacts':[path],
        'commands':[step]}
    append_answer_step(task['commands'],'answer_text',validate_answer_text_payload({
        'status':'partial','text':'First part','unresolved':['Required comparison not performed']},task['commands']))
    assert not build_worker_report(task)['goal_completed']
    task['commands']=[step]
    append_answer_step(task['commands'],'answer_text',validate_answer_text_payload({
        'status':'success','text':'Required work completed','remaining':['Optional further comparison']},task['commands']))
    assert build_worker_report(task)['goal_completed']


@pytest.mark.parametrize('count,observed',[(1,True),(1,False),(2,False)])
def test_find_pptx_conditional_cli_open_or_list(monkeypatch,count,observed):
    request='Find desktop PPTX: open if one, otherwise list them.'
    task={'task_id':'conditional','goal_id':'g',
          'original_request':request,'proposed_objective':request,'device_ids':['device-1']}
    monkeypatch.setitem(tasks,'conditional',task)
    files=[f'C:/Desktop/{i}.pptx' for i in range(count)];sent=[]
    async def send(device,action,args):
        sent.append(action)
        if action=='execute_cmd':
            return {'returncode':0,'stdout':json.dumps(files) if args['command']=='discover' else 'pid=42','stderr':''}
        return {'status':'success' if observed else 'unknown','completion_state':'success' if observed else 'unknown',
            'requested_object':{'type':'file','path':files[0]},'object_open_verified':observed,
            'observed_object':{'observed_path':files[0],'title':'0 - PowerPoint','process':'powerpnt.exe','class_name':'PPTFrameClass'}}
    replies=[_message(tool_calls=[_tool_call('d','execute_cmd',{'command':'discover'})])]
    if count==1:
        replies.extend([_message(tool_calls=[_tool_call('o','execute_cmd',{'command':'open'})]),
            _message(tool_calls=[_tool_call('v','app_verify_launch',{'pid':42,'object_path':files[0]})])])
    replies.append(answer('Opened' if count==1 else '\n'.join(files)))
    refs=[f'step_{i+1}' for i in range(3 if count==1 else 1)]
    replies.append(_message(content=json.dumps({'coverage':'complete','evidence_refs':refs,'reason':'Conditional branch satisfied'}),finish_reason='stop'))
    result=_run_case(replies,poll_task_id='conditional',user_message=request,send_command_fn=send,
                     cfg={'model':'fake'})
    report=build_worker_report({**task,'status':'done','commands':result['commands'],'answer':result['answer']})
    assert report['goal_completed']==(count>1 or observed)
    assert sent==(['execute_cmd','execute_cmd','app.verify_launch'] if count==1 else ['execute_cmd'])
