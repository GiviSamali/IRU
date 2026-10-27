"""Outcome wording and file-content coverage share the canonical WorkerReport."""
import hashlib

import pytest

from server.worker_reports import build_worker_report, coverage_binding, needs_semantic_assessment, result_binding
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


@pytest.mark.parametrize('coverage', [None, 'partial', 'complete', 'receipt_only', 'stale_read', 'counts_only'])
def test_valid_docx_does_not_prove_requested_content(coverage):
    path = 'C:/Temp/report.docx'
    package = b'valid package with sections one and two' if coverage=='complete' else b'valid package with only section one'
    digest = hashlib.sha256(package).hexdigest()
    native = {'path': path, 'device_id': 'pc', 'source_task_id': 'local', 'verified': True,
              'created_or_changed': True, 'size_bytes': 4096, 'sha256': digest,
              'format': 'docx', 'artifact_role': 'final'}
    steps = [{'step_id': 'step_1', 'tool_name': 'office_docx', 'target_device_id': 'pc',
              'result': {'status': 'success', 'action': 'create', 'artifact_receipts': [native]}},
             {'step_id': 'step_2', 'tool_name': 'office_docx', 'target_device_id': 'pc',
              'result': {'status': 'success', 'action': 'inspect', 'path': path, 'sha256': digest,
                         'items': [{'type': 'paragraph', 'text': 'Section one'}]}}]
    if coverage == 'complete':
        steps[-1]['result']['items'].append({'type': 'paragraph', 'text': 'Section two'})
    if coverage == 'stale_read':steps[-1]['result']['sha256']='0'*64
    if coverage == 'counts_only':steps[-1]['result'].pop('items')
    task = {'task_id': 'local', 'status': 'done', 'device_ids': ['pc'],
            'original_request': 'Write a report with sections one and two',
            'execution_scope': 'general', 'assigned_artifacts': [path],
            'answer': 'Готово. Отчёт полностью проверен.', 'commands': steps}
    payload = {'text': task['answer']}
    if coverage:
        payload['_coverage'] = {'coverage': 'partial' if coverage=='partial' else 'complete',
            'evidence_refs': ['step_1' if coverage=='receipt_only' else 'step_2'],
            'reason': 'Section two is absent' if coverage == 'partial' else 'content comparison supplied',
            'binding': coverage_binding(task['original_request'], steps, task['answer'])}
    steps.append({'step_id': 'step_3', 'tool_name': 'answer.text', 'status': 'terminal', 'result': payload})
    assert needs_semantic_assessment(task)
    report = build_worker_report(task)
    assert report['artifacts'][0]['verified']  # native format/identity proof survives
    if coverage != 'complete':
        assert not report['goal_completed'] and report['status'] == 'partial'
        assert report['artifacts'][0]['content_verified'] is False
        assert report['error_code'] == 'artifact_content_unconfirmed'
        view = worker_presentation(task, report)['conversational_response']
        assert 'Готово' not in view and 'полностью проверен' not in view
        assert 'содержан' in view.lower()
    else:
        # A bound content assessment is still powerless without current file proof.
        assert report['goal_completed'] and report['artifacts'][0]['content_verified']
        native['verified'] = False
        assert not build_worker_report(task)['goal_completed']


def test_old_bound_file_success_is_not_reused_as_content_verification(monkeypatch):
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
    assert restored['worker_report']['status']=='partial'
    assert not restored['worker_report']['goal_completed']
    assert 'Готово' not in worker_presentation(restored)['conversational_response']
