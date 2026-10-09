import asyncio,json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from server import database as db,orchestrator as orch
from server.runtime_state import tasks


def test_highlights_are_absent_from_schema_and_instructions():
 assert 'highlights' not in orch.Decision.model_fields
 assert 'highlights' not in orch.TOOL['function']['parameters']['properties']
 assert 'HighlightRange' not in orch.Decision.model_json_schema().get('$defs',{})
 assert 'highlights' not in orch.SYSTEM


@pytest.mark.parametrize('value',[None,'bad',{},[],[{'start':0,'end':0}],[{'start':2,'end':-1}],[{'start':0,'end':3,'kind':'result'}]])
def test_only_retired_key_is_ignored_without_mutating_input(value):
 raw={'intent':'conversation','answer':'Привет!','highlights':value};saved=json.dumps(raw)
 decision=orch.Decision.model_validate_json(saved)
 assert decision.answer=='Привет!' and not hasattr(decision,'highlights')
 assert json.dumps(raw)==saved and 'highlights' not in decision.model_dump()


@pytest.mark.parametrize('critical',[{'intent':'invalid'},{'scope':None},{'execution_mode':'unapproved'},{'target_device_ids':[None]},{'source_task_ids':[42]},{'unexpected_instruction':'execute'}])
def test_core_validation_stays_strict(critical):
 with pytest.raises(ValidationError):orch.Decision.model_validate({'intent':'delegate','objective':'Read data','highlights':[{'start':0,'end':0}],**critical})


def test_existing_chat_survives_obsolete_zero_end_metadata(client,monkeypatch):
 user=db.create_user('no-highlights');chat=db.create_chat(user['id'],'existing')['id']
 db.add_message(chat,'user','Предыдущая реплика');db.add_message(chat,'assistant','Предыдущий ответ')
 calls=[]
 async def completion(*args,**kwargs):
  calls.append(args[3]);return {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{'name':'orchestrator_decision','arguments':json.dumps({'intent':'conversation','answer':'Я помню контекст разговора.','highlights':[{'start':0,'end':0},{'start':2,'end':0}]})}}]}}]}
 async def forbidden(*args,**kwargs):raise AssertionError('No handoff')
 monkeypatch.setattr(orch,'_chat_completion_request',completion);monkeypatch.setattr(orch,'load_llm_config',lambda:{'model':'mock'})
 async def run():
  for index,text in enumerate(['Привет','Напомним, что ты вообще знаешь?','Что тебе не нравится?','Почему?']):
   reply=await orch.run_turn(SimpleNamespace(message=text,request_id=f'no-highlight-{index}',device_id='',modes={},broadcast=False),user,chat,forbidden)
   task=tasks[reply['task_id']]
   assert task['status']=='done' and 'highlights' not in task
   assert 'highlights' not in db.get_messages(chat)[-1]
   assert 'highlights' not in orch.restore_dialogue(reply['task_id'],user['id'])
 asyncio.run(run());assert len(calls)==4
