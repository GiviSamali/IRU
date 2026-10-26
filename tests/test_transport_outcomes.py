"""Delivery ambiguity belongs to the common agent boundary, not shell confirmation."""
import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from server import task_runtime as runtime
from server.command_confirmation import command_confirmation
from server.routers import ws as ws_routes, tasks as routes
from test_browser_runtime_guards import setup_runtime
from test_confirmed_command_outcome import scenario, approve, finished


@pytest.mark.parametrize('risk,voice', [(None, True), ('', True), ('low', True), ('safe', True),
                                      ('high', False), ('medium', False), ('unrecognised', False)])
def test_legacy_risk_only_restricts_confirmation_channel(risk, voice):
    card = command_confirmation({'command': 'custom-operation', 'params': {'risk': risk}})
    assert card['voice_allowed'] is voice
    assert ('speech' in card) is voice
    if not voice:
        assert card['kind'] == 'dangerous'


@pytest.mark.parametrize('action,params', [
    ('execute_cmd', {'command': 'New-Item report.txt'}),
    ('write_content', {'path': 'report.txt', 'content': 'report'}),
    ('app.launch', {'command': 'python build.py'}),
    ('window.control', {'action': 'minimize'}),
])
def test_socket_disconnect_after_acceptance_blocks_replay_but_allows_observation(client, monkeypatch, action, params):
    user, key, chat = setup_runtime(client, monkeypatch)
    task = runtime.tasks['guard-task']
    task.update(results={}, commands=[], tasks=[], device_ids=[key], chat_id=chat, worker_id='worker')
    accepted, observations = [], []
    monkeypatch.setattr(ws_routes, 'get_user_by_token', lambda token: user)
    monkeypatch.setattr(ws_routes, 'check_device_limit', lambda *args: {'allowed': True})
    monkeypatch.setattr(ws_routes, 'add_audit_log', lambda *args: None)
    class Socket:
        def __init__(self):
            self.queue = asyncio.Queue()
            self.reading = asyncio.Event()
        async def accept(self):
            pass
        async def close(self, **kwargs):
            pass
        async def receive_text(self):
            self.reading.set()
            value = await self.queue.get()
            if value is None:
                raise WebSocketDisconnect()
            return json.dumps(value)
        async def send_text(self, message):
            accepted.append(json.loads(message)['payload'])
            # The real ws router now fails the already-sent command's future.
            await self.queue.put(None)
    class ObservationSocket:
        async def send_text(self, message):
            payload = json.loads(message)['payload']
            observations.append(payload['action'])
            runtime.devices[key]['pending'].pop(payload['id']).set_result({'path': 'C:/Temp', 'files': []})
    async def snapshot(*args, **kwargs):
        observations.append('device_refresh_state')
        return {'status': 'ok'}
    monkeypatch.setattr(runtime, 'collect_device_live_snapshot', snapshot)
    async def process(**kwargs):
        result = await kwargs['send_command_fn']('givi', action, params)
        assert result['status'] == 'unknown'
        await handler
        # A reconnect does not reset the task's uncertainty or authorise a replay.
        runtime.devices[key] = {'user_id': user['id'], 'info': {}, 'pending': {}, 'ws': ObservationSocket()}
        for repeat_action, repeat_params in [(action, params), ('execute_cmd', {'command': 'python retry.py'}),
                                             ('app.launch', {'command': 'python retry.py'})]:
            repeated = await kwargs['send_command_fn']('givi', repeat_action, repeat_params)
            assert repeated['status'] == 'blocked'
        read = await kwargs['send_command_fn']('givi', 'list_dir', {'path': 'C:/Temp'})
        assert read['files'] == []
        state = await kwargs['device_tool_fn']('device_refresh_state', {'device_id': 'givi'})
        assert state['status'] == 'ok'
        denied = await kwargs['device_tool_fn']('device_prepare_runtime', {'device_id': 'givi'})
        assert denied['status'] == 'blocked'
        return {'answer': 'Unknown; observations collected', 'commands': [], 'tasks': []}
    monkeypatch.setattr(runtime, 'process_nl_command', process)
    async def no_probe(**kwargs):
        pass
    monkeypatch.setattr(runtime, '_probe_python_toolchain_if_needed', no_probe)
    async def scenario():
        nonlocal handler
        socket = Socket()
        handler = asyncio.create_task(ws_routes.websocket_agent(socket, 'givi', user_token='token'))
        try:
            await socket.reading.wait()
            await asyncio.wait_for(runtime.run_nl_task('guard-task', user['id'], 'create and check', [key], chat), 3)
        finally:
            if not handler.done():
                handler.cancel()
                await asyncio.gather(handler, return_exceptions=True)
    handler = None
    asyncio.run(scenario())
    assert len(accepted) == 1 and accepted[0]['action'] == action
    assert observations == ['list_dir', 'device_refresh_state']
    assert task['_action_outcome_unknown'] and task['task_receipt']['task_status'] == 'partial'


