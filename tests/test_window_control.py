import asyncio
import copy
import inspect
import json
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from core import window_control as wc
from server.window_policy import ordinary_window_request, silent_window_success


def window(handle=101, pid=10, title='Document — Word', process='WINWORD.EXE'):
    return {'handle': handle, 'pid': pid, 'title': title, 'process_name': process, 'class_name': 'App',
            'minimized': False, 'maximized': False, 'foreground': handle == 101,
            'bounds': {'left': 20, 'top': 30, 'right': 820, 'bottom': 630}}

class FakeAdapter:
    def __init__(self, rows=None):
        self.rows = copy.deepcopy(rows if rows is not None else [window()]); self.calls = []; self.current = 101
        self.screens = [
            {'handle':1, 'monitor':1, 'primary':True, 'work_area': {'left':0, 'top':0, 'right':1365, 'bottom':728}},
            {'handle':2, 'monitor':2, 'primary':False, 'work_area': {'left':-1600, 'top':-200, 'right':0, 'bottom':700}}]
        self.close_pending = False; self.verify = True
    def windows(self): return copy.deepcopy(self.rows)
    def foreground(self): return self.current
    def record(self, handle): return next((copy.deepcopy(r) for r in self.rows if r['handle'] == handle), None)
    def monitors(self): return copy.deepcopy(self.screens)
    def monitor_for(self, handle): return 1
    def perform(self, handle, action, rect=None):
        self.calls.append((handle, action, rect))
        if not self.verify: return False
        row = next(r for r in self.rows if r['handle'] == handle)
        if action == 'close':
            if not self.close_pending: self.rows.remove(row)
        elif action == 'activate': self.current = handle
        elif action in {'minimize','maximize','restore'}:
            row['minimized'] = action == 'minimize'; row['maximized'] = action == 'maximize'
        else: row['bounds'] = rect; row['minimized'] = row['maximized'] = False
        return True


def test_active_and_user_window_list_hide_native_handles():
    control = wc.WindowControl(FakeAdapter())
    assert control.run('active')['window']['title'] == 'Document — Word'
    result = control.run('list')['windows'][0]
    assert len(result['window_id']) == 32 and 'handle' not in result
    assert control.run('list')['windows'][0]['window_id'] == result['window_id']

@pytest.mark.parametrize('target', ['Word', 'Document', 'WINWORD.EXE'])
def test_unique_target(target):
    c = wc.WindowControl(FakeAdapter())
    assert c.run('minimize', target=target)['status'] == 'success'


def test_ambiguous_is_never_randomly_selected():
    adapter = FakeAdapter([window(), window(202,20,'Other — Word')]); c = wc.WindowControl(adapter)
    result = c.run('minimize', target='Word')
    assert result['status'] == 'ambiguous' and len(result['candidates']) == 2 and not adapter.calls
    chosen = result['candidates'][1]['window_id']
    assert c.run('minimize', window_id=chosen)['window']['pid'] == 20


def test_missing_and_invented_id_do_not_fallback():
    adapter = FakeAdapter(); c = wc.WindowControl(adapter)
    assert c.run('minimize', target='Excel')['error'] == 'window_not_found'
    assert c.run('minimize', window_id='0'*32)['error'] == 'window_not_found'
    assert not adapter.calls


def test_pid_title_and_last_target():
    c = wc.WindowControl(FakeAdapter([window(),window(202,20,'Other — Word')]))
    assert c.run('minimize', target='Word', pid=20)['window']['pid'] == 20
    assert c.run('restore', target='last')['window']['pid'] == 20
    assert c.run('find', target='Word', title='Document')['window']['pid'] == 10

@pytest.mark.parametrize('action', ['minimize','maximize','restore','activate'])
def test_state_verified_and_silent(action):
    result = wc.WindowControl(FakeAdapter()).run(action)
    assert result['status'] == 'success' and result['response_policy'] == 'silent_on_success'

@pytest.mark.parametrize('action, expected', [
    ('left', {'left':0,'top':0,'right':682,'bottom':728}),
    ('right', {'left':682,'top':0,'right':1365,'bottom':728})])
def test_snap_uses_work_area_without_resolution_assumptions(action, expected):
    assert wc.WindowControl(FakeAdapter()).run(action)['window']['bounds'] == expected


