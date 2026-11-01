"""Ordinary execution has one operation result and no automatic semantic judge.

Fake providers validate code contracts, never real-model quality. All real files
used by these tests are temporary files created by pytest when owner runs them.
"""
import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from server import database as db, task_runtime as runtime
from server.runtime_state import tasks
from server.tool_completion import normalize_operation_result
from server.worker_reports import build_worker_report, operation_facts
from server.run_journal import make_run_step, append_tool_step, validate_answer_text_payload
from server.tool_registry import canonical_tool_name, compact_device_passport, list_tools
from server.controller_tools import NON_PIPELINE_TOOLS
from test_tool_only_protocol import _run_case, _message, _tool_call


@pytest.fixture(autouse=True)
def local_only(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'operation-contract.sqlite')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'agent'))
    db.init_db()


def terminal(text='Operation completed',unresolved=None):
    return _message(tool_calls=[_tool_call('final','answer_text',{'result':text,'unresolved':unresolved or []})])


def step(tool,result,index=1,device='pc',command=''):
    return make_run_step(journal=[{'idx':i} for i in range(1,index)],tool_name=tool,
                         result=result,target_device_id=device,command=command)


def report(operations,*,artifacts=None,devices=None,unresolved=None,text='Operation completed',**fields):
    payload=validate_answer_text_payload({'result':text,'unresolved':unresolved or []},operations)
    end={'step_id':'terminal','tool_name':'answer.text','tool_type':'answer','status':'terminal','result':payload}
    return build_worker_report({'task_id':'t','status':'done','device_ids':['pc'] if devices is None else devices,
        'assigned_artifacts':artifacts or [],'answer':text,'commands':operations+[end],**fields})


def native_write(path='C:/a.txt',text='changed'):
    digest=hashlib.sha256(text.encode()).hexdigest()
    return {'status':'ok','verified':True,'committed':True,'path':path,
            'file_sha256':digest,'total_size':len(text.encode()),'bytes_written':len(text.encode())}


def ordinary_responses():
    # Representative shapes come from each actual producer/adapter, not a new runtime registry.
    responses={
      'execute_cmd':{'returncode':0},
      'write_content':native_write(),
      'read_file':{'status':'ok','path':'C:/a.txt','text':'a','complete':True,'file_sha256':'a'*64},
      'list_dir':{'path':'C:/','dirs':[],'files':[]},
      'get_file_link':{'status':'success','url':'/api/download/x','file_path':'C:/a.txt'},
      'web_search':{'answer':None,'results':[]},
      'remember_fact':{'status':'ok','fact_id':1},
      'forget_fact':{'status':'ok','result':'Fact deleted'},
      'memory.get_stats':{'status':'ok','facts_count':0},
      'memory.list_facts':{'status':'ok','facts':[],'facts_count':0},
      'system.list_tools':{'files':[],'web':[]},
      'system.get_last_run_summary':{'status':'success','source_status':'failed','last_task_id':'old','used_tools':[]},
      'device.get_passport':{'device_id':'pc','hostname':'pc','online':False,'context_handles':{},'health_status':'unknown'},
      'device.refresh_state':{'status':'ok','device_id':'pc','health_summary':{'health_status':'unknown'}},
      'device.activate':{'status':'ok','device_id':'pc','activation_summary':{'activation_status':'active'}},
      'device.repair_activation':{'status':'ok','device_id':'pc','activation_summary':{'activation_status':'active'}},
      'device.check_runtime':{'status':'success','device_id':'pc','mode':'check','runtime_summary':{'runtime_status':'missing'}},
      'device.prepare_runtime':{'status':'success','device_id':'pc','mode':'prepare','runtime_summary':{'runtime_status':'ok'}},
      'device.repair_runtime':{'status':'success','device_id':'pc','mode':'repair','runtime_summary':{'runtime_status':'ok'}},
      'window.control':{'status':'success','action':'activate','completion_state':'success'},
      'window.list':{'status':'ok','windows':[]},
      'window.find':{'status':'found','matches':[{'handle':42}]},
      'window.verify':{'status':'verified','verified':True,'window':{'handle':42}},
      'window.focus':{'status':'focused','window':{'handle':42}},
      'window.close':{'status':'closed','window_closed':True},
      'app.launch':{'status':'launched','pid':123,'process_alive':True},
      'app.verify_launch':{'status':'verified','verified':True,'window':{'handle':42}},
      'app.close':{'status':'closed','pid':123},
      'app.open_url':{'status':'opened_verified','completion_state':'success'},
      'transfer_file':{'status':'success','source_device':'pc','target_device':'phone','target_path':'/a.txt','sha256':'a'*64,'sha256_verified':True},
    }
    for name in ('web.tabs','web.read','web.elements','web.fill','web.activate','web.wait','web.focus'):
        responses[name]={'status':'success','tab_id':1,'page':{'document_id':'doc','revision':'rev'}}
    for suffix in ('docx','xlsx','pptx'):
        responses['office_'+suffix]={'status':'success','action':'create','path':'C:/a.'+suffix,
                                    'sha256':'a'*64,'verified':True,'artifact_receipts':[]}
    return responses


