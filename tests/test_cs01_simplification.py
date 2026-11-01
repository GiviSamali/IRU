"""Core simplification: contracts and real projections, no external services."""
from types import SimpleNamespace
import json
import pytest
from server import controller, orchestrator as orch, database as db
from server.controller_prompts import SYSTEM_PROMPT_TEMPLATE, WINDOWS_RULES
from server.runtime_state import tasks
from server.response_presentation import worker_presentation
from test_orchestrator_worker import owners
from server.worker_reports import build_worker_report
from test_tool_only_protocol import _run_case, _message, _tool_call


def test_worker_prompt_has_no_competing_user_speech_or_chunk_write_protocol():
    assert 'РјСѓР¶СЃРєРѕРј СЂРѕРґРµ' not in SYSTEM_PROMPT_TEMPLATE
    assert 'Р¤РћР РњРђРўРР РћР’РђРќРР• РџРРЎР¬РњР•РќРќРћР“Рћ РћРўР’Р•РўРђ' not in SYSTEM_PROMPT_TEMPLATE
    assert 'РґР°Р»СЊС€Рµ append=true' not in SYSTEM_PROMPT_TEMPLATE
    assert '### 2. write_content' not in SYSTEM_PROMPT_TEMPLATE
    assert 'wmctrl' not in WINDOWS_RULES


def route_prompt(monkeypatch):
    from dataclasses import fields
    runtime=SimpleNamespace(**{f.name:'' for f in fields(controller.LLMRuntimeContext)})
    runtime.cfg={'model':'mock'};runtime.mem_user_id=None
    monkeypatch.setitem(tasks,'cs-open',{'window_effects':['open']})
    kw=controller._build_route_kwargs(runtime=runtime,
        user_message='Open a.docx',device_id='pc',device_info={},all_devices={},send_command_fn=None,
        get_file_link_fn=None,chat_history=[],user_id=None,chat_id=None,device_profile=None,modes={},poll_task_id='cs-open')
    return kw['system_msg']


def test_actual_open_route_does_not_override_native_opener(monkeypatch):
    prompt=route_prompt(monkeypatch)
    assert 'limited to existing windows' not in prompt and 'never shell' not in prompt
    assert 'Сам выбирай инструменты' in prompt


def test_entry_points_share_one_worker_presentation():
    t={'task_id':'t','device_ids':['pc'],'status':'done','window_effects':['open'],
       'commands':[{'step_id':'step_1','tool_name':'app.launch','target_device_id':'pc',
                    'result':{'status':'failed','error':'window_not_found','summary':'Window not found'}}]}
    assert worker_presentation(t)==orch.present_worker_result(t)


def test_owned_memory_ranks_normalized_matches_before_limit(owners):
    a,b=owners
    with db.get_db() as c:
        for owner,text in [(a,f'РР РЈ РїСЂРµРґРїРѕС‡С‚РµРЅРёРµ {i}') for i in range(20)]+[
            (a,'Р–СѓСЂРЅР°Р» СЃРµСЂРІРёСЃР° IRU РЅР° СѓР·Р»Рµ alpha'),(b,'Р–СѓСЂРЅР°Р» СЃРµСЂРІРёСЃР° РР РЈ SECRET')]:
            c.execute('INSERT INTO user_memory(user_id,fact_text,category,created_at) VALUES(?,?,?,?)',
                      (str(owner['id']),text,'general',0))
    for query in ('Р–СѓСЂРЅР°Р»Рµ РР РЈ','Р¶СѓСЂРЅР°Р»Р° IRU','Р¶СѓСЂРЅР°Р»РѕРј РР РЈ'):
        selected=orch.relevant_facts(a['id'],query)
        assert 'alpha' in selected[0]['text']
        assert not any('SECRET' in f['text'] for f in selected)


def test_memory_expansion_is_candidate_data_and_never_foreign_authority(owners):
    a,b=owners
    with db.get_db() as c:
        for owner,text in [(a,'Р”Р»СЏ Р¶СѓСЂРЅР°Р»Р° СЃРµСЂРІРёСЃР° IRU: ssh host journalctl -u iru'),(b,'SECRET СЃРµСЂРІРµСЂ')]:
            c.execute('INSERT INTO user_memory(user_id,fact_text,category,created_at) VALUES(?,?,?,0)',
                      (str(owner['id']),text,'general'))
    for query in ('СЃРµСЂРІРµСЂ','СЃРµСЂРІРµСЂРѕРј','СЃРµСЂРІРµСЂРµ','РџРѕРјРЅРёС€СЊ РєРѕРјР°РЅРґС‹ РЅР° СЃРµСЂРІРµСЂРµ?'):
        facts=orch.relevant_facts(a['id'],query)
        assert any('journalctl' in f['text'] and f['retrieval']=='candidate_only' for f in facts)
        assert not any('SECRET' in f['text'] for f in facts)


