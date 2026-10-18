"""B1-03: exact, live confirmation without an extra model or executor."""
import asyncio
import json
import time

import pytest
from fastapi import HTTPException

from server import api_support, task_runtime as runtime
from server.command_confirmation import ConfirmedAction, command_confirmation
from server.routers import tasks as routes
from test_browser_runtime_guards import setup_runtime


@pytest.mark.parametrize("command,expected", [
    ('python -c "print(42)"', 'ALLOW'), ('whoami', 'ALLOW'),
    ('python opaque_user_program.py', 'ALLOW'), ('New-Item report.txt', 'ALLOW'),
    ('Remove-Item report.txt', 'CONFIRM'), ('shutdown /s /t 0', 'CONFIRM'),
    ('diskpart', 'DENY'),
])
def test_existing_patterns_and_unknown_code(command, expected):
    result = api_support.assess_action('execute_cmd', {'command': command, 'risk': 'low'})
    assert result['decision'] == expected


def test_card_uses_decision_not_preview_or_worker_risk(monkeypatch):
    assessment = api_support.assess_action('execute_cmd', {'command': 'Remove-Item report.txt'})
    def no_classification(*args):
        pytest.fail('display must not classify the command again')
    monkeypatch.setattr('server.command_confirmation.assess_action', no_classification)
    card = command_confirmation({'command': 'ordinary preview', 'params': {'risk': 'safe'}}, assessment)
    assert card['kind'] == 'deletion' and not card['voice_allowed']


def test_skip_confirm_and_untyped_receipt_are_not_authority(monkeypatch):
    monkeypatch.setitem(runtime.devices, '1:pc', {'user_id': 1, 'info': {}})
    monkeypatch.setattr(runtime, 'get_device_profile', lambda *a, **kw: None)
    async def scenario():
        for receipt in (None, {}, {'approved': True}):
            with pytest.raises(api_support.ActionConfirmationRequired):
                await runtime.send_command_to_agent('1:pc', 'execute_cmd',
                    {'command': 'Remove-Item report.txt'}, user_id=1, skip_confirm=True, confirmation=receipt)
    asyncio.run(scenario())


def test_internal_receipt_is_bound_and_one_shot():
    params = {'command': 'Remove-Item report.txt'}
    assessment = api_support.assess_action('execute_cmd', params)
    receipt = ConfirmedAction('1:pc', 'execute_cmd', params, assessment)
    for device, action, args in [('2:pc', 'execute_cmd', params),
                                ('1:pc', 'app.launch', params),
                                ('1:pc', 'execute_cmd', {'command': 'Remove-Item other.txt'})]:
        with pytest.raises(RuntimeError):
            receipt.consume(device, action, args)
    assert receipt.consume('1:pc', 'execute_cmd', params) == assessment
    with pytest.raises(RuntimeError):
        receipt.consume('1:pc', 'execute_cmd', params)


@pytest.mark.parametrize('choice', ['accept', 'deny', 'cancel', 'cancel_after', 'expired', 'changed', 'scope', 'disconnect', 'lost'])
def test_live_worker_confirmation(client, monkeypatch, choice):
    user, key, chat = setup_runtime(client, monkeypatch)
    task = runtime.tasks['guard-task']
    task.update(worker_id='worker', device_ids=[key], modes={'autonomous': True}, chat_id=chat)
    dispatched, progress, evaluations = [], [], []
    assess = runtime.assess_action
    def counted(*args, **kwargs):
        evaluations.append(args[0])
        return assess(*args, **kwargs)
    monkeypatch.setattr(runtime, 'assess_action', counted)
    class WS:
        async def send_text(self, message):
            call = json.loads(message)['payload']
            dispatched.append(call['params']['command'])
            if choice == 'lost':
                raise ConnectionError('lost after delivery')
            runtime.devices[key]['pending'].pop(call['id']).set_result(
                {'returncode': 0, 'stdout': 'OK: verified', 'stderr': ''})
    runtime.devices[key]['ws'] = WS()
    monkeypatch.setattr(routes, 'get_current_user', lambda req: user)
    params = {'command': 'Remove-Item report.txt'}
    async def process(**kwargs):
        progress.append('entered')
        result = await kwargs['send_command_fn']('givi', 'execute_cmd', params)
        progress.append('resumed')
        if choice == 'lost':
            assert result['status'] == 'unknown'
            alternate = await kwargs['send_command_fn']('givi', 'app.launch', {'command': 'python retry.py'})
            assert alternate['status'] == 'blocked'
        return {'answer': 'result', 'commands': [], 'tasks': []}
    monkeypatch.setattr(runtime, 'process_nl_command', process)
    async def no_probe(**kwargs):
        pass
    monkeypatch.setattr(runtime, '_probe_python_toolchain_if_needed', no_probe)
    async def scenario():
        running = asyncio.create_task(runtime.run_nl_task('guard-task', user['id'], 'delete and continue', [key], chat))
        try:
            for _ in range(200):
                if task['status'] == 'confirm':
                    break
                await asyncio.sleep(0)
            assert task['status'] == 'confirm' and dispatched == [] and progress == ['entered']
            card = task['confirm_data']
            body = routes.CommandDecisionBody(confirmation_id=card['confirmation_id'], accepted=choice != 'deny')
            if choice == 'cancel':
                await routes.api_cancel_task('guard-task', None)
            elif choice == 'expired':
                card['expires_at'] = time.time() - 1
                with pytest.raises(HTTPException) as error:
                    await routes.api_command_decision('guard-task', body, None)
                assert error.value.status_code == 409
                await routes.api_cancel_task('guard-task', None)
            else:
                if choice == 'changed':
                    params['command'] = 'Remove-Item other.txt'
                if choice == 'scope':
                    task['device_ids'] = []
                if choice == 'disconnect':
                    runtime.devices[key]['ws'] = object()
                await routes.api_command_decision('guard-task', body, None)
                with pytest.raises(HTTPException):
                    await routes.api_command_decision('guard-task', body, None)
                if choice == 'cancel_after':
                    await routes.api_cancel_task('guard-task', None)
            await asyncio.wait_for(running, 3)
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
    asyncio.run(scenario())
    assert dispatched == (['Remove-Item report.txt'] if choice in {'accept', 'lost'} else [])
    assert progress.count('entered') == 1
    assert len(evaluations) == 1  # approval and card reuse the initial result
    if choice == 'accept':
        assert progress == ['entered', 'resumed']
    if choice == 'lost':
        assert task['_action_outcome_unknown']


