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
from server.controller_tools import NON_PIPELINE_TOOLS
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


def test_exact_replace_without_hash_preserves_unrelated_edits(tmp_path):
    path = tmp_path / 'file.txt'
    current = b'0.42\r\nother line changed\r\n'
    path.write_bytes(current)
    result = edit(path, current, expected_sha256=None)
    assert result['committed'] and path.read_bytes() == b'0.21\r\nother line changed\r\n'


@pytest.mark.parametrize('with_hash', [True, False])
def test_external_change_before_commit_is_retained(tmp_path, monkeypatch, with_hash):
    path = tmp_path / 'file.txt'
    path.write_bytes(b'0.42')
    write = actions._write_content
    def conflicting(*args, **kwargs):
        path.write_bytes(b'other writer')
        return write(*args, **kwargs)
    monkeypatch.setattr(actions, '_write_content', conflicting)
    result = edit(path, b'0.42', expected_sha256=sha(b'0.42') if with_hash else None)
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
    without_hash = {key: value for key, value in args.items() if key != 'expected_sha256'}
    rejected = send('write_content', without_hash)
    assert rejected['error'] == 'expected_sha256_required_by_agent' and not rejected['dispatched']
    assert len(sent) == 3
    dev['info']['file_replace_protocol'] = 3
    send('write_content', without_hash)
    send('get_file_content', {'path': args['path'], 'text_only': True})
    assert sent[3]['params'] == without_hash and sent[4]['action'] == 'get_file_content'


def test_rejected_exact_request_does_not_claim_replacement_text_is_whole_file_hash():
    compact = compact_write_content_result({'content':'new','expected_text':'old'},
        {'status':'error','committed':False,'error':'unsupported'})
    assert 'expected_content_sha256' not in compact and compact['committed'] is False


def run_controller(responses, send, monkeypatch, tmp_path, *, captured=None):
    from server.controller_non_pipeline import process_non_pipeline_command
    from server.runtime_state import tasks
    from test_tool_only_protocol import _completion_fn
    monkeypatch.setattr(db,'DB_PATH',tmp_path / 'controller.sqlite')
    db.init_db()
    monkeypatch.setitem(tasks,'ec01-controller',{'task_id':'ec01-controller','goal_id':'goal',
        'proposed_objective':'Replace four values without changing other bytes'})
    return asyncio.run(process_non_pipeline_command(user_message='Replace four values', device_id='pc',
        device_info={'os':'Windows','file_replace_protocol':2}, send_command_fn=send,
        get_file_link_fn=lambda *a:'',chat_history=[],user_id=None,chat_id=None,modes={},
        poll_task_id='ec01-controller',cfg={'model':'mock'},system_msg='system',
        machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=6,
        pick_model_fn=lambda *a:'mock',chat_completion_request_fn=_completion_fn(responses,captured)))


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
    assert any(entry['result'].get('error') in {'mutation_blocked_after_unknown', 'tool_not_offered_this_turn'}
               for entry in result['commands'])


def test_registration_preserves_exact_replacement_negotiation(monkeypatch):
    from core import runtime
    monkeypatch.setattr(runtime,'collect_system_info',lambda **kw:{'file_replace_protocol':2})
    monkeypatch.setattr(runtime,'load_device_passport_cache',lambda:None)
    assert runtime.build_registration_payload('pc','test')['file_replace_protocol'] == 2


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
    assert 'execute_cmd' in first_tools
    assert 'BLOCKED' in next(t['function']['description'] for t in captured[0]['tools'] if t['function']['name']=='execute_cmd')
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


def test_runtime_checks_assignment_device_before_exact_write(client,monkeypatch):
    from server import task_runtime as runtime
    from test_browser_runtime_guards import setup_runtime
    user,key,chat = setup_runtime(client,monkeypatch)
    runtime.devices[key]['info']['file_replace_protocol'] = 2
    runtime.tasks['guard-task'].update(worker_id='worker-1',
        device_ids=[key],chat_id=chat,message='Replace a text value')
    observed = []
    async def process(**kwargs):
        result = await kwargs['send_command_fn']('foreign','write_content',{'path':'C:/Temp/file.txt',
            'expected_text':'old','content':'new','expected_sha256':sha(b'old')})
        observed.append(result)
        return {'answer':'Edit not dispatched','commands':[],'tasks':[]}
    async def forbidden(*args,**kwargs):
        pytest.fail('Rejected workflow must not reach native transport')
    monkeypatch.setattr(runtime,'process_nl_command',process)
    monkeypatch.setattr(runtime,'send_command_to_agent',forbidden)
    asyncio.run(runtime.run_nl_task('guard-task',user['id'],'Replace a text value',[key],chat))
    assert len(observed) == 1 and observed[0]['error'] == 'device_outside_worker_assignment'


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
