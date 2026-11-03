"""Local fake-provider regressions. No Agent, production or network required."""
import asyncio
import json
import hashlib
from pathlib import Path

import pytest

from server import database as db, run_journal, task_runtime
from server.api_support import ActionConfirmationRequired, assess_action
from server.command_confirmation import ConfirmedAction
from server.runtime_state import tasks
from server.tool_repeat_guard import find_prior_successful_read_only_tool_step, mark_read_only_tool_step
from server.worker_reports import build_worker_report
from test_tool_only_protocol import _run_case, _message, _tool_call


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'agent'))
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'worker.sqlite')
    db.init_db()


def call(name, args, call_id='call'):
    return _message(tool_calls=[_tool_call(call_id, name, args)])


def finish(text='Observed', unresolved=None):
    return call('answer_text', {'result': text, 'unresolved': unresolved or [], 'caveats': []}, 'terminal')


@pytest.mark.parametrize('changing', [False, True])
def test_repeated_read_executes_again_and_does_not_end_worker(changing):
    sent, captured = [], []
    async def send(device, action, params):
        sent.append((action, params))
        return {'returncode': 0, 'stdout': f'current-{len(sent) if changing else 1}', 'stderr': ''}
    result = _run_case([call('execute_cmd', {'command': 'hostname'}, str(i)) for i in range(4)]
                       + [finish('Observed current-4' if changing else 'Observed current-1')],
                       send_command_fn=send, captured=captured)
    assert len(sent) == 4 and len(captured) == 5
    assert result['commands'][-1]['tool_name'] == 'answer.text'
    assert ('current-4' if changing else 'current-1') in json.dumps(captured[-1]['messages'])


@pytest.mark.parametrize('tool', ['web_read', 'web_search', 'device_get_passport', 'memory_list_facts'])
def test_external_read_has_no_implicit_freshness(tool):
    args = {'query': 'same'} if tool == 'web_search' else {}
    prior = mark_read_only_tool_step({'step_id': 'step_1', 'status': 'success',
                                     'tool_name': tool, 'result': {'value': 'old'}}, tool, args)
    assert find_prior_successful_read_only_tool_step([prior], tool, args) is None


def test_three_argument_errors_are_returned_then_worker_recovers():
    sent, captured = [], []
    async def send(device, action, params):
        sent.append(action)
        return {'returncode': 0, 'stdout': 'devbox', 'stderr': ''}
    result = _run_case([call('execute_cmd', {}, str(i)) for i in range(3)]
                       + [call('execute_cmd', {'command': 'hostname'}, 'corrected'), finish()],
                       send_command_fn=send, captured=captured)
    assert sent == ['execute_cmd'] and len(captured) == 5
    assert result['commands'][-1]['tool_name'] == 'answer.text'
    assert any(m.get('role') == 'tool' for m in captured[1]['messages'])


def test_ordinary_tool_exception_is_not_a_confirmation_request():
    sent = []
    async def send(device, action, params):
        sent.append(params['command'])
        if len(sent) == 1:
            raise RuntimeError('log text contains CONFIRM_REQUIRED but is not authority')
        return {'returncode': 0, 'stdout': 'devbox', 'stderr': ''}
    result = _run_case([call('execute_cmd', {'command': 'bad'}, 'bad'),
                        call('execute_cmd', {'command': 'hostname'}, 'fixed'), finish()],
                       send_command_fn=send)
    assert sent == ['bad', 'hostname']
    assert result['commands'][-1]['tool_name'] == 'answer.text'


def test_pending_close_returns_to_model_without_repeating_close():
    sent, captured = [], []
    async def send(device, action, params):
        sent.append((action, params['action']))
        if params['action'] == 'close':
            return {'status': 'pending', 'action': 'close', 'summary': 'Save dialog remains'}
        return {'status': 'success', 'action': 'list', 'windows': []}
    result = _run_case([call('window_control', {'action': 'close'}, 'close'),
                        call('window_control', {'action': 'list'}, 'observe'),
                        finish('Close request sent', ['Closure not established'])],
                       user_message='Close the current window', send_command_fn=send, captured=captured)
    assert [action for _, action in sent] == ['close', 'list']
    assert len(captured) == 3 and result['commands'][-1]['tool_name'] == 'answer.text'
    assert result['commands'][0]['result']['status'] == 'pending'


