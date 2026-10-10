"""Connected P0 lifecycle regressions. Model/device effects are controlled emulations.

Exports are optional test artifacts: browser tests replay actual API/history values,
not independently authored outcome fixtures. No production subsystem is introduced.
"""
import asyncio
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from server import database as db, orchestrator as orch, task_runtime as runtime, voice
from server import controller_non_pipeline as controller
from server.runtime_state import tasks, devices, TASK_TTL
from server.worker_scheduler import WorkerScheduler, owned_job
from test_tool_only_protocol import _message, _tool_call, _completion_fn
from test_orchestrator_worker import wait_until


def terminal(text, count=1, kind='grounded_report', action=False, sufficient=True, basis=None):
    return _tool_call('terminal','answer_text',{'answer_type':kind,'text':text,
        'basis':basis if basis is not None else [f'step_{i+1}' for i in range(count)],
        'self_check':{'depends_on_current_external_state':True,'claims_completed_action':action,
                      'has_sufficient_evidence':sufficient,'missing_evidence_question':''}})


def shell(command, stdout, code=0):
    return ('execute_cmd',{'command':command},{'returncode':code,'stdout':stdout,'stderr':''})


INFO = {
 1:('У тебя есть твоя папка на рабочем столе?', [shell('Get-ChildItem C:/Users/Owner/Desktop','IRU\nNotes'),shell('Get-ChildItem C:/Users/Owner/Desktop/IRU','notes')], 'На рабочем столе есть IRU и Notes.'),
 2:('Прочитай существующую заметку', [shell('Get-Content C:/Temp/note.txt','Встреча в 15:00.')], 'В заметке: Встреча в 15:00.'),
 3:('Сколько свободного места на системном диске?', [shell('Get-PSDrive C','Free=123456789')], 'Свободно 123456789 байт.'),
 4:('Какие окна открыты?', [('window_control',{'action':'list'}, {})], 'Открыты First — Notepad и Second — Notepad.'),
}


def worker_case(number):
    if number in INFO:
        goal,steps,text=copy.deepcopy(INFO[number]);return goal,steps,terminal(text,len(steps)),'success'
    if number==6:
        paths=['C:/Temp/first.txt','C:/Temp/second.txt']
        steps=[('write_content',{'path':p,'content':str(i)}, {'status':'ok','path':p,'bytes_written':1,'summary':'OK: file_written'}) for i,p in enumerate(paths)]
        check=shell('Get-Item C:/Temp/first.txt,C:/Temp/second.txt','OK: both_files_verified')
        check[2]['files_verified']=paths;steps.append(check)
        return 'Создай два разных файла и проверь оба',steps,terminal('Оба файла созданы и существуют.',3,action=True),'success'
    if number==7:
        steps=[shell('Start-Process notepad.exe C:/Temp/first.txt','OK: first_open_verified'),shell('Start-Process notepad.exe C:/Temp/second.txt','OK: second_open_verified')]
        return 'Открой первый файл, затем второй',steps,terminal('Оба файла открыты.',2,action=True),'success'
    if number in {8,9}:
        action='close' if number==8 else 'minimize'
        result={'status':'success','completion_state':'success','action':action,'target':'Notepad','verified':True}
        return ('Закрой экземпляр Notepad с PID 42, оставь PID 43' if number==8 else 'Сверни Notepad с PID 42 только на pc'), [('window_control',{'action':action,'target':'Notepad','pid':42},result)],terminal('Действие выполнено на pc.',action=True),'success'
    if number==11:
        return 'Открой файл и подтверди результат',[shell('Start-Process notepad.exe C:/Temp/first.txt','process started')],terminal('Файл открыт.',action=True),'unknown'
    if number==12:
        return 'Прочитай недоступную заметку',[shell('Get-Content C:/Temp/missing.txt','ERROR: file_not_found',1)],terminal('Файл не найден.',kind='error_report',sufficient=False),'failed'
    if number==13:
        steps=[('write_content',{'path':'C:/Temp/first.txt','content':'1'}, {'status':'ok','path':'C:/Temp/first.txt','bytes_written':1,'summary':'OK: file_written'}),shell('Get-Content C:/Temp/missing.txt','ERROR: denied',5)]
        return 'Создай первый файл, затем прочитай второй',steps,terminal('Первый файл создан. Второй файл прочитать не удалось.',2,'partial_report',True,False,basis=['step_1']),'partial'
    if number==14:
        return 'Создай файл и подтверди создание',[shell('Get-Item C:/Temp/first.txt','first.txt')],terminal('Доказательств создания файла нет; подтверждено только его существование.',kind='partial_report',sufficient=False),'partial'
    if number==15:
        return 'Проверь папку',[shell('Get-ChildItem C:/Users/Owner/Desktop','IRU')],terminal('**Папка:** `IRU` существует.'),'success'
    if number in {16,17,18,19}:
        return 'Прочитай проверенную заметку',[shell('Get-Content C:/Temp/note.txt','Проверенная заметка.')],terminal('Проверенная заметка.'),'success'
    raise AssertionError(number)


