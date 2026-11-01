import asyncio,json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from server import database as db,orchestrator as orch
from server.runtime_state import tasks






@pytest.mark.parametrize('critical',[{'intent':'invalid'},{'scope':None},{'target_device_ids':[None]},{'target_device_ids':[None]},{'source_task_ids':[42]},{'unexpected_instruction':'execute'}])
def test_core_validation_stays_strict(critical):
 with pytest.raises(ValidationError):orch.Decision.model_validate({'intent':'delegate','objective':'Read data',**critical})


def test_existing_chat_keeps_history_with_strict_decisions(client,monkeypatch):
 user=db.create_user('no-highlights');chat=db.create_chat(user['id'],'existing')['id']
 db.add_message(chat,'user','Предыдущая реплика');db.add_message(chat,'assistant','Предыдущий ответ')
 calls=[]
 async def completion(*args,**kwargs):
  calls.append(args[3]);return {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{'name':'orchestrator_decision','arguments':json.dumps({'intent':'conversation','answer':'Я помню контекст разговора.'})}}]}}]}
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