@pytest.mark.parametrize('tool',list(ordinary_responses()))
def test_all_ordinary_response_shapes_have_successful_operation_outcome(tool):
    assert normalize_operation_result(tool,ordinary_responses()[tool])=='success'


def test_fixture_matrix_covers_every_registered_nonterminal_worker_tool():
    names={canonical_tool_name(t['function']['name']) for t in NON_PIPELINE_TOOLS
           if not canonical_tool_name(t['function']['name']).startswith('answer.')}
    assert names<=set(ordinary_responses()), 'Add the REAL producer shape for newly registered tools'


def test_real_list_dir_and_passport_do_not_require_invented_status(tmp_path):
    from agent.core.actions import list_dir
    listing=list_dir(str(tmp_path))
    assert 'status' not in listing and normalize_operation_result('list_dir',listing)=='success'
    passport=compact_device_passport('pc',{'info':{'hostname':'pc'},'ws':None})
    assert passport['status']=='success'
    legacy={k:v for k,v in passport.items() if k!='status'}
    assert normalize_operation_result('device_get_passport',legacy)=='success'
    assert normalize_operation_result('system_list_tools',list_tools())=='success'


def test_past_task_failure_is_data_not_failure_to_read_summary(monkeypatch):
    from server import task_summary
    monkeypatch.setattr(task_summary,'tasks',{'old':{'task_id':'old','user_id':1,'chat_id':7,
        'status':'failed','created_at':1,'answer':'Failed earlier','commands':[]}})
    result=task_summary.get_last_run_summary(user_id=1,chat_id=7)
    assert result['status']=='success' and result['source_status']=='failed'
    assert normalize_operation_result('system_get_last_run_summary',result)=='success'
    legacy={**result,'status':'failed'};legacy.pop('source_status')
    assert normalize_operation_result('system_get_last_run_summary',legacy)=='success'


def test_one_cli_command_can_complete_all_assigned_actions_without_assessor(monkeypatch):
    monkeypatch.setitem(tasks,'t',{'task_id':'t','device_ids':['device-1'],
        'original_request':'Create a folder and rename a file'})
    seen=[];calls=[]
    async def send(device,action,args):
        calls.append(args['command']);return {'returncode':0}
    result=_run_case([_message(tool_calls=[_tool_call('cli','execute_cmd',{
        'command':'New-Item C:/out -ItemType Directory -ErrorAction Stop; Rename-Item C:/a.txt b.txt -ErrorAction Stop'})]),
        terminal('Folder created and file renamed')],poll_task_id='t',send_command_fn=send,captured=seen,
        cfg={'model':'mock'})
    assert len(seen)==2 and len(calls)==1 and all(x.get('phase')!='result_coverage' for x in seen)
    assert build_worker_report({**tasks['t'],'status':'done','commands':result['commands'],'answer':result['answer']})['goal_completed']


def test_required_missing_second_file_cannot_be_waived_by_worker_or_cli():
    operation=step('write_content',native_write('C:/word.txt'))
    result=report([operation],artifacts=['C:/word.txt','C:/slides.pptx'])
    assert result['status']=='partial' and not result['goal_completed']
    assert result['artifacts'][0]['verified']


@pytest.mark.parametrize('secondary',[
    {'status':'failed','error':'denied'}, {'status':'unknown','error':'timeout'}, {}, None])
def test_optional_failed_read_does_not_revoke_successful_primary_write(secondary):
    original=step('write_content',native_write())
    read=step('read_file',secondary,index=2)
    result=report([original,read],artifacts=['C:/a.txt'])
    assert result['goal_completed'] and result['artifacts'][0]['verified']
    assert operation_facts(original)=='success' and normalize_operation_result('read_file',secondary)=='failed'


