import asyncio
import json
import pytest
from server import controller_pipeline as pipeline
from server.controller_tools import WORKER_TOOLS, NON_PIPELINE_TOOLS
from server.pipeline_step_control import step_handoff
from test_controller_pipeline_budget import _shared_context
from test_controller_trust import _run_non_pipeline_case
from test_multi_device_completion import evidence
from server.run_journal import append_answer_step
from server.tool_completion import synthesize_terminal_answer_payload

ARGS = {'source_device_id': 'a', 'target_device_id': 'b', 'source_path': 'file', 'target_directory': 'desktop'}
SUCCESS = {'status': 'success', 'sha256_verified': True, 'filename': 'file', 'target_device': 'b', 'source_device': 'a', 'path': 'target/file', 'target_path': 'target/file'}


@pytest.mark.parametrize('success', [True, False])
def test_worker_transfer_completes_or_fails_without_another_llm_turn(monkeypatch, success):
    monkeypatch.setattr(pipeline.db, 'get_device_profile', lambda *a, **kw: None)
    calls = []
    async def llm(**kw):
        calls.append(kw)
        assert len(calls) == 1
        return {'choices': [{'message': {'tool_calls': [{'id': 'x', 'function': {'name': 'transfer_file', 'arguments': json.dumps(ARGS)}}]}}]}
    async def send(device, action, args):
        assert action == 'transfer_file' and args == ARGS
        return SUCCESS if success else {'status': 'failed', 'error': 'target_offline'}
    result = asyncio.run(pipeline.run_pipeline_worker(client=None, cfg={'model': 'mock'}, model='mock', shared=_shared_context(),
        overall_goal='Transfer', step={'title': 'Transfer', 'instruction': 'Transfer', 'completion_check': {'tool': 'transfer_file', 'target_device_id': 'b'}},
        completed_steps=[], chat_history=[], send_command_fn=send, get_file_link_fn=lambda *a: '', machine_guid=None,
        mem_user_id=None, poll_task_id=None, chat_completion_request_fn=llm, worker_tools=WORKER_TOOLS))
    assert result['status'] == ('ok' if success else 'error')
    assert len(calls) == 1
    if success:
        handoff = step_handoff(result['answer'], result['commands'], 'done')
        assert 'target/file' in handoff['artifacts']
        assert 'target_device' in json.dumps(handoff)


def test_tool_available_and_non_pipeline_dispatch(monkeypatch):
    from server import database
    monkeypatch.setattr(database, 'get_device_profile', lambda *a, **kw: None)
    for tools in [WORKER_TOOLS, NON_PIPELINE_TOOLS]:
        assert any(t['function']['name'] == 'transfer_file' for t in tools)
    calls = []
    async def send(device, action, args):
        calls.append((action, args)); return SUCCESS
    from test_controller_pipeline_budget import _answer_call
    _run_non_pipeline_case([
        {'choices': [{'message': {'tool_calls': [{'id':'x', 'function': {'name':'transfer_file','arguments':json.dumps(ARGS)}}]}}]},
        {'choices': [{'message': {'tool_calls': [_answer_call('done','Передано')]}}]},
    ], send_command_fn=send)
    assert calls == [('transfer_file', ARGS)]


@pytest.mark.parametrize('failure', [False, True])
def test_plan_create_transfer_open_dependency(monkeypatch, failure):
    monkeypatch.setattr(pipeline.db, 'create_task', lambda **kw: 1)
    monkeypatch.setattr(pipeline.db, 'update_step', lambda *a, **kw: None)
    monkeypatch.setattr(pipeline.db, 'finish_task', lambda *a, **kw: None)
    monkeypatch.setattr(pipeline.db, 'get_device_profile', lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, 'build_memory_block', lambda *a: '')
    monkeypatch.setattr(pipeline, 'collect_tasks', lambda *a: [])
    monkeypatch.setattr(pipeline, 'push_tasks_view', lambda *a: None)
    # Even if the old recovery classifier considers this recoverable, downstream must not run.
    monkeypatch.setattr(pipeline, '_step_has_recoverable_failure', lambda *a: True)
    calls = []
    async def completion(**kw):
        if kw['phase'] == 'pipeline.plan':
            return {'choices':[{'message':{'content':json.dumps({'steps':[
                {'title':'Create','device_id':'a'}, {'title':'Transfer','device_id':'a'}, {'title':'Open','device_id':'b'}]})}}]}
        return {'choices':[{'message':{'content':''}}]}
    async def worker(**kw):
        title = kw['step']['title']; calls.append(title)
        journal = []
        if title == 'Transfer':
            from server.run_journal import append_tool_step
            append_tool_step(journal, {'tool_name': 'transfer_file', 'action':'transfer_file', 'target_device_id':'a', 'step_index':1,
                'result': {'status':'failed','error':'download_failed'} if failure else SUCCESS})
            if failure: return {'status':'error','answer':'Передача не выполнена','commands':journal}
        else:
            evidence(journal, kw['step']['device_id'], {'returncode':0, 'stdout':'OK: done'})
        append_answer_step(journal, 'answer_text', synthesize_terminal_answer_payload(journal[-1]), target_device_id=kw['step']['device_id'])
        return {'status':'ok','answer':'done','commands':journal}
    monkeypatch.setattr(pipeline, 'run_pipeline_worker', worker)
    result = asyncio.run(pipeline.process_pipeline_subagents(user_message='Create, transfer, open', device_id='a', device_info={'hostname':'a'},
        all_devices={d:{'info':{'hostname':d}} for d in ('a','b')}, send_command_fn=None, get_file_link_fn=None, chat_history=[],
        load_llm_config_fn=lambda:{'model':'mock'}, pick_model_fn=lambda *a:'mock', chat_completion_request_fn=completion,
        worker_tools=WORKER_TOOLS, windows_rules='', linux_rules=''))
    assert calls == (['Create','Transfer'] if failure else ['Create','Transfer','Open'])
    assert result['answer'].startswith('План выполнен не полностью.' if failure else 'План выполнен.')
