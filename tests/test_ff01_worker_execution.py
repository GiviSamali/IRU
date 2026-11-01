"""FF-01: fake model/tools only. Historical LIVE IDs are not primary journals."""
import asyncio
import copy
import hashlib
import json

import pytest

from server import database as db
from server.controller_non_pipeline import process_non_pipeline_command
from server.controller_tools import NON_PIPELINE_TOOLS
from server.memory_intent_guard import ORIGINAL_WORKER_REQUEST, MEMORY_AUTHORIZATION, memory_permissions_from_human_request
from server.runtime_state import tasks, devices
from server.worker_reports import build_worker_report
from server.worker_scheduler import WorkerScheduler, init_worker_storage
from test_tool_only_protocol import _message, _tool_call


PROJECT = 'C:/SyntheticFF01/project.txt'
LITE = 'C:/SyntheticFF01/lite.txt'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def call(name,args,ident='call'):
    return _message(tool_calls=[_tool_call(ident,name,args)])


def answer(text):
    return call('answer_text',{'result':text,'unresolved':[]},'answer')


class FileAgent:
    def __init__(self,raw=b'status=draft\r\n'):
        self.files = {PROJECT:raw}
        self.sent = []

    async def send(self,device,action,params,**kwargs):
        self.sent.append((device,action,copy.deepcopy(params)))
        path = params.get('path')
        raw = self.files.get(path,b'')
        if action == 'get_file_content':
            return {'status':'ok','path':path,'text':raw.decode(),'file_sha256':sha(raw),'complete':True}
        assert action == 'write_content', action
        previous = sha(raw) if path in self.files else None
        old = params.get('expected_text')
        if old is not None:
            if params['expected_sha256'] != sha(raw):
                return {'status':'error','path':path,'error':'replacement_precondition_hash_mismatch','committed':False}
            if old.encode() not in raw:
                return {'status':'error','path':path,'error':'replacement_source_missing','committed':False}
            assert raw.count(old.encode()) == params.get('expected_matches',1)
            new = raw.replace(old.encode(),params['content'].encode())
        else:
            new = params['content'].encode()
        self.files[path] = new
        return {'status':'ok','path':path,'committed':True,'verified':True,'file_sha256':sha(new),
            'previous_sha256':previous,'total_size':len(new),'bytes_written':len(new),
            'mode':'replace' if old is not None else 'overwrite','verification_scope':'file_bytes_only'}


@pytest.fixture
def owner(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path / 'ff01.sqlite')
    db.init_db()
    init_worker_storage()
    user = db.create_user('ff01-owner')
    user['chat_id'] = db.create_chat(user['id'],'FF-01')['id']
    monkeypatch.setenv('IRU_FREE_FORM_REPORT','1')
    return user


async def controller(responses,send,*,task_id='ff-controller',request='РџСЂРѕС‡РёС‚Р°Р№ СЃР°Рј Рё РёР·РјРµРЅРё project.txt.',captured=None):
    queue = list(responses)
    async def completion(**kwargs):
        if captured is not None:captured.append({**kwargs,'messages':copy.deepcopy(kwargs['messages'])})
        assert queue,'Unexpected additional model call'
        return queue.pop(0)
    return await process_non_pipeline_command(user_message=request,device_id='pc',
        device_info={'os':'Windows','file_replace_protocol':2},send_command_fn=send,get_file_link_fn=lambda *a:'',
        chat_history=[],user_id=None,chat_id=None,modes={},poll_task_id=task_id,
        cfg={'model':'fake'},system_msg='system',machine_guid=None,mem_user_id=None,
        non_pipeline_tools=NON_PIPELINE_TOOLS,max_iterations=9,pick_model_fn=lambda *a:'fake',
        chat_completion_request_fn=completion)


def replacement(old='draft',new='ready',raw=b'status=draft\r\n'):
    return {'path':PROJECT,'content':new,'expected_text':old,'expected_sha256':sha(raw),'expected_matches':1}


