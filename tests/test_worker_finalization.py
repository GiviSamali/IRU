"""Offline finalization regressions; no provider, device, or production DB."""
import pytest

from server import database as db
from server.controller_trust import sanitize_result_text
from server.run_journal import append_answer_step, unfinished_execution_result
from server.worker_reports import bounded_report_summary, build_worker_report
from server.python_env import classify_command_error
from test_tool_only_protocol import _run_case, _message, _tool_call, _answer_call, _answer_payload


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'finalization.sqlite')
    db.init_db()


def test_typed_partial_with_sufficient_subset_is_not_replaced_by_first_error():
    journal = [{'step_id':'step_1', 'tool_name':'execute_cmd', 'tool_type':'control',
                'status':'failed', 'result':{'returncode':127, 'stdout':'observed subset', 'stderr':'BOM'}}]
    payload = _answer_payload('Наблюдения сохранены. База не прочитана.', answer_type='partial_report', basis=['step_1'])
    payload['self_check'].update(claims_completed_action=False, has_sufficient_evidence=True)
    append_answer_step(journal, 'answer_text', payload)
    assert sanitize_result_text(payload['text'], journal) == payload['text']
    # A raw completion claim still receives the legacy guard.
    assert sanitize_result_text('Всё выполнено.', journal) == 'Всё выполнено.'  # authenticity no longer decides goal


def test_two_invalid_bases_stop_without_twenty_protocol_turns():
    captured = []
    result = _run_case([
        _message(tool_calls=[_tool_call('read','write_content',{'path':'C:/Temp/observations.txt','content':'observed subset'})]),
        _message(tool_calls=[_answer_call('bad1','Done', answer_type='grounded_report', basis=['old_step'])]),
        _message(tool_calls=[_answer_call('bad2','Done', answer_type='grounded_report', basis=['old_step'])]),
    ], captured=captured, max_iterations=20)
    assert len(captured) == 2
    assert result['commands'][-1]['tool_name']=='answer.text'
    assert not build_worker_report({'task_id':'local','status':'done','commands':result['commands'], 'device_ids':['device-1'],'answer':result['answer']})['goal_completed']
    assert result['commands'][-1]['result']['basis']==['step_1']


def test_truncated_provider_output_keeps_observation_without_dispatching_it():
    sent = []
    async def send(device, action, params):
        sent.append(action)
        return {'status':'ok','verified':True,'committed':True,'path':'C:/Temp/observations.txt',
                'file_sha256':'a'*64,'content_sha256':'a'*64}
    result = _run_case([
        _message(tool_calls=[_tool_call('read','write_content',{'path':'C:/Temp/observations.txt','content':'measured subset'})]),
        _message(tool_calls=[_tool_call('cut','execute_cmd',{'command':'NEVER RUN'})], finish_reason='length'),
    ], send_command_fn=send, max_iterations=20)
    assert sent == ['write_content']
    assert result['task_receipt']['terminal_reason'] == 'model_output_truncated'
    assert not build_worker_report({'task_id':'local','status':'done','commands':result['commands'], 'device_ids':['device-1'],'answer':result['answer']})['goal_completed']
    assert 'a'*64 in result['answer']


def test_fallback_preserves_failures_observations_and_hides_url_credentials():
    journal = [{'step_id':'step_1','tool_name':'execute_cmd','tool_type':'control','status':'failed',
                'result':{'returncode':127,'stdout':'count=42 user_token=private-secret','stderr':'BOM'}}]
    result = unfinished_execution_result(journal, 'terminal_answer_missing')
    assert result['commands'] is journal
    assert 'count=42' in result['answer'] and 'returncode=127' in result['answer']
    assert 'private-secret' not in result['answer']
    assert result['task_receipt']['task_status'] == 'failed'


@pytest.mark.parametrize('stderr', [
    'bash: line 1: \ufeff\ufeffjournalctl: command not found',
    "bash: line 30: $'\\r': command not found",
    'SyntaxError: invalid non-printable character U+FEFF',
])
def test_transport_encoding_error_does_not_suggest_installing_missing_command(stderr):
    result = classify_command_error({'returncode':127,'stderr':stderr})
    assert result['error_type'] == 'shell_syntax_error'
    assert 'transmitted bytes' in result['recovery_hint']
    assert result['effect_verification'] == 'not_established_by_error_classification'


@pytest.mark.parametrize('block', [
    '\n'.join(f'{i}. Item ' + 'x'*30 for i in range(1,30)),
    '\n'.join('- Item ' + 'x'*30 for _ in range(30)),
    '| key | value |\n|---|---|\n' + '| k | long value |\n'*30,
    '```python\nprint(1)\n\n' + 'print(2)\n'*90 + '```',
    'Ordinary paragraph. '*70,
])
def test_summary_does_not_cut_markdown_blocks(block):
    prefix = 'Confirmed observation.\n\n'
    source = prefix + block
    result = bounded_report_summary(source, limit=260)
    assert result == prefix.rstrip() + '\n[Сокращено. Полный текст сохранён в отчёте задачи.]'
    assert source == prefix + block


def test_summary_preserves_small_complete_blocks_and_no_dangling_seven():
    source = 'Report.\n\n' + '\n'.join(f'{i}. ' + 'x'*180 for i in range(1,10))
    result = bounded_report_summary(source)
    assert '\n7.\n' not in result and result.startswith('Report.\n')
    small = '1. One\n2. Two\n\n| k | v |\n|---|---|\n| a | b |\n\n```\nx\n```'
    assert bounded_report_summary(small) == small
