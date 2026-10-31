"""Core simplification: contracts and real projections, no external services."""
from types import SimpleNamespace
import json
import pytest
from server import controller, orchestrator as orch, database as db
from server.controller_prompts import SYSTEM_PROMPT_TEMPLATE, WINDOWS_RULES
from server.runtime_state import tasks
from server.response_presentation import worker_presentation
from test_orchestrator_worker import owners
from server.worker_reports import build_worker_report, coverage_binding
from test_tool_only_protocol import _run_case, _message, _tool_call


def test_worker_prompt_has_no_competing_user_speech_or_chunk_write_protocol():
    assert 'мужском роде' not in SYSTEM_PROMPT_TEMPLATE
    assert 'ФОРМАТИРОВАНИЕ ПИСЬМЕННОГО ОТВЕТА' not in SYSTEM_PROMPT_TEMPLATE
    assert 'дальше append=true' not in SYSTEM_PROMPT_TEMPLATE
    assert '### 2. write_content' not in SYSTEM_PROMPT_TEMPLATE
    assert 'wmctrl' not in WINDOWS_RULES


def route_prompt(monkeypatch):
    from dataclasses import fields
    runtime=SimpleNamespace(**{f.name:'' for f in fields(controller.LLMRuntimeContext)})
    runtime.cfg={'model':'mock'};runtime.mem_user_id=None
    monkeypatch.setitem(tasks,'cs-open',{'execution_scope':'window','window_effects':['open']})
    kw=controller._build_route_kwargs(route=controller.ROUTE_REGISTRY['non_pipeline'],runtime=runtime,
        user_message='Open a.docx',device_id='pc',device_info={},all_devices={},send_command_fn=None,
        get_file_link_fn=None,chat_history=[],user_id=None,chat_id=None,device_profile=None,modes={},poll_task_id='cs-open')
    return kw['system_msg']


def test_actual_open_route_does_not_override_native_opener(monkeypatch):
    prompt=route_prompt(monkeypatch)
    assert 'app_launch' in prompt and 'limited to existing windows' not in prompt
    assert 'never shell' in prompt and 'requested' in prompt.lower()


def test_entry_points_share_one_worker_presentation():
    t={'task_id':'t','device_ids':['pc'],'status':'done','execution_scope':'window','window_effects':['open'],
       'commands':[{'step_id':'step_1','tool_name':'app.launch','target_device_id':'pc',
                    'result':{'status':'failed','error':'window_not_found','summary':'Window not found'}}]}
    assert worker_presentation(t)==orch.present_worker_result(t)


def test_owned_memory_ranks_normalized_matches_before_limit(owners):
    a,b=owners
    with db.get_db() as c:
        for owner,text in [(a,f'ИРУ предпочтение {i}') for i in range(20)]+[
            (a,'Журнал сервиса IRU на узле alpha'),(b,'Журнал сервиса ИРУ SECRET')]:
            c.execute('INSERT INTO user_memory(user_id,fact_text,category,created_at) VALUES(?,?,?,?)',
                      (str(owner['id']),text,'general',0))
    for query in ('Журнале ИРУ','журнала IRU','журналом ИРУ'):
        selected=orch.relevant_facts(a['id'],query)
        assert 'alpha' in selected[0]['text']
        assert not any('SECRET' in f['text'] for f in selected)


def test_memory_expansion_is_candidate_data_and_never_foreign_authority(owners):
    a,b=owners
    with db.get_db() as c:
        for owner,text in [(a,'Для журнала сервиса IRU: ssh host journalctl -u iru'),(b,'SECRET сервер')]:
            c.execute('INSERT INTO user_memory(user_id,fact_text,category,created_at) VALUES(?,?,?,0)',
                      (str(owner['id']),text,'general'))
    for query in ('сервер','сервером','сервере','Помнишь команды на сервере?'):
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
        assert f'Runtime action turns remaining: {research_turns+1-i}/{research_turns+1}' in messages[0]['content']
        assert messages[0]['content'].count('Runtime action turns remaining:')==1
    t.update(status='done',commands=result['commands'],answer=result['answer'])
    assert not build_worker_report(t)['goal_completed']
    assert len([c for c in result['commands'] if c.get('tool_name')=='web_search'])==research_turns