class Device:
    """Production command transport ends at this emulated socket, never the OS."""
    def __init__(self,key,steps):
        self.key=key;self.steps=list(steps);self.calls=[];self.files={};self.gate=None;self.transport_error=None
        self.adapter=None;self.windows=None
        if any(name=='window_control' for name,_,_ in steps):
            from test_window_control import FakeAdapter,window,wc
            self.adapter=FakeAdapter([window(101,42,'First — Notepad','notepad.exe'),window(202,43,'Second — Notepad','notepad.exe')])
            self.windows=wc.WindowControl(self.adapter)
    async def send_text(self,wire):
        payload=json.loads(wire)['payload'];self.calls.append(copy.deepcopy(payload))
        if self.gate is not None:await self.gate.wait()
        assert self.steps,'Unexpected device dispatch'
        name,args,result=self.steps.pop(0)
        canonical={'window_list':'window.list','window_control':'window.control'}.get(name,name)
        assert payload['action']==canonical,(payload,canonical)
        params=payload.get('params') or {}
        for key,value in args.items():assert params.get(key)==value,(params,args)
        if name=='window_control':
            result=self.windows.run(**params)
            assert result['status']=='success',result
        if name=='write_content':self.files[args['path']]=args['content']
        if result.get('files_verified'):assert all(p in self.files for p in result['files_verified'])
        future=devices[self.key]['pending'].pop(payload['id'])
        if self.transport_error:future.set_exception(RuntimeError('controlled transport error after dispatch'))
        else:future.set_result(copy.deepcopy(result))


@pytest.fixture
def world(client,monkeypatch):
    owner=db.create_user('integrity-owner');owner['chat_id']=db.create_chat(owner['id'],'P0 lifecycle')['id']
    db.set_user_plan(owner['id'],'pro')
    key=f"{owner['id']}:pc"
    db.upsert_device_profile('pc',owner['id'],{'hostname':'pc','os':'Windows','desktop_path':'C:/Users/Owner/Desktop'})
    async def probe(**kw):return None
    owner['_editor_calls']=[]
    owner['_tts_calls']=[]
    async def no_editor(*args,**kw):
        owner['_editor_calls'].append(True)
        raise AssertionError('No extra voice/editor model calls')
    async def tts(text):
        owner['_tts_calls'].append(text)
        return b'controlled-ogg'
    monkeypatch.setenv('YANDEX_API_KEY','controlled-fixture')
    monkeypatch.setattr(voice,'synthesize',tts)
    monkeypatch.setattr(runtime,'_probe_python_toolchain_if_needed',probe)
    monkeypatch.setattr(voice,'shorten_answer',no_editor)
    return client,owner,key,monkeypatch