def test_changed_path_is_a_definite_rejection_before_confirmed_dispatch(client, monkeypatch):
    user, key, chat = setup_runtime(client, monkeypatch)
    task = runtime.tasks['guard-task']
    monkeypatch.setattr(routes, 'get_current_user', lambda req: user)
    sent, results = [], []
    class Socket:
        async def send_text(self, message):
            sent.append(message)
    runtime.devices[key]['ws'] = Socket()
    async def process(**kwargs):
        results.append(await kwargs['send_command_fn']('givi', 'execute_cmd', {'command': 'Remove-Item report.txt'}))
        return {'answer': 'Rejected before dispatch', 'commands': [], 'tasks': []}
    monkeypatch.setattr(runtime, 'process_nl_command', process)
    async def scenario():
        worker = asyncio.create_task(runtime.run_nl_task('guard-task', user['id'], 'delete', [key], chat))
        try:
            for _ in range(200):
                if task['status'] == 'confirm':
                    break
                await asyncio.sleep(0)
            assert task['status'] == 'confirm'
            def rejected(*args):
                raise ValueError('path no longer permitted')
            monkeypatch.setattr(runtime, 'validate_execute_command_paths_for_device', rejected)
            await routes.api_confirm_task('guard-task', None, confirmation_id=task['confirm_data']['confirmation_id'])
            await asyncio.wait_for(worker, 2)
        finally:
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(scenario())
    assert sent == [] and not task.get('_action_outcome_unknown')
    assert results[0]['status'] == 'blocked'
    assert results[0]['error'] == 'confirmed_action_not_dispatched'


def test_legacy_predispatch_rejection_is_failed_not_unknown(scenario, monkeypatch):
    def rejected(*args):
        raise ValueError('path rejected before delivery')
    monkeypatch.setattr(runtime, 'validate_execute_command_paths_for_device', rejected)
    approve(scenario)
    task = finished(scenario)
    assert scenario.agent.calls == []
    assert task['task_receipt']['command_outcome'] == 'failed'
    assert task['task_receipt']['terminal_reason'] == 'confirmed_action_not_dispatched'


def test_legacy_disconnect_response_is_unknown(scenario):
    scenario.agent.result = {'error': 'AGENT_DISCONNECTED: reply lost'}
    approve(scenario)
    task = finished(scenario)
    assert len(scenario.agent.calls) == 1
    assert task['task_receipt']['command_outcome'] == 'unknown'


@pytest.mark.parametrize('initial_tool', ['execute_cmd', 'write_content'])
def test_plan_worker_can_observe_after_unknown_without_dispatching_mutations(initial_tool):
    from test_plan_stability import worker
    def msg(tool_call):
        return {"choices":[{"message":{"tool_calls":[tool_call]}}]}
    from test_pipeline_recovery_receipt import _answer_text_call
    calls, dispatched = [], []
    def call(name, args):
        return {'id': str(len(calls)), 'function': {'name': name, 'arguments': json.dumps(args)}}
    async def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return msg(call(initial_tool, {'command': 'New-Item C:/Temp/report.txt',
                                           'artifact_paths': ['C:/Temp/report.txt']} if initial_tool == 'execute_cmd'
                            else {'path': 'report.txt', 'content': 'x'}))
        assert 'execute_cmd' not in {tool['function']['name'] for tool in kwargs['tools']}
        if len(calls) == 2:
            # Even an unadvertised mutating tool selected by the model cannot run.
            return msg(call('app_launch', {'command': 'python retry.py'}))
        if len(calls) == 3:
            return msg(call('list_dir', {'path': 'C:/Temp'}))
        result = _answer_text_call('answer', 'Результат исходного действия неизвестен; каталог прочитан.', ['step_1', 'step_3'])
        args = json.loads(result['function']['arguments'])
        args.update(answer_type='partial_report')
        args['self_check'].update(claims_completed_action=False, has_sufficient_evidence=False,
                                 missing_evidence_question='Результат исходного действия не доказан.')
        result['function']['arguments'] = json.dumps(args)
        return msg(result)
    async def send(device, action, params):
        dispatched.append(action)
        return ({'status': 'unknown', 'error': 'confirmed_transport_outcome_unknown'} if len(dispatched) == 1
                else {'path': 'C:/Temp', 'files': []})
    result = worker(completion, send, cfg={'model': 'mock', 'answer_auditor_enabled': False})
    assert dispatched == [initial_tool, 'list_dir'] and len(calls) == 4
    assert result['commands'][0]['result']['status'] == 'unknown'
    assert result['commands'][1]['result']['error'] == 'mutation_blocked_after_unknown'
    assert result['commands'][2]['result']['files'] == []
    assert result['commands'][-1]['result']['answer_type'] == 'partial_report'
    from server.worker_reports import build_worker_report
    report = build_worker_report({'task_id':'local-unknown','status':'done',
        'device_ids':['pc'],'commands':result['commands'],'answer':result['answer']})
    assert report['status'] == 'unknown'
    assert report['goal_completed'] is False