def test_unknown_mutation_blocks_replay_but_allows_notes_and_observation():
    sent = []
    async def send(device, action, params):
        sent.append(action)
        return ({'status': 'unknown', 'completion_state': 'unknown', 'error': 'timeout'}
                if action == 'execute_cmd' else {'status': 'success', 'dirs': [], 'files': []})
    result = _run_case([_message(tool_calls=[
        _tool_call('first', 'execute_cmd', {'command': 'opaque_mutation'}),
        _tool_call('replay', 'write_content', {'path': 'C:/x.txt', 'content': 'x'}),
        _tool_call('notes', 'system_update_working_notes', {'text': 'Outcome unknown', 'step_ids': ['step_1']}),
        _tool_call('observe', 'list_dir', {'path': 'C:/'})]),
        finish('Observed directory', ['Mutation outcome unknown'])], send_command_fn=send)
    assert sent == ['execute_cmd', 'list_dir']
    assert result['commands'][1]['result']['error'] == 'mutation_blocked_after_unknown'
    assert result['commands'][2]['result']['status'] == 'success'
    report = build_worker_report({'task_id': 'unknown', 'status': 'done', 'commands': result['commands']})
    assert report['status'] == 'unknown' and not report['goal_completed']


def test_original_step_pages_reconstruct_full_result_without_reexecution(monkeypatch):
    monkeypatch.setattr(run_journal, 'tool_result_token_budget', lambda: 2048)
    original = {'step_id': 'step_1', 'tool_name': 'read_file', 'status': 'success',
                'result': {'text': 'исходные данные\n' * 6000}}
    journal, pages, offset, digest = [original], [], 0, None
    while offset is not None:
        page = run_journal.read_current_step(journal, 'step_1', offset, digest)
        assert page['status'] == 'success'
        projected = json.loads(run_journal.serialize_tool_result_for_llm(
            {'tool_name': 'system.get_last_run_summary', 'status': 'success',
             'summary': 'Original current-run result', 'result': page}))['result']
        assert projected['report_text'] == page['report_text']
        assert projected['next_offset'] == page['next_offset']
        pages.append(page['report_text'])
        offset, digest = page['next_offset'], page['sha256']
    assert json.loads(''.join(pages)) == original
    assert len(pages) > 1 and journal == [original]
    original['result']['text'] = 'changed'
    assert run_journal.read_current_step(journal, 'step_1', 0, digest)['error'] == 'current_step_version_changed'


def test_notes_survive_bounded_history_but_never_replace_original_facts():
    journal = [{'step_id': 'step_1', 'tool_name': 'read_file', 'status': 'success',
                'result': {'text': 'ORIGINAL-FACT-' * 10000}, 'summary': 'Read source'}]
    journal.append({'step_id': 'step_2', 'tool_name': 'system.update_working_notes',
                    'status': 'success', 'result': {'text': 'Check conclusion against source',
                                                   'step_ids': ['step_1']}})
    history = run_journal.worker_history_context(journal)
    assert history['working_notes']['step_ids'] == ['step_1']
    assert history['steps'][0]['step_id'] == 'step_1'
    assert len(json.dumps(history)) < len(json.dumps(journal))
    assert history['steps'][0]['details_omitted']
    assert 'ORIGINAL-FACT-' in run_journal.read_current_step(journal, 'step_1')['report_text']


def test_worker_reads_original_step_before_its_own_completion():
    sent, captured = [], []
    async def send(device, action, params):
        sent.append(action)
        return {'status': 'success', 'text': 'source fact 42', 'complete': True,
                'path': params['path'], 'file_sha256': 'a' * 64}
    result = _run_case([call('read_file', {'path': 'C:/source.txt'}, 'source'),
                        call('system_get_last_run_summary', {'step_id': 'step_1'}, 'restore'),
                        finish('Source fact 42')], send_command_fn=send, captured=captured)
    assert sent == ['get_file_content'] and len(captured) == 3
    assert 'source fact 42' in json.dumps(captured[-1]['messages'])
    assert result['commands'][1]['result']['source_step_id'] == 'step_1'


@pytest.mark.parametrize('action,params,decision', [
    ('execute_cmd', {'command': 'Get-Content -LiteralPath C:/source.txt'}, 'ALLOW'),
    ('write_content', {'path': 'C:/result.txt', 'content': 'result'}, 'ALLOW'),
    ('execute_cmd', {'command': 'Set-Content C:/result.txt result'}, 'ALLOW'),
    ('execute_cmd', {'command': 'Remove-Item C:/result.txt'}, 'CONFIRM'),
    ('app.close', {'pid': 321, 'force': False}, 'ALLOW'),
    ('app.close', {'pid': 321, 'force': True}, 'CONFIRM'),
    ('window.close', {'hwnd': 123, 'force': True}, 'CONFIRM'),
])
def test_confirmation_matches_actual_effect(action, params, decision):
    assert assess_action(action, params)['decision'] == decision