def test_mixed_goal_actual_runtime_projects_only_assignment_preserves_authority_and_effects(owner,monkeypatch):
    from server import task_runtime as runtime
    from server.controller_shared import WORKER_MEMORY_QUERY
    agent = FileAgent()
    key = f"{owner['id']}:pc"
    devices[key] = {'user_id':owner['id'],'info':{'os':'Windows','file_replace_protocol':2},'ws':object(),'pending':{}}
    human = 'РџСЂРѕС‡РёС‚Р°Р№ СЃР°Рј Рё РёР·РјРµРЅРё project.txt; РѕС‚РґРµР»СЊРЅРѕ СЃРѕР·РґР°Р№ lite.txt. Р—Р°РїРѕРјРЅРё: С‚РµСЃС‚РѕРІС‹Р№ С†РІРµС‚ Р·РµР»С‘РЅС‹Р№.'
    objectives = ['Р’ project.txt Р·Р°РјРµРЅРё draft РЅР° ready.','РЎРѕР·РґР°Р№ lite.txt СЃ С‚РµРєСЃС‚РѕРј lite.']
    seen = []
    monkeypatch.setattr(runtime,'send_command_to_agent',agent.send)

    async def process(**kwargs):
        objective = kwargs['user_message']
        seen.append(objective)
        assert objective in objectives and objective != human
        assert ORIGINAL_WORKER_REQUEST.get() == human
        assert WORKER_MEMORY_QUERY.get() == objective
        assert MEMORY_AUTHORIZATION.get()['request_sha256'] == sha(human.encode())
        assert 'remember_fact' in memory_permissions_from_human_request(objective)
        task_id = kwargs['poll_task_id']
        task = tasks[task_id]
        assert task['message'] == task['original_request'] == human
        assert task['worker_context'][-1] == {'role':'user','content':objective}
        handoff = task['worker_context'][0]['content']
        assert human in handoff and 'not a competing task list' in handoff
        assert 'follow the original request;' not in handoff
        if objective == objectives[0]:
            responses = [call('read_file',{'path':PROJECT}),call('write_content',replacement()),
                call('read_file',{'path':PROJECT}),answer('project.txt РёР·РјРµРЅС‘РЅ.')]
        else:
            responses = [call('write_content',{'path':LITE,'content':'lite'}),
                call('read_file',{'path':LITE}),answer('lite.txt СЃРѕР·РґР°РЅ.')]
        captured = []
        queue = list(responses)
        async def completion(**request):
            captured.append({**request,'messages':copy.deepcopy(request['messages'])})
            assert queue
            return queue.pop(0)
        result = await process_non_pipeline_command(**{name:kwargs[name] for name in (
            'user_message','device_id','device_info','send_command_fn','get_file_link_fn','chat_history',
            'user_id','chat_id','modes','poll_task_id','device_tool_fn')},cfg={'model':'fake'},
            system_msg='system',machine_guid=None,mem_user_id=None,non_pipeline_tools=NON_PIPELINE_TOOLS,
            max_iterations=6,pick_model_fn=lambda *a:'fake',chat_completion_request_fn=completion)
        assert captured[0]['messages'][-1] == {'role':'user','content':objective}
        assert not any(m.get('role')=='user' and m.get('content')==human for m in captured[0]['messages'])
        return result

    monkeypatch.setattr(runtime,'process_nl_command',process)
    async def execute(task):
        await runtime.run_nl_task(task['task_id'],task['user_id'],task['message'],task['device_ids'],task['chat_id'])
    async def scenario():
        scheduler = WorkerScheduler(execute)
        try:
            admitted = await scheduler.submit_goal({'user_id':owner['id'],'chat_id':owner['chat_id'],
                'message':human,'original_request':human,'context_history':[{'role':'user','content':human}],
                'device_ids':[key],'modes':{},'memory_authorizations':[{'operation':'remember_fact',
                    'human_quote':'Р—Р°РїРѕРјРЅРё: С‚РµСЃС‚РѕРІС‹Р№ С†РІРµС‚ Р·РµР»С‘РЅС‹Р№.'}]},[
                    {'key':'project','objective':objectives[0],'preserve_file_bytes':True,'artifacts':[PROJECT]},
                    {'key':'lite','objective':objectives[1],'artifacts':[LITE]}],request_key='mixed-ff')
            await asyncio.wait_for(scheduler.runners[owner['id']],timeout=10)
            assert seen == objectives
            for task in admitted['tasks']:
                assert task['message'] == task['original_request'] == human
                assert task['worker_report']['status'] == 'success', task.get('answer')
            with db.get_db() as connection:
                assert connection.execute("SELECT count(*) FROM messages WHERE role='assistant' AND chat_id=?",
                    (owner['chat_id'],)).fetchone()[0] == 0  # Workers never publish their own bubbles.
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())
    assert agent.files[PROJECT] == b'status=ready\r\n' and agent.files[LITE] == b'lite'
    assert [(action,params['path']) for _,action,params in agent.sent] == [
        ('get_file_content',PROJECT),('write_content',PROJECT),('get_file_content',PROJECT),
        ('write_content',LITE),('get_file_content',LITE)]