def test_stale_journal_label_does_not_override_actual_confirmed_success():
    operation=step('execute_cmd',{'returncode':0},command='New-Item folder')
    operation['status']='unknown'
    assert operation_facts(operation)=='success' and report([operation])['goal_completed']


def test_explicit_unresolved_content_preserves_file_success_but_goal_partial():
    operation=step('write_content',native_write())
    result=report([operation],artifacts=['C:/a.txt'],unresolved=['Required comparison is missing'])
    assert result['status']=='partial' and result['artifacts'][0]['verified']
    assert operation_facts(operation)=='success'


def test_bad_final_reply_does_not_erase_confirmed_file_receipt():
    operation=step('write_content',native_write())
    bad={'step_id':'bad','tool_name':'tool_only_protocol','tool_type':'system','status':'failed',
         'result':{'error':'invalid_terminal_payload'}}
    result=build_worker_report({'task_id':'t','status':'done','device_ids':['pc'],'assigned_artifacts':['C:/a.txt'],
        'commands':[operation,bad]})
    assert result['goal_completed'] and result['artifacts'][0]['sha256']==native_write()['file_sha256']


def test_lost_mutation_stays_unknown_and_blocks_another_mutation(monkeypatch):
    monkeypatch.setitem(tasks,'lost',{'task_id':'lost','device_ids':['device-1']})
    sent=[]
    async def send(device,action,args):sent.append(action);return {}
    result=_run_case([_message(tool_calls=[_tool_call('a','execute_cmd',{'command':'python mutate.py'}),
        _tool_call('b','write_content',{'path':'C:/a.txt','content':'x'})]),
        terminal('First outcome unknown',['First mutation outcome unknown'])],poll_task_id='lost',send_command_fn=send)
    assert sent==['execute_cmd'] and tasks['lost']['_action_outcome_unknown']
    assert result['commands'][1]['result']['error']=='mutation_blocked_after_unknown'
    assert build_worker_report({**tasks['lost'],'status':'done','commands':result['commands'],'answer':result['answer']})['status']=='unknown'


@pytest.mark.parametrize('action,params,unknown',[
    ('execute_cmd',{'command':'New-Item C:/out'},True),('list_dir',{'path':'C:/'},False)])
def test_malformed_response_after_real_transport_boundary(action,params,unknown,monkeypatch):
    key='1:pc'
    class Socket:
        async def send_text(self,message):
            payload=json.loads(message)['payload']
            runtime.devices[key]['pending'][payload['id']].set_result(['malformed'])
    monkeypatch.setitem(runtime.devices,key,{'user_id':1,'info':{'os':'Windows'},'ws':Socket(),'pending':{}})
    monkeypatch.setattr(runtime,'get_device_profile',lambda *a,**k:None)
    async def scenario():
        if unknown:
            with pytest.raises(runtime.AgentOutcomeUnknown,match='malformed_response_after_dispatch'):
                await runtime.send_command_to_agent(key,action,params,user_id=1)
        else:
            result=await runtime.send_command_to_agent(key,action,params,user_id=1)
            assert result['status']=='failed' and result['error']=='tool_result_contract_error'
    asyncio.run(scenario())


def test_read_runtime_missing_is_successful_observation_not_unknown():
    legacy={'status':'missing','runtime_summary':{'runtime_status':'missing'},'mode':'check'}
    assert normalize_operation_result('device_check_runtime',legacy)=='success'
    assert normalize_operation_result('device_prepare_runtime',{'status':'failed','mode':'prepare'})=='failed'


def test_cli_failure_not_overridden_by_success_text_and_whole_cli_requires_a_terminal():
    failed=step('execute_cmd',{'returncode':7,'stdout':'Done'})
    assert report([failed])['status']=='failed'
    success=step('execute_cmd',{'returncode':0})
    assert not build_worker_report({'task_id':'t','status':'done','commands':[success]})['goal_completed']


def test_append_does_not_redecide_normalized_operation(monkeypatch):
    from server import run_journal
    operation=step('write_content',native_write())
    monkeypatch.setattr(run_journal,'operation_step_status',lambda *a,**k:pytest.fail('Already normalized'))
    journal=[];append_tool_step(journal,operation)
    assert journal[0] is operation and operation['status']=='success'


