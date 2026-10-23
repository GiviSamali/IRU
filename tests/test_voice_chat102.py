"""Replay real written answers; never infer TTS success from HTTP status alone."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import database as db, orchestrator as orch, voice, controller
from server.runtime_state import tasks


REPLIES=json.loads((Path(__file__).parent/'fixtures'/'voice_chat102.json').read_text(encoding='utf-8-sig'))
# Original rejected candidates were not saved. These are explicit test candidates,
# not a reconstruction of historical model output.
SUMMARIES={
 2910:'Компьютер givi на связи.',
 2912:'Непрерывным должно быть состояние: цели, объекты и решения. Полный разговор можно хранить в архиве, а для работы использовать короткую память. Отменённые решения нужно отличать от действующих.',
 2914:REPLIES[2]['accepted_spoken_response'],
 2916:'История хранит сказанное, рабочая память — то, что сейчас в силе. Сжатие теряет оговорки, причины решений и отрицательный опыт. Поэтому полный текст нужен для сверки: иначе предположение превращается в факт.',
 2918:'Файл создан, но содержимое не проверено. Существование подтверждено, соответствие запросу — нет: это частичный результат. Следующим шагом предложила бы проверку содержимого.',
 2920:'Непрерывность держится на состоянии, а полный текст нужен для сверки. Остаётся вопрос, как проверять записи памяти. Если файл создан, но содержимое не проверено, это частичный результат.',
}


@pytest.mark.parametrize('reply',REPLIES,ids=lambda r:str(r['message_id']))
@pytest.mark.parametrize('mode',['recorded','primary','different_meaning','concise'])
def test_real_six_deliver_content_and_owner_only_diagnostics(client,monkeypatch,reply,mode):
 user=db.create_user('chat102-replay');chat=db.create_chat(user['id'],'replay')['id']
 candidate=(reply['accepted_spoken_response'] or '') if mode=='recorded' else SUMMARIES[reply['message_id']] if mode in {'primary','concise'} else 'Готово. Удалено extra_file.txt.'
 # Mock brevity examples verify preservation, not real-model style quality.
 # The explicit in-depth request retains its full written analysis.
 answer=SUMMARIES[reply['message_id']] if mode=='concise' and reply['message_id']!=2916 else reply['answer']
 calls=[];spoken=[]
 async def completion(*args,**kwargs):
  calls.append(kwargs)
  if kwargs.get('phase')=='voice_brief':
   return {'choices':[{'finish_reason':'stop','message':{'content':SUMMARIES[reply['message_id']]}}]}
  return {'choices':[{'finish_reason':'stop','message':{'tool_calls':[{'function':{
   'name':'orchestrator_decision','arguments':json.dumps({'intent':'conversation','answer':answer,'spoken_response':candidate})}}]}}]}
 async def forbidden(*args,**kwargs):raise AssertionError('No Worker in ordinary dialogue')
 async def synthesize(text):spoken.append(text);return b'mock-ogg'
 monkeypatch.setattr(orch,'_chat_completion_request',completion)
 monkeypatch.setattr(orch,'load_llm_config',lambda:{'model':'mock'})
 monkeypatch.setattr(controller,'load_llm_config',lambda:{'model':'deepseek-v4-flash'})
 monkeypatch.setattr(controller,'_chat_completion_request',completion)
 monkeypatch.setattr(voice,'synthesize',synthesize)
 monkeypatch.setenv('YANDEX_API_KEY','mock')
 cmd=SimpleNamespace(message=reply['request'],request_id='chat102-replay',device_id='',modes={},broadcast=False)
 result=asyncio.run(orch.run_turn(cmd,user,chat,forbidden));tid=result['task_id'];task=tasks[tid]
 chosen='primary' if candidate else 'short_source' if len(' '.join(voice.answer_parts(answer,keep_inline=True)))<=420 else 'editorial'
 expected=candidate if chosen=='primary' else ' '.join(voice.answer_parts(answer,keep_inline=True)) if chosen=='short_source' else SUMMARIES[reply['message_id']]
 headers={'X-Token':user['token']}
 first=client.post(f'/api/voice/tasks/{tid}/speech',headers=headers)
 assert first.status_code==200
 count=int(first.headers['X-Voice-Parts'])
 for part in range(1,count):
  assert client.post(f'/api/voice/tasks/{tid}/speech?part={part}',headers=headers).status_code==200
 assert ' '.join(spoken)==expected
 assert all(len(part)<=voice.CHUNK_SIZE for part in spoken)
 assert len(calls)==(2 if chosen=='editorial' else 1) and task['answer']==answer
 assert task['speech_diagnostics']['selection']['mode']==chosen
 assert task['speech_diagnostics']['selection']['editorial_used']==(chosen=='editorial')
 primary=task['speech_diagnostics']['primary']
 assert primary['candidate']==candidate
 assert primary['reason']==('accepted' if candidate else 'missing_candidate')
 # Ordinary history never exposes raw candidates. Diagnostics survive a restart
 # through the existing owner-checked task route.
 assert 'speechDiagnostics' not in db.get_messages(chat)[-1]
 tasks.pop(tid)
 restored=client.get(f'/api/tasks/{tid}',headers=headers)
 assert restored.status_code==200
 assert restored.json()['task']['speech_diagnostics']==task['speech_diagnostics']
 before=len(calls)
 client.post(f'/api/voice/tasks/{tid}/speech',headers=headers)
 assert len(calls)==before
 assert spoken[-1]==expected
 other=db.create_user('not-the-owner')
 assert client.get(f'/api/tasks/{tid}',headers={'X-Token':other['token']}).status_code==404
 assert client.post(f'/api/voice/tasks/{tid}/speech',headers={'X-Token':other['token']}).status_code==404


@pytest.mark.parametrize('raw,reason',[('Не проверено. '*50,'candidate_too_long'),('Ответ \ud800','invalid_encoding')])
def test_malformed_optional_candidate_is_retained_without_changing_decision_schema(client,monkeypatch,raw,reason):
 decision=orch.Decision(intent='conversation',answer='Ответ сохранён.',spoken_response=raw)
 assert decision._spoken_candidate==raw
 if len(raw)>420:assert decision.spoken_response==''
 assert '_spoken_candidate' not in orch.Decision.model_json_schema()['properties']
 assert '_spoken_candidate' not in decision.model_dump()
 user=db.create_user('invalid-speech');chat=db.create_chat(user['id'],'invalid')['id']
 async def decide(*args,**kwargs):return decision,{}
 async def forbidden(*args,**kwargs):raise AssertionError('No worker or editor for invalid optional speech')
 monkeypatch.setattr(orch,'decide',decide)
 monkeypatch.setattr(voice,'shorten_answer',forbidden)
 cmd=SimpleNamespace(message='Объясни',request_id='invalid-speech',device_id='',modes={},broadcast=False)
 tid=asyncio.run(orch.run_turn(cmd,user,chat,forbidden))['task_id']
 assert asyncio.run(voice.spoken_parts(tasks[tid]))==[decision.answer]
 tasks.pop(tid)
 diagnostics=orch.restore_dialogue(tid,user['id'])['speech_diagnostics']['primary']
 if reason=='invalid_encoding':
  assert diagnostics['candidate'] is None and json.loads(diagnostics['candidate_json'])==raw
 else:assert diagnostics=={'candidate':raw,'reason':reason}


@pytest.mark.parametrize('answer,speech',[
 ('Файл не удалён.','Файл удалён.'),
 ('Компьютер не в сети.','Компьютер в сети.'),
 ('Программа не запущена.','Программа запущена.'),
 ('Файл создан, но содержимое не проверено.','Готово. Файл создан.'),
 ('Не удалось подтвердить сохранение файла.','Файл сохранён.'),
 ('Файл сохранён, но загрузка ещё идёт.','Файл сохранён.'),
 ('Задача выполнена частично.','Всё сделала.'),
 ('Не могу сказать, выполнено ли действие.','Действие выполнено.'),
])
def test_dialogue_opposite_or_unconfirmed_words_are_not_semantically_filtered(answer,speech):
 check={}
 assert voice.conversational_speech(speech,answer,informational=True,diagnostic=check)==speech
 assert check['reason']=='accepted'


@pytest.mark.parametrize('answer,speech',[
 ('Отменённые решения нужно отличать от действующих, иначе память вводит в заблуждение.','Память должна различать отменённые и действующие решения.'),
 ('Непроверенный аргумент ещё не стал фактом. Полный текст нужен для сверки.','Непроверенный аргумент требует сверки.'),
 ('Частичный контекст и архив решают разные задачи: один нужен для работы, другой для сверки.','Частичный контекст нужен для работы, архив — для сверки.'),
 ('Нельзя терять оговорки при сжатии, поэтому полный текст остаётся в архиве.','При сжатии нельзя терять оговорки.'),
])
def test_analytical_words_do_not_prohibit_shortening(answer,speech):
 diagnostic={}
 assert voice.conversational_speech(speech,answer,informational=True,diagnostic=diagnostic)==speech
 assert diagnostic['reason']=='accepted'
