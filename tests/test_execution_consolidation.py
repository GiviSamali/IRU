"""Controlled consolidation scenarios; no real commands, provider or device."""
import hashlib
import json
from pathlib import Path

import pytest

from server import database as db
from server.tool_completion import normalize_operation_result, operation_step_status
from server.run_journal import validate_answer_text_payload, append_answer_step
from server.worker_reports import build_worker_report, result_binding, receipt_from_report
from server.response_presentation import worker_presentation, normalized_worker_report
from server.worker_scheduler import _final_snapshot, restore_task
from test_tool_only_protocol import _run_case, _message, _tool_call


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'consolidation.sqlite')
    db.init_db()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_step(path, before=b'old', after=b'new', index=1, device='pc'):
    return {'step_id':f'step_{index}','tool_name':'write_content','tool_type':'typed',
            'target_device_id':device,'status':'success','result':{
                'status':'ok','path':str(path),'bytes_written':len(after),'total_size':len(after),
                'previous_sha256':digest(before) if before is not None else None,
                'file_sha256':digest(after),'verified':True,'committed':True}}


def task(path, commands, answer='Changed file.'):
    return {'task_id':'local','worker_id':'worker-1','status':'done','device_ids':['pc'],
            'original_request':f'Change {path}', 'assigned_artifacts':[str(path)],
            'commands':commands,'answer':answer}


def with_content_check(t, path, content=b'new'):
    """Explicit read-back plus bound comparison, independent of the write receipt."""
    from server.worker_reports import coverage_binding, coverage_request
    steps=t['commands']
    steps.append({'step_id':'step_content','tool_name':'read_file','target_device_id':'pc',
        'result':{'status':'success','path':str(path),'sha256':digest(content),'content':content.decode()}})
    payload={'text':t['answer'],'_coverage':{'coverage':'complete','evidence_refs':['step_content'],
        'binding':coverage_binding(coverage_request(t),steps,t['answer'])}}
    steps.append({'step_id':'step_final','tool_name':'answer.text','status':'terminal','result':payload})
    return t


@pytest.mark.parametrize('result,expected', [
    ({'returncode':0,'stdout':''},'success'),
    ({'status':'unknown','returncode':0,'stdout':'maybe'},'unknown'),
    ({'status':'partial','returncode':0,'stdout':'subset'},'partial'),
    ({'returncode':1,'stdout':'','stderr':'failed'},'failed'),
    ({'dispatched':False,'error':'denied'},'not_executed'),
    ({'returncode':True,'stdout':'not a valid exit code'},'unknown'),
])
def test_one_normalizer(result, expected):
    from server.tool_registry import tool_log_entry
    from server.run_journal import make_run_step
    assert normalize_operation_result('execute_cmd',result)==expected
    step=operation_step_status('execute_cmd',result)
    assert tool_log_entry('execute_cmd',result)['tool_status'] == make_run_step(journal=[],tool_name='execute_cmd',result=result)['status'] == step



def test_verified_file_change_completes_typed_contract(tmp_path):
    path=tmp_path/'scene.json';t=with_content_check(task(path,[write_step(path)]),path)
    report=build_worker_report(t)
    assert report['status']=='success' and report['goal_completed']


def test_undo_invalidates_earlier_change(tmp_path):
    path=tmp_path/'scene.json'
    report=build_worker_report(task(path,[write_step(path),write_step(path,b'new',b'old',2)]))
    assert report['status']=='partial' and not report['goal_completed']
    assert report['artifacts'][0]['sha256']==digest(b'old')


@pytest.mark.parametrize('semantic_complete',[False,True])
def test_f7477289_synthetic_final_version_overrides_false_success(tmp_path,semantic_complete):
    # Reconstructed description, not the unavailable historical journal/path.
    path=tmp_path/'synthetic-atlas.js'
    original=b'const nodeSize = 0.42;'
    smaller=b'const nodeSize = 0.21;'
    steps=[write_step(path,original,smaller,4),
           write_step(path,smaller,original,18),
           write_step(path,original,smaller,19),
           write_step(path,smaller,original,20)]
    t=task(path,steps,'Размер узлов уменьшен до 0.21.')
    if semantic_complete:
        from server.worker_reports import coverage_binding
        payload=validate_answer_text_payload({'answer':t['answer']},steps)
        payload['_coverage']={'coverage':'complete','evidence_refs':['step_4'],
            'binding':coverage_binding(t['original_request'],steps,t['answer'])}
        append_answer_step(steps,'answer_text',payload)
    report=build_worker_report(t)
    assert report['goal_completed'] is False and report['status']=='partial'
    assert report['artifacts'][0]['sha256']==digest(original)
    assert report['error_code']=='required_file_change_reverted'
    assert 'не сохранилось' in report['summary']
    assert 'уменьшен до 0.21' not in report['summary']
    assert 'уменьшен до 0.21' not in worker_presentation(t)['conversational_response']
    snapshot=_final_snapshot(t,report,t)
    restored=restore_task({'task_id':t['task_id'],'payload':json.dumps(snapshot),
        'state':report['status'],'report':json.dumps(report)})
    assert not normalized_worker_report(restored)['goal_completed']
    assert 'уменьшен до 0.21' not in worker_presentation(restored)['conversational_response']


