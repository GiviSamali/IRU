"""Speech is generated for intent; no filename stripping or extra model call."""
import asyncio
import json

import pytest
from server import goal_completion as completion, voice, worker_scheduler
from server.goal_presentation import goal_presentation
from server.routers import voice as routes
from server.runtime_state import tasks
from test_orchestrator_worker import owners
from test_v3_goal_coverage_and_speech import origin
from test_ff021_narration import goal


@pytest.mark.parametrize('user_request,written,spoken',[
    ('Дописать Привет в конец mixed.txt','Дописал «Привет» в конец mixed.txt.','Дописал «Привет» в конец файла.'),
    ('Создай документ PDF','Создал report.pdf.','Документ PDF готов.'),
    ('Назови расширение mixed.txt','Расширение файла — txt.','Расширение файла — txt.'),
    ('Назови точное имя файла','Имя файла — mixed.txt.','Имя файла — mixed.txt.'),
    ('Назови полный путь к mixed.txt',r'Файл сохранён: C:\Demo\mixed.txt',r'Путь к файлу: C:\Demo\mixed.txt'),
    ('Где сохранён файл?',r'Файл сохранён: /home/demo/mixed.txt',r'Файл сохранён: /home/demo/mixed.txt'),
])
def test_goal_speech_intent_survives_cache_and_actual_tts_route(owners,monkeypatch,user_request,written,spoken):
    owner,_=owners; g=goal(owner);g['original_request']=user_request;origin(owner,g)
    calls=[];audio=[]
    monkeypatch.setattr(completion,'load_llm_config',lambda:{'model':'mock'})
    async def fake(*args,**kwargs):
        calls.append(args[3])
        assert 'Не зачитывай пути и имена файлов с расширениями' in args[3][0]['content']
        assert 'прямо спрашивает путь, имя, расширение' in args[3][0]['content']
        return {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{
            'name':'goal_completion_reply','arguments':json.dumps({'answer':written,'spoken_response':spoken})}}]}}]}
    monkeypatch.setattr(completion,'_chat_completion_request',fake)
    monkeypatch.setattr(routes,'get_current_user',lambda _:owner)
    monkeypatch.setattr(routes,'check_rate_limit',lambda _:True)
    monkeypatch.setattr(worker_scheduler,'goal_view',lambda *args:g)
    monkeypatch.setattr(voice,'speech_configured',lambda:True)
    async def synthesize(text):audio.append(text);return b'fake'
    async def forbidden(*args,**kwargs):raise AssertionError('No additional speech LLM')
    monkeypatch.setattr(voice,'synthesize',synthesize)
    monkeypatch.setattr(voice,'shorten_answer',forbidden)
    tasks['turn']={'task_id':'turn','user_id':owner['id'],'chat_id':owner['chat_id'],
        'kind':'orchestrator','result_first':True,'goal_id':g['goal_id'],'status':'done','message':'Диалог','answer':''}
    async def scenario():
        reply=await completion.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id'])
        assert reply['message']==written and reply['spoken_message']==spoken
        assert (await routes.task_speech('turn',None,part=0)).status_code==200
        tasks.pop('turn')
        assert (await routes.task_speech('turn',None,part=0)).status_code==200
        assert audio==[spoken,spoken] and len(calls)==1
    asyncio.run(scenario())


def test_supplied_path_does_not_mean_request_to_read_it_and_format_checks_stay():
    path=r'Путь к файлу: C:\Demo\mixed.txt'
    assert not voice.conversational_speech(path,request=r'Измени C:\Demo\mixed.txt')
    assert not voice.conversational_speech(path,request='Не озвучивай полный путь')
    assert voice.conversational_speech(path,request='Озвучь полный путь')==path
    assert not voice.conversational_speech('<script>bad</script>',request='Назови полный путь')
