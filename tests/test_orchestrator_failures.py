import asyncio,json,sqlite3
from types import SimpleNamespace
import httpx
import pytest
from server import orchestrator as orch,database as db
from server.runtime_state import tasks,devices


def owner():
 user=db.create_user('p0-diagnostics');user['chat_id']=db.create_chat(user['id'],'existing')['id']
 db.add_message(user['chat_id'],'user','Предыдущая реплика')
 db.add_message(user['chat_id'],'assistant','Предыдущий ответ')
 return user


def payload(arguments):
 return {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{'name':'orchestrator_decision','arguments':json.dumps(arguments)}}]}}]}


@pytest.mark.parametrize('bad,stage,error_type',[
 ({'choices':[{'finish_reason':'stop','message':{'content':'ordinary text','tool_calls':[]}}]},'llm_response','ValueError'),
 (payload({'intent':'conversation','answer':'reply','scope':None}),'decision_validation','ValidationError'),
 (payload({'intent':'conversation','answer':'reply','execution_mode':'unapproved'}),'decision_validation','ValidationError'),
])
def test_failed_decision_is_diagnosed_and_does_not_poison_following_turns(client,monkeypatch,caplog,bad,stage,error_type):
 user=owner();calls=[];delegated=[]
 answers=iter([bad,payload({'intent':'conversation','answer':'Привет!'}),payload({'intent':'conversation','answer':'Обсудим вопрос.'}),payload({'intent':'clarify','answer':'Что именно уточнить?'}),payload({'intent':'delegate','objective':'Показать состояние','scope':'device','target_device_ids':['pc']})])
 async def completion(*args,**kwargs):calls.append(args[3]);return next(answers)
 async def delegate(choice,**kwargs):delegated.append(choice);return {'task_id':'fake-worker','status':'running'}
 monkeypatch.setattr(orch,'_chat_completion_request',completion);monkeypatch.setattr(orch,'load_llm_config',lambda:{'model':'mock'})
 devices[f"{user['id']}:pc"]={'user_id':user['id'],'ws':object(),'info':{'hostname':'pc','os':'Windows'}}
 async def run():
  replies=[]
  for index,text in enumerate(['Проблемная реплика','Привет','Обычный вопрос','Уточнение предыдущего вопроса','Покажи состояние устройства']):
   cmd=SimpleNamespace(message=text,request_id=f'p0-turn-{index}',device_id='pc',modes={},broadcast=False)
   replies.append(await orch.run_turn(cmd,user,user['chat_id'],delegate))
  return replies
 with caplog.at_level('ERROR',logger='iru.orchestrator'):replies=asyncio.run(run())
 failed=tasks[replies[0]['task_id']]
 assert failed['orchestrator_error']==error_type and failed['orchestrator_error_stage']==stage
 records=[json.loads(r.message.split('orchestrator_failure ',1)[1]) for r in caplog.records if r.name=='iru.orchestrator']
 assert records[0]['task_id']==failed['task_id'] and records[0]['stage']==stage and records[0]['traceback']
 assert all(tasks[r['task_id']]['status']=='done' for r in replies[1:]) and len(calls)==5 and len(delegated)==1
 assert all(len(rows)>=3 for rows in calls) and len(db.get_messages(user['chat_id']))==12
 assert 'traceback' not in replies[0] and 'ValidationError' not in replies[0]['answer']


@pytest.mark.parametrize('stage',['context_for','llm_config','llm_request','handoff'])
def test_phase_errors_do_not_log_exception_messages_context_or_credentials(client,monkeypatch,caplog,stage):
 user=owner();secret='PRIVATE_SECRET_AND_CONTEXT_DO_NOT_LOG'
 async def completion(*args,**kwargs):return payload({'intent':'delegate','objective':'Read data','scope':'server'})
 async def handoff(*args,**kwargs):raise RuntimeError(secret)
 def fail_context(*args,**kwargs):raise sqlite3.OperationalError(secret)
 def fail_config():raise FileNotFoundError(secret)
 async def fail_provider(*args,**kwargs):
  request=httpx.Request('POST','https://provider.invalid/?api_key='+secret)
  raise httpx.HTTPStatusError(secret,request=request,response=httpx.Response(401,request=request,text=secret))
 monkeypatch.setattr(orch,'_chat_completion_request',completion);monkeypatch.setattr(orch,'load_llm_config',lambda:{'model':'mock'})
 if stage=='context_for':monkeypatch.setattr(orch,'context_for',fail_context)
 if stage=='llm_config':monkeypatch.setattr(orch,'load_llm_config',fail_config)
 if stage=='llm_request':monkeypatch.setattr(orch,'_chat_completion_request',fail_provider)
 cmd=SimpleNamespace(message='Обычная реплика '+secret,request_id='p0-private',device_id='',modes={},broadcast=False)
 with caplog.at_level('ERROR',logger='iru.orchestrator'):reply=asyncio.run(orch.run_turn(cmd,user,user['chat_id'],handoff))
 record=json.loads(next(r.message.split('orchestrator_failure ',1)[1] for r in caplog.records if r.name=='iru.orchestrator'))
 assert record['stage']==stage and record['traceback'] and secret not in caplog.text and secret not in reply['answer']
 if stage=='llm_request':assert record['http_status']==401


def test_extra_field_validation_log_hides_even_a_secret_field_name(client,monkeypatch,caplog):
 user=owner();secret='PRIVATESECRETKEYFIELD'
 async def completion(*args,**kwargs):return payload({'intent':'conversation','answer':'Hello',secret:'PRIVATEVALUE'})
 async def forbidden(*args,**kwargs):raise AssertionError('No Worker')
 monkeypatch.setattr(orch,'_chat_completion_request',completion);monkeypatch.setattr(orch,'load_llm_config',lambda:{'model':'mock'})
 cmd=SimpleNamespace(message='Привет',request_id='p0-extra',device_id='',modes={},broadcast=False)
 with caplog.at_level('ERROR',logger='iru.orchestrator'):asyncio.run(orch.run_turn(cmd,user,user['chat_id'],forbidden))
 assert secret not in caplog.text and 'PRIVATEVALUE' not in caplog.text and 'extra_forbidden' in caplog.text and '<extra>' in caplog.text

@pytest.mark.parametrize('helper',['wants_full_speech','conversational_speech'])
def test_optional_voice_presentation_failure_keeps_dialogue_decision(client,monkeypatch,helper):
 from server import voice
 user=owner()
 async def completion(*args,**kwargs):return payload({'intent':'conversation','answer':'LAN links nearby devices.','spoken_response':'LAN links nearby devices.'})
 async def forbidden(*args,**kwargs):raise AssertionError('No Worker')
 def broken(*args,**kwargs):raise RuntimeError('optional voice decoration unavailable')
 monkeypatch.setattr(orch,'_chat_completion_request',completion)
 monkeypatch.setattr(orch,'load_llm_config',lambda:{'model':'mock'})
 monkeypatch.setattr(voice,helper,broken)
 cmd=SimpleNamespace(message='I mean the network',request_id='p0-voice-decoration',device_id='',modes={},broadcast=False)
 reply=asyncio.run(orch.run_turn(cmd,user,user['chat_id'],forbidden))
 assert reply['answer']=='LAN links nearby devices.'
 assert tasks[reply['task_id']]['status']=='done'
 assert db.get_messages(user['chat_id'])[-1]['content']==reply['answer']
