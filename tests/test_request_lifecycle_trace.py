"""Lifecycle traces contain control flow, not request/tool/answer content."""
import asyncio
import json
import time
import pytest
from server import task_runtime as runtime, database as db, run_journal as journal
from server.runtime_state import devices,tasks


def setup(client,monkeypatch,mode=None,kind='SIMPLE'):
    user=db.create_user('trace-user');chat=db.create_chat(user['id'])
    db.upsert_device_profile('givi',user['id'],{'hostname':'GIVI','os':'Windows'})
    key=f"{user['id']}:givi";devices[key]={'user_id':user['id'],'ws':object(),'info':{'hostname':'GIVI','os':'Windows'},'pending':{}}
    tasks['trace-task']={'task_id':'trace-task','user_id':user['id'],'chat_id':chat['id'],'message':'test','device_ids':[key],'status':'running','results':{},'modes':mode or {},'created_at':time.time()}
    async def classify(*a,**kw):return kind,'suggestion' if kind=='PLAN' else ''
    monkeypatch.setattr(runtime,'classify_task_complexity',classify)
    return user,chat,key


@pytest.mark.parametrize('pipeline',[False,True])
def test_nl_trace_records_mode_tools_and_terminal_without_sensitive_content(client,monkeypatch,caplog,pipeline):
    user,chat,key=setup(client,monkeypatch,{'pipeline':pipeline})
    async def process(**kw):
        commands=[]
        journal.append_tool_step(commands,journal.make_run_step(journal=commands,tool_name='web.read',target_device_id='givi',
            result={'status':'success','text':'PRIVATE_DOM SECRET_TOKEN'},command='PRIVATE_COMMAND'))
        journal.append_answer_step(commands,'answer_text',{'answer_type':'grounded_report','text':'PRIVATE_ANSWER','basis':['step_1']})
        return {'answer':'PRIVATE_ANSWER','commands':commands,'tasks':[],
            'task_receipt':{'task_status':'completed','answer_source':'model','terminal_reason':'success_criteria'}}
    monkeypatch.setattr(runtime,'process_nl_command',process)
    caplog.set_level('INFO',logger='uvicorn.error.iru.lifecycle')
    asyncio.run(runtime.run_nl_task('trace-task',user['id'],'PRIVATE_REQUEST SECRET_PASSWORD',[key],chat['id']))
    trace=tasks['trace-task']['diagnostic_trace'];wire=json.dumps(trace)
    assert all(row['task_id']=='trace-task' for row in trace)
    assert any(row['event']=='classification' for row in trace)
    assert any(row.get('controller')==('pipeline' if pipeline else 'non_pipeline') for row in trace)
    assert any(row.get('tool_name')=='web.read' and row.get('status')=='success' for row in trace)
    assert any(row.get('tool_name')=='answer.text' and row.get('answer_type')=='grounded_report' for row in trace)
    assert trace[-1]['event']=='request_finished' and trace[-1]['source']=='model'
    logs=' '.join(row.getMessage() for row in caplog.records if row.name=='uvicorn.error.iru.lifecycle')
    for secret in ('PRIVATE_DOM','SECRET_TOKEN','PRIVATE_COMMAND','PRIVATE_ANSWER','PRIVATE_REQUEST','SECRET_PASSWORD'):
        assert secret not in wire and secret not in logs


def test_plan_suggestion_records_why_controller_did_not_execute(client,monkeypatch):
    user,chat,key=setup(client,monkeypatch,kind='PLAN')
    async def process(**kw):pytest.fail('PLAN suggestion does not start an ordinary worker')
    monkeypatch.setattr(runtime,'process_nl_command',process)
    asyncio.run(runtime.run_nl_task('trace-task',user['id'],'task',[key],chat['id']))
    trace=tasks['trace-task']['diagnostic_trace']
    assert any(row.get('classification')=='PLAN' for row in trace)
    assert trace[-1]['source']=='plan_suggestion'
    # Tests the real branch, does not declare hardware identity its cause.