def test_negative_monitor_coordinates_and_move():
    c = wc.WindowControl(FakeAdapter())
    assert c.run('left',monitor=2)['window']['bounds'] == {'left':-1600,'top':-200,'right':-800,'bottom':700}
    assert c.run('move_monitor',monitor=1)['window']['bounds']['left'] == 0
    assert c.run('move',x=-1200,y=-100)['window']['bounds']['top'] == -100
    assert c.run('resize',width=350,height=250)['window']['bounds'] == {'left':-1200,'top':-100,'right':-850,'bottom':150}
    assert c.run('move_monitor',monitor=99)['error'] == 'monitor_not_found'
    assert 'handle' not in c.run('monitors')['monitors'][0]


def test_close_native_request_and_save_dialog(monkeypatch):
    adapter = FakeAdapter(); c = wc.WindowControl(adapter)
    assert c.run('close')['status'] == 'success'
    assert adapter.calls == [(101,'close',None)]
    adapter = FakeAdapter(); adapter.close_pending = True; c = wc.WindowControl(adapter)
    clock = iter([0,1]); monkeypatch.setattr(wc.time,'monotonic',lambda: next(clock))
    result = c.run('close')
    assert result['status'] == 'pending' and 'response_policy' not in result and adapter.rows
    native = inspect.getsource(wc.WindowsAdapter.perform)
    assert 'PostMessageW' in native and '0x0010' in native
    assert 'TerminateProcess' not in native


def test_reused_handle_different_pid_rejected():
    adapter = FakeAdapter(); c = wc.WindowControl(adapter)
    identifier = c.run('list')['windows'][0]['window_id']; adapter.rows[0]['pid'] = 99
    assert c.run('minimize',window_id=identifier)['error'] == 'window_not_found'
    assert not adapter.calls

@pytest.mark.parametrize('args', [{'action':'arbitrary_api'}, {'action':'resize','width':-1,'height':2},
    {'action':'move','x':True,'y':0}, {'action':'minimize','target':''}, {'action':'move_monitor'}, {'action':'move'}])
def test_invalid_parameters(args):
    adapter = FakeAdapter(); assert wc.WindowControl(adapter).run(**args)['status'] == 'failed'; assert not adapter.calls


def test_no_synthetic_input_or_shell_execution_and_pointer_sized_bindings():
    source = Path(wc.__file__).read_text(encoding='utf-8')
    for forbidden in ['SendKeys','SendInput','keybd_event','mouse_event','pyautogui','subprocess','os.system','eval(', 'exec(', 'TerminateProcess']:
        assert forbidden not in source
    assert "W.HWND" in source and 'argtypes' in source and 'SetThreadDpiAwarenessContext' in source

@pytest.mark.parametrize('message', ['Сверни это окно','Сверни браузер','Разверни Word','Word вправо',
    'Поставь браузер на левую половину экрана','Перенеси это окно на второй монитор',
    'Верни окно в обычный размер','Покажи Excel','Закрой блокнот','Какие окна сейчас открыты?',
    'Какое окно сейчас активно?','Поставь браузер слева, а VS Code справа','Сверни браузер на Second','На первом ПК Word вправо'])
def test_window_commands_are_ordinary_without_classifier_llm(message, monkeypatch):
    from server import controller
    monkeypatch.setattr(controller,'load_llm_config',lambda: pytest.fail('classification LLM not needed'))
    assert ordinary_window_request(message)
    assert asyncio.run(controller.classify_task_complexity(message)) == ('SIMPLE','')

@pytest.mark.parametrize('message', ['Создай Word и сверни браузер','Сверни браузер и скачай файл','Разработай план управления окнами'])
def test_mixed_tasks_do_not_bypass_classification(message):
    assert not ordinary_window_request(message)


def test_response_policy_keeps_information_and_errors_audible():
    command = {'tool_name':'window.control','result':{'status':'success','response_policy':'silent_on_success'}}
    task = {'status':'done','commands':[command,{'tool_name':'answer.text','tool_type':'answer'}]}
    assert silent_window_success(task)
    for result in [{'status':'failed','response_policy':'silent_on_success'}, {'status':'pending'}, {'status':'success','windows':[]}]:
        assert not silent_window_success({**task,'commands':[{'tool_name':'window.control','result':result}]})
    assert not silent_window_success({**task,'commands':[command,{'tool_name':'execute_cmd','result':{'returncode':0}}]})


def tool_call(name, args, identifier='window'):
    return {'choices':[{'message':{'tool_calls':[{'id':identifier,'function':{'name':name,'arguments':json.dumps(args)}}]}}]}