@pytest.mark.parametrize('tool,result', [
    ('app.launch',{'status':'opened_verified','pid':12,'window':{'handle':123,'pid':12,'title':'Folder'},
                   'requested_object':{'type':'folder','path':'C:/SyntheticFF01'},
                   'object_open_verified':True,
                   'observed_object':{'observed_path':'C:/SyntheticFF01','kind':'folder',
                                      'title':'Folder','process':'explorer.exe',
                                      'class_name':'CabinetWClass','hwnd':'66052','matched_name':'SyntheticFF01'}}),
    ('app.open_url',{'status':'opened_verified','launched':True,'window_found':True,'window':{'pid':12,'title':'Browser'}}),
    ('app.open_url',{'status':'opened_verified','launched':True,'window_found':True,'window_title':'Browser'})])
@pytest.mark.parametrize('followup', ['none','window.find','window.control'])
def test_verified_open_without_completion_state_survives_followup_observation(tool,result,followup):
    steps = [{'step_id':'s1','tool_name':tool,'target_device_id':'pc','result':result}]
    if followup != 'none':
        steps.append({'step_id':'s2','tool_name':followup,'target_device_id':'pc',
            'result':{'status':'success','window':{'window_id':'a'*32,'title':'Folder'}},
            'window_request':{'action':'find'}})
    steps.append({'tool_name':'answer.text','status':'terminal','result':{
        '_result_contract':3,'text':'Requested object opened','unresolved':[]}})
    task = {'task_id':'open-ff','status':'done','device_ids':['pc'],
        'window_effects':['open'],'commands':steps,'answer':'РћС‚РєСЂС‹С‚РёРµ РїРѕРґС‚РІРµСЂР¶РґРµРЅРѕ.',
        'open_objects':[{'type':'folder','path':'C:/SyntheticFF01','device_id':'pc'} if tool=='app.launch'
                        else {'type':'url','path':'https://ya.ru','device_id':'pc'}]}
    report = build_worker_report(task)
    assert report['status'] == 'success' and report['goal_completed'] is True


@pytest.mark.parametrize('result', [
    {'status':'launched','pid':12,'process_alive':True,'window':None},
    {'status':'ok','returncode':0,'window':None},
    {'status':'launched_verified','window':None},
    {'status':'ambiguous','window':{'pid':12},'error':'ambiguous_window'},
    {'status':'failed','error':'file_not_found','window':None},
    {'status':'unknown','completion_state':'unknown','window':{'pid':12}},
])
def test_process_only_error_ambiguous_or_unknown_never_prove_window_open(result):
    report = build_worker_report({'task_id':'negative-open','status':'done','device_ids':['pc'],
        'window_effects':['open'],'commands':[
            {'step_id':'s1','tool_name':'app.launch','target_device_id':'pc','result':result}],
        'answer':'Process observation only.'})
    assert not report['goal_completed'] and report['status'] != 'success'


def test_verified_open_then_close_does_not_claim_the_window_remains_open():
    report = build_worker_report({'task_id':'closed','status':'done','device_ids':['pc'],
        'window_effects':['open'],'commands':[
            {'step_id':'s1','tool_name':'app.launch','target_device_id':'pc',
             'result':{'status':'launched_verified','window':{'pid':12}}},
            {'step_id':'s2','tool_name':'window.control','target_device_id':'pc',
             'result':{'status':'success','action':'close','completion_state':'success'}}]})
    assert not report['goal_completed']


def test_open_then_minimize_sequence_accepts_object_verified_launch(owner,monkeypatch):
    monkeypatch.setitem(tasks,'ff-controller',{'task_id':'ff-controller',
        'window_effects':['open','minimize']})
    sent = []
    async def send(device,action,params):
        sent.append(action)
        if action == 'app.launch':return {'status':'opened_verified','window':{'handle':12,'pid':3,'title':'Folder'},
            'requested_object':{'type':'folder','path':'C:/SyntheticFF01'},
            'object_open_verified':True,
            'observed_object':{'observed_path':'C:/SyntheticFF01','kind':'folder','title':'Folder',
                               'process':'explorer.exe','class_name':'CabinetWClass',
                               'hwnd':'66052','matched_name':'SyntheticFF01'}}
        assert action == 'window.control' and params['action'] == 'minimize'
        return {'status':'success','action':'minimize','completion_state':'success','summary':'Свёрнуто'}
    result = asyncio.run(controller([call('app_launch',{'command':'explorer.exe C:/SyntheticFF01'}),
        call('window_control',{'action':'minimize','target':'current'}),answer('Opened and minimized')],send))
    assert sent == ['app.launch','window.control']
    report = build_worker_report({'task_id':'ff-controller','status':'done','device_ids':['pc'],
        'window_effects':['open','minimize'],
        'open_objects':[{'type':'folder','path':'C:/SyntheticFF01','device_id':'pc'}],**result})
    assert report['goal_completed']