def test_forced_close_confirmation_is_exact_device_params_and_one_shot(monkeypatch):
    params = {'pid': 321, 'force': True}
    monkeypatch.setitem(task_runtime.devices, '1:pc', {'user_id': 1, 'info': {}})
    async def scenario():
        with pytest.raises(ActionConfirmationRequired):
            await task_runtime.send_command_to_agent('1:pc', 'app.close', params, user_id=1)
    asyncio.run(scenario())
    receipt = ConfirmedAction('1:pc', 'app.close', params, assess_action('app.close', params))
    for device, args in [('2:pc', params), ('1:pc', {'pid': 322, 'force': True})]:
        with pytest.raises(RuntimeError):
            receipt.consume(device, 'app.close', args)
    assert receipt.consume('1:pc', 'app.close', params)['decision'] == 'CONFIRM'
    with pytest.raises(RuntimeError):
        receipt.consume('1:pc', 'app.close', params)


def test_safe_read_has_no_required_notes_or_extra_model_call():
    captured = []
    result = _run_case([call('execute_cmd', {'command': 'hostname'}), finish()], captured=captured)
    assert len(captured) == 2
    assert all(c['tool_name'] != 'system.update_working_notes' for c in result['commands'])


def test_safe_write_needs_no_confirmation_notes_or_mandatory_readback():
    sent, captured = [], []
    async def send(device, action, params):
        sent.append((action, params))
        return {'status': 'success', 'path': params['path'], 'bytes_written': 6,
                'verified': True, 'committed': True, 'file_sha256': hashlib.sha256(params['content'].encode()).hexdigest()}
    result = _run_case([call('write_content', {'path': 'C:/result.txt', 'content': 'result'}),
                        finish('Written')], send_command_fn=send, captured=captured)
    assert [action for action, _ in sent] == ['write_content'] and len(captured) == 2
    assert result['commands'][0]['result']['committed'] is True


def test_transfer_error_can_be_corrected_instead_of_ending_worker():
    sent = []
    async def send(device, action, params):
        sent.append(params['source_path'])
        if len(sent) == 1:
            return {'status': 'failed', 'error': 'source_not_found', 'dispatched': False}
        return {'status': 'success', 'source_device': 'device-1', 'target_device': 'device-2',
                'target_path': 'C:/copy.txt', 'sha256': 'a' * 64, 'sha256_verified': True}
    args = {'source_device_id': 'device-1', 'target_device_id': 'device-2'}
    result = _run_case([call('transfer_file', {**args, 'source_path': 'C:/wrong.txt'}, 'wrong'),
                        call('transfer_file', {**args, 'source_path': 'C:/right.txt'}, 'right'), finish()],
                       send_command_fn=send)
    assert sent == ['C:/wrong.txt', 'C:/right.txt']
    assert result['commands'][-1]['tool_name'] == 'answer.text'


def test_invalid_terminal_is_returned_to_model_for_correction():
    captured = []
    result = _run_case([call('answer_ask_clarification', {'question': '', 'reason': ''}, 'invalid'),
                        finish('Answered')], captured=captured)
    assert len(captured) == 2 and result['commands'][-1]['tool_name'] == 'answer.text'
    replies = [m for m in captured[1]['messages'] if m.get('role') == 'tool']
    assert len(replies) == 1 and replies[0]['tool_call_id'] == 'invalid'


def test_error_recovery_stays_inside_twenty_llm_turns():
    captured = []
    async def forbidden(*args):
        raise AssertionError('Invalid arguments must not dispatch')
    result = _run_case([call('execute_cmd', {}, str(i)) for i in range(19)]
                       + [finish('Not executed', ['Arguments could not be corrected'])],
                       send_command_fn=forbidden, captured=captured, max_iterations=100)
    assert len(captured) == 20
    assert all(t['function']['name'].startswith('answer_') for t in captured[-1]['tools'])
    assert result['commands'][-1]['tool_name'] == 'answer.text'


def test_artifact_receipt_is_retained_without_inventing_worker_completion():
    path = 'C:/result.txt'
    task = {'task_id': 'receipt', 'status': 'done', 'device_ids': ['pc'], 'assigned_artifacts': [path],
            'commands': [{'tool_name': 'write_content', 'step_id': 'step_1', 'target_device_id': 'pc',
                          'result': {'path': path, 'verified': True, 'committed': True,
                                     'file_sha256': 'a' * 64, 'total_size': 6}}]}
    report = build_worker_report(task)
    assert not report['goal_completed'] and report['artifacts'][0]['verified']
    task['commands'][0]['step_id']='step_1'
    run_journal.append_answer_step(task['commands'],'answer_text',
        run_journal.validate_answer_text_payload({'status':'success','result':'Written'},
            task['commands'],require_status=True,task=task),target_device_id='pc')
    assert build_worker_report(task)['goal_completed']