def test_broadcast_projection_reuses_worker_report_despite_optional_read_failure():
    operations=[step('write_content',native_write()),step('read_file',{'error':'denied'},index=2)]
    payload=validate_answer_text_payload({'result':'Saved','unresolved':[]},operations)
    operations.append({'tool_name':'answer.text','tool_type':'answer','status':'terminal','result':payload})
    result={'answer':'Saved','commands':operations}
    assert runtime._device_operation_outcome(result,{'task_id':'t','assigned_artifacts':['C:/a.txt']},'pc')=='ok'


def test_legacy_open_receipt_without_invented_object_requirement_completes():
    operation=step('app.launch',{'status':'launched_verified','window':{'visible':True,'title':'Folder'},'pid':1})
    assert report([operation],window_effects=['open'])['goal_completed']


def test_untyped_cli_can_apply_window_effects_but_not_bypass_missing_file():
    operation=step('execute_cmd',{'returncode':0},command='python open_and_arrange.py')
    assert report([operation],window_effects=['open','maximize'])['goal_completed']
    assert not report([operation],window_effects=['open'],artifacts=['C:/missing.pptx'])['goal_completed']


def test_remaining_content_quality_is_explicitly_trusted_to_worker(tmp_path,monkeypatch):
    from agent.core.office_tools import office
    path=tmp_path/'one-slide.pptx'
    native=office('office_pptx','create',str(path),data={'slides':[{'title':'One thin slide'}]},_source_task_id='t')
    assert native['status']=='success'
    for item in native['artifact_receipts']:item['device_id']='pc'
    operation=step('office_pptx',native)
    result=report([operation],artifacts=[str(path)],original_request='A deep and useful presentation about AI')
    # Deliberate limitation: code cannot judge "deep/useful" without trusting Worker.
    assert result['goal_completed'] and operation_facts(operation)=='success'
    missing=report([operation],artifacts=[str(path)],unresolved=['Required slides are absent'])
    assert missing['status']=='partial' and missing['artifacts'][0]['verified']


@pytest.mark.parametrize('tool,result,expected',[
    ('read_file',{'status':'unknown','error':'timeout'},False),
    ('execute_cmd',{'returncode':0},False),
    ('write_content',{},True)])
def test_durable_recovery_flag_does_not_spread_read_uncertainty(tool,result,expected):
    from server.worker_scheduler import unknown_recovery_required
    entry=step(tool,result);entry['status']='unknown'
    assert unknown_recovery_required({'commands':[entry]})==expected


def test_unknown_runtime_check_has_no_mutation_lock():
    from server.api_support import is_recovery_observation
    assert is_recovery_observation('device_check_runtime',{})
    assert is_recovery_observation('device.prepare_runtime',{'mode':'check'})
    assert not is_recovery_observation('device.prepare_runtime',{'mode':'repair'})


def test_untyped_omission_requires_honest_worker_accounting():
    # No new plan/LLM is introduced to parse two natural-language obligations.
    first=step('execute_cmd',{'returncode':0},command='New-Item C:/only-folder')
    request='Create a folder and rename a file'
    honest=report([first],original_request=request,unresolved=['File rename not performed'])
    assert honest['status']=='partial'
    falsely_claimed=report([first],original_request=request,text='Both actions completed')
    # This is the explicitly accepted trust boundary, not proof that rename happened.
    assert falsely_claimed['goal_completed']


@pytest.mark.parametrize('tool,result',[
    ('system.list_tools',{'files':[]}),
    ('system.get_last_run_summary',{'status':'success','last_task_id':'old','used_tools':[]})])
def test_real_system_observation_is_not_discarded_as_protocol_event(tool,result):
    operation=step(tool,result)
    assert operation['tool_type']=='system'
    outcome=report([operation],devices=[])
    assert outcome['goal_completed'] and operation['step_id'] in outcome['evidence_refs']


def test_analysis_of_supplied_text_can_finish_without_unnecessary_tool():
    end={'step_id':'step_1','tool_name':'answer.text','tool_type':'answer','status':'terminal',
         'result':{'text':'Summary of the supplied text','_result_contract':3,'unresolved':[]}}
    outcome=build_worker_report({'task_id':'t','status':'done','device_ids':[],
        'original_request':'Summarize this provided text','commands':[end]})
    assert outcome['goal_completed'] and outcome['evidence_refs']==['step_1']