def test_failed_helper_does_not_erase_verified_deliverable(tmp_path):
    path=tmp_path/'report.txt'
    failed={'step_id':'step_2','tool_name':'execute_cmd','tool_type':'control','status':'failed',
            'result':{'returncode':127,'stdout':'useful observation','stderr':'BOM'}}
    assert build_worker_report(with_content_check(task(path,[write_step(path),failed]),path))['status']=='success'


def test_helper_alone_does_not_complete_original_goal(tmp_path):
    t=task(tmp_path/'report.txt',[write_step(tmp_path/'helper.sh',None,b'helper')])
    assert not build_worker_report(t)['goal_completed']


def test_lost_mutation_response_stays_unknown(tmp_path):
    path=tmp_path/'report.txt'
    unknown={'step_id':'step_2','tool_name':'execute_cmd','tool_type':'control','status':'unknown',
             'result':{'status':'unknown','error':'confirmed_transport_outcome_unknown'}}
    report=build_worker_report(task(path,[write_step(path),unknown]))
    assert report['status']=='unknown' and report['artifacts']


def test_invalid_final_json_does_not_repair_or_repeat_mutation():
    calls=[];sent=[]
    async def send(device, action, params):sent.append(action);return {'returncode':0,'stdout':'observed'}
    bad={'id':'bad','function':{'name':'answer_text','arguments':'{"answer":'}}
    result=_run_case([
        _message(tool_calls=[_tool_call('observe','execute_cmd',{'command':'mock','artifact_paths':[]})]),
        _message(tool_calls=[bad]),
    ],captured=calls,send_command_fn=send,max_iterations=20)
    assert len(calls)==2 and sent==['execute_cmd']
    assert 'observed' in result['answer']


def test_truncation_keeps_text_without_executing_call():
    calls=[];sent=[]
    async def send(*args):sent.append(args);raise AssertionError('Must not execute truncated call')
    result=_run_case([_message('Useful partial explanation',tool_calls=[_tool_call('cut','execute_cmd',
        {'command':'NEVER RUN','artifact_paths':[]})],finish_reason='length')],
        captured=calls,send_command_fn=send,max_iterations=1)
    assert not sent and len(calls)==1 and 'Useful partial explanation' in result['answer']


def test_substantive_partial_survives_presentation(tmp_path):
    path=tmp_path/'report.txt';text='Established A and B. C remains unknown.'
    t=task(path,[write_step(tmp_path/'helper.sh')],text)
    view=worker_presentation(t)
    assert text in view['conversational_response']
    assert 'ошибкой:' not in view['conversational_response']


def test_missing_required_artifact_is_not_success(tmp_path):
    t=task(tmp_path/'missing.docx',[])
    assert not build_worker_report(t)['goal_completed']


def test_cancel_keeps_artifacts(tmp_path):
    path=tmp_path/'report.txt';t=task(path,[write_step(path)]);t['cancel_requested']=True
    report=build_worker_report(t)
    assert report['status']=='cancelled' and report['artifacts']


def test_multiple_devices_keep_different_outcomes(tmp_path):
    path=tmp_path/'report.txt';t=task(path,[write_step(path)])
    t['device_ids']=['pc','other']
    t['commands'].append({'step_id':'step_2','tool_name':'execute_cmd','tool_type':'control',
        'target_device_id':'other','status':'unknown','result':{'status':'unknown'}})
    assert build_worker_report(t)['status']=='unknown'


