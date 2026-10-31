"""Read-only production diagnosis reproduced using synthetic Goal data only."""
import asyncio
import copy
import json

import pytest
from server import database as db, goal_completion as completion, worker_scheduler
from server.goal_presentation import goal_presentation
from server.routers import chats
from test_orchestrator_worker import owners
from test_v3_goal_coverage_and_speech import origin


SHORT = 'Дописал «Привет» в конец mixed.txt.'
TECHNICAL = ('Дописал «Привет» в конец mixed.txt.\n'
    'device_id=pc; C:\\Demo\\mixed.txt; SHA-256 ' + 'a'*64 + '\n'
    'До: 41 байт, UTF-8 с BOM efbbbf, CRLF/LF. После: 53 байта.\n'
    'Первая попытка replacement_source_missing; точная замена успешна.\n'
    'Других потребителей файла не проверял.')


def goal(owner, status='success'):
    return {'goal_id':'ff021','revision':1,'owner_user_id':owner['id'],'chat_id':owner['chat_id'],
        'original_request':'«Привет» в конец mixed.txt','status':status,'terminal':True,
        'goal_completed':status=='success','tasks':[{'task_id':'edit','required_for_goal':True,
            'status':status,'execution_scope':'general',
            'objective':'Дописать «Привет» в конец mixed.txt, сохранив существующее содержимое.',
            'assignment_scope':'Конец файла mixed.txt','assignment_constraints':['Сохранить BOM, CRLF и остальные байты'],
            'unresolved':[],'recommendations':['Можно проверить потребителей файла'],
            'coverage_observation':{'coverage':'complete','reason':TECHNICAL,'evidence_refs':['step_1']},
            'result':{'sha256':'frozen'},'report':{'status':status,'goal_completed':status=='success',
                'summary':TECHNICAL,'evidence_refs':['step_1'],'artifacts':[{'path':'C:/Demo/mixed.txt',
                    'name':'mixed.txt','device_id':'pc','sha256':'a'*64,'size_bytes':53,'verified':True}]}}]}


def provider(monkeypatch, answer=None):
    calls=[]
    monkeypatch.setattr(completion,'load_llm_config',lambda:{'model':'mock'})
    async def fake(*args,**kwargs):
        material=json.loads(args[3][-1]['content'])['untrusted_evidence']; calls.append(material)
        # Echo stress fixture: technical summary at input makes it into output.
        # This tests data flow, not the quality of a real language model.
        text=answer or (TECHNICAL if 'summary' in material['worker_outcomes'][0] else SHORT)
        return {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'function':{
            'name':'goal_completion_reply','arguments':json.dumps({'answer':text,'spoken_response':SHORT})}}]}}]}
    monkeypatch.setattr(completion,'_chat_completion_request',fake)
    return calls


