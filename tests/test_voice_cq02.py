"""CQ-02: mock real provider boundaries, compare TTS text and call counts."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from server import controller, database as db, orchestrator as orch, voice
from server.runtime_state import tasks
from test_voice_chat102 import REPLIES, SUMMARIES


@pytest.mark.parametrize('failure',['provider','timeout','opposite','too_long','incomplete','budget'])
def test_editor_failure_is_bounded_and_never_reads_long_source(client,monkeypatch,failure):
 user=db.create_user('cq02-failure');chat=db.create_chat(user['id'],'cq02')['id'];calls=[];tts=[]
 answer=('Документ сохранён. '*35)+'Но отправка не завершена.'
 async def completion(*args,**kwargs):
  calls.append(kwargs['phase'])
  if kwargs['phase']=='orchestrator':
   return {'choices':[{'finish_reason':'stop','message':{'tool_calls':[{'function':{
    'name':'orchestrator_decision','arguments':json.dumps({'intent':'conversation','answer':answer,'spoken_response':''})}}]}}]}
  if failure=='provider':raise RuntimeError('PRIVATE provider error')
  if failure=='timeout':raise asyncio.TimeoutError()
  content='Документ сохранён.' if failure=='opposite' else 'x'*421 if failure=='too_long' else 'Не завершено.'
  return {'choices':[{'finish_reason':'length' if failure=='incomplete' else 'stop','message':{'content':content}}]}
 async def forbidden(*args,**kwargs):raise AssertionError('No Worker')
 async def synthesize(text):tts.append(text);return b'mock-ogg'
 cfg={'model':'deepseek-v4-flash'}
 if failure=='budget':cfg['deepseek_pricing_usd_per_1m']={'deepseek-v4-flash':{'output':1000}}
 monkeypatch.setattr(orch,'load_llm_config',lambda:cfg)
 monkeypatch.setattr(controller,'load_llm_config',lambda:cfg)
 monkeypatch.setattr(orch,'_chat_completion_request',completion)
 monkeypatch.setattr(controller,'_chat_completion_request',completion)
 monkeypatch.setattr(voice,'synthesize',synthesize);monkeypatch.setenv('YANDEX_API_KEY','mock')
 cmd=SimpleNamespace(message='Объясни результат',request_id='failure',device_id='',modes={},broadcast=False)
 tid=asyncio.run(orch.run_turn(cmd,user,chat,forbidden))['task_id'];headers={'X-Token':user['token']}
 for repeat in range(3):
  response=client.post(f'/api/voice/tasks/{tid}/speech',headers=headers)
  assert response.status_code==200 and response.headers['X-Voice-Parts']=='1'
  assert tts[-1]==voice.SPEECH_UNAVAILABLE and len(tts[-1])<420
  assert tasks[tid]['answer']==answer
  check=tasks[tid]['speech_diagnostics']
  assert check['selection']['mode']=='unavailable'
  assert check['primary']['reason']=='missing_candidate'
  if failure=='budget':assert check['selection']['reason']=='editorial_cost_budget'
  if failure=='opposite':assert check['editorial']['reason']=='outcome_mismatch'
  assert 'PRIVATE' not in json.dumps(check)
  tasks.pop(tid)
  assert client.get(f'/api/tasks/{tid}',headers=headers).status_code==200
 assert calls==(['orchestrator'] if failure=='budget' else ['orchestrator','voice_brief'])


def test_real_long_memory_answer_fits_reserved_editor_budget(monkeypatch):
 reply=REPLIES[3];seen=[]
 monkeypatch.setattr(controller,'load_llm_config',lambda:{'model':'deepseek-v4-flash'})
 async def completion(**kwargs):
  seen.append(kwargs)
  return {'choices':[{'finish_reason':'stop','message':{'content':SUMMARIES[2916]}}]}
 monkeypatch.setattr(controller,'_chat_completion_request',completion)
 task={'answer':reply['answer'],'message':reply['request'],'status':'done','_speech_informational':True}
 assert asyncio.run(voice.shorten_answer(task))==SUMMARIES[2916]
 reserve=task['speech_diagnostics']['selection']['editorial_reserved_usd']
 assert .002<reserve<=voice.VOICE_BRIEF_MAX_COST_USD==.005
 assert len(seen)==1 and seen[0]['phase']=='voice_brief' and 'tools' not in seen[0]


@pytest.mark.parametrize('message',['Почему?','Подробнее про второй пункт','Продолжи'])
def test_contextual_followup_keeps_previous_topic_and_needed_depth(client,monkeypatch,message):
 user=db.create_user('cq02-followup');chat=db.create_chat(user['id'],'followup')['id']
 previous='Первый пункт — хранить полный архив. Второй пункт — проверять записи памяти по исходным словам.'
 db.add_message(chat,'assistant',previous)
 answer=('Второй пункт нужен, потому что сжатие может превратить предположение в факт. '
         'Поэтому запись памяти должна сохранять связь с исходными словами. '
         'Если связь потеряна, уверенность в такой записи нужно снизить. ')*3
 speech='Записи памяти нужно сверять с исходными словами: иначе предположение превращается в факт. Потеря источника снижает уверенность.'
 calls=[];tts=[]
 async def completion(*args,**kwargs):
  context=json.loads(args[3][1]['content'])['untrusted_context']
  assert any(row['content']==previous for row in context['history'])
  assert args[3][-1]['content']==message
  calls.append(kwargs)
  return {'choices':[{'finish_reason':'stop','message':{'tool_calls':[{'function':{
   'name':'orchestrator_decision','arguments':json.dumps({'intent':'conversation','answer':answer.strip(),'spoken_response':speech})}}]}}]}
 async def forbidden(*args,**kwargs):raise AssertionError('No clarification, Worker or extra LLM')
 async def synthesize(text):tts.append(text);return b'mock-ogg'
 monkeypatch.setattr(orch,'_chat_completion_request',completion)
 monkeypatch.setattr(orch,'load_llm_config',lambda:{'model':'mock'})
 monkeypatch.setattr(voice,'shorten_answer',forbidden)
 monkeypatch.setattr(voice,'synthesize',synthesize);monkeypatch.setenv('YANDEX_API_KEY','mock')
 cmd=SimpleNamespace(message=message,request_id='followup',device_id='',modes={},broadcast=False)
 tid=asyncio.run(orch.run_turn(cmd,user,chat,forbidden))['task_id']
 response=client.post(f'/api/voice/tasks/{tid}/speech',headers={'X-Token':user['token']})
 assert response.status_code==200 and tts==[speech] and len(calls)==1
 assert tasks[tid]['answer']==answer.strip() and tasks[tid]['dialogue_intent']=='conversation'


def test_explicit_full_read_survives_restore_without_editor(client,monkeypatch):
 user=db.create_user('cq02-reader');chat=db.create_chat(user['id'],'read')['id'];answer=REPLIES[3]['answer'];tts=[]
 async def decide(*args,**kwargs):return orch.Decision(intent='conversation',answer=answer,spoken_response=SUMMARIES[2916]),{}
 async def forbidden(*args,**kwargs):raise AssertionError('No Worker or editor for explicit full read')
 async def synthesize(text):tts.append(text);return b'mock-ogg'
 monkeypatch.setattr(orch,'decide',decide);monkeypatch.setattr(voice,'shorten_answer',forbidden)
 monkeypatch.setattr(voice,'synthesize',synthesize);monkeypatch.setenv('YANDEX_API_KEY','mock')
 cmd=SimpleNamespace(message='Прочитай полностью',request_id='full',device_id='',modes={},broadcast=False)
 tid=asyncio.run(orch.run_turn(cmd,user,chat,forbidden))['task_id'];headers={'X-Token':user['token']}
 for repeat in range(2):
  tts.clear();first=client.post(f'/api/voice/tasks/{tid}/speech',headers=headers)
  assert first.status_code==200
  for part in range(1,int(first.headers['X-Voice-Parts'])):
   assert client.post(f'/api/voice/tasks/{tid}/speech?part={part}',headers=headers).status_code==200
  assert ' '.join(tts)==' '.join(voice.answer_parts(answer,keep_inline=True))
  assert tasks[tid]['speech_diagnostics']['selection']['mode']=='explicit_full'
  assert tasks[tid]['speech_diagnostics']['selection']['editorial_used'] is False
  tasks.pop(tid);assert client.get(f'/api/tasks/{tid}',headers=headers).status_code==200
