"""A rejected extra call must not erase a separately verified file result."""
import asyncio
import hashlib
import json

import pytest

from server import database as db, task_runtime as runtime
from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_tools import NON_PIPELINE_TOOLS
from server.controller_trust import has_grounded_terminal_answer
from server.worker_scheduler import WorkerScheduler, owned_job
from test_orchestrator_worker import owners, task


@pytest.mark.parametrize('variant', ['other_device', 'after_terminal'])
def test_schema_rejection_exception_cannot_hide_other_device_or_later_failure(variant):
    digest = hashlib.sha256(b'alpha').hexdigest()
    write = {'step_id': 'step_1', 'tool_name': 'write_content', 'status': 'success', 'device_id': 'pc',
             'result': {'status': 'ok', 'path': 'C:/Temp/report.txt', 'bytes_written': 5,
                        'verified': True, 'committed': True, 'file_sha256': digest, 'content_sha256': digest}}
    rejected = {'step_id': 'step_2', 'tool_name': 'execute_cmd', 'status': 'failed',
                'device_id': 'other' if variant == 'other_device' else 'pc',
                'result': {'status': 'validation_error', 'error': 'unknown_tool_arguments', 'dispatched': False}}
    terminal = {'step_id': 'step_3', 'tool_name': 'answer.text', 'status': 'terminal', 'result': {
        'answer_type': 'grounded_report', 'text': 'File created.', 'basis': ['step_1'],
        'self_check': {'depends_on_current_external_state': True, 'claims_completed_action': True,
                       'has_sufficient_evidence': True, 'missing_evidence_question': ''}}}
    journal = [write, rejected, terminal] if variant == 'other_device' else [write, terminal, rejected]
    assert has_grounded_terminal_answer('File created.', journal) is False


@pytest.mark.parametrize('case', [
    'schema_rejection', 'execution_error', 'unknown', 'pre_dispatch_denial',
    'missing_write', 'bad_hash', 'unverified_write', 'auditor_rejects',
])
def test_file_result_acceptance_through_runtime_report_and_history(owners, tmp_path, monkeypatch, case):
    owner, _ = owners
    path = tmp_path / 'report.txt'
    content = 'alpha\nbeta\n'
    request = f'Create {path} with exactly this content: {content}'
    answer = f'Created {path}; read-back content matches the request.'
    sent, audits = [], []
    monkeypatch.setenv('IRU_ANSWER_AUDITOR_ENABLED', '1')

    async def send(device, action, params):
        sent.append(action)
        if action == 'write_content':
            assert params['path'] == str(path) and params['content'] == content
            if case == 'missing_write':
                return {'status': 'ok', 'path': str(path), 'bytes_written': 0}
            path.write_bytes(params['content'].encode('utf-8'))
            observed = path.read_bytes()
            assert observed.decode('utf-8') == content
            digest = hashlib.sha256(observed).hexdigest()
            return {'status': 'ok', 'path': str(path), 'encoding': 'utf-8',
                    'bytes_written': len(observed), 'chars_written': len(content),
                    'content_sha256': digest, 'file_sha256': '0' * 64 if case == 'bad_hash' else digest,
                    'total_size': len(observed), 'verified': case != 'unverified_write',
                    'committed': True, 'append': False}
        assert action == 'execute_cmd' and case in {'execution_error', 'unknown', 'pre_dispatch_denial'}
        if case == 'unknown':
            return {'status': 'unknown', 'error': 'confirmed_transport_outcome_unknown', 'dispatched': False}
        if case == 'pre_dispatch_denial':
            return {'status': 'blocked', 'error': 'policy_denied', 'dispatched': False}
        return {'returncode': 1, 'stdout': '', 'stderr': 'actual execution failed'}

    async def process(**kwargs):
        turns = []

        async def completion(**call):
            if call.get('tools') is None:
                audits.append(json.loads(call['messages'][-1]['content']))
                # Deliberately permissive auditor: deterministic guards must still
                # refuse real failures, unknown outcomes and missing write evidence.
                return {'choices': [{'message': {'content': json.dumps({
                    'valid': case != 'auditor_rejects', 'reason': 'file write verified' if case != 'auditor_rejects' else 'goal not established',
                    'action_steps': ['step_1'], 'verification_steps': ['step_1']})}}]}
            turns.append(1)
            if len(turns) == 1:
                name, args = 'write_content', {'path': str(path), 'content': content}
            elif len(turns) == 2:
                args = {'command': 'redundant probe', 'artifact_paths': []}
                if case not in {'execution_error', 'unknown', 'pre_dispatch_denial'}:
                    args['unexpected_argument'] = True
                name = 'execute_cmd'
            else:
                name, args = 'answer_text', {
                    'answer_type': 'grounded_report', 'text': answer, 'basis': ['step_1'],
                    'self_check': {'depends_on_current_external_state': True,
                                   'claims_completed_action': True, 'has_sufficient_evidence': True,
                                   'missing_evidence_question': ''}}
            return {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [{
                'id': str(len(turns)), 'function': {'name': name, 'arguments': json.dumps(args)}}]}}]}

        return await process_non_pipeline_command(
            user_message=kwargs['user_message'], device_id=kwargs['device_id'],
            device_info=kwargs['device_info'], send_command_fn=send,
            get_file_link_fn=lambda *args: '', chat_history=[], user_id=None, chat_id=None,
            modes={}, poll_task_id=kwargs['poll_task_id'],
            cfg={'model': 'mock-model', 'answer_auditor_enabled': True}, system_msg='system',
            machine_guid=None, mem_user_id=None, non_pipeline_tools=NON_PIPELINE_TOOLS,
            max_iterations=4, pick_model_fn=lambda *args: 'mock-model',
            chat_completion_request_fn=completion)

    monkeypatch.setattr(runtime, 'process_nl_command', process)

    async def scenario():
        async def execute(t):
            await runtime.run_nl_task(t['task_id'], owner['id'], t['message'], t['device_ids'], owner['chat_id'])

        scheduler = WorkerScheduler(execute)
        try:
            template = task(owner, request)
            template['results'] = {}
            job = await scheduler.submit(template, request_key='file-acceptance')
            await scheduler.runners[owner['id']]
            saved = owned_job(job['task_id'], owner['id'])
            report = json.loads(saved['report'])
            with db.get_db() as c:
                message = c.execute('SELECT content,commands,task_metadata FROM messages WHERE id=?',
                                    (saved['message_id'],)).fetchone()
            metadata = json.loads(message['task_metadata'])
            assert metadata['workerReport'] == report
            if case == 'schema_rejection':
                assert path.read_text(encoding='utf-8') == content
                assert sent == ['write_content'] and audits
                assert report['status'] == 'success' and report['goal_completed'] is True
                rejected = job['commands'][1]['result']
                assert rejected['status'] == 'validation_error' and rejected['dispatched'] is False
                assert job['task_receipt']['answer_source'] == 'audited_terminal'
                assert job['task_receipt']['action_evidence']
                assert job['answer'] == message['content'] == answer
                assert report['artifacts'][0]['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                assert report['status'] != 'success' and report['goal_completed'] is False
                if case == 'missing_write':
                    assert not path.exists()
                elif case in {'execution_error', 'unknown', 'pre_dispatch_denial'}:
                    assert sent == ['write_content', 'execute_cmd']
                else:
                    assert sent == ['write_content']
        finally:
            await scheduler.shutdown()

    asyncio.run(scenario())