def test_source_missing_repeated_requests_still_allow_read_and_corrected_replacement(owner,monkeypatch):
    monkeypatch.setitem(tasks,'ff-controller',{'preserve_file_bytes':True})
    agent = FileAgent(b'status=draft\r\n')
    wrong = replacement(old='draft-v0')
    result = asyncio.run(controller([call('write_content',wrong,'bad1'),call('write_content',wrong,'bad2'),
        call('write_content',wrong,'bad3'),call('read_file',{'path':PROJECT}),
        call('write_content',replacement()),answer('РСЃРїСЂР°РІР»РµРЅРѕ РїРѕ РїСЂРѕС‡РёС‚Р°РЅРЅРѕРјСѓ С„Р°Р№Р»Сѓ.')],agent.send))
    assert [action for _,action,_ in agent.sent] == ['write_content','get_file_content','write_content']
    assert agent.files[PROJECT] == b'status=ready\r\n'
    assert not any(step.get('tool_name')=='answer.ask_clarification' for step in result['commands'])
    assert result['answer'] == 'РСЃРїСЂР°РІР»РµРЅРѕ РїРѕ РїСЂРѕС‡РёС‚Р°РЅРЅРѕРјСѓ С„Р°Р№Р»Сѓ.'


def test_missing_value_may_already_be_desired_without_another_write(owner):
    agent = FileAgent(b'status=ready\r\n')
    wrong = replacement(raw=agent.files[PROJECT])
    result = asyncio.run(controller([call('write_content',wrong),call('read_file',{'path':PROJECT}),
        answer('Р’ С„Р°Р№Р»Рµ СѓР¶Рµ ready; РЅРѕРІСѓСЋ Р·Р°РїРёСЃСЊ РЅРµ РІС‹РїРѕР»РЅСЏР».')],agent.send))
    assert [action for _,action,_ in agent.sent] == ['write_content','get_file_content']
    assert agent.files[PROJECT] == b'status=ready\r\n'
    assert 'РЅРѕРІСѓСЋ Р·Р°РїРёСЃСЊ РЅРµ РІС‹РїРѕР»РЅСЏР»' in result['answer']


def test_real_unknown_blocks_replacement_retry_but_allows_observation(owner):
    sent = []
    async def send(device,action,params):
        sent.append(action)
        if action=='write_content':return {'status':'unknown','error':'confirmed_transport_outcome_unknown'}
        return {'status':'ok','path':PROJECT,'complete':True,'text':'status=ready\r\n','file_sha256':sha(b'status=ready\r\n')}
    result = asyncio.run(controller([call('write_content',replacement()),call('read_file',{'path':PROJECT}),
        call('write_content',replacement()),answer('РСЃС…РѕРґ Р·Р°РїРёСЃРё РЅРµРёР·РІРµСЃС‚РµРЅ; РїРѕРІС‚РѕСЂ РЅРµ РІС‹РїРѕР»РЅРµРЅ.')],send))
    assert sent == ['write_content','get_file_content']
    assert any(step['result'].get('error')=='mutation_blocked_after_unknown' for step in result['commands'])


def test_assignment_cannot_grant_memory_permission_absent_from_human_request(owner,monkeypatch):
    monkeypatch.setitem(tasks,'ff-controller',{'goal_id':'goal','proposed_objective':'Р—Р°РїРѕРјРЅРё С„Р°РєС‚ Рѕ С†РІРµС‚Рµ.',
        'original_request':'РџСЂРѕС‡РёС‚Р°Р№ project.txt.'})
    async def no_device(*args):pytest.fail('Memory rejection needs no device call')
    result = asyncio.run(controller([call('remember_fact',{'text':'С†РІРµС‚ Р·РµР»С‘РЅС‹Р№','category':'other'}),
        answer('Р Р°Р·СЂРµС€РµРЅРёРµ РЅР° СЃРѕС…СЂР°РЅРµРЅРёРµ С„Р°РєС‚Р° РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚.')],no_device))
    assert result['commands'][0]['result']['error'] == 'memory_write_requires_explicit_user_intent'