def install(world,steps,answer,number):
    client,owner,key,monkeypatch=world
    device=Device(key,steps)
    devices[key]={'user_id':owner['id'],'ws':device,'info':{'hostname':'pc','os':'Windows'},'pending':{}}
    responses=[_message(tool_calls=[_tool_call(str(i),name,args)]) for i,(name,args,_) in enumerate(steps)]
    if number==14:responses.append(_message(tool_calls=[terminal('Я создала файл.',action=True)]))
    responses.append(_message(tool_calls=[answer]))
    audits=[]
    async def auditor(**kw):
        audits.append(kw['answer_payload']['text'])
        return (kw['answer_payload']['text']!='Я создала файл.','unsupported creation' if kw['answer_payload']['text']=='Я создала файл.' else '',False)
    monkeypatch.setattr(controller,'audit_answer_payload',auditor)
    async def process(**kw):
        complete=_completion_fn(copy.deepcopy(responses))
        return await controller.process_non_pipeline_command(
            user_message=kw['user_message'],device_id=kw['device_id'],device_info=kw['device_info'],
            send_command_fn=kw['send_command_fn'],get_file_link_fn=kw['get_file_link_fn'],chat_history=kw['chat_history'],
            user_id=None,chat_id=None,modes=kw['modes'],poll_task_id=kw['poll_task_id'],
            cfg={'model':'controlled-fixture','max_tokens':512},system_msg='system',machine_guid=None,mem_user_id=None,
            non_pipeline_tools=[],max_iterations=8,pick_model_fn=lambda *a:'controlled-fixture',
            chat_completion_request_fn=complete)
    monkeypatch.setattr(runtime,'process_nl_command',process)
    return device,audits


async def admit(world,scheduler,goal,tid):
    client,owner,key,monkeypatch=world
    async def decide(*a,**kw):return orch.Decision(intent='delegate',objective=goal,scope='device',target_device_ids=['pc'],execution_mode='simple'),{}
    monkeypatch.setattr(orch,'decide',decide)
    async def delegate(choice,**kw):
        assert choice.objective==goal
        return await scheduler.submit({'task_id':tid,'user_id':owner['id'],'chat_id':owner['chat_id'],
            'message':goal,'original_request':goal,'device_ids':[key],'modes':{},'kind':'worker',
            'orchestrated':True,'orchestrator_execution_mode':'simple','created_at':time.time(),'status':'running','results':{}},**kw)
    cmd=SimpleNamespace(message=goal,request_id=tid+'-turn',device_id='pc',modes={},broadcast=False)
    reply=await orch.run_turn(cmd,owner,owner['chat_id'],delegate)
    assert reply['worker_task_id']==tid
    return tasks[tid]


async def capture(world,task,number,expected,device,phases=None):
    client,owner,key,monkeypatch=world;headers={'X-Token':owner['token']};tid=task['task_id']
    response=client.get('/api/tasks/'+tid,headers=headers)
    assert response.status_code==200,response.text
    api=response.json()['task'];report=api['worker_report']
    if task.get('worker_id'):
        assert report['status']==expected
        assert api['presentation_status']==expected and report['goal_completed']==(expected=='success')
        assert report['evidence_refs'] or expected in {'cancelled','waiting_confirmation','queued','running'}
        assert api['task_receipt']==task.get('task_receipt')
        assert api['commands']==task.get('commands')
    else:
        assert report is None and api['task_receipt'] is None
    ops=client.get('/api/operations',headers=headers).json()
    operation=next((o for o in ops['operations'] if o['task_id']==tid),None)
    assert (operation['status']==expected) if operation else not task.get('worker_id')
    history=client.get(f"/api/chats/{owner['chat_id']}/messages",headers=headers).json()
    messages=history['messages'];same=[m for m in messages if m.get('_taskId')==tid]
    assert len(same)==1
    assert same[0]['content']==api['conversational_response']
    if report:assert same[0]['workerReport']==report
    spoken=await voice.spoken_parts(task)
    before_speech=len(owner['_tts_calls'])
    speech=client.post('/api/voice/tasks/'+tid+'/speech',headers=headers)
    assert speech.status_code in ({502} if number==17 else {200,204})
    if speech.status_code==200:
        assert speech.content==b'controlled-ogg'
        assert owner['_tts_calls'][before_speech:]==[spoken[0]]
    else:
        assert len(owner['_tts_calls'])==before_speech
    assert not owner['_editor_calls']
    stable=copy.deepcopy(api)
    tasks.pop(tid)
    reread=client.get('/api/tasks/'+tid,headers=headers)
    assert reread.status_code==200
    restored=reread.json()['task']
    for field in ('answer','commands','task_receipt','worker_report','conversational_response','execution_details','presentation_status'):
        assert restored[field]==stable[field],(number,field,restored[field],stable[field])
    assert await voice.spoken_parts(tasks[tid])==spoken
    if device:
        journal_operations=[c for c in stable['commands'] or [] if c.get('status')!='terminal']
        assert len(device.calls)==len(journal_operations)
    else:
        assert all(c.get('status')=='terminal' for c in stable['commands'] or [])
    record={'number':number,'expected':expected,'api':stable,'restored':restored,'operations':ops,'history':history,
            'spoken':spoken,'speech_status':speech.status_code,'voice_config':client.get('/api/voice/config',headers=headers).json(),'calls':device.calls if device else [],'phases':phases or []}
    export_record(record)
    return record