def test_two_window_actions_on_same_device_without_plan(monkeypatch):
    from server import database
    from test_controller_trust import _run_non_pipeline_case
    monkeypatch.setattr(database,'get_device_profile',lambda *a,**kw: None)
    adapter = FakeAdapter([window(),window(202,20,'VS Code','Code.exe')]); control = wc.WindowControl(adapter); sent=[]
    async def send(device,action,args):
        sent.append((device,action,args)); return control.run(**args)
    result = _run_non_pipeline_case([
        tool_call('window_control',{'action':'left','target':'Word'},'one'),
        tool_call('window_control',{'action':'right','target':'VS Code'},'two'),
        tool_call('answer_text',{'answer_type':'grounded_report','text':'Word слева, VS Code справа.','basis':['step_1','step_2'],
            'self_check':{'depends_on_current_external_state':True,'claims_completed_action':True,'has_sufficient_evidence':True,'missing_evidence_question':''}},'answer')],send_command_fn=send)
    assert len(sent) == 2 and all(s[1] == 'window.control' for s in sent)
    assert result['tasks'] == [] and 'Word слева' in result['answer']
    assert silent_window_success({'status':'done','commands':result['commands']})

@pytest.mark.parametrize('device', ['Second','unknown','offline'])
def test_explicit_device_routing_without_fallback(monkeypatch,device):
    from server import database
    from test_controller_trust import _run_non_pipeline_case
    monkeypatch.setattr(database,'get_device_profile',lambda *a,**kw: None)
    sent=[]
    async def send(target,action,args):
        sent.append(target)
        if target != 'Second': raise RuntimeError('target_device_not_found_or_offline')
        return wc.WindowControl(FakeAdapter()).run(**args)
    result = _run_non_pipeline_case([tool_call('window_control',{'action':'minimize','target':'Word','device_id':device}),
        tool_call('answer_text',{'answer_type':'pure_text','text':'Результат','basis':[],
            'self_check':{'depends_on_current_external_state':False,'claims_completed_action':False,'has_sufficient_evidence':True,'missing_evidence_question':''}},'answer')],send_command_fn=send)
    assert sent == [device]
    if device != 'Second': assert result['commands'][0]['result']['error']


def test_unknown_parameters_rejected_before_dispatch():
    from server.controller_tools import NON_PIPELINE_TOOLS, WORKER_TOOLS
    from server.tool_arg_validation import validate_and_sanitize_tool_args
    for tools in (NON_PIPELINE_TOOLS,WORKER_TOOLS):
        schema = next(t for t in tools if t['function']['name'] == 'window_control')
        assert validate_and_sanitize_tool_args('window_control',{'action':'minimize','code':'arbitrary'},schema)[2]['error'] == 'unknown_tool_arguments'


def test_voice_endpoint_does_not_call_tts_for_success(client,monkeypatch):
    from test_voice import setup_task
    from server.runtime_state import tasks
    from server import voice
    headers = setup_task()
    tasks['voice-task']['commands'] = [{'tool_name':'window.control','result':{'status':'success','response_policy':'silent_on_success'}}]
    monkeypatch.setattr(voice,'speech_configured',lambda: pytest.fail('silent task must not use TTS'))
    assert client.post('/api/voice/tasks/voice-task/speech',headers=headers).status_code == 204


def test_transport_enforces_ownership_even_with_same_device_id():
    from server.task_runtime import send_command_to_agent
    from server.runtime_state import devices
    sent=[]
    class Socket:
        def __init__(self,key): self.key=key
        async def send_text(self,text):
            sent.append(self.key); payload=json.loads(text)['payload']
            devices[self.key]['pending'].pop(payload['id']).set_result({'status':'success'})
    for owner in (1,2):
        key=f'{owner}:Second'; devices[key]={'user_id':owner,'pending':{},'ws':Socket(key)}
    asyncio.run(send_command_to_agent('1:Second','window.control',{'action':'minimize'},user_id=1))
    with pytest.raises(RuntimeError,match='target_device_not_found'):
        asyncio.run(send_command_to_agent('2:Second','window.control',{'action':'minimize'},user_id=1))
    with pytest.raises(RuntimeError,match='target_device_not_found'):
        asyncio.run(send_command_to_agent('1:offline','window.control',{'action':'minimize'},user_id=1))
    assert sent == ['1:Second']


