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


async def controller(responses,send,*,task_id='ff-controller',request='Прочитай сам и измени project.txt.',captured=None):
    queue = list(responses)
    async def completion(**kwargs):
        if captured is not None:captured.append({**kwargs,'messages':copy.deepcopy(kwargs['messages'])})
        assert queue,'Unexpected additional model call'
        return queue.pop(0)
    return await process_non_pipeline_command(user_message=request,device_id='pc',
        device_info={'os':'Windows','file_replace_protocol':2},send_command_fn=send,get_file_link_fn=lambda *a:'',
        chat_history=[],user_id=None,chat_id=None,modes={},poll_task_id=task_id,
        cfg={'model':'fake','answer_auditor_enabled':False},system_msg='system',machine_guid=None,mem_user_id=None,
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
    human = 'Прочитай сам и измени project.txt; отдельно создай lite.txt. Запомни: тестовый цвет зелёный.'
    objectives = ['В project.txt замени draft на ready.','Создай lite.txt с текстом lite.']
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
                call('read_file',{'path':PROJECT}),answer('project.txt изменён.')]
        else:
            responses = [call('write_content',{'path':LITE,'content':'lite'}),
                call('read_file',{'path':LITE}),answer('lite.txt создан.')]
        captured = []
        queue = list(responses)
        async def completion(**request):
            captured.append({**request,'messages':copy.deepcopy(request['messages'])})
            assert queue
            return queue.pop(0)
        result = await process_non_pipeline_command(**{name:kwargs[name] for name in (
            'user_message','device_id','device_info','send_command_fn','get_file_link_fn','chat_history',
            'user_id','chat_id','modes','poll_task_id','device_tool_fn')},cfg={'model':'fake','answer_auditor_enabled':False},
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
                    'human_quote':'Запомни: тестовый цвет зелёный.'}]},[
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
    ('app.launch',{'status':'launched_verified','pid':12,'window':{'handle':123,'pid':12,'title':'Folder'}}),
    ('app.open_url',{'status':'opened_verified','launched':True,'window_found':True,'window':{'pid':12,'title':'Browser'}}),
    ('app.open_url',{'status':'opened_verified','launched':True,'window_found':True,'window_title':'Browser'})])
@pytest.mark.parametrize('followup', ['none','window.find','window.control'])
def test_verified_open_without_completion_state_survives_followup_observation(tool,result,followup):
    steps = [{'step_id':'s1','tool_name':tool,'target_device_id':'pc','result':result}]
    if followup != 'none':
        steps.append({'step_id':'s2','tool_name':followup,'target_device_id':'pc',
            'result':{'status':'success','window':{'window_id':'a'*32,'title':'Folder'}},
            'window_request':{'action':'find'}})
    task = {'task_id':'open-ff','status':'done','device_ids':['pc'],'execution_scope':'window',
        'window_effects':['open'],'commands':steps,'answer':'Открытие подтверждено.'}
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
        'execution_scope':'window','window_effects':['open'],'commands':[
            {'step_id':'s1','tool_name':'app.launch','target_device_id':'pc','result':result}],
        'answer':'Process observation only.'})
    assert not report['goal_completed'] and report['status'] != 'success'


def test_verified_open_then_close_does_not_claim_the_window_remains_open():
    report = build_worker_report({'task_id':'closed','status':'done','device_ids':['pc'],
        'execution_scope':'window','window_effects':['open'],'commands':[
            {'step_id':'s1','tool_name':'app.launch','target_device_id':'pc',
             'result':{'status':'launched_verified','window':{'pid':12}}},
            {'step_id':'s2','tool_name':'window.control','target_device_id':'pc',
             'result':{'status':'success','action':'close','completion_state':'success'}}]})
    assert not report['goal_completed']


def test_open_then_minimize_sequence_accepts_legacy_launch_receipt(owner,monkeypatch):
    monkeypatch.setitem(tasks,'ff-controller',{'task_id':'ff-controller','execution_scope':'window',
        'window_effects':['open','minimize']})
    sent = []
    async def send(device,action,params):
        sent.append(action)
        if action == 'app.launch':return {'status':'launched_verified','window':{'handle':12,'pid':3,'title':'Folder'}}
        assert action == 'window.control' and params['action'] == 'minimize'
        return {'status':'success','action':'minimize','completion_state':'success','summary':'Свёрнуто'}
    result = asyncio.run(controller([call('app_launch',{'command':'explorer.exe C:/SyntheticFF01'}),
        call('window_control',{'action':'minimize','target':'current'})],send))
    assert sent == ['app.launch','window.control']
    report = build_worker_report({'task_id':'ff-controller','status':'done','device_ids':['pc'],
        'execution_scope':'window','window_effects':['open','minimize'],**result})
    assert report['goal_completed']


