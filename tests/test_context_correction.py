"""Bounded SQLite reads, explicit human boundaries and stable source pages."""
import asyncio
from contextlib import contextmanager
import json

import pytest

from server import context_history as hist, context_budget as budget, database as db, controller, controller_onboarding
from server.worker_context import capture_history
from test_context_token_budget import owned, config


def trace_db(monkeypatch):
    statements=[];original=db.get_db
    @contextmanager
    def traced():
        with original() as c:
            c.set_trace_callback(statements.append)
            yield c
    monkeypatch.setattr(db,'get_db',traced)
    return statements


def test_bounded_intake_and_exact_sql_source_reads(owned,monkeypatch):
    with db.get_db() as c:
        c.executemany('INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)',
            [(owned['chat_id'],'user','Материал '+str(i)+' '+('x'*500),i) for i in range(500)])
        first=c.execute('SELECT MIN(id) FROM messages WHERE chat_id=?',(owned['chat_id'],)).fetchone()[0]
    statements=trace_db(monkeypatch);selection={}
    history,summaries=hist.history_for(owned['id'],owned['chat_id'],query='zzmissing',selection=selection)
    assert len(history)==34 and not summaries and selection['selection_incomplete'] and selection['total_messages']==500
    assert len(capture_history(owned['chat_id']))==32
    assert len(hist.source_rows(owned['id'],owned['chat_id'],[first]))==1
    reads=[q.lower() for q in statements if q.lower().startswith('select') and 'from messages' in q.lower() and 'content' in q.lower()]
    assert reads and all(' limit ' in q or 'id in (' in q for q in reads)
    assert any('id in (' in q for q in reads)


def test_only_selected_summary_sources_are_validated(owned,monkeypatch):
    first=db.add_message(owned['chat_id'],'user','Выбор и точная причина.')['id']
    rows=hist.source_rows(owned['id'],owned['chat_id'],[first])
    with db.get_db() as c:
        c.execute('INSERT INTO context_summaries VALUES(?,?,?,?,?,?,?,?)',('summary',owned['id'],owned['chat_id'],json.dumps([first]),hist.digest(rows),json.dumps([{'text':'Решение пользователя','source_ids':[first]}]),'[]',1))
        c.executemany('INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)',[(owned['chat_id'],'user','unrelated '+str(i),i+2) for i in range(300)])
    statements=trace_db(monkeypatch)
    history,saved=hist.history_for(owned['id'],owned['chat_id'])
    assert saved[0]['source_message_ids']==[first] and all(r['id']!=first for r in history)
    reads=[q.lower() for q in statements if q.lower().startswith('select') and 'from messages' in q.lower() and 'content' in q.lower()]
    assert all(' limit ' in q or 'id in (' in q for q in reads)


@pytest.mark.parametrize('pointer',[-1,True,1.5,9,0])
def test_invalid_explicit_human_boundary_fails_before_send(pointer):
    request={'model':'test-model','max_tokens':100,'messages':[{'role':'system','content':'Rules'},{'role':'user','content':'Current request'}]}
    with pytest.raises(ValueError,match='invalid_current_human_boundary'):budget.fit_request(request,config(),pointer)


def test_missing_boundary_does_not_guess_user_payload_as_history():
    request={'model':'test-model','max_tokens':1000,'messages':[{'role':'system','content':'Rules'},
        {'role':'user','content':'Evidence payload '+'x'*10000},{'role':'user','content':'JSON protocol repair'}]}
    with pytest.raises(ValueError,match='mandatory'):budget.fit_request(request,config(6000))
    assert len(request['messages'])==3


@pytest.mark.parametrize('field,value',[('context_window_tokens',0),('context_safety_tokens',-1),('context_reasoning_reserve_tokens',-1)])
def test_reserves_cannot_expand_capacity_by_negative_values(field,value):
    cfg=config();cfg[field]=value
    with pytest.raises(ValueError,match='invalid_'):budget.limits(cfg,'test-model',2400)


def test_source_pages_are_exact_for_unicode_nul_and_detect_edits(owned):
    raw=('Не удалять. 😀\x00 "quoted" \\ путь\n'*700)
    mid=db.add_message(owned['chat_id'],'user',raw)['id'];offset=0;parts=[];sha=None
    while True:
        result=hist.resolve_sources(owned['id'],owned['chat_id'],[mid],offset,expected_sha256=sha)
        assert result['status']=='success'
        sha=result['sha256'];parts.append(result['text'])
        if result['next_offset'] is None:break
        offset=result['next_offset']
    assert json.loads(''.join(parts))[0]['content']==raw
    assert hist.resolve_sources(owned['id'],owned['chat_id'],[mid],4000)['error']=='source_revision_required'
    with db.get_db() as c:c.execute('UPDATE messages SET content=? WHERE id=?',('Изменено '+raw,mid))
    assert hist.resolve_sources(owned['id'],owned['chat_id'],[mid],4000,expected_sha256=sha)=={'status':'error','error':'source_changed'}


