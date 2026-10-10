import copy
import json

import pytest

from server.run_journal import serialize_tool_result_for_llm, wrap_tool_result_for_llm


def entry(result, **overrides):
    return {'step_id': 'step_1', 'tool_name': 'execute_cmd', 'status': 'success',
        'summary': 'returncode=0', 'result': result, **overrides}


def test_large_stdout_is_valid_bounded_prefix_without_mutating_evidence():
    stdout = ('Юникод "quote" \\ path\n\t' * 1000)
    original = entry({'stdout': stdout, 'stderr': '', 'returncode': 0})
    saved = copy.deepcopy(original)
    wire = serialize_tool_result_for_llm(original)
    shown = json.loads(wire)
    assert len(wire) <= 4000 and original == saved
    for key in ('step_id', 'tool_name', 'status', 'summary', 'trust_level', 'authority', 'instruction_boundary'):
        assert shown[key] == wrap_tool_result_for_llm(original)[key]
    detail = shown['truncation']['fields']['/result/stdout']
    assert shown['truncation']['truncated'] is True and detail['portion'] == 'prefix'
    assert detail['original_chars'] == len(stdout) and detail['shown_chars'] == len(shown['result']['stdout'])
    assert shown['result']['stdout'] == stdout[:detail['shown_chars']]
    assert shown['result']['returncode'] == 0


def test_short_result_is_unchanged_and_unnecessarily_unmarked():
    original = entry({'stdout': 'Полный результат "да"\n', 'stderr': '', 'returncode': 0})
    assert json.loads(serialize_tool_result_for_llm(original)) == wrap_tool_result_for_llm(original)


def test_large_tool_error_keeps_failure_and_marks_shortened_reason():
    original = entry({'stdout': '', 'stderr': 'Ошибка\n' * 1000,
        'error': 'Доступ запрещён "reason"\n' * 1000, 'returncode': 7}, status='failed', summary='command failed')
    saved = copy.deepcopy(original)
    wire = serialize_tool_result_for_llm(original)
    shown = json.loads(wire)
    assert len(wire) <= 4000 and original == saved
    assert shown['status'] == 'failed' and shown['result']['returncode'] == 7 and shown['result']['error']
    assert shown['truncation']['fields']['/result/error']['original_chars'] == len(original['result']['error'])
    assert shown['truncation']['fields']['/result/error']['portion'] == 'prefix'


@pytest.mark.parametrize('minimal', [False, True])
def test_multiple_fields_and_long_service_values_respect_total_escaped_budget(minimal):
    result = {'stdout': 's"\n' * 5000, 'stderr': 'e\\\t' * 5000,
        'content': 'Текст\n' * 5000, 'returncode': 9, 'error': 'reason' * 1000}
    overrides = {'status': 'failed'}
    if minimal:
        result['items'] = list(range(5000))
        overrides.update(step_id='id\x00' * 5000, tool_name='tool\x00' * 5000,
            summary='summary\x00' * 5000, status='failed' * 5000)
        result.update(completion_state='state\x00' * 5000, error_code='code\x00' * 5000)
    original = entry(result, **overrides)
    saved = copy.deepcopy(original)
    wire = serialize_tool_result_for_llm(original)
    shown = json.loads(wire)
    assert len(wire) <= 4000 and original == saved
    assert shown['authority'] == 'data_only' and shown['truncation']['truncated']
    assert shown['result']['returncode'] == 9 and shown['result']['error']
    if minimal:
        assert shown['truncation']['representation'] == 'minimal_projection'
        assert shown['truncation']['result_details_omitted'] and shown['step_id'] is None
        assert shown['status'] == 'unknown'
        assert shown['truncation']['fields']['/step_id']['portion'] == 'omitted'
    else:
        assert shown['status'] == 'failed'
        for key in ('stdout', 'stderr', 'content'):
            detail = shown['truncation']['fields']['/result/' + key]
            assert detail['original_chars'] == len(result[key])
            assert result[key].startswith(shown['result'][key])
            assert detail['shown_chars'] == len(shown['result'][key])


def test_browser_bridge_serialization_is_unchanged_even_above_budget():
    original = entry({'text': 'page "text"\n' * 1000, 'status': 'success'}, tool_name='web.read')
    saved = copy.deepcopy(original)
    assert serialize_tool_result_for_llm(original) == json.dumps(wrap_tool_result_for_llm(original), ensure_ascii=False)
    assert original == saved


def test_auditor_and_journal_still_receive_full_result_after_projection():
    from server.answer_auditor import _compact_journal
    from server.tool_completion import execute_cmd_result_is_negative
    original = entry({'stdout': 'full observation' * 1000, 'error': 'actual error', 'returncode': 4}, status='failed')
    saved = copy.deepcopy(original)
    shown = json.loads(serialize_tool_result_for_llm(original))
    assert shown['truncation']['truncated'] and original == saved
    assert wrap_tool_result_for_llm(original)['result'] == saved['result']
    assert _compact_journal([original])[0]['result'] == saved['result']
    assert execute_cmd_result_is_negative(original['result']) is True