def test_snapshot_restore_and_render_do_not_reassess(tmp_path, monkeypatch):
    from server import worker_reports
    path=tmp_path/'report.txt';t=task(path,[write_step(path)])
    report=build_worker_report(t);snapshot=_final_snapshot(t,report,t)
    job={'task_id':'local','payload':json.dumps(snapshot),'state':report['status'],'report':json.dumps(report)}
    monkeypatch.setattr(worker_reports,'build_worker_report',lambda *a: (_ for _ in ()).throw(AssertionError('reassessment')))
    restored=restore_task(job)
    assert normalized_worker_report(restored)==report
    assert report['status']==normalized_worker_report(restored)['status']


def test_optional_narration_fields_do_not_change_execution(tmp_path):
    path=tmp_path/'report.txt';t=task(path,[write_step(path)]);report=build_worker_report(t)
    t.update(worker_report=report,worker_report_binding=result_binding(t,report))
    t['speech_error']='TTS unavailable';t['execution_details']='optional formatting failure'
    assert normalized_worker_report(t)==report


def test_unknown_recovery_never_repeats_mutation(monkeypatch):
    from server.runtime_state import tasks
    monkeypatch.setitem(tasks,'unknown-case',{'task_id':'unknown-case','_action_outcome_unknown':True})
    sent=[]
    async def send(*args):sent.append(args);raise AssertionError('Must remain blocked')
    result=_run_case([_message(tool_calls=[_tool_call('repeat','write_content',
        {'path':'C:/Temp/no.txt','content':'never'})])],poll_task_id='unknown-case',send_command_fn=send,max_iterations=1)
    assert not sent and result['commands']


def test_minimal_contract_ignores_legacy_self_verification():
    payload=validate_answer_text_payload({'answer':'Useful result'},[])
    assert payload['text']=='Useful result' and 'self_check' not in payload
    with pytest.raises(ValueError):validate_answer_text_payload({'answer':'Done','verified':True},[])


def test_unobserved_effect_claim_cannot_complete_goal(tmp_path):
    path=tmp_path/'report.txt';t=task(path,[write_step(path)])
    payload=validate_answer_text_payload({'answer':'Changed other file','effects':{'changed':['other.txt']}},t['commands'])
    append_answer_step(t['commands'],'answer_text',payload)
    assert not build_worker_report(t)['goal_completed']


@pytest.mark.parametrize('result',[
    {'returncode':1,'stdout':'','stderr':'disk full'},
    {'status':'unknown','error':'confirmed_transport_outcome_unknown'},
    {'dispatched':False,'status':'blocked','error':'denied'},
    {'returncode':0,'stdout':'helper completed'},
])
def test_complete_semantics_never_proves_opaque_effect(result):
    from server.worker_reports import coverage_binding
    steps=[{'step_id':'step_1','tool_name':'execute_cmd','tool_type':'control','result':result}]
    t={'task_id':'effects','status':'done','original_request':'Create required file',
       'device_ids':['pc'],'commands':steps,'answer':'File created.'}
    payload={'text':t['answer'],'_coverage':{'coverage':'complete','evidence_refs':['step_1'],
        'binding':coverage_binding(t['original_request'],steps,t['answer'])}}
    append_answer_step(steps,'answer_text',payload)
    assert not build_worker_report(t)['goal_completed']


def test_complete_semantics_covers_verified_read_only_analysis():
    from server.worker_reports import coverage_binding
    steps=[{'step_id':'step_1','tool_name':'web_search','result':{'results':[{'text':'observation'}]}}]
    text='Analysis based on the observation.'
    payload={'text':text,'_coverage':{'coverage':'complete','evidence_refs':['step_1'],
        'binding':coverage_binding('Analyze sources',steps,text)}}
    append_answer_step(steps,'answer_text',payload)
    assert build_worker_report({'task_id':'analysis','status':'done','execution_scope':'analysis','original_request':'Analyze sources',
        'answer':text,'commands':steps})['goal_completed']


@pytest.mark.parametrize('basis',[None,[],['old_step']])
def test_failure_channel_derives_current_references(basis):
    from server.run_journal import validate_answer_report_failure_payload
    payload={'message':'Storage failed','reason':'disk full','recoverable':False,'suggested_next_action':'Inspect storage'}
    if basis is not None:payload['basis']=basis
    journal=[{'step_id':'step_1','tool_name':'execute_cmd','status':'failed','result':{'returncode':1}}]
    accepted=validate_answer_report_failure_payload(payload,journal)
    assert accepted['message']==payload['message'] and accepted['basis']==['step_1']


