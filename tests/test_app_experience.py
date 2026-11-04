import asyncio

import pytest

from server import database as db
from server import task_runtime as runtime
from test_browser_runtime_guards import setup_runtime
from test_tool_only_protocol import _message, _run_case, _tool_call


def test_app_experience_is_shared_across_chats_but_isolated_by_owner_and_device(client, monkeypatch):
    user, key, first_chat = setup_runtime(client, monkeypatch)
    second_chat = db.create_chat(user['id'])['id']
    other = db.create_user('other-experience-owner')
    other_chat = db.create_chat(other['id'])['id']
    second_key = f"{user['id']}:other"
    foreign_key = f"{other['id']}:givi"
    for target, owner in [(key, user), (second_key, user), (foreign_key, other)]:
        monkeypatch.setitem(runtime.devices, target, {
            'user_id': owner['id'], 'info': {'os': 'Windows', 'app_experience_protocol': 1},
            'ws': object(), 'pending': {},
        })
    requests = []

    async def send(target, action, params, **kwargs):
        assert action == 'app.executable_identity'
        assert target == f"{kwargs['user_id']}:{target.split(':', 1)[-1]}"
        requests.append((target, params['pid']))
        if params['pid'] == 404:
            return {'status': 'unavailable'}
        return {'status': 'success', 'executable_identity': 'a' * 64}

    monkeypatch.setattr(runtime, 'send_command_to_agent', send)

    def run(owner, target, chat_id, *operations):
        results = []

        async def controller(**kwargs):
            for args in operations:
                results.append(await kwargs['device_tool_fn']('app_experience', args))
            return {'answer': 'Done', 'commands': [], 'tasks': []}

        monkeypatch.setattr(runtime, 'process_nl_command', controller)
        runtime.tasks['guard-task'].update(status='running', worker_id='worker-1', user_id=owner['id'],
                                           device_ids=[target], chat_id=chat_id)
        asyncio.run(runtime.run_nl_task('guard-task', owner['id'], 'Inspect the open editor', [target], chat_id))
        return results

    get = {'action': 'get', 'pid': 10}
    first = {'action': 'save', 'pid': 10, 'note': 'Attach to the editor instance.', 'step_id': 'step_1'}
    improved = {**first, 'note': 'Attach to the editor and resolve the document by path.', 'step_id': 'step_2'}
    missing, saved, repeated, updated = run(user, key, first_chat, get, first, first, improved)
    assert missing['found'] is False and saved['saved'] and not repeated['saved'] and updated['saved']
    loaded, unavailable, foreign = run(user, key, second_chat, {**get, 'pid': 11},
                                       {**get, 'pid': 404}, {**get, 'device_id': foreign_key})
    assert loaded['note'] == improved['note']
    assert unavailable['error'] == 'app_identity_unavailable'
    assert foreign['error'] == 'device_outside_worker_assignment'
    assert run(user, second_key, second_chat, get)[0]['found'] is False
    assert run(other, foreign_key, other_chat, get)[0]['found'] is False
    with db.get_db() as conn:
        rows = conn.execute('SELECT note, step_id FROM app_experience').fetchall()
    assert [tuple(row) for row in rows] == [(improved['note'], 'step_2')]
    assert (foreign_key, 10) in requests  # Only the other owner's own call reached this device.


@pytest.mark.parametrize('source,dispatched', [('toolbook', False), ('failed', False), ('success', True)])
def test_experience_save_requires_current_successful_instrument_step(source, dispatched):
    calls = []
    opened = _message(tool_calls=[_tool_call('book', 'system_list_tools', {'open_chapters': ['app']})])
    execute = _message(tool_calls=[_tool_call('op', 'execute_cmd', {'command': 'inspect editor'})])
    save = _message(tool_calls=[_tool_call('note', 'app_experience', {
        'action': 'save', 'pid': 10, 'step_id': 'step_1' if source == 'toolbook' else 'step_2',
        'note': 'Attach to the currently open editor.',
    })])
    done = _message(tool_calls=[_tool_call('done', 'answer_text', {
        'status': 'failed' if source == 'failed' else 'success', 'result': 'Finished.',
    })])

    async def send(device, action, params):
        if source == 'failed':
            return {'status': 'failed', 'error': 'Editor rejected request'}
        return {'returncode': 0, 'stdout': 'Editor response', 'stderr': ''}

    async def experience(name, args):
        calls.append((name, args))
        return {'status': 'success', 'saved': True}

    result = _run_case([opened, *([] if source == 'toolbook' else [execute]), save, done],
                       device_info={'os': 'Windows', 'app_experience_protocol': 1},
                       device_tool_fn=experience, send_command_fn=send)
    assert bool(calls) is dispatched
    step = next(entry for entry in result['commands'] if entry['tool_name'] == 'app.experience')
    assert step['tool_type'] == 'system'
    assert step['result'].get('saved', False) is dispatched


def test_worker_without_app_experience_never_contacts_the_tool():
    async def unexpected(*args):
        pytest.fail('App experience is optional')

    result = _run_case([_message(tool_calls=[_tool_call('done', 'answer_text', {
        'status': 'success', 'result': 'Done.',
    })])], device_info={'os': 'Windows', 'app_experience_protocol': 1}, device_tool_fn=unexpected)
    assert result['answer'] == 'Done.'