def test_source_missing_repeated_requests_still_allow_read_and_corrected_replacement(owner,monkeypatch):
    monkeypatch.setitem(tasks,'ff-controller',{'preserve_file_bytes':True})
    agent = FileAgent(b'status=draft\r\n')
    wrong = replacement(old='draft-v0')
    result = asyncio.run(controller([call('write_content',wrong,'bad1'),call('write_content',wrong,'bad2'),
        call('write_content',wrong,'bad3'),call('read_file',{'path':PROJECT}),
        call('write_content',replacement()),answer('Исправлено по прочитанному файлу.')],agent.send))
    assert [action for _,action,_ in agent.sent] == ['write_content','get_file_content','write_content']
    assert agent.files[PROJECT] == b'status=ready\r\n'
    assert not any(step.get('tool_name')=='answer.ask_clarification' for step in result['commands'])
    assert result['answer'] == 'Исправлено по прочитанному файлу.'


def test_missing_value_may_already_be_desired_without_another_write(owner):
    agent = FileAgent(b'status=ready\r\n')
    wrong = replacement(raw=agent.files[PROJECT])
    result = asyncio.run(controller([call('write_content',wrong),call('read_file',{'path':PROJECT}),
        answer('В файле уже ready; новую запись не выполнял.')],agent.send))
    assert [action for _,action,_ in agent.sent] == ['write_content','get_file_content']
    assert agent.files[PROJECT] == b'status=ready\r\n'
    assert 'новую запись не выполнял' in result['answer']


def test_real_unknown_blocks_replacement_retry_but_allows_observation(owner):
    sent = []
    async def send(device,action,params):
        sent.append(action)
        if action=='write_content':return {'status':'unknown','error':'confirmed_transport_outcome_unknown'}
        return {'status':'ok','path':PROJECT,'complete':True,'text':'status=ready\r\n','file_sha256':sha(b'status=ready\r\n')}
    result = asyncio.run(controller([call('write_content',replacement()),call('read_file',{'path':PROJECT}),
        call('write_content',replacement()),answer('Исход записи неизвестен; повтор не выполнен.')],send))
    assert sent == ['write_content','get_file_content']
    assert any(step['result'].get('error')=='mutation_blocked_after_unknown' for step in result['commands'])


def test_assignment_cannot_grant_memory_permission_absent_from_human_request(owner,monkeypatch):
    monkeypatch.setitem(tasks,'ff-controller',{'goal_id':'goal','proposed_objective':'Запомни факт о цвете.',
        'original_request':'Прочитай project.txt.','execution_scope':'general'})
    async def no_device(*args):pytest.fail('Memory rejection needs no device call')
    result = asyncio.run(controller([call('remember_fact',{'text':'цвет зелёный','category':'other'}),
        answer('Разрешение на сохранение факта отсутствует.')],no_device))
    assert result['commands'][0]['result']['error'] == 'memory_write_requires_explicit_user_intent'


def test_only_new_observation_of_the_same_file_releases_known_missing_rejection(owner):
    agent = FileAgent()
    agent.files[LITE] = b'lite'
    wrong = replacement(old='absent')
    result = asyncio.run(controller([call('write_content',wrong,'first'),
        call('read_file',{'path':LITE}),call('write_content',wrong,'still-same'),
        call('read_file',{'path':PROJECT}),call('write_content',wrong,'fresh-basis'),
        answer('В project.txt нужный исходный фрагмент отсутствует.')],agent.send,
        request='Прочитай project.txt и lite.txt; исправь project.txt.'))
    assert [action for _,action,_ in agent.sent] == ['write_content','get_file_content','get_file_content','write_content']
    assert agent.files[PROJECT] == b'status=draft\r\n'
    assert result['answer'] == 'В project.txt нужный исходный фрагмент отсутствует.'


def test_server_only_runtime_projects_assignment_without_rewriting_original(owner,monkeypatch):
    from server import task_runtime as runtime
    human = 'Проанализируй сведения; другой Worker создаст lite.txt.'
    objective = 'Проанализируй только сведения.'
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
        return {'answer':'Получены сведения.','commands':[]}
    monkeypatch.setattr(runtime,'process_onboarding_message',process)
    asyncio.run(runtime.run_onboarding_task('ff-onboarding',owner['id'],human,owner['chat_id']))
    assert seen == [objective]
    assert tasks['ff-onboarding']['original_request'] == tasks['ff-onboarding']['message'] == human


def test_restored_goal_without_handoff_keeps_human_scope_as_context_not_user_instruction(owner,monkeypatch):
    human = 'Измени project.txt и отдельно создай lite.txt.'
    objective = 'Измени только project.txt.'
    monkeypatch.setitem(tasks,'ff-controller',{'goal_id':'goal','worker_id':'worker-1',
        'message':human,'original_request':human,'proposed_objective':objective})
    captured = []
    async def no_device(*args):pytest.fail('This projection test needs no device action')
    asyncio.run(controller([answer('Контекст принят.')],no_device,request=human,captured=captured))
    messages = captured[0]['messages']
    assert messages[-1] == {'role':'user','content':objective}
    assert human in messages[0]['content']
    assert not any(message.get('role')=='user' and message.get('content')==human for message in messages)
    assert tasks['ff-controller']['message'] == tasks['ff-controller']['original_request'] == human