@pytest.mark.parametrize('steps',[[],[{'step_id':'step_1','tool_name':'read_file','result':{'content':'existing unrelated file'}}]])
def test_general_effect_goal_cannot_become_analysis_via_final_assessment(steps):
    from server.worker_reports import coverage_binding
    steps=json.loads(json.dumps(steps))
    text='Created requested file.'
    payload={'text':text,'_coverage':{'coverage':'complete','evidence_refs':[s['step_id'] for s in steps],
        'binding':coverage_binding('Create requested file',steps,text)}}
    append_answer_step(steps,'answer_text',payload)
    assert not build_worker_report({'task_id':'missing-effect','status':'done','execution_scope':'general',
        'original_request':'Create requested file','answer':text,'commands':steps})['goal_completed']


@pytest.mark.parametrize('repaired',[False,True])
def test_latest_native_verification_failure_invalidates_old_file_receipt(tmp_path,repaired):
    path=tmp_path/'result.txt'
    steps=[write_step(path),{'step_id':'step_2','tool_name':'execute_cmd','target_device_id':'pc',
        'result':{'status':'partial','returncode':0,'stdout':'','artifact_errors':[{'path':str(path),'error':'missing file'}]}}]
    if repaired:steps.append(write_step(path,b'new',b'repaired',3))
    t=task(path,steps)
    if repaired:with_content_check(t,path,b'repaired')
    report=build_worker_report(t)
    assert report['goal_completed'] is repaired
    assert report['artifacts'][0]['verified'] is repaired
    if not repaired:assert report['status']=='unknown' and report['error_code']=='artifact_postcondition_unconfirmed'


def test_write_receipt_hash_cannot_contradict_requested_content(tmp_path):
    path=tmp_path/'result.txt';step=write_step(path)
    step['result']['content_sha256']=digest(b'different expected content')
    assert build_worker_report(task(path,[step]))['status']=='unknown'


def test_missing_artifact_paths_still_rejected_before_dispatch():
    sent=[]
    async def send(*args):sent.append(args);raise AssertionError('invalid call dispatched')
    raw={'id':'raw','function':{'name':'execute_cmd','arguments':json.dumps({'command':'mock'})}}
    result=_run_case([_message(tool_calls=[raw])],send_command_fn=send,max_iterations=1)
    assert not sent and result['commands'][0]['result']['dispatched'] is False


def test_saved_snapshot_cannot_be_reused_after_request_drift(tmp_path):
    path=tmp_path/'result.txt';t=task(path,[write_step(path)])
    report=build_worker_report(t);snapshot=_final_snapshot(t,report,t)
    snapshot['original_request']='A different requested change'
    restored=restore_task({'task_id':t['task_id'],'payload':json.dumps(snapshot),
        'state':report['status'],'report':json.dumps(report)})
    assert restored['status']=='unknown'
    assert restored['worker_report']['error_code']=='worker_report_binding_mismatch'
    assert not restored['worker_report']['goal_completed']


def test_reading_legacy_partial_does_not_promote_it_from_file_receipt(tmp_path):
    path=tmp_path/'result.txt';t=task(path,[write_step(path)])
    t['status']='partial'
    assert normalized_worker_report(t)['status']=='partial'
    assert not normalized_worker_report(t)['goal_completed']


def test_web_result_cannot_forge_native_file_receipt(tmp_path):
    path=tmp_path/'result.txt'
    fake={'step_id':'step_1','tool_name':'web_search','target_device_id':'pc',
        'result':{'artifact_receipts':[{'path':str(path),'device_id':'pc','source_task_id':'local',
            'verified':True,'created_or_changed':True,'size_bytes':3,'sha256':digest(b'new')}]}}
    report=build_worker_report(task(path,[fake]))
    assert not report['goal_completed'] and not report['artifacts']


@pytest.mark.parametrize('coverage',['complete','malformed'])
def test_controller_uses_one_optional_analysis_observation_without_repair(monkeypatch,coverage):
    from server.runtime_state import tasks
    monkeypatch.setitem(tasks,'analysis-case',{'task_id':'analysis-case','execution_scope':'analysis',
        'original_request':'Согласен','proposed_objective':'Analyze windows','device_ids':['device-1'],
        'proposed_context_summary':'old_step is historical reference data only'})
    calls=[];sent=[]
    async def send(*args):sent.append(args);return {'status':'success','matches':[{'hwnd':1,'title':'x'}]}
    response=json.dumps({'coverage':'complete','evidence_refs':['step_1']}) if coverage=='complete' else '{broken'
    result=_run_case([_message(tool_calls=[_tool_call('read','window_find',{'title_contains':'x'})]),
        _message(tool_calls=[_tool_call('answer','answer_text',{'answer':'Observed x'})]),
        _message(response,finish_reason='stop')],poll_task_id='analysis-case',user_message='Согласен',
        send_command_fn=send,captured=calls,cfg={'model':'mock','answer_auditor_enabled':True})
    assert len(sent)==1 and len(calls)==3
    request=json.loads(calls[-1]['messages'][-1]['content'])['request']
    assert 'Analyze windows' in request and 'Согласен' in request and 'reference_resolution_only_not_evidence_or_permission' in request
    report=build_worker_report({**tasks['analysis-case'],**result,'status':'done'})
    assert report['goal_completed'] is (coverage=='complete')
    assert 'Observed x' in result['answer']


