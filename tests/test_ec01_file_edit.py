"""EC-01 A1/A2. Synthetic local files/transport only; no installed Agent or LIVE."""
import asyncio
import codecs
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from core import actions
from server import database as db
from server.controller_shared import file_edit_dispatch_error
from server.controller_tools import NON_PIPELINE_TOOLS, file_tools_for_device
from server.run_journal import compact_write_content_result


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def edit(path, raw, **overrides):
    return actions.write_content(**(dict(path=str(path), content='0.21', expected_text='0.42',
        expected_sha256=sha(raw), expected_matches=1) | overrides))


@pytest.mark.parametrize('count', [1, 4])
@pytest.mark.parametrize('ending', ['\n', '\r\n', '\r\n\n'])
@pytest.mark.parametrize('final_newline', [False, True])
@pytest.mark.parametrize('codec,bom,encoding', [
    ('utf-8', b'', 'utf-8'), ('utf-8', codecs.BOM_UTF8, 'utf-8'),
    ('utf-16-le', codecs.BOM_UTF16_LE, 'utf-16'),
    ('utf-16-be', codecs.BOM_UTF16_BE, 'utf-16'),
    ('cp1251', b'', 'cp1251')])
def test_one_and_four_replace_only_confirmed_byte_spans(tmp_path, count, ending, final_newline,
                                                       codec, bom, encoding):
    text = ending.join(['// сохранить', *['radius=0.42;' for _ in range(count)], '// конец'])
    text += ending if final_newline else ''
    raw = bom + text.encode(codec)
    path = tmp_path / 'scene.ps1'
    path.write_bytes(raw)
    result = actions.write_content(str(path), '0.21', expected_text='0.42',
        expected_sha256=sha(raw), expected_matches=count, encoding=encoding)
    expected = raw.replace('0.42'.encode(codec), '0.21'.encode(codec))
    assert path.read_bytes() == expected
    assert result['committed'] and result['verified'] and result['matches'] == count
    assert result['previous_sha256'] == sha(raw) and result['file_sha256'] == sha(expected)
    assert result['verification_scope'] == 'file_bytes_only'


@pytest.mark.parametrize('raw,old,new,count,encoding', [
    (b'0.42', 'missing', 'new', 1, 'utf-8'),
    (b'0.42 0.42', '0.42', 'new', 1, 'utf-8'),
    (b'0.42', '0.42', 'new', 4, 'utf-8'),
    (b'aaa', 'aa', 'new', 2, 'utf-8'),
    (b'0.42', '0.42', 'new', True, 'utf-8'),
    (b'0.42', '0.42', 'new', 0, 'utf-8'),
    (b'0.42', '0.42', 'new', '1', 'utf-8'),
    (b'\xff 0.42', '0.42', 'new', 1, 'utf-8'),
    (b'0.42', '0.42', 'new', 1, 'iso2022_jp'),
    (codecs.BOM_UTF16_LE + '0.42'.encode('utf-16-le'), '0.42', 'new', 1, 'utf-8'),
    (b'0.42\r\n', '0.42\r\n', 'new\n', 1, 'utf-8'),
    (b'0.42\n', '0.42\n', 'new', 1, 'utf-8'),
])
def test_unsupported_or_unconfirmed_edit_never_writes(tmp_path, raw, old, new, count, encoding):
    path = tmp_path / 'file.txt'
    path.write_bytes(raw)
    result = actions.write_content(str(path), new, expected_text=old, expected_matches=count,
                                   expected_sha256=sha(raw), encoding=encoding)
    assert result['committed'] is False and result['error']
    assert path.read_bytes() == raw
    assert not list(tmp_path.glob('.iru-*'))


def test_stale_replay_and_replacement_containing_original_are_not_repeated(tmp_path):
    path = tmp_path / 'file.txt'
    raw = b'0.42'
    path.write_bytes(raw)
    args = dict(path=str(path), content='0.42 plus', expected_text='0.42',
                expected_sha256=sha(raw), expected_matches=1)
    assert actions.write_content(**args)['committed']
    assert actions.write_content(**args)['committed'] is False
    assert path.read_bytes() == b'0.42 plus'