def test_worker_tools_do_not_offer_preview_only_confirmation():
    from server.controller_tools import NON_PIPELINE_TOOLS, WORKER_TOOLS
    for tools in (NON_PIPELINE_TOOLS, WORKER_TOOLS):
        assert 'answer_request_confirmation' not in {tool['function']['name'] for tool in tools}


@pytest.mark.parametrize('command,lost,preview', [('whoami', False, False), ('Remove-Item report.txt', False, False),
                                        ('whoami', True, False), ('Remove-Item report.txt', True, False),
                                        ('Remove-Item report.txt', False, True)])
def test_simple_model_loop_keeps_journal_without_extra_model_calls(client, monkeypatch, command, lost, preview):
    from server.controller_non_pipeline import process_non_pipeline_command
    from server.controller_tools import NON_PIPELINE_TOOLS
    from test_command_error_controller_loop import _execute_call
    from test_pipeline_recovery_receipt import _answer_text_call
    user, key, chat = setup_runtime(client, monkeypatch)
    task = runtime.tasks['guard-task']
    task.update(results={}, commands=[], tasks=[], device_ids=[key], chat_id=chat, message='execute and report')
    calls, sent = [], []
    class WS:
        async def send_text(self, message):
            payload = json.loads(message)['payload']
            sent.append(payload)
            if lost and payload['action'] == 'execute_cmd':
                raise ConnectionError('delivered but response lost')
            runtime.devices[key]['pending'].pop(payload['id']).set_result(
                {'returncode': 0, 'stdout': 'OK: verified', 'stderr': ''})
    runtime.devices[key]['ws'] = WS()
    async def completion(**kwargs):
        calls.append(kwargs['messages'])
        if preview and len(calls) == 1:
            call = {'id': 'preview', 'function': {'name': 'answer_request_confirmation', 'arguments': json.dumps({
                'message': 'Confirm?', 'action': 'execute_cmd', 'command_preview': command, 'risk': 'high', 'basis': []})}}
        elif lost and len(calls) == 2:
            assert 'list_dir' in {tool['function']['name'] for tool in kwargs['tools']}
            assert 'execute_cmd' not in {tool['function']['name'] for tool in kwargs['tools']}
            call = {'id': 'observe', 'function': {'name': 'list_dir', 'arguments': json.dumps({'path': 'C:/Temp'})}}
        else:
            call = (_execute_call('action', command) if len(calls) == 1 + int(preview) else
                    _answer_text_call('answer', 'Результат проверен.', ['step_1']))
        if lost and len(calls) == 3:
            args = json.loads(call['function']['arguments'])
            args.update(answer_type='partial_report', text='Исход команды неизвестен; наблюдение получено.')
            args['self_check'].update(claims_completed_action=False, has_sufficient_evidence=False,
                                     missing_evidence_question='Результат исходной команды не установлен.')
            call['function']['arguments'] = json.dumps(args)
        return {'choices': [{'message': {'tool_calls': [call]}}]}
    async def process(**kwargs):
        return await process_non_pipeline_command(
            user_message=kwargs['user_message'], device_id='givi', device_info={'os': 'Windows'},
            send_command_fn=kwargs['send_command_fn'], get_file_link_fn=lambda *a: '',
            chat_history=[], user_id=user['id'], chat_id=chat, modes={}, poll_task_id='guard-task',
            cfg={'model': 'mock', 'answer_auditor_enabled': False}, system_msg='system',
            machine_guid=None, mem_user_id=None, non_pipeline_tools=NON_PIPELINE_TOOLS,
            max_iterations=4, pick_model_fn=lambda *a: 'mock', chat_completion_request_fn=completion)
    async def no_probe(**kwargs):
        pass
    monkeypatch.setattr(runtime, 'process_nl_command', process)
    monkeypatch.setattr(runtime, '_probe_python_toolchain_if_needed', no_probe)
    monkeypatch.setattr(routes, 'get_current_user', lambda req: user)
    async def scenario():
        execution = asyncio.create_task(runtime.run_nl_task('guard-task', user['id'], 'execute and report', [key], chat))
        try:
            if command.startswith('Remove'):
                for _ in range(200):
                    if task['status'] == 'confirm':
                        break
                    await asyncio.sleep(0)
                assert sent == [] and len(calls) == 1 + int(preview)
                await routes.api_confirm_task('guard-task', None,
                    confirmation_id=task['confirm_data']['confirmation_id'])
            await asyncio.wait_for(execution, 5)
        finally:
            if not execution.done():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
    asyncio.run(scenario())
    assert len(sent) == (2 if lost else 1) and len(calls) == (3 if lost else 2) + int(preview)
    assert task['commands'][0]['tool_name'] == 'execute_cmd'
    if lost:
        assert task['task_receipt']['task_status'] != 'completed'
        assert task['commands'][0]['result']['status'] == 'unknown'
