"""Context regressions inspect the actual provider HTTP JSON, without LIVE LLMs."""
import copy
import json

import httpx
import pytest

from server import controller, database as db, run_journal
from server.context_budget import request_budget
from test_tool_only_protocol import _message, _run_case, _tool_call


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'context.sqlite')
    db.init_db()


def call(name, args, ident):
    return _message(tool_calls=[_tool_call(ident, name, args)])


def terminal():
    return call('answer_text', {'status':'success','evidence_refs':['step_1'],
        'result': 'Observed', 'unresolved': []}, 'end')


def run_requests(monkeypatch, responses, send, *, cfg=None, on_request=None, max_iterations=8, **worker_kwargs):
    """Use the real request builder/fit_request; replace only its HTTP client."""
    requests = []
    cfg = cfg or {'model': 'mock', 'base_url': 'https://provider.invalid',
                  'api_key': 'fake-not-a-live-key', 'max_tokens': 512,
                  'unknown_model_input_tokens': 1_000_000}

    def completion_factory(responses, captured=None):
        queue = list(responses)

        class FakeHTTP:
            async def post(self, url, *, json, headers):
                requests.append(copy.deepcopy(json))
                if on_request:
                    on_request(json, cfg, len(requests))
                assert queue, 'Unexpected additional model call'
                return httpx.Response(200, json=queue.pop(0), request=httpx.Request('POST', url))

        async def complete(**kwargs):
            return await controller._chat_completion_request(**{**kwargs, 'client': FakeHTTP()})

        return complete

    monkeypatch.setattr('test_tool_only_protocol._completion_fn', completion_factory)
    result = _run_case(responses, send_command_fn=send, cfg=cfg,
                       user_message='TASK_MUST_SURVIVE: inspect permitted sources', max_iterations=max_iterations, **worker_kwargs)
    return result, requests


def tool_data(request):
    return [json.loads(m['content']) for m in request['messages'] if m['role'] == 'tool']


def assert_protocol(request):
    messages = request['messages']
    for index, message in enumerate(messages):
        if message.get('tool_calls'):
            replies = []
            for item in messages[index + 1:]:
                if item['role'] != 'tool':
                    break
                replies.append(item['tool_call_id'])
            assert replies == [c['id'] for c in message['tool_calls']]
        if message['role'] == 'tool':
            start = index - 1
            while messages[start]['role'] == 'tool':
                start -= 1
            assert messages[start].get('tool_calls')


@pytest.mark.parametrize('pages', [2, 3])
def test_a_e_original_pages_accumulate_in_actual_next_request(monkeypatch, pages):
    monkeypatch.setattr(run_journal, 'tool_result_token_budget', lambda: 30000)
    sent = []

    async def send(device, action, args):
        sent.append(action)
        return {'returncode': 0, 'stdout': 'BEGIN-' + 'a' * 4500 + '-MIDDLE-' + 'b' * 4500 + '-END', 'stderr': ''}

    responses = [call('execute_cmd', {'command': 'hostname'}, 'source')]
    responses += [call('system_get_last_run_summary', {'step_id': 'step_1', 'report_offset': offset}, f'page-{offset}')
                  for offset in range(0, pages * 4000, 4000)]
    result, requests = run_requests(monkeypatch, [*responses, terminal()], send)
    original_pages = [s['result'] for s in result['commands'] if s['tool_name'] == 'system.get_last_run_summary']
    for count in range(1, pages + 1):
        visible = [d['result'] for d in tool_data(requests[count + 1])
                   if d['tool_name'] == 'system.get_last_run_summary']
        assert visible == original_pages[:count]
        assert [p['report_offset'] for p in visible] == list(range(0, count * 4000, 4000))
        assert len({p['sha256'] for p in visible}) == 1
        assert all(p['source_step_id'] == 'step_1' for p in visible)
    assert sent == ['execute_cmd']
    assert len(requests) == pages + 2
    for request in requests:
        assert_protocol(request)


def test_b_independent_directory_observations_remain_available(monkeypatch):
    async def send(device, action, args):
        return {'path': args['path'], 'dirs': [], 'files': [
            {'name': f'OBSERVED_{args["path"][-1]}.txt', 'path': args['path'] + '/file.txt', 'size': 17}]}

    responses = [call('list_dir', {'path': 'C:/Folder' + name}, name) for name in 'ABC']
    _, requests = run_requests(monkeypatch, [*responses, terminal()], send)
    visible = [d['result']['files'][0]['name'] for d in tool_data(requests[-1])]
    assert visible == [f'OBSERVED_{name}.txt' for name in 'ABC']