def test_two_actions_can_finish_from_evidence_without_answer_tool(monkeypatch):
    from server import database
    from test_controller_trust import _run_non_pipeline_case
    monkeypatch.setattr(database,'get_device_profile',lambda *a,**kw: None)
    control=wc.WindowControl(FakeAdapter([window(),window(202,20,'VS Code','Code.exe')]))
    async def send(device,action,args): return control.run(**args)
    import test_controller_trust
    def raw_completion(responses):
        queue=list(responses)
        async def request(**kwargs):
            assert queue, 'unexpected extra LLM call'
            return queue.pop(0)
        return request
    monkeypatch.setattr(test_controller_trust,'_make_completion_fn',raw_completion)
    result=_run_non_pipeline_case([tool_call('window_control' ,{'action':'left','target':'Word'},'one'),
        tool_call('window_control',{'action':'right','target':'VS Code'},'two'),
        {'choices':[{'message':{'content':''}}]}],send_command_fn=send)
    assert 'слева' in result['answer'] and 'справа' in result['answer']
    assert result['commands'][-1]['tool_name'] == 'answer.text'


@pytest.mark.parametrize('message, actions', [('Сверни Word',['minimize']),
    ('Поставь браузер слева, а VS Code справа',['left','right']), ('Сверни браузер на Second',['minimize']),
    ('Перенеси Word на второй монитор',['move_monitor'])])
def test_fast_path_has_one_llm_call_per_mutation_and_no_shell(monkeypatch,message,actions):
    from server import database
    from server.controller_non_pipeline import process_non_pipeline_command
    from server.window_policy import window_action_sequence
    monkeypatch.setattr(database,'get_device_profile',lambda *a,**kw: None)
    assert window_action_sequence(message) == actions
    requests=[]; sent=[]
    async def completion(**kw):
        requests.append(kw)
        names={t['function']['name'] for t in kw['tools']}
        assert 'window_control' in names and 'execute_cmd' not in names and 'create_plan' not in names
        assert len(requests) <= len(actions)
        return tool_call('window_control',{'action':actions[len(requests)-1], 'target':'Word'},str(len(requests)))
    async def send(device,action,args):
        sent.append(action); return wc.WindowControl(FakeAdapter()).run(**args,monitor=2) if args['action']=='move_monitor' else wc.WindowControl(FakeAdapter()).run(**args)
    result=asyncio.run(process_non_pipeline_command(user_message=message,device_id='device-1',device_info={'os':'Windows'},
        send_command_fn=send,get_file_link_fn=lambda *a:'',chat_history=[],user_id=None,chat_id=None,modes={},poll_task_id=None,
        cfg={'model':'mock'},system_msg='system',machine_guid=None,mem_user_id=None,non_pipeline_tools=[],max_iterations=12,
        pick_model_fn=lambda *a:'mock',chat_completion_request_fn=completion))
    assert len(requests)==len(sent)==len(actions)
    assert result['commands'][-1]['tool_name']=='answer.text' and result['tasks']==[]


def test_window_only_rejects_shell_before_agent(monkeypatch):
    from server import database
    from server.controller_non_pipeline import process_non_pipeline_command
    monkeypatch.setattr(database,'get_device_profile',lambda *a,**kw: None)
    responses=iter([tool_call('execute_cmd',{'command':'forbidden'},'shell'),tool_call('window_control',{'action':'minimize','target':'Word'})])
    sent=[]
    async def completion(**kw): return next(responses)
    async def send(device,action,args):
        sent.append(action); return wc.WindowControl(FakeAdapter()).run(**args)
    result=asyncio.run(process_non_pipeline_command(user_message='Сверни Word',device_id='device-1',device_info={'os':'Windows'},
        send_command_fn=send,get_file_link_fn=lambda *a:'',chat_history=[],user_id=None,chat_id=None,modes={},poll_task_id=None,
        cfg={'model':'mock'},system_msg='system',machine_guid=None,mem_user_id=None,non_pipeline_tools=[],max_iterations=12,
        pick_model_fn=lambda *a:'mock',chat_completion_request_fn=completion))
    assert sent == ['window.control']
    assert result['commands'][0]['result']['error'] == 'window_capability_required'


