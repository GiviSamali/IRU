"""Archive discovery and durable retry suppression, with no live provider calls."""
import importlib
import asyncio
import json

import pytest

from server import context_history as hist, database as db, controller, controller_onboarding
from server.task_summary import get_last_run_summary
from test_context_token_budget import owned, config, install, decide, fill


def archive(user, count=12):
    chat=user['chat_id']
    with db.get_db() as c:
        c.executemany('INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)',
            [(chat,'user',f'Материал {i}: нейтральная запись.',i) for i in range(1,1401)])
        ids=[r[0] for r in c.execute('SELECT id FROM messages WHERE chat_id=? ORDER BY id',(chat,))]
        reason='Используем WAL: читатели не мешают записи, отдельный сервис не нужен.'
        c.execute('UPDATE messages SET content=? WHERE id=?',(reason,ids[249]))
        for n in range(count):
            sources=ids[n*100:(n+1)*100] if n<14 else [ids[-1]]
            mid=ids[249] if n==2 else sources[0]
            text=reason if n==2 else f'Обзор раздела {n}: '+('архивный материал '*80)
            originals=[dict(r) for r in c.execute('SELECT id,role,content,commands,task_metadata,created_at FROM messages WHERE id IN ('+
                ','.join('?' for _ in sources)+') ORDER BY id',sources)]
            c.execute('INSERT INTO context_summaries VALUES(?,?,?,?,?,?,?,?)',
                (f'archive-{n:02}',user['id'],chat,json.dumps(sources),'v2:'+hist.digest(originals),
                 json.dumps([{'text':text,'source_ids':[mid]}],ensure_ascii=False),'[]',n))
    return ids,reason


def test_vague_question_discovers_third_of_twelve_summaries(owned,monkeypatch):
    ids,reason=archive(owned)
    calls,seen=install(monkeypatch,config())
    decide(owned,'А почему мы тогда выбрали именно этот вариант?')
    ctx=seen[-1]
    assert 'archive-02' not in [s['id'] for s in ctx['history_summaries']]
    directory=ctx['history_selection']['summary_archive']
    entry=next(e for e in directory['entries'] if e['id']=='archive-02')
    assert entry['preview']==reason and len(directory['entries'])==12
    # Main prompt contains bounded previews, not the full archived material.
    assert 'архивный материал '*30 not in json.dumps(directory,ensure_ascii=False)
    saved=get_last_run_summary(owned['id'],owned['chat_id'],**entry['read_arguments'])
    source_ids=json.loads(saved['text'])['items'][0]['source_ids']
    original=get_last_run_summary(owned['id'],owned['chat_id'],source_message_ids=source_ids)
    assert source_ids==[ids[249]] and reason in original['text']
    assert saved['authority']==original['authority']=='historical_data_only'


def test_archive_directory_pages_and_enforces_ownership(owned):
    archive(owned,20)
    page=get_last_run_summary(owned['id'],owned['chat_id'],summary_archive_offset=0)
    assert len(page['entries'])==16 and page['total_summaries']==20
    next_page=get_last_run_summary(owned['id'],owned['chat_id'],**page['next_arguments'])
    assert len(next_page['entries'])==4 and next_page['next_arguments'] is None
    assert not {e['id'] for e in page['entries']}&{e['id'] for e in next_page['entries']}
    other=db.create_user('other')
    for args in ({'summary_archive_offset':0},{'source_summary_id':'archive-02'}):
        assert get_last_run_summary(other['id'],owned['chat_id'],**args)['error']=='source_not_available'
        wrong=db.create_chat(owned['id'],'wrong')['id']
        assert not get_last_run_summary(owned['id'],wrong,**args).get('entries')
    for value in (-1,True,1.5):
        assert hist.summary_archive(owned['id'],owned['chat_id'],value)['error']=='invalid_summary_offset'


def test_summary_text_pages_have_revision_and_sources(owned):
    archive(owned)
    offset=0;sha=None;parts=[]
    while True:
        result=get_last_run_summary(owned['id'],owned['chat_id'],source_summary_id='archive-00',
            report_offset=offset,expected_source_sha256=sha)
        assert result['status']=='success' and len(result['text'])<=4000
        parts.append(result['text']);sha=result['sha256']
        if result['next_offset'] is None:break
        offset=result['next_offset']
    assert json.loads(''.join(parts))['source_message_ids']
    assert hist.summary_archive(owned['id'],owned['chat_id'],summary_id='archive-00',report_offset=1)['error']=='source_revision_required'
    with db.get_db() as c:c.execute('UPDATE context_summaries SET content=? WHERE id=?',
        (json.dumps([{'text':'Изменение','source_ids':[1]}]),'archive-00'))
    assert hist.summary_archive(owned['id'],owned['chat_id'],summary_id='archive-00',expected_sha256=sha)['error']=='source_changed'