def test_legacy_launch_binds_actual_native_argv_without_new_required_fields():
    path='C:/Desktop/Folder'
    operation=step('app.launch',{'status':'launched_verified','command':'explorer.exe '+path,
        'args':[path],'window':{'visible':True,'title':'Folder'}})
    assert report([operation],window_effects=['open'],open_objects=[{'type':'folder','path':path}])['goal_completed']
    # Known conflicting identity is not rescued by the submitted command.
    operation['result']['observed_object']={'observed_path':'C:/Different'}
    assert not report([operation],window_effects=['open'],open_objects=[{'type':'folder','path':path}])['goal_completed']


def test_known_new_file_version_blocks_stale_goal_proof_without_revoking_original_write():
    original=step('write_content',native_write())
    changed=step('read_file',{'status':'ok','path':'C:/a.txt','text':'external edit','complete':True,
        'file_sha256':hashlib.sha256(b'external edit').hexdigest()},index=2)
    result=report([original,changed],artifacts=['C:/a.txt'])
    assert not result['goal_completed'] and result['status']=='partial'
    assert operation_facts(original)=='success' and operation_facts(changed)=='success'


@pytest.mark.parametrize('tool,result,expected',[
    ('list_dir',{'path':'C:/','dirs':[],'files':[]},'failed'),
    ('execute_cmd',{'returncode':0},'unknown')])
def test_corrupt_report_refuses_completion_without_inventing_read_mutation(tool,result,expected):
    outcome=report([step(tool,result)],_report_integrity_failed=True)
    assert outcome['status']==expected and not outcome['goal_completed']


@pytest.mark.parametrize('platform_name',['windows','linux'])
def test_known_spawn_failure_is_not_a_lost_mutation(platform_name,monkeypatch):
    import importlib
    from types import SimpleNamespace
    platform=importlib.import_module('agent.platforms.'+platform_name)
    def missing(*a,**k):raise FileNotFoundError('shell executable missing')
    monkeypatch.setattr(platform.subprocess,'run',missing)
    if platform_name=='windows':
        monkeypatch.setattr(platform.subprocess,'STARTUPINFO',lambda:SimpleNamespace(dwFlags=0,wShowWindow=0),raising=False)
        monkeypatch.setattr(platform.subprocess,'STARTF_USESHOWWINDOW',1,raising=False)
        monkeypatch.setattr(platform.subprocess,'CREATE_NO_WINDOW',0,raising=False)
    result=platform.execute_cmd('program')
    assert result['status']=='failed' and result['dispatched'] is False
    assert normalize_operation_result('execute_cmd',result)=='not_executed'


@pytest.mark.parametrize('required',['C:/required.txt','required.txt','/tmp/required.txt'])
def test_single_wrong_file_is_not_silently_matched_to_literal_requirement(required):
    actual=step('write_content',native_write('C:/wrong.txt'))
    outcome=report([actual],artifacts=[required])
    assert outcome['status']=='partial' and not outcome['goal_completed']
    assert outcome['artifacts'][0]['path']=='C:/wrong.txt' and outcome['artifacts'][0]['verified']


@pytest.mark.parametrize('status',['failed','unknown'])
@pytest.mark.parametrize('summary',[{}, {'runtime_status':'ok'}, {'runtime_status':'missing'}])
def test_runtime_summary_never_overrides_negative_operation_status(status,summary):
    result={'status':status,'runtime_summary':summary}
    assert normalize_operation_result('device.check_runtime',result)=='failed'
    assert normalize_operation_result('device_check_runtime',result)=='failed'


@pytest.mark.parametrize('completion',['failed','unknown','partial_success'])
def test_runtime_summary_respects_completion_state(completion):
    result={'status':'success','completion_state':completion,'runtime_summary':{'runtime_status':'ok'}}
    assert normalize_operation_result('device.check_runtime',result)==('partial' if completion=='partial_success' else 'failed')


@pytest.mark.parametrize('negative',[{'returncode':1},{'returncode':0,'status':'started'}])
def test_failed_or_pending_mutation_cannot_be_cured_by_later_read(negative):
    mutate=step('execute_cmd',negative,command='python patch.py')
    read=step('read_file',{'status':'ok','text':'existing','complete':True,'path':'C:/a.txt','file_sha256':'a'*64},index=2)
    assert not report([mutate,read])['goal_completed']


def test_legacy_write_ack_is_successful_operation_without_fabricated_hash_proof():
    result={'status':'ok','path':'C:/a.txt','bytes_written':1}
    assert normalize_operation_result('write_content',result)=='success'
    result['mode']='replace_exact'
    assert normalize_operation_result('write_content',result)=='unknown'