def office_projection(missing_page=False, facet=None, coverage='partial', stale=False):
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
    assessment={'coverage':coverage,'evidence_refs':[s['step_id'] for s in steps],
                'binding':coverage_binding('request',steps,text)}
    if facet is not None:assessment['artifacts']=facet
    steps.append(dict(step_id='answer',tool_name='answer.text',status='terminal',
                      result=dict(text=text,_coverage=assessment)))
    t=dict(task_id='t',status='done',original_request='request',device_ids=['pc'],assigned_artifacts=[path],answer=text,commands=steps)
    return build_worker_report(t)


@pytest.mark.parametrize('missing_page',[False,True])
def test_office_observation_is_independent_and_missing_page_cannot_be_overruled(missing_page):
    report=office_projection(missing_page,coverage='complete' if missing_page else 'partial')
    assert not report['goal_completed']
    assert report['artifacts'][0]['content_observed'] is not missing_page
    assert not report['artifacts'][0]['content_verified']  # semantics not proven by reads alone


def content_facet(coverage='complete',**overrides):
    return dict(device_id='pc',path='C:/report.pptx',sha256='a'*64,coverage=coverage,
                evidence_refs=['step_2','step_3'],**overrides)


def test_content_requirement_and_whole_goal_are_separate_in_one_assessment():
    report=office_projection(facet=[content_facet()])
    assert report['artifacts'][0]['content_verified']
    assert report['status']=='partial' and not report['goal_completed']


@pytest.mark.parametrize('case',['wrong_meaning','missing_page','stale','wrong_device','wrong_path','duplicate','malformed'])
def test_content_facet_never_overrides_negative_or_unbound_native_facts(case):
    item=content_facet('partial' if case=='wrong_meaning' else 'complete')
    if case=='wrong_device':item['device_id']='other'
    if case=='wrong_path':item['path']='C:/other.pptx'
    facets=[] if case=='malformed' else [item,item] if case=='duplicate' else [item]
    report=office_projection(missing_page=case=='missing_page',stale=case=='stale',facet=facets,coverage='complete')
    assert not report['goal_completed'] and not report['artifacts'][0]['content_verified']


def test_positive_content_and_goal_assessment_still_require_current_native_pages():
    report=office_projection(facet=[content_facet()],coverage='complete')
    assert report['goal_completed'] and report['artifacts'][0]['content_verified']


def test_existing_assessor_returns_content_facet_in_one_call():
    import asyncio
    from server.answer_auditor import assess_result_coverage
    calls=[]
    async def provider(**kwargs):
        calls.append(kwargs)
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'coverage':'partial','evidence_refs':['step_2'],'reason':'independent condition',
            'artifacts':[dict(device_id='pc',path='C:/report.pptx',sha256='a'*64,
                              coverage='complete',evidence_refs=['step_2'])]})}}]}
    result=asyncio.run(assess_result_coverage(client=None,cfg={'answer_auditor_enabled':True},
        chat_completion_request_fn=provider,user_request='request',
        current_run_journal=[{'step_id':'step_2','tool_name':'office_pptx','tool_type':'typed','result':{'items':[{'text':'content'}]}}],
        answer_payload={'text':'Observed content'}))
    assert len(calls)==1 and calls[0]['phase']=='result_coverage'
    assert result['coverage']=='partial' and result['artifacts'][0]['coverage']=='complete'


def test_failed_discovery_does_not_prevent_native_open(monkeypatch):
    monkeypatch.setattr(db,'get_device_profile',lambda *a,**k:None)
    monkeypatch.setitem(tasks,'open-recovery',dict(task_id='open-recovery',execution_scope='window',window_effects=['open']))
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


@pytest.mark.xfail(strict=True,reason='CS-01 remaining: Assignment and native open receipts lack mandatory object binding; requires a separately reviewed protocol contract')
@pytest.mark.parametrize('case',['two_objects_one_open','wrong_object','same_object_twice'])
def test_remaining_open_identity_must_not_be_inferred_from_effect_count(case):
    effects=['open','open'] if case=='same_object_twice' else ['open']
    steps=[dict(step_id=f'step_{i+1}',tool_name='app.launch',target_device_id='pc',status='success',
                result=dict(status='launched_verified',window={'title':'Other document','visible':True}))
           for i in range(len(effects))]
    task=dict(task_id='objects',status='done',device_ids=['pc'],execution_scope='window',window_effects=effects,
              proposed_objective='Open A.docx and B.pptx' if case!='wrong_object' else 'Open A.docx',commands=steps)
    assert not build_worker_report(task)['goal_completed']