def export_record(record):
    directory=os.getenv('IRU_P0_LIFECYCLE_EXPORT')
    if directory:
        target=Path(directory);target.mkdir(parents=True,exist_ok=True)
        (target/f"case-{record['number']:02}.json").write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')


@pytest.mark.parametrize('number',[1,2,3,4,6,7,8,9,11,12,13,14,15,16,17,18,19])
def test_worker_connected_lifecycle(world,number):
    client,owner,key,monkeypatch=world
    goal,steps,answer,expected=worker_case(number)
    device,audits=install(world,steps,answer,number)
    other=None
    if number==9:
        other_key=f"{owner['id']}:other"
        other=Device(other_key,copy.deepcopy(steps))
        devices[other_key]={'user_id':owner['id'],'ws':other,'info':{'hostname':'other','os':'Windows'},'pending':{}}
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task=await admit(world,scheduler,goal,f'p0-case-{number}')
            await scheduler.runners[owner['id']]
            assert not device.steps and len(device.calls)==len(steps)
            if number==6:assert set(device.files)=={'C:/Temp/first.txt','C:/Temp/second.txt'}
            if number==8:
                assert [row['pid'] for row in device.adapter.rows]==[43]
                assert device.adapter.calls==[(101,'close',None)]
            if number==9:
                assert device.adapter.rows[0]['minimized'] is True
                assert device.adapter.rows[1]['minimized'] is False
                assert device.adapter.calls==[(101,'minimize',None)]
                assert not other.calls and not other.adapter.calls
                assert all(row['minimized'] is False for row in other.adapter.rows)
            if number==14:assert 'Я создала файл.' in audits and task['answer']!='Я создала файл.'
            if number==16:
                task['history_metadata']={'executionDetails':{'invalid':True}}
                task['orchestrator_metrics']='invalid metrics'
                task['worker_report']={**task['worker_report'],'summary':{'invalid':'summary'}}
            voice_failure=None
            if number==17:
                monkeypatch.setenv('YANDEX_API_KEY','controlled-fixture')
                async def failed_tts(text):raise RuntimeError('controlled TTS unavailable')
                monkeypatch.setattr(voice,'synthesize',failed_tts)
                before=copy.deepcopy(task['worker_report'])
                for _ in range(2):
                    failed=client.post(f"/api/voice/tasks/{task['task_id']}/speech",headers={'X-Token':owner['token']})
                    assert failed.status_code==502
                    voice_failure={'status':failed.status_code,'body':failed.json()}
                assert task['worker_report']==before
                assert task['answer']==json.loads(answer['function']['arguments'])['text']
            record=await capture(world,task,number,expected,device)
            if voice_failure:
                record['voice_failure']=voice_failure;export_record(record)
            if number==19:
                from server.worker_scheduler import scheduler as app_scheduler
                client.portal.call(app_scheduler.shutdown)
                script="""import json,sys
from fastapi.testclient import TestClient
from server.main import app
request=json.loads(sys.stdin.read())
with TestClient(app) as client:
 response=client.get('/api/tasks/'+request['id'],headers={'X-Token':request['token']})
 assert response.status_code==200
 print('P0='+json.dumps(response.json()['task'],ensure_ascii=True))
"""
                result=subprocess.run([sys.executable,'-c',script],input=json.dumps({'id':task['task_id'],'token':owner['token']}),text=True,capture_output=True,encoding='utf-8',timeout=30,
                    env={**os.environ,'IRU_DB_PATH':str(db.DB_PATH),'PYTHONIOENCODING':'utf-8'})
                assert result.returncode==0,result.stderr
                fresh=json.loads(next(line[3:] for line in result.stdout.splitlines() if line.startswith('P0=')))
                for field in ('answer','commands','task_receipt','worker_report','conversational_response','presentation_status'):assert fresh[field]==record['api'][field]
                record['fresh_api']=fresh;export_record(record)
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