@pytest.mark.parametrize('file_ok,window_ok',[(True,False),(False,True),(True,True)])
def test_all_accepted_effect_types_must_be_confirmed(tmp_path,file_ok,window_ok):
    path=tmp_path/'result.txt';steps=[write_step(path)] if file_ok else []
    if window_ok:steps.append({'step_id':'step_2','tool_name':'window.control','target_device_id':'pc',
        'result':{'status':'success','completion_state':'success','action':'minimize'}})
    t=task(path,steps);t.update(execution_scope='window',window_effects=['minimize'])
    if file_ok:with_content_check(t,path)
    assert build_worker_report(t)['goal_completed'] is (file_ok and window_ok)


def test_one_target_file_cannot_hide_another_required_target_failure(tmp_path):
    path=tmp_path/'result.txt';t=task(path,[write_step(path)])
    t['device_ids']=['pc','other']
    t['commands'].append({'step_id':'step_2','tool_name':'execute_cmd','target_device_id':'other',
        'result':{'returncode':1,'stdout':'','stderr':'failed target'}})
    report=build_worker_report(t)
    assert not report['goal_completed'] and report['status']=='partial'


@pytest.mark.parametrize('second_observed',[False,True])
def test_analysis_coverage_requires_each_declared_target(second_observed):
    from server.worker_reports import coverage_binding
    steps=[{'step_id':'step_1','tool_name':'read_file','target_device_id':'a','result':{'content':'a'}}]
    if second_observed:steps.append({'step_id':'step_2','tool_name':'read_file','target_device_id':'b','result':{'content':'b'}})
    payload={'text':'Analysis of both targets','_coverage':{'coverage':'complete','evidence_refs':[s['step_id'] for s in steps],
        'binding':coverage_binding('Analyze both',steps,'Analysis of both targets')}}
    append_answer_step(steps,'answer_text',payload)
    report=build_worker_report({'task_id':'both-analysis','status':'done','execution_scope':'analysis','device_ids':['a','b'],
        'original_request':'Analyze both','commands':steps,'answer':payload['text']})
    assert report['goal_completed'] is second_observed


@pytest.mark.parametrize('valid_hash',[False,True])
def test_verified_transfer_binds_both_targets_and_actual_digest(tmp_path,valid_hash):
    path=tmp_path/'result.txt'
    step={'step_id':'step_1','tool_name':'transfer_file','target_device_id':'b',
        'result':{'status':'success','sha256_verified':True,'sha256':digest(b'new') if valid_hash else 'invalid',
            'source_device':'a','target_device':'b','target_path':str(path),'bytes_transferred':3}}
    t=task(path,[step]);t['device_ids']=['a','b']
    report=build_worker_report(t)
    assert report['goal_completed'] is valid_hash


def test_office_delivery_needs_format_proof_not_only_text_write_hash(tmp_path):
    path=tmp_path/'result.docx'
    report=build_worker_report(task(path,[write_step(path,None,b'plain text, not an Office package')]))
    assert not report['goal_completed'] and not report['artifacts'][0]['verified']


def test_office_delivery_accepts_actual_native_package_receipt(tmp_path,monkeypatch):
    import zipfile
    import sys
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'agent'))
    from core.actions import _verify_artifact
    path=tmp_path/'result.docx'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>')
    receipt={**_verify_artifact(str(path)),'device_id':'pc','source_task_id':'local','verified':True,'created_or_changed':True}
    step={'step_id':'step_1','tool_name':'execute_cmd','target_device_id':'pc',
        'result':{'returncode':0,'stdout':'','artifact_receipts':[receipt]}}
    report=build_worker_report(task(path,[step]))
    assert not report['goal_completed']  # Valid empty DOCX does not prove requested content.
    assert report['artifacts'][0]['verified'] and report['artifacts'][0]['sha256']==digest(path.read_bytes())
    assert report['artifacts'][0]['content_verified'] is False