def test_protocol_recovery_is_visible_and_logging_failure_does_not_break_task(client,monkeypatch):
    user,chat,key=setup(client,monkeypatch)
    async def process(**kw):
        commands=[]
        journal.append_tool_step(commands,{'action':'tool_only_protocol','result':{'status':'failed','error':'PRIVATE_ERROR'},'status':'failed'})
        return {'answer':'Не удалось получить ответ.','commands':commands,'tasks':[]}
    def fail_log(*a,**kw):raise OSError('diagnostic destination unavailable')
    monkeypatch.setattr(runtime,'process_nl_command',process)
    monkeypatch.setattr(journal._TRACE_LOGGER,'info',fail_log)
    asyncio.run(runtime.run_nl_task('trace-task',user['id'],'task',[key],chat['id']))
    assert tasks['trace-task']['status']=='done'
    assert any(row.get('source')=='protocol_recovery' for row in tasks['trace-task']['diagnostic_trace'])
    assert 'PRIVATE_ERROR' not in json.dumps(tasks['trace-task']['diagnostic_trace'])


def test_no_device_onboarding_trace_uses_same_lifecycle(client,monkeypatch):
    user,chat,key=setup(client,monkeypatch)
    async def process(**kw):return {'answer':'server reply','commands':[]}
    monkeypatch.setattr(runtime,'process_onboarding_message',process)
    asyncio.run(runtime.run_onboarding_task('trace-task',user['id'],'PRIVATE_ONBOARDING',chat['id']))
    trace=tasks['trace-task']['diagnostic_trace']
    assert any(row.get('controller')=='onboarding' for row in trace)
    assert trace[-1]['event']=='request_finished'
    assert 'PRIVATE_ONBOARDING' not in json.dumps(trace)


def test_unavailable_trace_context_does_not_break_execution(client,monkeypatch):
    user,chat,key=setup(client,monkeypatch)
    class Unavailable:
        def set(self,*a):raise OSError("tracing unavailable")
        def get(self):raise OSError("tracing unavailable")
    async def process(**kw):return {'answer':'reply','commands':[],'tasks':[]}
    monkeypatch.setattr(journal,'_DIAGNOSTIC_CONTEXT',Unavailable())
    monkeypatch.setattr(runtime,'process_nl_command',process)
    asyncio.run(runtime.run_nl_task('trace-task',user['id'],'task',[key],chat['id']))
    assert tasks['trace-task']['status']=='done'


def test_existing_task_api_exposes_trace_only_to_owner(client,monkeypatch):
    user,chat,key=setup(client,monkeypatch)
    async def process(**kw):return {'answer':'reply','commands':[],'tasks':[]}
    monkeypatch.setattr(runtime,'process_nl_command',process)
    asyncio.run(runtime.run_nl_task('trace-task',user['id'],'task',[key],chat['id']))
    response=client.get('/api/tasks/trace-task',headers={'X-Token':user['token']})
    assert response.status_code==200 and response.json()['task']['diagnostic_trace'][-1]['event']=='request_finished'
    other=db.create_user('other-trace-user')
    assert client.get('/api/tasks/trace-task',headers={'X-Token':other['token']}).status_code==404


def test_ordinary_controller_debug_logs_do_not_dump_literals(monkeypatch,capsys):
    from test_browser_integration import run_normal,call,grounded
    async def send(device,operation,args):
        assert args['command']=='PRIVATE_COMMAND SECRET_TOKEN'
        return {'returncode':0,'stdout':'PRIVATE_FILE_CONTENT','stderr':''}
    run_normal(monkeypatch,'Run task',[call('execute_cmd',{'command':'PRIVATE_COMMAND SECRET_TOKEN'}),grounded('PRIVATE_REPLY')],send)
    logs=capsys.readouterr().out
    for value in ('PRIVATE_COMMAND','SECRET_TOKEN','PRIVATE_FILE_CONTENT','PRIVATE_REPLY'):
        assert value not in logs