def test_dialogue_clarification_connected_lifecycle(world):
    client,owner,key,monkeypatch=world
    async def forbidden(*a,**kw):raise AssertionError('Clarification must not dispatch Worker or tools')
    seen=[]
    async def decide(message,context,**kw):
        seen.append(copy.deepcopy(context))
        if message=='LAN или WAN?':return orch.Decision(intent='clarify',answer='Ты спрашиваешь о компьютерной сети?',spoken_response='Ты спрашиваешь о компьютерной сети?'),{}
        assert 'LAN' in json.dumps(context,ensure_ascii=False)
        return orch.Decision(intent='conversation',answer='LAN — локальная сеть. WAN связывает удалённые сети.',spoken_response='LAN — локальная сеть. WAN связывает удалённые сети.'),{}
    monkeypatch.setattr(orch,'decide',decide)
    async def scenario():
        for i,message in enumerate(['LAN или WAN?','Я про сеть']):
            cmd=SimpleNamespace(message=message,request_id=f'p0-dialogue-{i}',device_id='',modes={},broadcast=False)
            reply=await orch.run_turn(cmd,owner,owner['chat_id'],forbidden)
            assert reply['worker_task_id'] is None
        await capture(world,tasks[reply['task_id']],5,'success',None)
        assert len(seen)==2
    asyncio.run(scenario())


def test_queue_and_independent_dialogue_connected_lifecycle(world):
    client,owner,key,monkeypatch=world
    goal,steps,answer,_=worker_case(2)
    device,_=install(world,steps,answer,10)
    device.steps.extend(copy.deepcopy(steps))
    async def scenario():
        scheduler=WorkerScheduler();device.gate=asyncio.Event()
        try:
            first=await admit(world,scheduler,goal,'p0-case-10-first')
            await wait_until(lambda:len(device.calls)==1)
            second=await admit(world,scheduler,goal,'p0-case-10-second')
            assert second['status']=='queued' and len(device.calls)==1
            headers={'X-Token':owner['token']}
            waiting=client.get('/api/tasks/'+second['task_id'],headers=headers).json()['task']
            active=client.get('/api/tasks/'+first['task_id'],headers=headers).json()['task']
            assert waiting['worker_report']['status']=='queued' and active['worker_report']['status']=='running'
            assert client.post('/api/voice/tasks/'+first['task_id']+'/speech',headers=headers).status_code==409
            assert client.post('/api/voice/tasks/'+second['task_id']+'/speech',headers=headers).status_code==409
            phase_operations=client.get('/api/operations',headers=headers).json()
            phase_history=client.get(f"/api/chats/{owner['chat_id']}/messages",headers=headers).json()
            async def decide(*a,**kw):return orch.Decision(intent='conversation',answer='Можем поговорить, пока задача выполняется.',spoken_response='Можем поговорить, пока задача выполняется.'),{}
            async def forbidden(*a,**kw):raise AssertionError('Independent conversation must not dispatch another Worker')
            monkeypatch.setattr(orch,'decide',decide)
            cmd=SimpleNamespace(message='Как дела?',request_id='p0-case-10-dialogue',device_id='',modes={},broadcast=False)
            dialogue=await orch.run_turn(cmd,owner,owner['chat_id'],forbidden)
            assert dialogue['answer']=='Можем поговорить, пока задача выполняется.' and len(device.calls)==1
            assert await voice.spoken_parts(tasks[dialogue['task_id']])
            device.gate.set();await scheduler.runners[owner['id']]
            assert len(device.calls)==2 and not device.steps
            # Each task has one dispatched operation; retain per-task call evidence.
            one=SimpleNamespace(calls=device.calls[:1]);two=SimpleNamespace(calls=device.calls[1:])
            await capture(world,second,10,'success',two,phases=[{'api':active,'operations':phase_operations,'history':phase_history},{'api':waiting,'operations':phase_operations,'history':phase_history}])
            await capture(world,first,10,'success',one,phases=[{'api':active,'operations':phase_operations,'history':phase_history},{'api':waiting,'operations':phase_operations,'history':phase_history}])
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