def test_office_transfer_attests_copy_identity_without_claiming_format_validation(tmp_path):
    path=tmp_path/'copy.docx'
    step={'step_id':'step_1','tool_name':'transfer_file','target_device_id':'b',
        'result':{'status':'success','sha256_verified':True,'sha256':digest(b'source bytes'),
            'source_device':'a','target_device':'b','target_path':str(path),'bytes_transferred':12}}
    t=task(path,[step]);t.update(device_ids=['a','b'],original_request='Copy source byte for byte')
    report=build_worker_report(t)
    assert report['goal_completed'] and report['artifacts'][0]['verification_scope']=='copy_identity'


def test_verified_effect_is_not_downgraded_or_misdescribed_by_missing_final_text(tmp_path):
    path=tmp_path/'result.txt';t=task(path,[write_step(path)],'Unconfirmed truncated model fragment')
    t['commands'].append({'step_id':'step_2','tool_name':'llm_request','tool_type':'system',
        'status':'failed','result':{'error':'model_output_truncated'}})
    report=build_worker_report(t)
    assert not report['goal_completed'] and report['status']=='partial'
    view=worker_presentation(t,report)['conversational_response']
    assert digest(b'new') in view and 'содержания не подтверждена' in view
    assert 'Unconfirmed truncated model fragment' not in view


@pytest.mark.parametrize('signal',['unknown_flag','unknown','cancelled','waiting_confirmation'])
def test_new_control_signal_cannot_reuse_cached_success(tmp_path,signal):
    path=tmp_path/'result.txt';t=task(path,[write_step(path)])
    report=build_worker_report(t);snapshot=_final_snapshot(t,report,t)
    if signal=='unknown_flag':snapshot['_action_outcome_unknown']=True
    else:snapshot['status']=signal
    current=normalized_worker_report(snapshot)
    assert not current['goal_completed']
    assert current['status']==('unknown' if signal=='unknown_flag' else signal)
    if signal=='waiting_confirmation':assert current['error_code'] is None


def test_unknown_quarantine_survives_snapshot_restore(tmp_path):
    path=tmp_path/'result.txt';t=task(path,[write_step(path)])
    t['_action_outcome_unknown']=True
    report=build_worker_report(t);snapshot=_final_snapshot(t,report,t)
    restored=restore_task({'task_id':t['task_id'],'payload':json.dumps(snapshot),
        'state':report['status'],'report':json.dumps(report)})
    assert restored['_action_outcome_unknown'] is True and not restored['worker_report']['goal_completed']


def test_deployed_flat_entrypoint_and_result_contracts_import_without_network(tmp_path):
    import os
    import subprocess
    import sys
    script='''
import asyncio,json
import main
from worker_reports import build_worker_report
from run_journal import validate_answer_report_failure_payload,append_answer_step
from response_presentation import worker_presentation
from answer_auditor import assess_result_coverage
steps=[{'step_id':'step_1','tool_name':'execute_cmd','status':'failed','result':{'returncode':1,'stdout':'','stderr':'controlled failure'}}]
failure=validate_answer_report_failure_payload({'message':'Failed','reason':'controlled','recoverable':False,'suggested_next_action':'Inspect'},steps)
assert failure['basis']==['step_1']
async def fake(**kwargs):return {'choices':[{'message':{'content':json.dumps({'coverage':'complete','evidence_refs':['step_1']})}}]}
payload={'text':'Created'}
payload['_coverage']=asyncio.run(assess_result_coverage(client=None,cfg={'model':'mock','answer_auditor_enabled':True},chat_completion_request_fn=fake,user_request='Create required file',current_run_journal=steps,answer_payload=payload))
append_answer_step(steps,'answer_text',payload)
task={'task_id':'flat-test','status':'done','original_request':'Create required file','assigned_artifacts':['C:/Temp/absent.txt'],'commands':steps,'answer':'Created'}
report=build_worker_report(task)
assert not report['goal_completed']
assert 'Created' not in worker_presentation(task,report)['conversational_response']
print('FLAT_ENTRYPOINT_OK')
'''
    env={**os.environ,'IRU_DB_PATH':str(tmp_path/'isolated-import.sqlite'),'PYTHONIOENCODING':'utf-8'}
    result=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1]/'server',
        env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stderr
    assert 'FLAT_ENTRYPOINT_OK' in result.stdout