@pytest.mark.parametrize('pending',[False,True])
def test_controller_stops_for_ambiguity_or_save_dialog_without_retry(monkeypatch,pending):
    from server import database
    from test_controller_trust import _run_non_pipeline_case
    monkeypatch.setattr(database,'get_device_profile',lambda *a,**kw: None)
    adapter=FakeAdapter([window()] if pending else [window(),window(202,20,'Other — Word')])
    adapter.close_pending=pending
    control=wc.WindowControl(adapter)
    async def send(device,action,args): return control.run(**args)
    result=_run_non_pipeline_case([tool_call('window_control',{'action':'close' if pending else 'minimize','target':'Word'})],send_command_fn=send)
    assert 'сохранить' in result['answer'] if pending else 'Какое выбрать?' in result['answer']
    assert len(adapter.calls)==(1 if pending else 0)
    assert not silent_window_success({'status':'done','commands':result['commands']})


@pytest.mark.parametrize('message', ['Сверни', 'На весь экран', 'открой gpt', 'отлично А теперь проводник на второй виртуальный рабочий стол'])
def test_reported_voice_phrases_skip_classification_llm(monkeypatch,message):
    from server import controller
    monkeypatch.setattr(controller,'load_llm_config',lambda: pytest.fail('No classifier LLM for this window request'))
    assert asyncio.run(controller.classify_task_complexity(message)) == ('SIMPLE','')


def run_actual_window_request(monkeypatch,message,send,completion=None,history=None,modes=None):
    from server import database
    from server.controller_non_pipeline import process_non_pipeline_command
    monkeypatch.setattr(database,'get_device_profile',lambda *a,**kw: None)
    async def no_llm(**kw): pytest.fail('Direct window command must not call LLM')
    return asyncio.run(process_non_pipeline_command(user_message=message,device_id='device-1',device_info={'os':'Windows'},
        send_command_fn=send,get_file_link_fn=lambda *a:'',chat_history=history or [],user_id=None,chat_id=None,modes=modes or {},poll_task_id=None,
        cfg={'model':'flash','model_reasoner':'pro'},system_msg='system',machine_guid=None,mem_user_id=None,non_pipeline_tools=[],max_iterations=12,
        pick_model_fn=lambda cfg,modes: 'pro' if modes.get('autonomous') else 'flash',chat_completion_request_fn=completion or no_llm))


def test_bare_minimize_only_changes_current_window_without_llm(monkeypatch):
    adapter=FakeAdapter([window(),window(202,20,'Browser','comet.exe')]); control=wc.WindowControl(adapter)
    sent=[]
    async def send(device,action,args): sent.append((device,action,args)); return control.run(**args)
    result=run_actual_window_request(monkeypatch,'Сверни',send)
    assert sent == [('device-1','window.control',{'action':'minimize','target':'current'})]
    assert adapter.rows[0]['minimized'] and not adapter.rows[1]['maximized']
    assert silent_window_success({'status':'done','commands':result['commands']})


def test_fullscreen_followup_uses_observed_same_device_window_without_llm(monkeypatch):
    adapter=FakeAdapter(); control=wc.WindowControl(adapter)
    previous=control.run('restore',target='Word'); adapter.rows.append(window(202,20,'Browser','comet.exe')); adapter.current=202
    history=[{'role':'assistant','commands':[{'tool_name':'window.control','target_device_id':'device-1','result':previous}]},
             {'role':'user','content':'на весь экран'}]
    async def send(device,action,args): return control.run(**args)
    result=run_actual_window_request(monkeypatch,'на весь экран',send,history=history)
    assert adapter.rows[0]['maximized'] and not adapter.rows[1]['maximized']
    assert silent_window_success({'status':'done','commands':result['commands']})


def test_relative_reference_never_uses_other_device_id(monkeypatch):
    from server.window_policy import direct_window_action
    history=[{'role':'assistant','commands':[{'tool_name':'window.control','target_device_id':'Second',
        'result':{'status':'success','completion_state':'success','window':{'window_id':'a'*32}}}]}]
    assert direct_window_action('на весь экран',history,'device-1') is None


def test_ambiguous_relative_window_asks_without_agent_or_llm(monkeypatch):
    adapter=FakeAdapter([window(),window(202,20,'Browser','comet.exe')]); control=wc.WindowControl(adapter)
    history=[{'role':'assistant','commands':[{'tool_name':'window.control','target_device_id':'device-1','result':control.run('find',pid=pid)} for pid in (10,20)]}]
    for entry in history[0]['commands']: entry['result']['completion_state']='success'
    async def send(*args): pytest.fail('Ambiguous follow-up must not act')
    result=run_actual_window_request(monkeypatch,'на весь экран',send,history=history)
    assert 'Какое из предыдущих окон' in result['answer']


