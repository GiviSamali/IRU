"""Outcome wording and file-content coverage share the canonical WorkerReport."""
import hashlib

import pytest

from server.worker_reports import build_worker_report, coverage_binding, result_binding
from server.response_presentation import worker_presentation
from server.worker_scheduler import _final_snapshot, restore_task
import json


@pytest.mark.parametrize('outcome', ['partial', 'unknown'])
def test_incomplete_outcome_never_presents_model_done_as_final(outcome):
    task = {'task_id': 'local', 'status': 'done', 'device_ids': ['pc'],
            'answer': 'Готово. Всё выполнено и проверено.', 'commands': [
                {'step_id': 'step_1', 'tool_name': 'execute_cmd', 'target_device_id': 'pc',
                 'result': {'status': outcome, 'returncode': 0, 'stdout': 'observed fact'}},
                {'step_id': 'step_2', 'tool_name': 'answer.text', 'status': 'terminal',
                 'result': {'text': 'Готово. Всё выполнено и проверено.'}}]}
    report = build_worker_report(task)
    assert report['status'] == outcome and not report['goal_completed']
    for candidate in (task, restore_task({'task_id': 'local', 'state': outcome,
            'payload': json.dumps(_final_snapshot(task, report, task)), 'report': json.dumps(report)})):
        view = worker_presentation(candidate)
        assert 'Готово' not in view['conversational_response']
        assert 'Всё выполнено и проверено' not in view['conversational_response']
        assert 'observed fact' in view['conversational_response']
        assert 'Готово' in view['execution_details']  # journal material retained, not the verdict


@pytest.mark.parametrize('complete',[False,True])
def test_file_integrity_is_independent_of_worker_content_completeness(complete):
    path='C:/Temp/report.docx';digest=hashlib.sha256(b'package').hexdigest()
    native={'path':path,'device_id':'pc','source_task_id':'local','verified':True,
            'created_or_changed':True,'size_bytes':4096,'sha256':digest,'format':'docx','artifact_role':'final'}
    result={'status':'success','action':'create','path':path,'sha256':digest,'verified':True,
            'artifact_receipts':[native],'operation_verified':True,
            'applied_input':{'data':{'elements':[{'text':'Section one'}]}}}
    text='Report complete' if complete else 'Section two is absent.'
    steps=[{'step_id':'step_1','tool_name':'office_docx','target_device_id':'pc','result':result}]
    payload={'text':text,'_result_contract':3,'unresolved':[] if complete else ['Section two is absent.']}
    # A legacy assessor must neither revoke the write nor hide missing Worker work.
    payload['_coverage']={'coverage':'partial' if complete else 'complete','evidence_refs':['step_1'],
        'binding':coverage_binding('Write sections one and two',steps,text)}
    steps.append({'step_id':'step_2','tool_name':'answer.text','tool_type':'answer','status':'terminal','result':payload})
    task={'task_id':'local','status':'done','device_ids':['pc'],'assigned_artifacts':[path],
          'original_request':'Write sections one and two','answer':text,'commands':steps}
    report=build_worker_report(task)
    assert report['artifacts'][0]['verified'] and report['artifacts'][0]['content_observed']
    assert report['goal_completed']==complete
    if not complete:
        assert report['status']=='partial' and 'Section two' in report['summary']
    native['verified']=False
    assert not build_worker_report(task)['goal_completed']



def test_bound_native_file_success_is_not_reassessed_on_read(monkeypatch):
    from server import answer_auditor
    monkeypatch.setattr(answer_auditor, 'assess_result_coverage',
        lambda **kwargs: pytest.fail('Reading a saved result must not invoke a model'))
    path='C:/Temp/report.txt'
    task={'task_id':'local','status':'success','device_ids':['pc'], 'assigned_artifacts':[path],
          'answer':'Готово.', 'commands':[{'step_id':'step_1','tool_name':'write_content',
              'target_device_id':'pc','result':{'status':'ok','path':path,'verified':True,
              'committed':True,'total_size':5,'file_sha256':hashlib.sha256(b'short').hexdigest()}}]}
    report=build_worker_report(task)
    # Construct a genuine old-contract snapshot: bound native bytes, no content check.
    old={**report,'status':'success','goal_completed':True,'summary':'Готово.','error_code':None,
         'artifacts':[{k:v for k,v in a.items() if k not in {'content_verified','verification_scope'}} for a in report['artifacts']]}
    task.update(worker_report=old,worker_report_binding=result_binding(task,old))
    restored=restore_task({'task_id':'local','state':'success','payload':json.dumps(task),'report':json.dumps(old)})
    assert restored['worker_report']['status']=='success'
    assert restored['worker_report']['goal_completed']
    assert restored['worker_report']['artifacts'][0]['verified']