def test_missing_and_stale_nonce_connected_lifecycle(world):
    client,owner,key,monkeypatch=world
    goal='Удалить тестовый файл только после моего подтверждения'
    steps=[shell('Remove-Item C:/Temp/denied.txt','OK: deleted')]
    device,_=install(world,steps,terminal('Файл удалён.',action=True),20)
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task=await admit(world,scheduler,goal,'p0-case-20')
            await wait_until(lambda:task['status']=='confirm')
            headers={'X-Token':owner['token']}
            waiting=client.get('/api/tasks/'+task['task_id'],headers=headers).json()['task']
            assert waiting['worker_report']['status']=='waiting_confirmation'
            phase_operations=client.get('/api/operations',headers=headers).json()
            phase_history=client.get(f"/api/chats/{owner['chat_id']}/messages",headers=headers).json()
            assert waiting['task_receipt'] is None and not device.calls
            assert client.post('/api/voice/tasks/'+task['task_id']+'/speech',headers=headers).status_code==409
            assert not owner['_tts_calls']
            response=client.post('/api/tasks/'+task['task_id']+'/command-decision',headers=headers,json={'accepted':True})
            assert response.status_code==422
            stale=client.post('/api/tasks/'+task['task_id']+'/command-decision',headers=headers,json={'confirmation_id':'stale-nonce','accepted':True,'via_voice':False})
            assert stale.status_code==409 and task['status']=='confirm' and not device.calls
            nonce=task['confirm_data']['confirmation_id']
            created=task['created_at']
            try:
                task['created_at']=time.time()-TASK_TTL-1
                expired=client.post('/api/tasks/'+task['task_id']+'/command-decision',headers=headers,json={'confirmation_id':nonce,'accepted':True,'via_voice':False})
                assert expired.status_code==409 and task['status']=='confirm' and not device.calls
            finally:task['created_at']=created
            denied=client.post('/api/tasks/'+task['task_id']+'/command-decision',headers=headers,json={'confirmation_id':nonce,'accepted':False,'via_voice':False})
            assert denied.status_code==200
            await scheduler.runners[owner['id']]
            assert not device.calls
            await capture(world,task,20,'cancelled',device,phases=[{'api':waiting,'operations':phase_operations,'history':phase_history,'rejected_statuses':[422,409,409]}])
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


def test_long_partial_voice_uses_structured_outcome_without_editor_model(world):
    client,owner,key,monkeypatch=world
    goal,steps,_,_=worker_case(13)
    text='Первый файл создан. '+('Его содержимое сохранено. '*25)+'Второй файл прочитать не удалось.'
    answer=terminal(text,2,'partial_report',True,False,basis=['step_1'])
    device,_=install(world,steps,answer,113)
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task=await admit(world,scheduler,goal,'p0-case-long-partial')
            await scheduler.runners[owner['id']]
            record=await capture(world,task,113,'partial',device)
            assert record['api']['answer']==text and record['api']['conversational_response']==text
            assert len(' '.join(record['spoken']))<=420
            assert 'не полностью' in ' '.join(record['spoken'])
        finally:await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize('uncertain',[False,True])
def test_confirmed_command_keeps_known_partial_or_uncertainty_visible(world,uncertain):
    client,owner,key,monkeypatch=world
    goal='Удалить тестовый файл, затем создать отчёт'
    steps=[shell('Remove-Item C:/Temp/denied.txt','OK: deletion_verified')]
    device,_=install(world,steps,terminal('Все действия выполнены.',action=True),114)
    device.transport_error=uncertain
    async def scenario():
        scheduler=WorkerScheduler()
        try:
            task=await admit(world,scheduler,goal,'p0-confirm-partial')
            await wait_until(lambda:task['status']=='confirm')
            response=client.post('/api/tasks/'+task['task_id']+'/command-decision',headers={'X-Token':owner['token']},
                json={'confirmation_id':task['confirm_data']['confirmation_id'],'accepted':True,'via_voice':False})
            assert response.status_code==200
            await scheduler.runners[owner['id']]
            assert len(device.calls)==1 and not device.steps
            record=await capture(world,task,114 if not uncertain else 115,'blocked',device)
            assert record['api']['task_receipt']['command_outcome']==('unknown' if uncertain else 'success')
            assert record['api']['task_receipt']['goal_completed'] is False
            assert record['api']['conversational_response']==record['api']['answer']
            if uncertain:assert 'могла выполниться' in record['api']['conversational_response']
            else:assert 'Подтверждённая команда выполнена' in record['api']['conversational_response']
        finally:await scheduler.shutdown()
    asyncio.run(scenario())