def test_c_result_over_old_1200_limit_is_not_discarded(monkeypatch):
    large = 'FACT_FROM_FIRST_READING-' + 'x' * 6000

    async def send(device, action, args):
        return {'returncode': 0, 'stdout': large if args['command'] == 'hostname' else 'second fact', 'stderr': ''}

    _, requests = run_requests(monkeypatch, [call('execute_cmd', {'command': 'hostname'}, 'first'),
        call('execute_cmd', {'command': 'whoami'}, 'second'), terminal()], send)
    assert tool_data(requests[-1])[0]['result']['stdout'] == large


def test_d_paged_projection_keeps_offsets_and_hash_when_text_cannot_fit(monkeypatch):
    cfg = {'model': 'mock', 'base_url': 'https://provider.invalid', 'api_key': 'fake-not-a-live-key',
           'max_tokens': 512, 'unknown_model_input_tokens': 1_000_000}

    def bound(request, cfg, count):
        if count == 1:
            cfg['unknown_model_input_tokens'] = request_budget(request, cfg)['estimated_input_tokens'] + 5000

    async def send(device, action, args):
        return {'returncode': 0, 'stdout': 'SOURCE-' + 'x' * 14000, 'stderr': ''}

    result, requests = run_requests(monkeypatch, [call('execute_cmd', {'command': 'hostname'}, 'source'),
        call('system_get_last_run_summary', {'step_id': 'step_1', 'report_offset': 0}, 'page0'),
        call('system_get_last_run_summary', {'step_id': 'step_1', 'report_offset': 4000}, 'page4000'),
        terminal()], send, cfg=cfg, on_request=bound)
    pages={s['step_id']:s['result'] for s in result['commands'] if s['tool_name']=='system.get_last_run_summary'}
    for request in requests:
        assert request_budget(request,cfg)['estimated_input_tokens'] <= request_budget(request,cfg)['input_limit_tokens']
        assert_protocol(request)
        for data in tool_data(request):
            if data['tool_name']=='system.get_last_run_summary':
                original=pages[data['step_id']]
                for key in ('source_step_id','report_offset','next_offset','total_chars','sha256'):
                    assert data['result'][key]==original[key]
                if data['result']['report_text'] != original['report_text']:
                    assert data['truncation']['truncated'] is True
                    assert data['truncation']['recover_report_offset']==original['report_offset']+len(data['result']['report_text'])


@pytest.mark.parametrize('multi', [False, True])
def test_d_f_real_budget_preserves_goal_recovery_and_protocol(monkeypatch, multi):
    cfg = {'model': 'mock', 'base_url': 'https://provider.invalid', 'api_key': 'fake-not-a-live-key',
           'max_tokens': 512, 'unknown_model_input_tokens': 1_000_000}

    def bound_after_mandatory_request(request, cfg, count):
        if count == 1:
            cfg['unknown_model_input_tokens'] = request_budget(request, cfg)['estimated_input_tokens'] + 14000

    async def send(device, action, args):
        return {'path': args['path'], 'dirs': [], 'files': [
            {'name': f'FACT_{index:04d}.txt', 'path': args['path'] + f'/FACT_{index:04d}.txt', 'size': index}
            for index in range(250)]}

    responses = []
    for turn in range(3):
        calls = [_tool_call(f'{turn}-{n}', 'list_dir', {'path': f'C:/Folder{turn}{n}'})
                 for n in range(2 if multi else 1)]
        message = _message(tool_calls=calls)
        message['choices'][0]['message']['reasoning_content'] = f'REASONING_{turn}'
        responses.append(message)
    result, requests = run_requests(monkeypatch, [*responses, terminal()], send,
                                    cfg=cfg, on_request=bound_after_mandatory_request)
    assert len(requests) == 4
    for request in requests:
        budget = request_budget(request, cfg)
        assert budget['estimated_input_tokens'] <= budget['input_limit_tokens']
        assert request['messages'][0] == requests[0]['messages'][0]
        assert any(m['role'] == 'user' and 'TASK_MUST_SURVIVE' in m['content'] for m in request['messages'])
        assert request['tools'] == requests[0]['tools']
        assert_protocol(request)
    final = json.dumps(requests[-1]['messages'], ensure_ascii=False)
    assert 'earlier_actions' in final and 'system_get_last_run_summary' in final
    assert 'FACT_0000.txt' in final and 'truncation' in final
    for step in result['commands']:
        if step['tool_name'] == 'list_dir':
            assert step['step_id'] in final
            assert len(step['result']['files']) == 250
    latest = next(m for m in reversed(requests[-1]['messages']) if m.get('tool_calls'))
    assert latest['reasoning_content'] == 'REASONING_2'