def test_range_recovers_only_this_chat_and_paginates_message_sets(owned):
    other=db.create_chat(owned['id'],'other')['id'];ids=[]
    with db.get_db() as c:
        for i in range(130):
            c.execute('INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)',(other,'user','PRIVATE OTHER CHAT',i))
            row=c.execute('INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)',(owned['chat_id'],'user','source '+str(i),i))
            ids.append(row.lastrowid)
    span={'first':ids[0],'last':ids[-1]};offset=0;parts=[];sha=None
    while True:
        part=hist.resolve_sources(owned['id'],owned['chat_id'],message_range=span,offset=offset,expected_sha256=sha)
        assert part['status']=='success';parts.append(part['text']);sha=part['sha256']
        if part['next_offset'] is None:break
        offset=part['next_offset']
    rows=json.loads(''.join(parts))
    assert [r['id'] for r in rows]==ids[:128] and 'PRIVATE' not in ''.join(parts)
    assert part['next_source_message_id']==ids[128]
    next_part=hist.resolve_sources(owned['id'],owned['chat_id'],message_range={'first':ids[128],'last':ids[-1]})
    assert next_part['source_message_ids']==ids[128:]


def test_token_diagnostics_remain_valid_json_and_bad_events_do_not_disable_calibration(owned):
    meta={'context_budget':{'endpoint':'provider.invalid','estimated_input_tokens':100,'actual_input_tokens':200,'calibration_factor':1},'large':'x'*4000}
    db.add_llm_usage_event(model='test-model',metadata=meta,request_ok=True)
    db.add_llm_usage_event(model='test-model',metadata='not valid JSON',request_ok=True)
    with db.get_db() as c:
        saved=json.loads(c.execute('SELECT metadata FROM llm_usage_events WHERE metadata LIKE ? ORDER BY id DESC LIMIT 1',('%context_budget%',)).fetchone()[0])
    assert saved['context_budget']['actual_input_tokens']==200 and saved['metadata_omitted']
    assert budget.calibrated_config(config(),'test-model')['_context_calibration']>=2


def test_server_only_loop_passes_real_user_boundary_and_source_options(owned,monkeypatch):
    mid=db.add_message(owned['chat_id'],'user','Точный источник решения.')['id'];seen=[]
    async def complete(**kwargs):
        messages=kwargs['messages'];pointer=kwargs['usage_context']['context_current_user_index']
        if kwargs.get('phase')=='onboarding.answer_auditor':
            assert pointer==1 and json.loads(messages[pointer]['content'])['user_request']=='Почему принято решение?'
            return {'choices':[{'message':{'content':json.dumps({'valid':True,'reason':'Exact owned original read'})}}]}
        assert messages[pointer]['content']=='Почему принято решение?'
        seen.append(kwargs)
        if len(seen)==1:
            return {'choices':[{'message':{'role':'assistant','tool_calls':[{'id':'source','type':'function','function':{
                'name':'system_get_last_run_summary','arguments':json.dumps({'source_message_range':{'first':mid,'last':mid}})}}]}}]}
        assert 'Точный источник решения.' in messages[-1]['content']
        payload={'answer_type':'grounded_report','text':'Причина в исходном сообщении.','basis':['step_1'],
            'self_check':{'depends_on_current_external_state':True,'claims_completed_action':False,'has_sufficient_evidence':True,'missing_evidence_question':''}}
        return {'choices':[{'message':{'role':'assistant','tool_calls':[{'id':'final','type':'function','function':{'name':'answer_text','arguments':json.dumps(payload)}}]}}]}
    monkeypatch.setattr(controller,'_chat_completion_request',complete)
    result=asyncio.run(controller_onboarding.process_onboarding_message('Почему принято решение?',
        chat_history=[{'role':'user','content':'Старое поручение.'},{'role':'assistant','content':'Исторический ответ.'},{'role':'user','content':'Почему принято решение?'}],
        usage_context={'chat_id':owned['chat_id'],'user_id':owned['id'],'worker_execution':True},load_llm_config_fn=lambda:{**config(),'answer_auditor_enabled':True},current_datetime_msk_fn=lambda:'2026-10-24'))
    assert len(seen)==2 and result['answer']=='Причина в исходном сообщении.'


def test_validated_summary_does_not_reload_covered_sources_and_invalidates_in_sql(owned,monkeypatch):
    rows=[]
    with db.get_db() as c:
        for i in range(160):
            cursor=c.execute('INSERT INTO messages(chat_id,role,content,created_at) VALUES(?,?,?,?)',(owned['chat_id'],'user','source '+str(i),i))
            rows.append(cursor.lastrowid)
    ids=rows[30:90];originals=hist.source_rows(owned['id'],owned['chat_id'],ids)
    with db.get_db() as c:c.execute('INSERT INTO context_summaries VALUES(?,?,?,?,?,?,?,?)',
        ('cached',owned['id'],owned['chat_id'],json.dumps(ids),hist.digest(originals),json.dumps([{'text':'Accepted decision','source_ids':[ids[0]]}]),'[]',1))
    assert hist.history_for(owned['id'],owned['chat_id'])[1]
    with db.get_db() as c:assert c.execute('SELECT source_hash FROM context_summaries').fetchone()[0].startswith('v2:')
    def forbidden(*args,**kwargs):raise AssertionError('Cached sources must not be reloaded on ordinary reads')
    monkeypatch.setattr(hist,'source_batches',forbidden)
    assert hist.history_for(owned['id'],owned['chat_id'])[1]
    with db.get_db() as c:
        c.execute('UPDATE messages SET content=? WHERE id=?',('Changed source',ids[0]))
        assert c.execute('SELECT COUNT(*) FROM context_summaries').fetchone()[0]==0
    assert 'Changed source' in hist.resolve_sources(owned['id'],owned['chat_id'],[ids[0]])['text']