def test_simple_success_supplies_compact_facts_and_preserves_all_evidence(owners,monkeypatch):
    owner,_=owners; g=goal(owner); before=copy.deepcopy(g); origin(owner,g)
    calls=provider(monkeypatch)
    reply=asyncio.run(completion.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id']))
    assert len(calls)==1 and reply['message']==reply['spoken_message']==SHORT
    assert calls[0]['reply_mode']=='action_result'
    payload=json.dumps(calls[0],ensure_ascii=False)
    for term in ['SHA-256','CRLF','BOM','53','device_id','replacement_source_missing','потребителей','C:/Demo']:
        assert term not in payload
    assert calls[0]['worker_outcomes'][0]['completed_action']==g['tasks'][0]['objective']
    assert calls[0]['files']==[{'name':'mixed.txt'}]
    assert reply['result_artifacts'][0]['sha256']=='a'*64
    assert reply['reply_metrics']['reply_mode']=='action_result' and g==before


def test_short_answer_survives_cache_history_and_runtime_restore(owners,monkeypatch):
    from server import voice
    from server.routers import voice as voice_routes
    from server.runtime_state import tasks
    owner,_=owners; g=goal(owner); mid=origin(owner,g); calls=provider(monkeypatch)
    spoken=[]
    monkeypatch.setattr(voice_routes,'get_current_user',lambda _:owner)
    monkeypatch.setattr(voice_routes,'check_rate_limit',lambda _:True)
    monkeypatch.setattr(voice,'speech_configured',lambda:True)
    async def synthesize(text):
        spoken.append(text)
        return b'fake-audio'
    async def forbidden(*args,**kwargs):
        raise AssertionError('Speech must reuse the saved short reply without another LLM')
    monkeypatch.setattr(voice,'synthesize',synthesize)
    monkeypatch.setattr(voice,'shorten_answer',forbidden)
    tasks['turn']={'task_id':'turn','user_id':owner['id'],'chat_id':owner['chat_id'],
        'status':'done','kind':'orchestrator','result_first':True,'goal_id':g['goal_id'],'answer':''}
    with db.get_db() as c:
        row=c.execute('SELECT task_metadata FROM messages WHERE id=?',(mid,)).fetchone()
        metadata=json.loads(row[0]); metadata['taskKind']='orchestrator'
        c.execute('UPDATE messages SET task_metadata=? WHERE id=?',(json.dumps(metadata),mid))
    monkeypatch.setattr(chats,'get_current_user',lambda _:owner)
    monkeypatch.setattr(worker_scheduler,'goal_view',lambda *args:copy.deepcopy(g))
    async def scenario():
        first=await completion.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id'])
        for _ in range(2):
            history=await chats.api_get_messages(owner['chat_id'],None)
            item=history['messages'][0]
            assert item['content']==item['conversationalResponse']==item['goalPresentation']['message']==SHORT
        completion._LOCKS.clear()
        again=await completion.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id'])
        assert first['message']==again['message']==again['spoken_message']==SHORT and len(calls)==1
        assert (await voice_routes.task_speech('turn',None,part=0)).status_code==200
        tasks.pop('turn')  # The voice endpoint now restores the turn from SQLite.
        assert (await voice_routes.task_speech('turn',None,part=0)).status_code==200
        assert spoken==[SHORT,SHORT] and len(calls)==1
        with db.get_db() as c:
            assert c.execute('SELECT count(*) FROM messages').fetchone()[0]==1
            meta=json.loads(c.execute('SELECT task_metadata FROM messages WHERE id=?',(mid,)).fetchone()[0])
            assert meta['goalCompletionSource']=='orchestrator_llm'
    asyncio.run(scenario())


@pytest.mark.parametrize('status',['partial','unknown','failed'])
def test_incomplete_outcome_keeps_substantive_limitations(owners,monkeypatch,status):
    owner,_=owners; g=goal(owner,status); origin(owner,g)
    g['tasks'][0]['unresolved']=['Запись не подтверждена; результат неизвестен.']
    g['tasks'][0]['report']['summary']='Часть данных прочитана. '+g['tasks'][0]['unresolved'][0]
    answer='Прочитал файл, но дописывание подтвердить не удалось.'
    calls=provider(monkeypatch,answer)
    reply=asyncio.run(completion.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id']))
    assert calls[0]['reply_mode']=='substantive_result'
    assert calls[0]['worker_outcomes'][0]['unresolved']==g['tasks'][0]['unresolved']
    assert reply['message']==answer and reply['status']==status and not reply['goal_completed']


@pytest.mark.parametrize('user_request,scope',[
    ('Исследуй журнал и объясни причины проблем.','analysis'),
    ('Сравни A.txt, B.txt и C.txt.','general'),
    ('Проверь, всё ли нормально.','general'),
    ('Посмотри репозиторий.','general'),
    ('Покажи SHA-256 и подробный технический отчёт по mixed.txt.','general'),
    ('Analyze the repository and explain the findings.','general'),
])
def test_substantive_requests_keep_full_findings_and_unlimited_written_length(owners,monkeypatch,user_request,scope):
    owner,_=owners; g=goal(owner); origin(owner,g)
    g['original_request']=user_request; g['tasks'][0]['execution_scope']=scope
    answer='\n\n'.join(f'{i}. Причина, подтверждённые данные и ограничение.' for i in range(1,100))
    g['tasks'][0]['report']['summary']=answer
    calls=provider(monkeypatch,answer)
    reply=asyncio.run(completion.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id']))
    assert calls[0]['reply_mode']=='substantive_result'
    assert calls[0]['worker_outcomes'][0]['summary']==answer
    assert len(answer)>2000 and reply['message']==answer and len(reply['spoken_message'])<=420


def test_mixed_analysis_member_and_remaining_questions_are_not_compacted(owners,monkeypatch):
    owner,_=owners; g=goal(owner); origin(owner,g)
    analysis=copy.deepcopy(g['tasks'][0]);analysis.update(task_id='analysis',execution_scope='analysis',objective='Исследовать данные')
    g['tasks'].append(analysis); calls=provider(monkeypatch,'Файл изменён. Выводы исследования сохранены.')
    asyncio.run(completion.present_completed_goal(g,goal_presentation(g),owner['id'],owner['chat_id']))
    assert calls[0]['reply_mode']=='substantive_result' and len(calls[0]['worker_outcomes'])==2
    assert all('summary' in item for item in calls[0]['worker_outcomes'])


@pytest.mark.parametrize('field,value',[
    ('unresolved',['Результат не удалось подтвердить']),
    ('coverage_observation',{'coverage':'partial','reason':'Не все результаты получены'}),
    ('execution_scope','analysis'),
])
def test_compact_projection_cannot_discard_declared_gaps_or_analysis(owners,field,value):
    owner,_=owners; g=goal(owner); g['tasks'][0][field]=value
    before=copy.deepcopy(g)
    material=completion._narration_material(g,goal_presentation(g))
    assert material['reply_mode']=='substantive_result'
    assert material['worker_outcomes'][0]['summary']==TECHNICAL and g==before


def test_two_simple_results_are_compact_but_superseded_members_are_not_narrated(owners):
    owner,_=owners; g=goal(owner)
    second=copy.deepcopy(g['tasks'][0]);second.update(task_id='create',objective='Создать beta.txt')
    old=copy.deepcopy(second);old.update(task_id='old-analysis',required_for_goal=False,execution_scope='analysis')
    g['tasks'].extend([second,old])
    material=completion._narration_material(g,goal_presentation(g))
    assert material['reply_mode']=='action_result' and len(material['worker_outcomes'])==2
    assert material['worker_outcomes'][1]['completed_action']=='Создать beta.txt'