def test_unsupported_virtual_desktop_never_runs_shell_or_llm(monkeypatch):
    async def send(*args): pytest.fail('Unsupported virtual desktop must not dispatch')
    result=run_actual_window_request(monkeypatch,'отлично А теперь проводник на второй виртуальный рабочий стол',send)
    assert 'не реализован' in result['answer'] and 'не изменены' in result['answer']
    assert result['commands'][0]['result']['error']=='virtual_desktop_not_supported'
    assert not silent_window_success({'status':'done','commands':result['commands']})


def test_gpt_alias_restores_existing_app_without_legacy_window_find(monkeypatch):
    control=wc.WindowControl(FakeAdapter([window(process='ChatGPT.exe',title='ChatGPT')]))
    sent=[]; prompts=[]
    async def send(device,action,args): sent.append(action); return control.run(**args)
    async def completion(**kw):
        prompts.append(kw); assert len(prompts)==1
        assert kw['model']=='flash' and kw['phase'].startswith('window_control.')
        assert {t['function']['name'] for t in kw['tools']} == {'window_control','answer_text','answer_ask_clarification','answer_report_failure'}
        assert all('OLD HWND' not in m['content'] and 'irrelevant research' not in m['content'] for m in kw['messages'])
        return tool_call('window_control',{'action':'activate','target':'gpt'})
    history=[{'role':'assistant','content':'irrelevant research'*1000,'commands':[{'tool_name':'window.find','result':{'match':{'handle':123,'title':'OLD HWND'}}}]}, {'role':'user','content':'открой gpt'}]
    result=run_actual_window_request(monkeypatch,'открой gpt',send,completion,history,{'autonomous':True})
    assert sent==['window.control'] and silent_window_success({'status':'done','commands':result['commands']})


def test_window_action_guard_blocks_unrequested_maximize(monkeypatch):
    control=wc.WindowControl(FakeAdapter()); sent=[]
    responses=iter([tool_call('window_control',{'action':'maximize','target':'Word'},'bad'),tool_call('window_control',{'action':'minimize','target':'Word'},'good')])
    async def send(device,action,args): sent.append(args['action']); return control.run(**args)
    async def completion(**kw): return next(responses)
    result=run_actual_window_request(monkeypatch,'Сверни Word',send,completion)
    assert sent==['minimize'] and result['commands'][0]['result']['error']=='unrequested_window_action'


def test_window_phase_disables_reasoning_even_for_pro_model():
    from server.controller import _thinking_request_fields
    assert _thinking_request_fields({'model_reasoner':'pro'},'pro',phase='window_control.iteration.1') == {'thinking':{'type':'disabled'}}



def test_compact_controller_prompt_preserves_device_inventory_without_full_profile():
    from types import SimpleNamespace
    from server.controller import _build_route_kwargs
    result=_build_route_kwargs(route=SimpleNamespace(name='non_pipeline',toolset_name='non_pipeline'),
        runtime=SimpleNamespace(cfg={},machine_guid=None,mem_user_id=None),
        user_message='открой gpt',device_id='givi',device_info={},
        all_devices={'givi':{'info':{'hostname':'first'}},'Second':{'info':{'hostname':'second'}}},
        send_command_fn=None,get_file_link_fn=None,chat_history=[],user_id=1,chat_id=1,device_profile=None,
        modes={},poll_task_id=None)
    prompt=result['system_msg']
    assert len(prompt)<1800 and 'givi' in prompt and 'Second' in prompt and 'first' in prompt
    assert 'window_control' in prompt and 'не выполняй' in prompt.lower()



def test_missing_gpt_stops_after_one_selection_without_shell_recovery(monkeypatch):
    control=wc.WindowControl(FakeAdapter([])); requests=[]; sent=[]
    async def completion(**kw):
        requests.append(kw); assert len(requests)==1
        return tool_call('window_control',{'action':'activate','target':'gpt'})
    async def send(device,action,args): sent.append(action); return control.run(**args)
    result=run_actual_window_request(monkeypatch,'открой gpt',send,completion)
    assert sent==['window.control'] and 'не найдено' in result['answer']
    assert result['commands'][-1]['tool_name']=='answer.report_failure'