@pytest.mark.parametrize('same', [True, False])
def test_search_repeat_feedback_reaches_provider(monkeypatch, same):
    from server import controller_non_pipeline
    calls = []

    async def search(cfg, query, limit):
        calls.append((query, limit))
        excerpt = 'same' if same or len(calls) == 1 else 'changed'
        return {'results': [{'url': 'https://example.org/a', 'title': 'A', 'content': excerpt}]}

    monkeypatch.setattr(controller_non_pipeline, '_run_web_search', search)
    args = {'query': 'example', 'max_results': 5}
    _, requests = run_requests(monkeypatch, [
        call('web_search', args, 'a'), call('web_search', args, 'b'), terminal(),
    ], None)
    assert calls == [('example', 5)] * 2
    assert len(requests) == 3
    feedback = 'The fresh search returned the same sources and excerpts as step_1'
    assert (feedback in json.dumps(requests[2]['messages'])) is same


@pytest.mark.parametrize('violation', [False, True])
def test_final_turn_restricts_provider_and_dispatch(monkeypatch, violation):
    dispatched = []

    async def send(device, action, args):
        dispatched.append(action)
        return {'path': 'C:/sample', 'dirs': [], 'files': []}

    read = call('list_dir', {'path': 'C:/sample'}, 'read')
    final = call('list_dir', {'path': 'C:/sample'}, 'unexpected') if violation else terminal()
    result, requests = run_requests(monkeypatch, [read, final], send, max_iterations=2,
        cfg={'model': 'deepseek-v4-flash', 'base_url': 'https://provider.invalid',
             'api_key': 'fake', 'max_tokens': 512})
    assert len(requests) == 2
    last = requests[-1]
    assert [item['function']['name'] for item in last['tools']] == ['answer_text']
    assert last['tool_choice'] == 'required'
    assert last['thinking'] == {'type': 'disabled'}
    assert any(item.get('step_id') == 'step_1' for item in tool_data(last))
    assert dispatched == ['list_dir']
    if violation:
        assert any(entry['result'].get('error') == 'final_turn_actions_disabled' for entry in result['commands'])
    else:
        assert result['commands'][-1]['result']['status'] == 'success'


def test_worker_opens_web_and_office_together(monkeypatch):
    from server import controller_non_pipeline
    from server.runtime_state import tasks

    monkeypatch.setattr('server.worker_scheduler.persist_worker_journal', lambda *args: None)
    monkeypatch.setitem(tasks, 'toolbook', {'task_id':'toolbook','user_id':1,'worker_id':'worker-1','device_ids':['device-1']})
    searches = []

    async def search(cfg, query, limit):
        searches.append(query)
        return {'results':[{'title':'Source','url':'https://example.org','content':'Observed fact'}]}

    monkeypatch.setattr(controller_non_pipeline, '_run_web_search', search)
    opened = _message(tool_calls=[
        _tool_call('open', 'system_list_tools', {'open_chapters':['web','office']}),
        _tool_call('early', 'web_search', {'query':'Not offered yet'}),
    ])
    _, requests = run_requests(monkeypatch, [opened,
        call('web_search', {'query':'Find evidence'}, 'search'),
        call('answer_text', {'status':'success','result':'Observed fact'}, 'done'),
    ], None, poll_task_id='toolbook',
        device_info={'os':'Windows','connected':True,'office_tools_version':1,'file_replace_protocol':2})
    names = [{t['function']['name'] for t in request['tools']} for request in requests]
    assert {'list_dir','read_file','answer_text','system_list_tools'} <= names[0]
    assert not {'web_search','office_pptx'} & names[0]
    assert {'web_search','office_pptx'} <= names[1] == names[2]
    assert searches == ['Find evidence']
    assert 'tool_not_offered_this_turn' in json.dumps(requests[1]['messages'])
    assert 'Observed fact' in json.dumps(requests[2]['messages'])


def test_worker_reads_directly_without_unavailable_office(monkeypatch):
    from server.runtime_state import tasks

    monkeypatch.setattr('server.worker_scheduler.persist_worker_journal', lambda *args: None)
    monkeypatch.setitem(tasks, 'toolbook-read', {
        'task_id':'toolbook-read','user_id':1,'worker_id':'worker-1','device_ids':['device-1'],
    })

    async def send(device, action, args):
        assert action == 'list_dir'
        return {'path':'C:/sample','dirs':[], 'files':[]}

    result, requests = run_requests(monkeypatch, [
        call('list_dir', {'path':'C:/sample'}, 'list'), terminal(),
    ], send, poll_task_id='toolbook-read',
        device_info={'os':'Windows','connected':True,'file_replace_protocol':2})
    assert len(requests) == 2
    assert 'office_pptx' not in requests[0]['messages'][0]['content']
    assert result['commands'][-1]['result']['status'] == 'success'
