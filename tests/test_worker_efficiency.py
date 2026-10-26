"""Offline regressions for the October 25 execution failures; no real device calls."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from core import actions
from server import database as db
from server.run_journal import (validate_answer_text_payload, ProtocolValidationError,
    latest_file_states, unfinished_execution_result, compact_write_content_result)
from server.worker_reports import bounded_report_summary
from server.worker_scheduler import dependency_context
from server.tool_repeat_guard import unchanged_command_evidence, command_observation_key
from test_tool_only_protocol import _run_case, _message, _tool_call, _answer_call, _answer_payload


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'efficiency.sqlite')
    db.init_db()


@pytest.mark.parametrize('raw,encoding', [
    (b'const radius=0.42;\r\n// keep\r\n', 'utf-8'),
    (b'\xef\xbb\xbfconst radius=0.42;\n// keep\n', 'utf-8'),
    ('радиус=0.42;\r\n'.encode('utf-16'), 'utf-16'),
])
def test_exact_edit_preserves_bytes_and_replay_is_not_a_mutation(tmp_path, raw, encoding):
    path = tmp_path / 'scene.js'
    path.write_bytes(raw)
    args = dict(path=str(path), expected_text='0.42', content='0.21', encoding=encoding,
                expected_sha256=digest(raw))
    result = actions.write_content(**args)
    assert result['committed'] and result['verified']
    expected = raw.replace('0.42'.encode('utf-16-le') if encoding == 'utf-16' else b'0.42',
                           '0.21'.encode('utf-16-le') if encoding == 'utf-16' else b'0.21')
    assert path.read_bytes() == expected
    assert result['file_sha256'] == digest(expected)
    assert compact_write_content_result(args, result)['previous_sha256'] == digest(raw)
    again = actions.write_content(**args)
    assert again['committed'] is False and path.read_bytes() == expected


@pytest.mark.parametrize('raw,old,error', [
    (b'abc', 'missing', 'source_missing'), (b'abc abc', 'abc', 'source_ambiguous'),
    (b'aaa', 'aa', 'source_ambiguous')])
def test_bad_match_never_changes_file(tmp_path, raw, old, error):
    path = tmp_path / 'file.txt'; path.write_bytes(raw)
    result = actions.write_content(str(path), 'new', expected_text=old, expected_sha256=digest(raw))
    assert error in result['error'] and result['committed'] is False
    assert path.read_bytes() == raw


def test_stale_hash_and_concurrent_write_are_preserved(tmp_path, monkeypatch):
    path = tmp_path / 'file.txt'; path.write_bytes(b'old')
    assert actions.write_content(str(path), 'new', expected_text='old', expected_sha256='0'*64)['committed'] is False
    original = actions._write_content
    def concurrent(*args, **kwargs):
        path.write_bytes(b'external edit')
        return original(*args, **kwargs)
    monkeypatch.setattr(actions, '_write_content', concurrent)
    result = actions.write_content(str(path), 'new', expected_text='old', expected_sha256=digest(b'old'))
    assert result['error'] == 'write_target_changed_before_commit'
    assert path.read_bytes() == b'external edit'


def test_replacement_containing_old_text_cannot_repeat(tmp_path):
    path = tmp_path / 'file.txt'; path.write_bytes(b'x')
    args = dict(expected_text='x', expected_sha256=digest(b'x'))
    assert actions.write_content(str(path), 'xx', **args)['committed']
    assert actions.write_content(str(path), 'xx', **args)['committed'] is False
    assert path.read_bytes() == b'xx'


def call(command):
    return _message(tool_calls=[_tool_call('call', 'execute_cmd', {'command':command, 'artifact_paths':[]})])


def syntax_error():
    return {'returncode':1, 'stdout':'', 'stderr':'ParserError: IncompleteDollarSubexpressionReference',
            'error':None, 'error_type':'shell_syntax_error'}


def test_stubborn_parser_error_uses_two_dispatches_not_sixteen():
    sent, captured = [], []
    async def send(*args):
        sent.append(args); return syntax_error()
    result = _run_case([call('broken')]*16, send_command_fn=send, captured=captured, max_iterations=20)
    assert len(sent) == 2 and len(captured) == 4
    from server.worker_reports import build_worker_report
    report=build_worker_report({**result,'task_id':'stubborn-parser','status':'done'})
    assert report['goal_completed'] is False
    assert result['task_receipt']['task_status'] == 'failed'
    assert 'shell_syntax_error' in str(captured[2]['messages'])


def test_parser_error_allows_corrected_method():
    sent=[]
    async def send(device, action, args):
        sent.append(args['command'])
        return syntax_error() if args['command']=='broken' else {'returncode':0,'stdout':'observed','stderr':''}
    responses=[call('broken'),call('broken'),call('broken'),call('corrected'),
        _message(tool_calls=[_answer_call('end','Observed only',answer_type='partial_report',basis=['step_3'])])]
    result=_run_case(responses,send_command_fn=send,max_iterations=20)
    assert sent==['broken','broken','corrected']
    assert result['answer']=='Observed only'


def test_unknown_outcome_is_not_reused_as_safe_failure():
    args={'command':'broken','artifact_paths':[]}
    e={'command':'broken','target_device_id':'pc','status':'failed',
       'execution_key':command_observation_key(args),'result':{**syntax_error(),'status':'unknown'}}
    assert unchanged_command_evidence([e,e],args,'pc','') is None


def file_step(number, before, after):
    return {'step_id':f'step_{number}', 'tool_name':'execute_cmd', 'status':'success',
        'device_id':'pc','command':'local test mutation', 'result':{'returncode':0,'stdout':'', 'stderr':'',
        'artifact_receipts':[{'path':'C:/scene.js','verified':True,'previous_sha256':digest(before),
                              'sha256':digest(after),'device_id':'pc'}]}}


@pytest.mark.parametrize('kind',['grounded_report','partial_report'])
def test_october_25_stale_basis_rejected_even_for_partial(kind):
    journal=[file_step(4,b'0.42',b'0.21'),file_step(18,b'0.21',b'0.42'),
             file_step(19,b'0.42',b'0.21'),file_step(20,b'0.21',b'0.42')]
    payload=_answer_payload('Reduced to 0.21',answer_type=kind,basis=['step_4','step_19'])
    normalized=validate_answer_text_payload(payload,journal)
    assert normalized['basis']==['step_4','step_18','step_19','step_20']  # Server, not model, binds all facts.
    assert latest_file_states(journal)[0]['sha256']==digest(b'0.42')
    payload.update(text='The edit was undone. Remaining task incomplete.',basis=['step_20'])
    assert validate_answer_text_payload(payload,journal)['text']==payload['text']


def test_dependency_debug_json_does_not_cost_five_model_calls():
    data={'task_id':'source','device_ids':['pc'],'answer':'File changed; browser not refreshed.',
          'report':{'status':'success','goal_completed':True,'summary':'File changed.',
                    'artifacts':[{'path':'C:/scene.js','sha256':digest(b'new')}],'evidence_refs':['step_1']},
          'commands':[{'technical_output':'x'*18000}]}
    result={'result_id':'r','sha256':digest(json.dumps(data).encode()),'data':data}
    material,pending=dependency_context([result])
    assert not pending and material[0]['requires_full_read'] is False
    assert material[0]['answer']==data['answer'] and material[0]['artifacts']==data['report']['artifacts']
    assert material[0]['full_material_tool']=='system_get_last_run_summary'
    assert material[0]['current_execution_evidence'] is False
    assert 'technical_output' not in str(material)
    assert len(json.dumps(data))//4000+1 == 5
    data['answer']='required source material '*1000
    assert dependency_context([result])[1]  # Do not silently omit genuine source material.


def test_summary_preserves_complete_units_and_full_source():
    source='Confirmed edit.\n\n' + ('Unfinished work remains. '*150)
    summary=bounded_report_summary(source)
    assert len(summary)<=2000 and '[Сокращено.' in summary
    assert summary.split('\n[Сокращено.')[0].endswith('.')
    assert len(source)>2000
    assert 'x'*100 not in bounded_report_summary('x'*5000)


def test_failed_finalization_keeps_latest_evidence_not_success():
    journal=[file_step(1,b'old',b'new')]
    result=unfinished_execution_result(journal,'terminal_answer_missing')
    assert result['commands'] is journal and result['task_receipt']['task_status']=='partial'
    assert digest(b'new') in result['answer']
    journal.append({'result':{'status':'unknown'}})
    assert unfinished_execution_result(journal,'terminal_answer_missing')['task_receipt']['task_status']=='unknown'
    failed=[{'tool_name':'execute_cmd','status':'failed','result':syntax_error()}]
    assert unfinished_execution_result(failed,'terminal_answer_missing')['task_receipt']['task_status']=='failed'


def test_worker_exact_edit_and_answer_without_shell(tmp_path):
    path=tmp_path/'scene.js'; path.write_bytes(b'const radius=0.42;')
    args={'path':str(path),'expected_text':'0.42','content':'0.21','expected_sha256':digest(path.read_bytes())}
    sent=[]
    async def send(device,action,params):
        sent.append(action)
        assert action=='write_content'
        return actions.write_content(**params)
    result=_run_case([_message(tool_calls=[_tool_call('edit','write_content',args)]),
        _message(tool_calls=[_answer_call('end','Radius is now 0.21.',answer_type='grounded_report',basis=['step_1'])])],
        send_command_fn=send,max_iterations=20)
    assert sent==['write_content'] and path.read_bytes()==b'const radius=0.21;'
    assert result['answer']=='Radius is now 0.21.'


def test_empty_replacement_is_a_supported_deletion(tmp_path):
    path=tmp_path/'scene.js'; path.write_bytes(b'keep; remove; keep;')
    args={'path':str(path),'expected_text':'remove; ','content':'','expected_sha256':digest(path.read_bytes())}
    async def send(device,action,params):
        return actions.write_content(**params)
    result=_run_case([_message(tool_calls=[_tool_call('edit','write_content',args)]),
        _message(tool_calls=[_answer_call('end','Fragment removed.',answer_type='grounded_report',basis=['step_1'])])],
        send_command_fn=send)
    assert path.read_bytes()==b'keep; keep;'
    assert result['commands'][0]['result']['verified']


def test_commit_failure_and_encoding_error_preserve_original(tmp_path,monkeypatch):
    path=tmp_path/'scene.js'; path.write_bytes(b'old')
    def fail(*args): raise OSError('simulated replace failure')
    monkeypatch.setattr(actions.os,'replace',fail)
    result=actions.write_content(str(path),'new',expected_text='old',expected_sha256=digest(b'old'))
    assert result['committed'] is False and path.read_bytes()==b'old'
    assert not list(tmp_path.glob('.iru-commit-*'))
    path.write_bytes(b'\xff old')
    result=actions.write_content(str(path),'new',expected_text='old',expected_sha256=digest(path.read_bytes()))
    assert result['committed'] is False and path.read_bytes()==b'\xff old'


def test_replacement_transport_keeps_path_policy_and_old_agent_guard(monkeypatch):
    import asyncio
    from server import task_runtime as runtime
    sent=[]
    class WS:
        async def send_text(self,message):
            payload=json.loads(message)['payload'];sent.append(payload)
            runtime.devices['1:pc']['pending'].pop(payload['id']).set_result({'status':'ok'})
    dev={'info':{'os':'Windows'},'pending':{},'ws':WS()}
    monkeypatch.setitem(runtime.devices,'1:pc',dev)
    monkeypatch.setattr(runtime,'get_device_profile',lambda *a,**kw:None)
    args={'path':'C:/Temp/file.txt','expected_text':'old','content':'new','expected_sha256':digest(b'old')}
    result=asyncio.run(runtime.send_command_to_agent('1:pc','write_content',args))
    assert result['dispatched'] is False and not sent
    dev['info']['file_replace_protocol']=1
    with pytest.raises(RuntimeError,match='BLOCKED'):
        asyncio.run(runtime.send_command_to_agent('1:pc','write_content',{**args,'path':'C:/Windows/file.txt'}))
    assert not sent
    asyncio.run(runtime.send_command_to_agent('1:pc','write_content',args))
    assert sent[0]['params']==args


def test_controller_keeps_written_result_when_terminal_repair_is_invalid(tmp_path):
    from server.worker_reports import build_worker_report
    path=tmp_path/'scene.js';path.write_bytes(b'0.42')
    args={'path':str(path),'expected_text':'0.42','content':'0.21','expected_sha256':digest(b'0.42')}
    sent=[]
    async def send(device,action,params):
        sent.append(action);return actions.write_content(**params)
    result=_run_case([_message(tool_calls=[_tool_call('edit','write_content',args)]),
                      _message('invalid raw final',finish_reason='stop')],
                     send_command_fn=send,max_iterations=1)
    assert sent==['write_content'] and path.read_bytes()==b'0.21'
    assert result['task_receipt']['task_status']=='partial'
    assert digest(b'0.21') in result['answer']
    report=build_worker_report({**result,'task_id':'local','status':'done','device_ids':['device-1']})
    assert report['status']=='partial' and not report['goal_completed']
    assert report['artifacts'][0]['sha256']==digest(b'0.21')
    assert digest(b'0.21') in report['summary']
