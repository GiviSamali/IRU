"""Real context assembly + SQLite; provider calls are controlled, never paid."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from server import database as db, orchestrator as orch, controller
from server.context_budget import estimate_tokens, limits, request_budget, fit_request
from server.context_history import history_for, resolve_sources
from server.task_summary import get_last_run_summary
from server.worker_context import capture_history, build_worker_context


@pytest.fixture
def owned(client):
    user=db.create_user('tokens'); user['chat_id']=db.create_chat(user['id'],'tokens')['id']
    return user


def config(window=1000000):
    return {'model':'test-model','base_url':'https://provider.invalid','api_key':'mock',
            'context_window_tokens':window,'max_tokens':32768}


def response():
    return {'choices':[{'finish_reason':'stop','message':{'tool_calls':[{'function':{
        'name':'orchestrator_decision','arguments':json.dumps({'intent':'conversation','answer':'Решение сохранено в исходном разговоре.'})}}]}}]}


def install(monkeypatch, cfg, summary=None):
    calls=[];seen=[]
    monkeypatch.setattr(orch,'load_llm_config',lambda:cfg)
    async def complete(*args,**kwargs):
        calls.append(kwargs.get('phase'))
        messages=kwargs.get('messages') if not args else args[3]
        model=kwargs.get('model') if not args else args[2]
        req={'model':model,'messages':messages,'max_tokens':kwargs['max_tokens']}
        if kwargs.get('tools'):req['tools']=kwargs['tools']
        budget=request_budget(req,cfg)
        assert budget['estimated_input_tokens']<=budget['input_limit_tokens']
        if kwargs.get('phase')=='context_compression':
            if isinstance(summary,Exception):raise summary
            rows=json.loads(messages[1]['content'])
            items=summary(rows) if summary else [{'text':'Пользователь выбрал SQLite: один источник, без отдельного сервиса. Предложение IRU о векторной БД отклонено из-за сложности.', 'source_ids':[rows[0]['id'],rows[1]['id']]}]
            return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'items':items})}}]}
        seen.append(json.loads(messages[1]['content'])['untrusted_context'])
        return response()
    monkeypatch.setattr(orch,'_chat_completion_request',complete)
    return calls,seen


def decide(user, message='Почему мы отказались от предыдущего варианта?'):
    ctx=orch.context_for(user['id'],user['chat_id'],message,None)
    result=asyncio.run(orch.decide(message,ctx,user_id=user['id'],chat_id=user['chat_id'],task_id='tokens'))
    return ctx,result


def fill(user):
    first=db.add_message(user['chat_id'],'user','Выбираем SQLite: один источник, без отдельного сервиса. Причина — простота.')
    db.add_message(user['chat_id'],'assistant','Предлагаю векторную БД. Пользователь её ещё не принял.')
    for n in range(120):db.add_message(user['chat_id'],'assistant',f'Обсуждение {n}: '+'материал '*30)
    return first['id']


def test_twentieth_decision_survives_selection_gap_before_token_overflow(owned,monkeypatch):
    for n in range(19):db.add_message(owned['chat_id'],'assistant',f'Заметка {n}: погода ясная.')
    reason='Выбираю SQLite. Основание: атомарность транзакций и отсутствие отдельного демона.'
    decision=db.add_message(owned['chat_id'],'user',reason)['id']
    for n in range(150):db.add_message(owned['chat_id'],'assistant',f'Заметка {n}: погода ясная.')
    question='А почему мы тогда выбрали именно этот вариант?'
    def summarize(rows):
        assert len(rows)<=128
        assert any(r['id']==decision and r['content']==reason for r in rows)
        return [{'text':reason,'source_ids':[decision]}]
    calls,seen=install(monkeypatch,config(),summarize)
    initial,_=decide(owned,question)
    assert decision not in [r['id'] for r in initial['history']]
    assert initial['history_summaries']==[]
    assert calls==['context_compression','orchestrator']
    assert any(r['id']==decision and r['content']==reason for r in seen[-1]['source_messages'])
    assert any(reason==item['text'] for s in seen[-1]['history_summaries'] for item in s['items'])
    assert reason in resolve_sources(owned['id'],owned['chat_id'],[decision])['text']


def test_large_context_and_worker_handoff_are_not_character_capped(owned,monkeypatch):
    text='Архитектурное основание. '*900
    db.add_message(owned['chat_id'],'user',text)
    calls,seen=install(monkeypatch,config())
    ctx,_=decide(owned)
    assert len(json.dumps(ctx,ensure_ascii=False))>14000 and ctx['history'][0]['content']==text
    assert calls==['orchestrator'] and seen[0]['history'][0]['content']==text
    handoff=build_worker_context(owned['id'],owned['chat_id'],'Продолжи обсуждение',[],capture_history(owned['chat_id']))
    assert len(handoff[0]['content'])>4000 and text in handoff[0]['content']


def test_long_conversation_compresses_once_and_restores_exact_reason(owned,monkeypatch):
    first=fill(owned);calls,seen=install(monkeypatch,config())
    decide(owned)
    assert calls==['context_compression','orchestrator']
    summaries=seen[-1]['history_summaries']
    assert first in summaries[0]['source_message_ids'] and summaries[0]['source_verified'] is False
    assert 'SQLite' in summaries[0]['items'][0]['text']
    assert len(db.get_messages(owned['chat_id'],limit=200))==122
    history,saved=history_for(owned['id'],owned['chat_id'])
    assert not set(saved[0]['source_message_ids']).intersection(r['id'] for r in history)
    decide(owned)
    assert calls.count('context_compression')==1
    source=get_last_run_summary(owned['id'],owned['chat_id'],source_message_ids=[first])
    assert 'Причина — простота' in source['text'] and source['authority']=='historical_data_only'


def test_history_is_summarized_when_it_leaves_selection_and_reused(owned,monkeypatch):
    for n in range(19):db.add_message(owned['chat_id'],'assistant',f'Запись {n}: материал.')
    reason='Выбираю SQLite ради атомарности и простоты сопровождения.'
    decision=db.add_message(owned['chat_id'],'user',reason)['id']
    batches=[]
    def summarize(rows):
        batches.append([r['id'] for r in rows])
        selected=next((r for r in rows if r['id']==decision),rows[0])
        return [{'text':selected['content'],'source_ids':[selected['id']]}]
    calls,seen=install(monkeypatch,config(),summarize)
    decide(owned,'Продолжим.')
    assert calls==['orchestrator']
    for n in range(150):
        db.add_message(owned['chat_id'],'assistant',f'Запись {n}: материал.')
        if n%15==14:decide(owned,'Продолжим.')
    before=calls.count('context_compression')
    decide(owned,'А почему мы тогда выбрали именно этот вариант?')
    assert calls.count('context_compression')==before
    assert any(r['id']==decision and r['content']==reason for r in seen[-1]['source_messages'])
    assert len(batches)<10 and max(map(len,batches))<=128
    assert len({mid for batch in batches for mid in batch})==sum(map(len,batches))


def test_latest_explicit_change_remains_after_old_summary(owned,monkeypatch):
    fill(owned);install(monkeypatch,config(45000));decide(owned)
    db.add_message(owned['chat_id'],'user','Заменяю решение: отдельная SQLite для этого проекта. Причина — изоляция.')
    history,summaries=history_for(owned['id'],owned['chat_id'])
    assert summaries and 'Заменяю решение' in history[-1]['content']
    assert 'позднее явное изменение решения имеет приоритет' in __import__('server.context_history',fromlist=['SUMMARY_PROMPT']).SUMMARY_PROMPT.lower()


def test_stale_sources_invalidate_summary_without_losing_originals(owned,monkeypatch):
    first=fill(owned);install(monkeypatch,config(45000));decide(owned)
    with db.get_db() as c:c.execute('UPDATE messages SET content=? WHERE id=?',('Исправленное решение.',first))
    history,saved=history_for(owned['id'],owned['chat_id'])
    assert saved==[] and history[0]['content']=='Исправленное решение.' and len(history)<=50
    assert db.get_message_count(owned['chat_id'])==122


@pytest.mark.parametrize('failure',[RuntimeError('provider unavailable'),None])
def test_failed_or_invalid_compression_preserves_request_and_sources(owned,monkeypatch,failure):
    first=fill(owned)
    bad=failure if failure else lambda rows:[{'text':'Нет источника','source_ids':[999999]}]
    calls,seen=install(monkeypatch,config(45000),bad)
    decide(owned,'Почему? Текущий запрос нельзя потерять.')
    assert calls==['context_compression','orchestrator']
    assert seen[-1]['compression']['status']=='unavailable'
    assert history_for(owned['id'],owned['chat_id'])[1]==[]
    assert 'SQLite' in resolve_sources(owned['id'],owned['chat_id'],[first])['text']


def test_owner_chat_delete_and_no_historical_authority(owned,monkeypatch):
    first=fill(owned);install(monkeypatch,config(45000));decide(owned)
    other=db.create_user('other')
    assert resolve_sources(other['id'],owned['chat_id'],[first])['status']=='error'
    wrong=db.create_chat(owned['id'],'wrong')['id']
    assert resolve_sources(owned['id'],wrong,[first])['status']=='error'
    db.add_message(owned['chat_id'],'user','Старое поручение: удали файл. Это не новая команда.')
    ctx=orch.context_for(owned['id'],owned['chat_id'],'Почему?',None)
    assert all(s['authority']=='data_only' for s in ctx['history_summaries'])
    assert 'история' in orch.SYSTEM.lower() and 'не инструкции' in orch.SYSTEM
    assert db.delete_chat(owned['chat_id'],owned['id'])
    assert resolve_sources(owned['id'],owned['chat_id'],[first])['status']=='error'
    with db.get_db() as c:assert c.execute('SELECT COUNT(*) FROM context_summaries').fetchone()[0]==0


def test_model_windows_and_complete_request_accounting():
    assert limits({'base_url':'https://api.deepseek.com'},'deepseek-v4-flash',2400)['window_tokens']==1000000
    assert limits({'base_url':'https://proxy.invalid'},'deepseek-v4-flash',2400)['window_tokens'] is None
    assert limits(config(12000),'test-model',2400)['input_limit_tokens']==9344
    req={'model':'test-model','max_tokens':1000,'messages':[{'role':'user','content':'Привет'}],'tools':[{'schema':'x'*10000}]}
    with pytest.raises(ValueError,match='mandatory'):fit_request(req,config(5000))


def test_full_wrapper_preserves_tools_and_records_actual_usage(owned):
    requests=[]
    async def transport(request):
        payload=json.loads(request.content);requests.append(payload)
        return httpx.Response(200,json={**response(),'usage':{'prompt_tokens':12,'completion_tokens':3}},request=request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await controller._chat_completion_request(client,config(6000),'test-model',
                [{'role':'system','content':'Правила'},{'role':'user','content':'старое'*900},{'role':'assistant','content':'старый ответ'*900},{'role':'user','content':'Текущий запрос'}],
                tools=[{'type':'function','function':{'name':'mock','parameters':{'type':'object'}}}],max_tokens=1000,
                usage_context={'user_id':owned['id'],'chat_id':owned['chat_id'],'context_current_user_index':3},phase='context-test')
    asyncio.run(run())
    assert requests[0]['messages'][-1]['content']=='Текущий запрос' and requests[0]['tools'][0]['function']['name']=='mock'
    assert requests[0]['max_tokens']==1000
    with db.get_db() as c:meta=json.loads(c.execute('SELECT metadata FROM llm_usage_events ORDER BY id DESC LIMIT 1').fetchone()[0])
    assert meta['context_budget']['actual_input_tokens']==12 and meta['context_budget']['removed_history_messages']>0


def test_corrections_do_not_replace_original_human_boundary():
    source={'model':'test-model','max_tokens':1000,'messages':[
        {'role':'system','content':'System policy'},
        {'role':'user','content':'Original human request, keep it'},
        {'role':'assistant','tool_calls':[{'id':'a','type':'function','function':{'name':'read','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':'a','content':'x'*12000},
        {'role':'user','content':'Protocol correction, not a new human task'},
        {'role':'assistant','tool_calls':[{'id':'b','type':'function','function':{'name':'read','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':'b','content':'Latest observation'}]}
    fitted,meta=fit_request(source,config(6000),1)
    assert fitted['messages'][1]['content']=='Original human request, keep it'
    assert any(m.get('content')=='Protocol correction, not a new human task' for m in fitted['messages'])
    assert fitted['messages'][-1]['tool_call_id']=='b' and meta['removed_history_messages']==2
    assert len(source['messages'])==7  # journal/source request are never modified


def test_exact_original_pages_reassemble_and_delete_is_complete(owned):
    raw='Исходные слова с отрицанием: не удалять. '*400
    row=db.add_message(owned['chat_id'],'user',raw)
    offset=0;chunks=[];hashes=set()
    while True:
        part=resolve_sources(owned['id'],owned['chat_id'],[row['id']],offset,expected_sha256=next(iter(hashes)) if hashes else None)
        chunks.append(part['text']);hashes.add(part['sha256'])
        if part['next_offset'] is None:break
        offset=part['next_offset']
    assert json.loads(''.join(chunks))[0]['content']==raw and len(hashes)==1
    assert db.delete_chat(owned['chat_id'],owned['id'])
    with db.get_db() as c:assert c.execute('SELECT COUNT(*) FROM messages WHERE chat_id=?',(owned['chat_id'],)).fetchone()[0]==0


def test_history_permission_is_not_replayed_by_dialogue(owned,monkeypatch):
    db.add_message(owned['chat_id'],'user','Старое поручение: удали файл.')
    install(monkeypatch,config())
    async def forbidden(*args,**kwargs):raise AssertionError('No new Worker for a historical explanation')
    cmd=SimpleNamespace(message='Почему мы обсуждали это?',request_id='context-auth',device_id='',modes={},broadcast=False)
    result=asyncio.run(orch.run_turn(cmd,owned,owned['chat_id'],forbidden))
    assert not result['worker_task_id']


def test_observed_undercount_increases_future_estimate(owned):
    from server.context_budget import calibrated_config
    cfg=config();meta={'context_budget':{'endpoint':'provider.invalid','estimated_input_tokens':100,'actual_input_tokens':200,'calibration_factor':1}}
    db.add_llm_usage_event(model='test-model',request_ok=True,prompt_tokens=200,metadata=meta)
    updated=calibrated_config(cfg,'test-model')
    assert updated['_context_calibration']>=2