@pytest.mark.parametrize('extension',['docx','pptx','xlsx','py','js'])
@pytest.mark.parametrize('research_turns',[2,7,12])
def test_research_keeps_assignment_and_budget_without_search_cap(monkeypatch,extension,research_turns):
    import copy
    from server import controller_non_pipeline as loop
    monkeypatch.setattr(db,'get_device_profile',lambda *a,**k:None)
    async def search(query,max_results):return {'results':[{'title':query,'url':'https://example.invalid/','content':'synthetic'}]}
    monkeypatch.setattr(loop,'run_web_search',search)
    tid='research';objective=f'Research, then produce final.{extension}'
    t={'task_id':tid,'goal_id':'g','status':'running','original_request':objective,'proposed_objective':objective,
       'device_ids':['device-1'],'assigned_artifacts':[f'C:/final.{extension}']}
    monkeypatch.setitem(tasks,tid,t)
    replies=[_message(tool_calls=[_tool_call(str(i),'web_search',{'query':f'source {i}'})]) for i in range(research_turns)]
    # Deliberately insufficient result: a series of successful observations is not a file.
    replies.append(_message(tool_calls=[_tool_call('end','answer_text',{'result':'Sources collected; file not created.','unresolved':['file not created']})]))
    observations=[]
    async def provider(**kwargs):
        observations.append(copy.deepcopy(kwargs['messages']))
        return replies.pop(0)
    # _run_case uses the same real controller, with this bounded scripted provider.
    monkeypatch.setattr('test_tool_only_protocol._completion_fn',lambda *a,**k:provider)
    result=_run_case([],poll_task_id=tid,user_message=objective,max_iterations=research_turns+1)
    assert len(observations)==research_turns+1
    for i,messages in enumerate(observations):
        assert objective in messages[0]['content']
        assert f'Worker LLM turns remaining, including this one: {research_turns+1-i}/{research_turns+1}' in messages[1]['content']
        assert sum(m.get('content','').count('Worker LLM turns remaining, including this one:') for m in messages)==1
        assert messages[0]==observations[0][0]
    t.update(status='done',commands=result['commands'],answer=result['answer'])
    assert not build_worker_report(t)['goal_completed']
    assert len([c for c in result['commands'] if c.get('tool_name')=='web_search'])==research_turns


def office_projection(missing_page=False, stale=False):
    path='C:/report.pptx';sha='a'*64
    receipt=dict(path=path,device_id='pc',source_task_id='t',verified=True,created_or_changed=True,
                 size_bytes=1000,sha256=sha,format='pptx',artifact_role='final')
    steps=[dict(step_id='step_1',tool_name='office_pptx',target_device_id='pc',
                result=dict(status='success',action='create',artifact_receipts=[receipt]))]
    for offset in ([0] if missing_page else [0,1]):
        steps.append(dict(step_id=f'step_{len(steps)+1}',tool_name='office_pptx',target_device_id='pc',
            result=dict(status='success',action='inspect',path=path,sha256=sha,offset=offset,total_items=2,
                        next_offset=1 if offset==0 else None,items=[{'text':str(offset)}])))
    if stale:steps[-1]['result']['sha256']='b'*64
    text='Observed content'
    steps.append(dict(step_id='answer',tool_name='answer.text',status='terminal',
                      result=dict(text=text)))
    t=dict(task_id='t',status='done',original_request='request',device_ids=['pc'],assigned_artifacts=[path],answer=text,commands=steps)
    return build_worker_report(t)


@pytest.mark.parametrize('missing_page',[False,True])
def test_office_observation_is_independent_and_missing_page_cannot_be_overruled(missing_page):
    report=office_projection(missing_page)
    assert report['goal_completed']
    assert report['artifacts'][0]['content_observed'] is not missing_page
    assert report['artifacts'][0]['content_verified'] is not missing_page












def test_failed_discovery_does_not_prevent_native_open(monkeypatch):
    monkeypatch.setattr(db,'get_device_profile',lambda *a,**k:None)
    monkeypatch.setitem(tasks,'open-recovery',dict(task_id='open-recovery',window_effects=['open']))
    sent=[]
    async def send(device,action,params):
        sent.append(action)
        if action=='window.control':return dict(status='failed',error='window_not_found')
        return dict(status='launched_verified',window={'title':'Report','visible':True})
    result=_run_case([
        _message(tool_calls=[_tool_call('find','window_control',{'action':'find','title':'Report'})]),
        _message(tool_calls=[_tool_call('open','app_launch',{'command':'explorer.exe C:/report.docx','expected_process':'explorer.exe'})]),
        _message(tool_calls=[_tool_call('done','answer_text',{'result':'Launch observed.','unresolved':[]})])],
        poll_task_id='open-recovery',send_command_fn=send,user_message='Open report.docx')
    assert sent==['window.control','app.launch']
    assert any(c.get('tool_name')=='app.launch' for c in result['commands'])


@pytest.mark.parametrize('case',['two_objects_one_open','wrong_object','same_object_twice'])
def test_remaining_open_identity_must_not_be_inferred_from_effect_count(case):
    effects=['open','open'] if case=='same_object_twice' else ['open']
    steps=[dict(step_id=f'step_{i+1}',tool_name='app.launch',target_device_id='pc',status='success',
                result=dict(status='launched_verified',window={'title':'Other document','visible':True}))
           for i in range(len(effects))]
    task=dict(task_id='objects',status='done',device_ids=['pc'],window_effects=effects,
              proposed_objective='Open A.docx and B.pptx' if case!='wrong_object' else 'Open A.docx',commands=steps)
    assert not build_worker_report(task)['goal_completed']