def test_only_new_observation_of_the_same_file_releases_known_missing_rejection(owner):
    agent = FileAgent()
    agent.files[LITE] = b'lite'
    wrong = replacement(old='absent')
    result = asyncio.run(controller([call('write_content',wrong,'first'),
        call('read_file',{'path':LITE}),call('write_content',wrong,'still-same'),
        call('read_file',{'path':PROJECT}),call('write_content',wrong,'fresh-basis'),
        answer('Р’ project.txt РЅСѓР¶РЅС‹Р№ РёСЃС…РѕРґРЅС‹Р№ С„СЂР°РіРјРµРЅС‚ РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚.')],agent.send,
        request='РџСЂРѕС‡РёС‚Р°Р№ project.txt Рё lite.txt; РёСЃРїСЂР°РІСЊ project.txt.'))
    assert [action for _,action,_ in agent.sent] == ['write_content','get_file_content','get_file_content','write_content']
    assert agent.files[PROJECT] == b'status=draft\r\n'
    assert result['answer'] == 'Р’ project.txt РЅСѓР¶РЅС‹Р№ РёСЃС…РѕРґРЅС‹Р№ С„СЂР°РіРјРµРЅС‚ РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚.'


def test_server_only_runtime_projects_assignment_without_rewriting_original(owner,monkeypatch):
    from server import task_runtime as runtime
    human = 'РџСЂРѕР°РЅР°Р»РёР·РёСЂСѓР№ СЃРІРµРґРµРЅРёСЏ; РґСЂСѓРіРѕР№ Worker СЃРѕР·РґР°СЃС‚ lite.txt.'
    objective = 'РџСЂРѕР°РЅР°Р»РёР·РёСЂСѓР№ С‚РѕР»СЊРєРѕ СЃРІРµРґРµРЅРёСЏ.'
    context = [{'role':'assistant','content':json.dumps({'human_authority_request':human},ensure_ascii=False)},
        {'role':'user','content':objective}]
    monkeypatch.setitem(tasks,'ff-onboarding',{'task_id':'ff-onboarding','worker_id':'worker-1','goal_id':'goal',
        'message':human,'original_request':human,'proposed_objective':objective,'worker_context':context,
        'orchestrated':True,'user_id':owner['id'],'chat_id':owner['chat_id'],'status':'running'})
    seen = []
    async def process(**kwargs):
        seen.append(kwargs['user_message'])
        assert kwargs['chat_history'][-1] == {'role':'user','content':objective}
        assert human in kwargs['chat_history'][0]['content']
        return {'answer':'РџРѕР»СѓС‡РµРЅС‹ СЃРІРµРґРµРЅРёСЏ.','commands':[]}
    monkeypatch.setattr(runtime,'process_onboarding_message',process)
    asyncio.run(runtime.run_onboarding_task('ff-onboarding',owner['id'],human,owner['chat_id']))
    assert seen == [objective]
    assert tasks['ff-onboarding']['original_request'] == tasks['ff-onboarding']['message'] == human


def test_restored_goal_without_handoff_keeps_human_scope_as_context_not_user_instruction(owner,monkeypatch):
    human = 'РР·РјРµРЅРё project.txt Рё РѕС‚РґРµР»СЊРЅРѕ СЃРѕР·РґР°Р№ lite.txt.'
    objective = 'РР·РјРµРЅРё С‚РѕР»СЊРєРѕ project.txt.'
    monkeypatch.setitem(tasks,'ff-controller',{'goal_id':'goal','worker_id':'worker-1',
        'message':human,'original_request':human,'proposed_objective':objective})
    captured = []
    async def no_device(*args):pytest.fail('This projection test needs no device action')
    asyncio.run(controller([answer('РљРѕРЅС‚РµРєСЃС‚ РїСЂРёРЅСЏС‚.')],no_device,request=human,captured=captured))
    messages = captured[0]['messages']
    assert messages[-1] == {'role':'user','content':objective}
    assert human in messages[0]['content']
    assert not any(message.get('role')=='user' and message.get('content')==human for message in messages)
    assert tasks['ff-controller']['message'] == tasks['ff-controller']['original_request'] == human
