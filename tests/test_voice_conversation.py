"""Speech projections only: fake provider/agent; no paid calls or filesystem actions."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from server import database as db, orchestrator as orch, voice
from server.runtime_state import tasks
from server.response_presentation import worker_presentation, worker_spoken_response
from server.worker_reports import build_worker_report


@pytest.mark.parametrize('message,answer,speech,intent',[
 ('Привет','Привет! Я на связи.','Привет, я здесь.','conversation'),
 ('Посоветуй занятие на дождливый вечер','Можно почитать книгу или посмотреть фильм дома.','Можно остаться дома с книгой или фильмом.','conversation'),
 ('А чем LAN отличается от WAN?','LAN соединяет устройства дома или в офисе. WAN связывает локальные сети на больших расстояниях.','LAN — сеть дома или в офисе, а WAN связывает такие сети между собой.','conversation'),
 ('Открой файл','Уточни, какой файл нужно открыть.','Какой файл открыть?','clarify'),
])
def test_same_primary_decision_provides_speech_persisted_and_owned(client,monkeypatch,message,answer,speech,intent):
 user=db.create_user('conversation-speaker');chat=db.create_chat(user['id'],'dialog')['id'];calls=[];tts=[]
 async def completion(*args,**kwargs):
  calls.append((args,kwargs));return {'choices':[{'finish_reason':'stop','message':{'tool_calls':[{'function':{'name':'orchestrator_decision','arguments':json.dumps({'intent':intent,'answer':answer,'spoken_response':speech})}}]}}]}
 monkeypatch.setattr(orch,'_chat_completion_request',completion);monkeypatch.setattr(orch,'load_llm_config',lambda:{'model':'mock'})
 async def forbidden(*args,**kwargs):raise AssertionError('No worker or extra speech LLM')
 monkeypatch.setattr(voice,'shorten_answer',forbidden)
 cmd=SimpleNamespace(message=message,request_id='spoken-primary',device_id='',modes={},broadcast=False)
 result=asyncio.run(orch.run_turn(cmd,user,chat,forbidden));task=tasks[result['task_id']]
 assert task['answer']==answer and task['dialogue_spoken_response']==speech and len(calls)==1
 row=db.get_messages(chat)[-1];assert row['content']==answer and row['spokenResponse']==speech
 tasks.pop(result['task_id']);headers={'X-Token':user['token']}
 restored=client.get('/api/tasks/'+result['task_id'],headers=headers).json()['task'];assert restored['answer']==answer
 monkeypatch.setenv('YANDEX_API_KEY','mock-key')
 async def synthesize(text):tts.append(text);return b'fake-ogg'
 monkeypatch.setattr(voice,'synthesize',synthesize)
 reply=client.post('/api/voice/tasks/'+result['task_id']+'/speech',headers=headers)
 assert reply.status_code==200 and tts==[speech] and reply.headers['X-Voice-Parts']=='1'
 assert len(calls)==1
 other=db.create_user('other-speaker')
 assert client.post('/api/voice/tasks/'+result['task_id']+'/speech',headers={'X-Token':other['token']}).status_code==404


@pytest.mark.parametrize('value',[None,42,{},'x'*421,'```code```','<script>bad</script>','port=9999','На Third всё открыла.'])
def test_invalid_optional_speech_falls_back_without_losing_primary_answer(value,monkeypatch):
 answer='В config.ini указан port=8080.'
 decision=orch.Decision(intent='conversation',answer=answer,spoken_response=value)
 speech=voice.conversational_speech(decision.spoken_response,answer)
 task={'kind':'orchestrator','status':'done','answer':answer,'dialogue_intent':'conversation','dialogue_spoken_response':speech,'dialogue_speech_answer':answer}
 async def forbidden(*args):raise AssertionError('Short fallback never needs another model')
 monkeypatch.setattr(voice,'shorten_answer',forbidden)
 assert asyncio.run(voice.spoken_parts(task))==[answer]


def test_long_primary_speech_avoids_editor_latency_and_does_not_fill_420(monkeypatch):
 original='LAN — локальная сеть дома или офиса. WAN связывает такие сети. '*25
 task={'kind':'orchestrator','status':'done','answer':original,'dialogue_intent':'conversation','dialogue_spoken_response':'LAN — сеть рядом, а WAN связывает такие сети.','dialogue_speech_answer':original}
 async def forbidden(*args):raise AssertionError('No editorial roundtrip')
 monkeypatch.setattr(voice,'shorten_answer',forbidden)
 started=time.monotonic();parts=asyncio.run(voice.spoken_parts(task))
 assert len(parts[0])<100 and task['answer']==original
 assert asyncio.run(voice.spoken_parts(task))==parts and time.monotonic()-started<1
 task['answer']='Изменённый ответ без тех значений.'
 assert asyncio.run(voice.spoken_parts(task))==[task['answer']]


@pytest.mark.parametrize('state',['queued','running'])
def test_delegation_speech_uses_actual_admission_not_model_completion(client,monkeypatch,state):
 user=db.create_user('admission-speaker');chat=db.create_chat(user['id'],'dialog')['id']
 async def decide(*args,**kwargs):return orch.Decision(intent='delegate',scope='server',objective='Проверить данные',spoken_response='Всё сделала.'),{}
 async def delegate(*args,**kwargs):return {'task_id':'fake-worker','status':state}
 monkeypatch.setattr(orch,'decide',decide)
 cmd=SimpleNamespace(message='Проверь данные',request_id='admission',device_id='',modes={},broadcast=False)
 reply=asyncio.run(orch.run_turn(cmd,user,chat,delegate));task=tasks[reply['task_id']]
 assert reply['answer']=='' and task['commands']==[]
  assert asyncio.run(voice.spoken_parts(task))==[]
 assert reply['worker_task_id']=='fake-worker'


def worker(status='done',action='app.open_url'):
 return {'kind':'worker','worker_id':'worker-1','task_id':'verified-worker','user_id':2,'device_ids':['2:pc'],
  'status':status,'message':'Открой страницу','answer':'Готово. Задача выполнена.',
  'commands':[{'tool_name':action,'device_id':'pc','step_id':'step_1','status':'success','result':{'status':'opened_verified','url':'https://example.invalid'}}],
  'task_receipt':{'task_status':'completed','goal_completed':True,'final_verification_status':'verified'}}


def test_action_speech_is_structured_not_rewritten_model_prose(monkeypatch):
 task=worker();raw=task['answer'];report=build_worker_report(task)
 async def forbidden(*args):raise AssertionError('Action does not need another model')
 monkeypatch.setattr(voice,'shorten_answer',forbidden)
 assert asyncio.run(voice.spoken_parts(task))==['Открыла страницу.']
 assert worker_presentation(task)['conversational_response']=='Страница открыта.'
 assert task['answer']==raw and build_worker_report(task)==report


@pytest.mark.parametrize('status',['partial','failed','blocked','unknown','cancelled'])
def test_worker_negatives_ignore_fabricated_model_speech_and_keep_outcome(status,monkeypatch):
 task=worker(status);task.update(dialogue_intent='conversation',dialogue_spoken_response='Всё сделала.',dialogue_speech_answer=task['answer'])
 task['commands']=[];task['task_receipt']={'task_status':status,'goal_completed':False}
 async def forbidden(*args):raise AssertionError('Negative result is deterministic')
 monkeypatch.setattr(voice,'shorten_answer',forbidden)
 assert asyncio.run(voice.spoken_parts(task))==[worker_presentation(task)['conversational_response']]
 assert 'Всё сделала' not in task['spoken_response'] and build_worker_report(task)['status']==status


@pytest.mark.parametrize('answer',['Не могу подтвердить результат.','Проверка не выполнена.','Задача выполнена частично.','Нужно подтверждение опасной команды.','Нет доступа к файлу.','Результат пока неизвестен.','Не могу сказать, выполнено ли действие.'])
def test_sensitive_source_cannot_get_successful_alternate_speech(answer):
 assert voice.conversational_speech('Всё сделала.',answer)==''


def test_full_read_wins_over_primary_speech_and_survives_restore(client,monkeypatch):
 user=db.create_user('full-reader');chat=db.create_chat(user['id'],'dialog')['id'];answer='Подробный текст для полного чтения. '*35
 async def decide(*args,**kwargs):return orch.Decision(intent='conversation',answer=answer,spoken_response='Текст есть в чате.'),{}
 async def forbidden(*args,**kwargs):raise AssertionError('No worker/editor call')
 monkeypatch.setattr(orch,'decide',decide);monkeypatch.setattr(voice,'shorten_answer',forbidden)
 cmd=SimpleNamespace(message='Прочитай ответ вслух полностью',request_id='full-reader',device_id='',modes={},broadcast=False)
 reply=asyncio.run(orch.run_turn(cmd,user,chat,forbidden));restored=orch.restore_dialogue(reply['task_id'],user['id'])
 assert restored['full_speech_requested'] and ' '.join(asyncio.run(voice.spoken_parts(restored)))==answer.strip()


def test_dialogue_changes_topic_while_worker_and_delayed_report_stays_grounded(client,monkeypatch):
 from server.worker_scheduler import init_worker_storage
 user=db.create_user('interleaved');chat=db.create_chat(user['id'],'dialog')['id'];init_worker_storage()
 db.upsert_device_profile('pc',user['id'],{'desktop_path':r'C:\Users\Demo\Desktop'})
 active=worker('running');active.update(task_id='long-ppt',user_id=user['id'],device_ids=[f"{user['id']}:pc"],chat_id=chat,message='Создай презентацию про LAN',created_at=time.time(),commands=[],task_receipt=None)
 tasks[active['task_id']]=active
 with db.get_db() as c:c.execute('INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(active['task_id'],user['id'],chat,'running',json.dumps(active),time.time(),time.time()))
 db.add_message(chat,'user','Создай презентацию про LAN');db.add_message(chat,'assistant','Хорошо, займусь.')
 seen=[]
 async def decide(message,context,**kwargs):
  seen.append(context);return orch.Decision(intent='conversation',answer='LAN — локальная сеть, WAN связывает такие сети.',spoken_response='LAN — сеть рядом, WAN связывает такие сети.'),{}
 async def forbidden(*args,**kwargs):raise AssertionError('Do not interrupt/restart Worker or call editor')
 monkeypatch.setattr(orch,'decide',decide);monkeypatch.setattr(voice,'shorten_answer',forbidden)
 cmd=SimpleNamespace(message='А чем LAN отличается от WAN?',request_id='topic-shift',device_id='',modes={},broadcast=False)
 reply=asyncio.run(orch.run_turn(cmd,user,chat,forbidden));assert seen[0]['tasks'][0]['task_id']=='long-ppt' and active['status']=='running'
 assert asyncio.run(voice.spoken_parts(tasks[reply['task_id']]))==['LAN — сеть рядом, WAN связывает такие сети.']
 active.update(status='done',answer='Технический журнал презентации.',commands=[{'tool_name':'write_content','device_id':'pc','step_id':'step_1','status':'success','result':{'path':r'C:\Users\Demo\Desktop\LAN.pptx','bytes_written':500}}],task_receipt={'task_status':'completed','goal_completed':True,'final_verification_status':'verified'})
 assert asyncio.run(voice.spoken_parts(active))==['Презентация готова. Файл на рабочем столе.']
 assert active['answer']=='Технический журнал презентации.'


@pytest.mark.parametrize('speech',['В config.ini порт 8080.','В config.ini параметр port равен 8080.'])
def test_assignment_value_can_be_spoken_naturally_without_an_invented_number(speech):
 assert voice.conversational_speech(speech,'В config.ini задан port=8080.')==speech
 assert voice.conversational_speech(speech.replace('8080','9090'),'В config.ini задан port=8080.')==''