def test_two_simultaneous_calls_with_same_basis_commit_once(tmp_path):
    path = tmp_path / 'file.txt'
    path.write_bytes(b'0.42')
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: edit(path, b'0.42'), range(2)))
    assert sum(r['committed'] is True for r in results) == 1
    assert path.read_bytes() == b'0.21'


def test_external_change_before_commit_is_retained(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_bytes(b'0.42')
    write = actions._write_content
    def conflicting(*args, **kwargs):
        path.write_bytes(b'other writer')
        return write(*args, **kwargs)
    monkeypatch.setattr(actions, '_write_content', conflicting)
    result = edit(path, b'0.42')
    assert result['committed'] is False and 'changed_before_commit' in result['error']
    assert path.read_bytes() == b'other writer'


def test_postcommit_mismatch_is_unknown_never_verified(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_bytes(b'0.42')
    replace = actions.os.replace
    def conflicting(source, destination):
        replace(source, destination)
        Path(destination).write_bytes(b'other writer')
    monkeypatch.setattr(actions.os, 'replace', conflicting)
    result = edit(path, b'0.42')
    assert result['committed'] is True and result['status'] == 'unknown'
    assert not result.get('verified') and path.read_bytes() == b'other writer'


@pytest.mark.skipif(os.name != 'nt', reason='Windows named mutex contract')
def test_other_agent_process_in_same_windows_session_cannot_write(tmp_path):
    path = tmp_path / 'file.txt'
    path.write_bytes(b'0.42')
    code = ('import sys; sys.path.insert(0,sys.argv[1]); from core.actions import _windows_write_mutex\n'
            'with _windows_write_mutex(sys.argv[2]):\n'
            ' print("locked",flush=True)\n'
            ' sys.stdin.readline()\n')
    child = subprocess.Popen([sys.executable, '-c', code,
        str(Path(__file__).resolve().parents[1] / 'agent'), str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        result = edit(path, b'0.42')
        assert result['committed'] is False and result['error'] == 'write_target_busy'
        assert path.read_bytes() == b'0.42'
    finally:
        child.communicate('\n', timeout=15)
    assert edit(path, b'0.42')['committed']


def test_native_read_is_complete_bounded_and_keeps_hash_and_newlines(tmp_path):
    path = tmp_path / 'file.txt'
    raw = codecs.BOM_UTF8 + 'текст\r\n0.42\n'.encode()
    path.write_bytes(raw)
    read = actions.get_file_content(str(path), text_only=True)
    assert read['complete'] and read['file_sha256'] == sha(raw)
    assert read['text'] == 'текст\r\n0.42\n' and read['bom'] == codecs.BOM_UTF8.hex()
    assert 'data_b64' not in read
    assert 'data_b64' in actions.get_file_content(str(path))  # Existing download contract.
    assert actions.get_file_content(str(path), text_only=True, max_size=3).get('error')
    assert path.read_bytes() == raw


def test_native_read_does_not_bind_text_to_a_changed_version(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'
    path.write_bytes(b'old')
    fingerprint = actions._file_fingerprint
    calls = []
    def changing(target):
        calls.append(target)
        if len(calls) == 2:
            path.write_bytes(b'new')
        return fingerprint(target)
    monkeypatch.setattr(actions,'_file_fingerprint',changing)
    result = actions.get_file_content(str(path),text_only=True)
    assert result['error'] == 'file_changed_during_read'
    assert 'text' not in result and 'file_sha256' not in result


@pytest.mark.parametrize('action,args', [
    ('execute_cmd', {'command':'arbitrary shell', 'preserve_file_bytes':False}),
    ('write_content', {'path':'C:/Temp/a.txt','content':'reconstructed whole file'}),
    ('write_content', {'expected_text':'old','expected_sha256':'0'*64,'append':True}),
    ('office_docx', {'action':'edit'}), ('transfer_file', {}),
    ('device_prepare_runtime', {}), ('unknown.action', {}),
])
def test_accepted_constraint_blocks_all_non_native_mutation_paths(action, args):
    assert file_edit_dispatch_error({'preserve_file_bytes':True}, action, args)
    assert file_edit_dispatch_error({'preserve_file_bytes':False}, action, args) is None


def test_native_exact_request_and_read_remain_available():
    task = {'preserve_file_bytes':True}
    assert file_edit_dispatch_error(task,'get_file_content',{}) is None
    assert file_edit_dispatch_error(task,'write_content',{'expected_text':'old',
        'expected_sha256':'0'*64,'expected_matches':4}) is None


@pytest.mark.parametrize('protocol', [None, 1, 2])
def test_negotiated_tools_do_not_offer_unsupported_modes(protocol):
    info = {'file_replace_protocol':protocol}
    normal = {t['function']['name']:t['function'] for t in file_tools_for_device(NON_PIPELINE_TOOLS,info)}
    assert 'execute_cmd' in normal
    assert ('read_file' in normal) == (protocol == 2)
    props = normal['write_content']['parameters']['properties']
    assert ('expected_text' in props) == (protocol == 2)
    assert ('expected_matches' in props) == (protocol == 2)
    restricted = {t['function']['name']:t['function'] for t in
                  file_tools_for_device(NON_PIPELINE_TOOLS,info,preserve=True)}
    assert 'execute_cmd' not in restricted
    assert ('write_content' in restricted) == (protocol == 2)
    assert 'list_dir' in restricted
    if 'write_content' in restricted:
        assert 'expected_sha256' in restricted['write_content']['parameters']['required']


def test_server_independently_negotiates_actual_target_and_keeps_path_policy(monkeypatch):
    from server import task_runtime as runtime
    sent = []
    class WS:
        async def send_text(self, message):
            payload = json.loads(message)['payload']
            sent.append(payload)
            dev['pending'].pop(payload['id']).set_result({'status':'ok'})
    dev = {'info':{'os':'Windows'}, 'ws':WS(), 'pending':{}}
    monkeypatch.setitem(runtime.devices,'1:pc',dev)
    monkeypatch.setattr(runtime,'get_device_profile',lambda *a,**k:None)
    args = {'path':'C:/Temp/file.txt','content':'new','expected_text':'old','expected_sha256':sha(b'old')}
    def send(action, params):
        return asyncio.run(runtime.send_command_to_agent('1:pc',action,params))
    assert send('write_content',args)['dispatched'] is False
    dev['info']['file_replace_protocol'] = 1
    assert send('write_content',{**args,'expected_matches':4})['dispatched'] is False
    assert send('get_file_content',{'path':args['path'],'text_only':True})['dispatched'] is False
    assert not sent
    send('write_content',args)  # Legacy unique replacement still supported.
    dev['info']['file_replace_protocol'] = 2
    send('write_content',{**args,'expected_matches':4})
    send('get_file_content',{'path':args['path'],'text_only':True})
    assert len(sent) == 3 and sent[1]['params']['expected_matches'] == 4
    with pytest.raises(RuntimeError,match='BLOCKED'):
        send('write_content',{**args,'path':'C:/Windows/file.txt','expected_matches':4})
    assert len(sent) == 3


def test_rejected_exact_request_does_not_claim_replacement_text_is_whole_file_hash():
    compact = compact_write_content_result({'content':'new','expected_text':'old'},
        {'status':'error','committed':False,'error':'unsupported'})
    assert 'expected_content_sha256' not in compact and compact['committed'] is False


def test_scheduler_persists_constraint_and_assignment_cannot_downgrade(tmp_path, monkeypatch):
    from server.worker_scheduler import WorkerScheduler, init_worker_storage
    from server.runtime_state import tasks
    monkeypatch.setattr(db,'DB_PATH',tmp_path / 'ec01.sqlite')
    db.init_db()
    init_worker_storage()
    user = db.create_user('ec01-owner')
    chat = db.create_chat(user['id'],'EC01')['id']
    scheduler = WorkerScheduler()
    monkeypatch.setattr(scheduler,'ensure_runner',lambda owner:None)
    base = {'user_id':user['id'],'chat_id':chat,'message':'Replace four values',
            'device_ids':[f"{user['id']}:pc"], 'preserve_file_bytes':True}
    result = asyncio.run(scheduler.submit_goal(base,[{'key':'edit','objective':'Exact edit',
        'preserve_file_bytes':False}],request_key='protected'))
    try:
        task = result['tasks'][0]
        assert task['preserve_file_bytes'] is True
        with db.get_db() as connection:
            saved = json.loads(connection.execute('SELECT payload FROM worker_jobs WHERE task_id=?',
                (task['task_id'],)).fetchone()[0])
        assert saved['preserve_file_bytes'] is True
    finally:
        for task in result['tasks']:
            tasks.pop(task['task_id'],None)


def run_controller(responses, send, monkeypatch, tmp_path, *, protected=True, captured=None):
    from server.controller_non_pipeline import process_non_pipeline_command
    from server.runtime_state import tasks
    from test_tool_only_protocol import _completion_fn
    monkeypatch.setattr(db,'DB_PATH',tmp_path / 'controller.sqlite')
    db.init_db()
    monkeypatch.setitem(tasks,'ec01-controller',{'task_id':'ec01-controller','goal_id':'goal',
        'proposed_objective':'Replace four values without changing other bytes',
        'preserve_file_bytes':protected})
    return asyncio.run(process_non_pipeline_command(user_message='Replace four values', device_id='pc',
        device_info={'os':'Windows','file_replace_protocol':2}, send_command_fn=send,
        get_file_link_fn=lambda *a:'',chat_history=[],user_id=None,chat_id=None,modes={},
        poll_task_id='ec01-controller',cfg={'model':'mock','answer_auditor_enabled':False},system_msg='system',
        machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=6,
        pick_model_fn=lambda *a:'mock',chat_completion_request_fn=_completion_fn(responses,captured)))


@pytest.mark.parametrize('protected', [False, True])
def test_worker_cannot_bypass_accepted_constraint_but_ordinary_task_keeps_tools(tmp_path, monkeypatch, protected):
    from test_tool_only_protocol import _message, _tool_call, _answer_call
    sent = []
    async def send(device, action, params):
        sent.append(action)
        return {'status':'ok','returncode':0,'stdout':'observed','stderr':''}
    result = run_controller([
        _message(tool_calls=[_tool_call('shell','execute_cmd',{'command':'opaque mutation'})]),
        _message(tool_calls=[_tool_call('overwrite','write_content',{'path':'C:/Temp/file.txt','content':'whole file'})]),
        _message(tool_calls=[_answer_call('end','Изменение не подтверждено.',answer_type='partial_report',basis=['step_1','step_2'])]),
    ],send,monkeypatch,tmp_path,protected=protected)
    if protected:
        assert sent == []
        assert all(entry['result'].get('dispatched') is False for entry in result['commands'][:2])
    else:
        assert sent == ['execute_cmd','write_content']


def test_unknown_outcome_blocks_second_mutation_but_allows_native_read(tmp_path, monkeypatch):
    from test_tool_only_protocol import _message, _tool_call, _answer_call
    path = tmp_path / 'file.txt'
    path.write_bytes(b'0.42 0.42 0.42 0.42\r\n')
    args = {'path':str(path),'expected_text':'0.42','content':'0.21',
            'expected_sha256':sha(path.read_bytes()),'expected_matches':4}
    sent = []
    async def send(device, action, params):
        sent.append(action)
        if action == 'write_content':
            # Transport lost its receipt after a real synthetic local commit.
            assert actions.write_content(**params)['committed']
            return {'status':'unknown','error':'confirmed_transport_outcome_unknown'}
        return actions.get_file_content(**params)
    result = run_controller([
        _message(tool_calls=[_tool_call('edit','write_content',args)]),
        _message(tool_calls=[_tool_call('retry','write_content',args)]),
        _message(tool_calls=[_tool_call('observe','read_file',{'path':str(path)})]),
        _message(tool_calls=[_answer_call('end','Исход записи был неизвестен; последующая версия прочитана.',
            answer_type='partial_report',basis=['step_1','step_3'])]),
    ],send,monkeypatch,tmp_path)
    assert sent == ['write_content','get_file_content']
    assert path.read_bytes() == b'0.21 0.21 0.21 0.21\r\n'
    assert any(entry['result'].get('error') == 'mutation_blocked_after_unknown' for entry in result['commands'])


def test_intake_constraint_is_strict_and_registration_preserves_negotiation(monkeypatch):
    from pydantic import ValidationError
    from server.orchestrator import Decision, Assignment
    from core import runtime
    assert Decision(intent='delegate',preserve_file_bytes=True).preserve_file_bytes
    with pytest.raises(ValidationError):
        Assignment(key='edit',objective='Edit file',preserve_file_bytes='true')
    monkeypatch.setattr(runtime,'collect_system_info',lambda **kw:{'file_replace_protocol':2})
    monkeypatch.setattr(runtime,'load_device_passport_cache',lambda:None)
    assert runtime.build_registration_payload('pc','test')['file_replace_protocol'] == 2


def test_mixed_goal_scopes_file_constraint_to_assignment_and_amend_cannot_remove_it(tmp_path,monkeypatch):
    from server.worker_scheduler import WorkerScheduler, init_worker_storage
    from server.runtime_state import tasks
    monkeypatch.setattr(db,'DB_PATH',tmp_path / 'mixed.sqlite')
    db.init_db()
    init_worker_storage()
    user = db.create_user('mixed-owner')
    chat = db.create_chat(user['id'],'Mixed')['id']
    scheduler = WorkerScheduler()
    monkeypatch.setattr(scheduler,'ensure_runner',lambda owner:None)
    base = {'user_id':user['id'],'chat_id':chat,'message':'Create doc, edit text and analyse',
            'device_ids':[f"{user['id']}:pc"], 'preserve_file_bytes':False}
    assignments = [{'key':'doc','objective':'Create a document'},
        {'key':'edit','objective':'Replace a value','preserve_file_bytes':True},
        {'key':'analysis','objective':'Analyse data'}]
    result = asyncio.run(scheduler.submit_goal(base,assignments,request_key='mixed'))
    try:
        created = result['tasks']
        assert [task['preserve_file_bytes'] for task in created] == [False,True,False]
        assert 'office_docx' in {t['function']['name'] for t in
            file_tools_for_device(NON_PIPELINE_TOOLS,{'file_replace_protocol':2},preserve=created[0]['preserve_file_bytes'])}
        assert file_edit_dispatch_error(created[0],'office_docx',{'action':'create'}) is None
        assert file_edit_dispatch_error(created[1],'execute_cmd',{})
        assert file_edit_dispatch_error(created[2],'execute_cmd',{}) is None
        amended = scheduler.amend_pending(created[1]['task_id'],user['id'],chat,expected_revision=1,
            human_request='Keep the same exact edit',objective='Exact edit remains',preserve_file_bytes=False)
        assert amended['revision'] == 2
        assert tasks[created[1]['task_id']]['preserve_file_bytes'] is True
        with db.get_db() as connection:
            saved = [json.loads(connection.execute('SELECT payload FROM worker_jobs WHERE task_id=?',
                (task['task_id'],)).fetchone()[0]) for task in created]
        assert [task['preserve_file_bytes'] for task in saved] == [False,True,False]
        with pytest.raises(ValueError,match='mixed_goal_requires_assignment_file_constraints'):
            asyncio.run(scheduler.submit_goal({**base,'preserve_file_bytes':True},assignments,request_key='ambiguous'))
    finally:
        for task in result['tasks']:
            tasks.pop(task['task_id'],None)


def test_retry_cannot_drop_prior_task_preservation(tmp_path,monkeypatch):
    from server.worker_scheduler import WorkerScheduler, init_worker_storage
    from server.runtime_state import tasks
    monkeypatch.setattr(db,'DB_PATH',tmp_path / 'retry.sqlite')
    db.init_db()
    init_worker_storage()
    user = db.create_user('retry-owner')
    chat = db.create_chat(user['id'],'Retry')['id']
    scheduler = WorkerScheduler()
    monkeypatch.setattr(scheduler,'ensure_runner',lambda owner:None)
    base = {'user_id':user['id'],'chat_id':chat,'message':'Edit text',
            'device_ids':[f"{user['id']}:pc"]}
    original = asyncio.run(scheduler.submit_goal(base,[{'key':'edit','objective':'Exact edit',
        'preserve_file_bytes':True}],request_key='original'))
    admitted = list(original['tasks'])
    try:
        source = admitted[0]['task_id']
        with db.get_db() as connection:
            connection.execute("UPDATE worker_jobs SET state='partial' WHERE task_id=?",(source,))
        tasks[source]['status'] = 'partial'
        retried = asyncio.run(scheduler.submit_goal(base,[{'key':'retry','objective':'Retry exact edit',
            'preserve_file_bytes':False,'replaces_source_task_id':source}],request_key='retry',
            goal_id=original['goal_id'],expected_revision=1))
        admitted.extend(retried['tasks'])
        assert retried['tasks'][0]['preserve_file_bytes'] is True
    finally:
        for task in admitted:
            tasks.pop(task['task_id'],None)


def test_discovery_and_explicit_read_route_keep_device_out_of_native_parameters(tmp_path,monkeypatch):
    from test_tool_only_protocol import _message, _tool_call, _answer_call
    raw = b'0.42\r\n'
    path = tmp_path / 'file.txt'
    path.write_bytes(raw)
    sent,captured = [],[]
    async def send(device,action,params):
        sent.append((device,action,dict(params)))
        assert 'device_id' not in params
        return actions.ACTIONS[action](**params)
    run_controller([
        _message(tool_calls=[_tool_call('discover','list_dir',{'path':str(tmp_path),'device_id':'selected-pc'})]),
        _message(tool_calls=[_tool_call('read','read_file',{'path':str(path),'device_id':'selected-pc'})]),
        _message(tool_calls=[_answer_call('end','Файл найден и прочитан.',answer_type='partial_report',basis=['step_1','step_2'])]),
    ],send,monkeypatch,tmp_path,captured=captured)
    assert [(device,action) for device,action,_ in sent] == [('selected-pc','list_dir'),('selected-pc','get_file_content')]
    assert sent[1][2] == {'path':str(path),'text_only':True,'max_size':256000}
    first_tools = {tool['function']['name'] for tool in captured[0]['tools']}
    assert {'list_dir','read_file','write_content'}.issubset(first_tools)
    assert 'execute_cmd' not in first_tools
    assert path.read_bytes() == raw


@pytest.mark.parametrize('text,extra,expected_complete', [
    ('short whole file',{},True), ('Большой файл\n' * 12000,{},False),
    ('small',{'entries':list(range(10000))},False)], ids=['full','truncated','minimal'])
def test_native_read_completeness_is_separate_from_model_visibility(monkeypatch,text,extra,expected_complete):
    import copy
    from server import run_journal as journal
    from server.context_budget import estimate_tokens
    monkeypatch.setattr(journal,'tool_result_token_budget',lambda:2048)
    entry = {'step_id':'step_1','tool_name':'read_file','status':'success','summary':'native read',
        'result':{'status':'ok','complete':True,'text':text,'file_sha256':sha(text.encode()),**extra}}
    before = copy.deepcopy(entry)
    wire = journal.serialize_tool_result_for_llm(entry)
    shown = json.loads(wire)
    assert entry == before and entry['result']['complete'] is True
    assert shown['result']['agent_read_complete'] is True
    assert shown['result']['llm_text_complete'] is expected_complete
    assert 'complete' not in shown['result']
    assert estimate_tokens(wire) <= 2048
    if expected_complete:
        assert shown['result']['text'] == text and 'truncation' not in shown
    else:
        assert shown['truncation']['truncated']
        if extra:
            assert shown['truncation']['representation'] == 'minimal_projection'
            assert 'text' not in shown['result']
        else:
            assert text.startswith(shown['result']['text']) and len(shown['result']['text']) < len(text)


def test_protection_context_does_not_claim_selection_or_human_authority_is_verified(monkeypatch):
    from server.runtime_state import tasks
    from server.controller_shared import worker_assignment_scope
    monkeypatch.setitem(tasks,'scope-test',{'goal_id':'goal','proposed_objective':'Edit permitted file',
        'preserve_file_bytes':True})
    _,block = worker_assignment_scope('scope-test')
    assert 'original human message remains the permission ceiling' in block
    assert 'does not prove that the file or spans match the human intent' in block
    assert 'llm_text_complete=true' in block


@pytest.mark.parametrize('protocol,target,error', [
    (1,'givi','agent_update_required_for_native_edit_workflow'),
    (2,'foreign','device_outside_worker_assignment')])
def test_runtime_checks_old_agent_workflow_and_assignment_device_before_transport(client,monkeypatch,protocol,target,error):
    from server import task_runtime as runtime
    from test_browser_runtime_guards import setup_runtime
    user,key,chat = setup_runtime(client,monkeypatch)
    runtime.devices[key]['info']['file_replace_protocol'] = protocol
    runtime.tasks['guard-task'].update(preserve_file_bytes=True,worker_id='worker-1',
        device_ids=[key],chat_id=chat,message='Replace a text value')
    observed = []
    async def process(**kwargs):
        result = await kwargs['send_command_fn'](target,'write_content',{'path':'C:/Temp/file.txt',
            'expected_text':'old','content':'new','expected_sha256':sha(b'old')})
        observed.append(result)
        return {'answer':'Edit not dispatched','commands':[],'tasks':[]}
    async def forbidden(*args,**kwargs):
        pytest.fail('Rejected workflow must not reach native transport')
    monkeypatch.setattr(runtime,'process_nl_command',process)
    monkeypatch.setattr(runtime,'send_command_to_agent',forbidden)
    asyncio.run(runtime.run_nl_task('guard-task',user['id'],'Replace a text value',[key],chat))
    assert len(observed) == 1 and observed[0]['error'] == error


def test_read_tool_contract_carries_no_file_write_permission():
    from server.tool_contracts import get_tool_contract
    contract = get_tool_contract('read_file')
    assert contract['permissions'] == ['file.read']
    assert contract['side_effects'] == [] and contract['risk_level'] == 'read_only'


def test_worker_receives_partial_text_marker_while_journal_keeps_full_agent_read(tmp_path,monkeypatch):
    from test_tool_only_protocol import _message, _tool_call, _answer_call
    from server import run_journal as journal
    monkeypatch.setattr(journal,'tool_result_token_budget',lambda:2048)
    text = 'radius=0.42;\r\n' * 3000
    path = tmp_path / 'large.txt'
    path.write_bytes(text.encode())
    captured = []
    async def send(device,action,params):
        assert action == 'get_file_content'
        return actions.get_file_content(**params)
    result = run_controller([
        _message(tool_calls=[_tool_call('read','read_file',{'path':str(path),'device_id':'pc'})]),
        _message(tool_calls=[_answer_call('end','В сообщение передана только часть текста файла.',
            answer_type='partial_report',basis=['step_1'])]),
    ],send,monkeypatch,tmp_path,captured=captured)
    raw = result['commands'][0]['result']
    assert raw['text'] == text and raw['complete'] is True
    assert raw['file_sha256'] == sha(path.read_bytes())
    messages = captured[1]['messages']
    projection = next(json.loads(message['content']) for message in messages if message.get('role') == 'tool')
    assert projection['result']['agent_read_complete'] is True
    assert projection['result']['llm_text_complete'] is False
    assert len(projection['result']['text']) < len(text)
    assert 'claim full inspection only when read_file projection llm_text_complete=true' in messages[0]['content']