@pytest.mark.parametrize('bad',[RuntimeError('offline'),lambda rows:[],lambda rows:[{'text':'нет источника','source_ids':[999999]}]])
def test_failed_fragment_waits_across_turns_and_reload_then_retries(owned,monkeypatch,bad):
    first=fill(owned)
    calls,seen=install(monkeypatch,config(),bad)
    decide(owned)
    assert seen[-1]['compression']['status']=='unavailable'
    with db.get_db() as c:
        failure=json.loads(c.execute('SELECT context_compression_failure FROM chats WHERE id=?',(owned['chat_id'],)).fetchone()[0])
    # A normal new turn may enlarge the pending batch; it must not defeat cooldown.
    db.add_message(owned['chat_id'],'user','Следующий вопрос.')
    importlib.reload(hist)
    decide(owned)
    assert calls.count('context_compression')==1
    assert seen[-1]['compression']['status']=='deferred'
    assert 'SQLite' in hist.resolve_sources(owned['id'],owned['chat_id'],[first])['text']
    with db.get_db() as c:
        failure['retry_at']=0
        c.execute('UPDATE chats SET context_compression_failure=? WHERE id=?',(json.dumps(failure),owned['chat_id']))
    successful,seen=install(monkeypatch,config())
    decide(owned)
    assert successful==['context_compression','orchestrator']
    with db.get_db() as c:
        assert c.execute('SELECT context_compression_failure FROM chats WHERE id=?',(owned['chat_id'],)).fetchone()[0] is None


def test_changed_original_bypasses_failed_fragment_cooldown(owned,monkeypatch):
    first=fill(owned);calls,_=install(monkeypatch,config(),RuntimeError('offline'))
    decide(owned)
    with db.get_db() as c:c.execute('UPDATE messages SET content=? WHERE id=?',('Изменённое решение.',first))
    decide(owned)
    assert calls.count('context_compression')==2


def test_malformed_json_is_not_retried_immediately_or_shared_between_chats(owned,monkeypatch):
    from server import orchestrator as orch
    fill(owned);calls=[]
    monkeypatch.setattr(orch,'load_llm_config',lambda:config())
    from test_context_token_budget import response
    async def broken(*args,**kwargs):
        calls.append(kwargs['phase'])
        if kwargs['phase']=='context_compression':
            return {'choices':[{'finish_reason':'stop','message':{'content':'{broken'}}]}
        return response()
    monkeypatch.setattr(orch,'_chat_completion_request',broken)
    decide(owned);decide(owned)
    assert calls==['context_compression','orchestrator','orchestrator']
    other={**owned,'chat_id':db.create_chat(owned['id'],'other')['id']}
    fill(other);decide(other)
    assert calls.count('context_compression')==2
    db.init_db()  # normal startup migrations preserve the retry deadline
    decide(owned)
    assert calls.count('context_compression')==2
    assert db.delete_chat(owned['chat_id'],owned['id'])


def test_directory_respects_whole_request_budget_and_keeps_page_pointer(owned):
    archive(owned,20)
    directory=hist.summary_archive(owned['id'],owned['chat_id'])
    ctx={'history':[],'history_summaries':[],'history_selection':{'summary_archive':directory}}
    def request(value):return {'model':'test-model','max_tokens':2400,
        'messages':[{'role':'user','content':json.dumps(value,ensure_ascii=False)}]}
    cfg=config(5000)
    bounded=hist.bounded_context(ctx,request,cfg)
    page=bounded['history_selection']['summary_archive']
    assert len(page['entries'])<16
    assert page['next_arguments']=={'summary_archive_offset':len(page['entries'])}
    assert len(directory['entries'])==16  # caller's context remains unchanged


def test_server_controller_reads_archive_summary_then_original(owned,monkeypatch):
    ids,reason=archive(owned);seen=[]
    arguments=[{'summary_archive_offset':0},{'source_summary_id':'archive-02'},{'source_message_ids':[ids[249]]}]
    async def complete(**kwargs):
        messages=kwargs['messages'];seen.append(messages)
        index=len(seen)-1
        if index:
            if index==1:assert 'archive-02' in messages[-1]['content']
            else:assert any(reason in m.get('content','') for m in messages if m['role']=='tool')
        if index<3:
            name='system_get_last_run_summary';args=arguments[index]
        else:
            name='answer_text';args={'answer_type':'grounded_report','text':reason,'basis':['step_3'],
                'self_check':{'depends_on_current_external_state':True,'claims_completed_action':False,
                'has_sufficient_evidence':True,'missing_evidence_question':''}}
        return {'choices':[{'message':{'role':'assistant','tool_calls':[{'id':f'call-{index}',
            'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}]}
    monkeypatch.setattr(controller,'_chat_completion_request',complete)
    result=asyncio.run(controller_onboarding.process_onboarding_message('Почему мы выбрали этот вариант?',
        usage_context={'chat_id':owned['chat_id'],'user_id':owned['id'],'worker_execution':True},
        load_llm_config_fn=lambda:{**config(),'answer_auditor_enabled':False},
        current_datetime_msk_fn=lambda:'2026-10-24'))
    assert len(seen)==4 and result['answer']==reason
