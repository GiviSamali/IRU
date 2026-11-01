"""Contract regressions only. Scripted providers do not establish LIVE LLM quality."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import database as db
from server.runtime_state import tasks
from server.run_journal import compact_search_material, serialize_tool_result_for_llm, unfinished_execution_result
from server.tool_repeat_guard import mark_read_only_tool_step, find_prior_successful_read_only_tool_step
from server.tool_completion import normalize_operation_result, confirmed_window_effect
from server.worker_reports import build_worker_report, semantic_assessment_question
from test_tool_only_protocol import _run_case, _message, _tool_call


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path/'efficiency.sqlite')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'agent'))
    db.init_db()


def answer(text='Findings', unresolved=None):
    return _message(tool_calls=[_tool_call('answer', 'answer_text',
                    {'result':text, 'unresolved':unresolved or []})])


def search_step(query='AI news', idx=1, result=None):
    return mark_read_only_tool_step({'step_id':f'step_{idx}', 'tool_name':'web_search',
        'tool_type':'typed', 'status':'success', 'command':'[web_search] '+query,
        'result':result or {'results':[{'title':'Dated source', 'url':f'https://example.org/{idx}',
                                      'content':'Unverified excerpt with date 2026-11-01. '*30}]}},
        'web_search', {'query':query,'max_results':5})


def test_search_cache_same_args_across_other_actions_and_within_batch():
    original=search_step()
    journal=[original,{'tool_name':'execute_cmd','status':'success','result':{'returncode':0}}]
    assert find_prior_successful_read_only_tool_step(journal,'web_search',{'query':'AI news'}) is original
    assert find_prior_successful_read_only_tool_step([original],'web_search',{'query':'AI news','max_results':5}) is original
    assert find_prior_successful_read_only_tool_step(journal,'web_search',{'query':'AI news research'}) is None
    assert find_prior_successful_read_only_tool_step(journal,'web_search',{'query':'AI news','max_results':10}) is None
    assert find_prior_successful_read_only_tool_step(journal,'web_search',{'query':'AI news','refresh':True}) is None


def test_search_errors_and_unknown_are_not_cached():
    for status in ('failed','unknown','partial','blocked'):
        step=search_step();step['status']=status
        assert find_prior_successful_read_only_tool_step([step],'web_search',{'query':'AI news'}) is None
    step=search_step();step['result']={'error':'unavailable'}
    assert find_prior_successful_read_only_tool_step([step],'web_search',{'query':'AI news'}) is None


def test_compaction_preserves_bounded_sources_and_does_not_mutate_journal():
    journal=[search_step(idx=n) for n in range(1,41)]
    journal.append(search_step(idx=41,result={'results':[{'title':'duplicate','url':'https://example.org/1#fragment','content':'same'}]}))
    original=copy.deepcopy(journal)
    material=compact_search_material(journal,max_chars=5000)
    assert material['trust_level']=='untrusted_search_excerpts' and material['details_omitted']
    assert len(json.dumps(material['sources'],ensure_ascii=False))<=5000
    assert all(s['title'] and s['url'] and s['excerpt'] and s['step_id'] for s in material['sources'])
    assert len({s['url'].split('#')[0] for s in material['sources']})==len(material['sources'])
    assert journal==original
    projected=json.loads(serialize_tool_result_for_llm(journal[0]))
    assert projected['authority']=='data_only' and projected['result']['sources'][0]['url']=='https://example.org/1'


def test_fallback_retains_search_material_without_claiming_complete():
    result=unfinished_execution_result([search_step()], 'terminal_answer_missing')
    assert 'https://example.org/1' in result['answer']
    assert 'достоверность и полнота не установлены' in result['answer']
    assert result['task_receipt']['task_status']!='success'


def test_forty_identical_batch_searches_dispatch_once(monkeypatch):
    from server import controller_non_pipeline as loop
    calls=[]
    async def search(cfg,query,limit):
        calls.append(query);return search_step()['result']
    monkeypatch.setattr(loop,'_run_web_search',search)
    batch=_message(tool_calls=[_tool_call(str(i),'web_search',{'query':'AI news'}) for i in range(40)])
    seen=[]
    result=_run_case([batch,answer('One reused source')],captured=seen)
    assert calls==['AI news'] and len(seen)==2
    assert len([s for s in result['commands'] if s['tool_name']=='web_search'])==1
    assert len([m for m in seen[1]['messages'] if m.get('role')=='tool'])==40


def test_twenty_call_budget_includes_reserved_terminal_and_assessor(monkeypatch):
    from server import controller_non_pipeline as loop
    monkeypatch.setitem(tasks,'research',{'task_id':'research','execution_scope':'analysis',
        'original_request':'Compare recent AI sources','device_ids':[]})
    dispatched=[]
    async def search(cfg,query,limit):
        dispatched.append(query);return search_step()['result']
    monkeypatch.setattr(loop,'_run_web_search',search)
    turns=[_message(tool_calls=[_tool_call(f'q{i}','web_search',{'query':f'AI detail {i}'})]) for i in range(18)]
    turns += [answer('Compared the supplied sources'),_message(content=json.dumps(
        {'coverage':'complete','evidence_refs':['step_1'],'reason':'scripted semantic fixture'}),finish_reason='stop')]
    seen=[]
    result=_run_case(turns,poll_task_id='research',max_iterations=20,captured=seen,
                     cfg={'model':'mock','answer_auditor_enabled':True})
    assert len(seen)==20 and len(dispatched)==18
    assert all(t['function']['name'].startswith('answer_') for t in seen[18]['tools'])
    assert seen[19]['phase']=='result_coverage'
    assert 'FINAL TURN' in seen[18]['messages'][0]['content']
    assert result['commands'][-1]['tool_name']=='answer.text'


def test_provider_ignoring_final_inventory_cannot_execute_new_action():
    seen=[]; sent=[]
    async def send(device,action,args):
        sent.append(action);return {'returncode':0,'stdout':'actual','stderr':''}
    result=_run_case([_message(tool_calls=[_tool_call('a','execute_cmd',{'command':'whoami'})]),
                      _message(tool_calls=[_tool_call('bad','execute_cmd',{'command':'write hidden'})])],
                     send_command_fn=send,max_iterations=2,captured=seen)
    assert len(seen)==2 and sent==['execute_cmd']
    assert any(s['result'].get('error')=='final_turn_actions_disabled' for s in result['commands'])
    assert result['task_receipt']['task_status']!='success'


def test_early_terminal_does_not_consume_budget():
    seen=[]
    _run_case([answer('No action necessary')],max_iterations=20,captured=seen)
    assert len(seen)==1


def office_result(task_id='news',device='device-1',path='C:/Desktop/news.pptx'):
    return {'status':'success','action':'create','path':path,'format':'pptx','verified':True,
        'operation_verified':True,'verification_scope':'supported_elements_roundtrip',
        'sha256':'a'*64,'size_bytes':1024,'artifact_role':'final','items':[{'text':'Dated AI facts and sources'}],
        'total_items':1,'offset':0,'next_offset':None,'artifact_receipts':[
            {'path':path,'device_id':device,'verified':True,'created_or_changed':True,
             'sha256':'a'*64,'size_bytes':1024,'source_task_id':task_id,'format':'pptx','artifact_role':'final'}]}


def test_news_to_pptx_scripted_path_four_worker_turns_one_semantic_call(monkeypatch):
    from server import controller_non_pipeline as loop
    path='C:/Desktop/news.pptx'
    task={'task_id':'news','worker_id':'worker-1','original_request':'Recent AI news presentation',
          'proposed_objective':'Recent AI news presentation','execution_scope':'general',
          'device_ids':['device-1'],'assigned_artifacts':[path]}
    monkeypatch.setitem(tasks,'news',task)
    queries=[]; dispatched=[]
    async def search(cfg,query,limit):
        queries.append(query);return search_step(query,idx=len(queries))['result']
    async def send(device,action,args):
        dispatched.append(action);assert action=='office_pptx' and args['action']=='create'
        return office_result()
    monkeypatch.setattr(loop,'_run_web_search',search)
    turns=[_message(tool_calls=[_tool_call('q1','web_search',{'query':'AI model announcements'}),
                                _tool_call('q2','web_search',{'query':'AI research recent'})]),
           _message(tool_calls=[_tool_call('q3','web_search',{'query':'AI regulation dated sources'})]),
           _message(tool_calls=[_tool_call('create','office_pptx',{'action':'create','path':path,
              'data':{'slides':[{'title':'News','bullets':['Dated fact https://example.org/1']}]}})]),
           answer('Created a presentation with dated facts and source links'),
           _message(content=json.dumps({'coverage':'complete','evidence_refs':['step_1','step_4'],
              'artifacts':[{'device_id':'device-1','path':path,'sha256':'a'*64,'coverage':'complete','evidence_refs':['step_4']}],
              'reason':'Fixture supplies the required topics and dated sources'}),finish_reason='stop')]
    seen=[]
    result=_run_case(turns,user_message=task['original_request'],poll_task_id='news',max_iterations=20,
        captured=seen,send_command_fn=send,cfg={'model':'mock','answer_auditor_enabled':True})
    assert len(seen)==5 and len(queries)==3 and dispatched==['office_pptx']
    assert 'https://example.org/1' in json.dumps(seen[2]['messages'])
    assert 'untrusted_search_excerpts' in json.dumps(seen[2]['messages'])
    assert seen[-1]['phase']=='result_coverage'
    assert 'question' in json.loads(seen[-1]['messages'][-1]['content'])
    report=build_worker_report({**task,'status':'done','commands':result['commands'],'answer':result['answer']})
    assert report['goal_completed'] and report['artifacts'][0]['content_verified']


@pytest.mark.parametrize('result,expected',[
    ({},'unknown'),({'status':'unrecognized'},'unknown'),({'status':'success'},'unknown'),
    ({'status':'partial'},'partial'),({'error':'failure'},'failed'),
    ({'status':'disconnected'},'unknown')])
def test_unrecognized_response_never_defaults_to_success(result,expected):
    assert normalize_operation_result('office_pptx',result)==expected


def test_successful_search_is_not_a_completed_research_assignment():
    report=build_worker_report({'task_id':'r','status':'done','execution_scope':'analysis',
        'original_request':'Compare A, B, C','commands':[search_step()]})
    assert not report['goal_completed']


def test_unknown_mutation_or_unfinished_work_does_not_spend_assessor_call():
    task={'task_id':'r','execution_scope':'analysis','_action_outcome_unknown':True}
    assert semantic_assessment_question(task,[search_step()],{'text':'Known facts','unresolved':[]}) is None
    task.pop('_action_outcome_unknown')
    assert semantic_assessment_question(task,[search_step()],{'text':'Known facts','unresolved':['Missing C']}) is None


def test_closed_native_window_is_sufficient_even_when_process_still_alive(monkeypatch):
    from agent.core import actions
    import ctypes
    fake_user32=SimpleNamespace(PostMessageW=lambda *a:1,IsWindow=lambda handle:False)
    monkeypatch.setattr(actions.platform_mod,'name','Windows')
    monkeypatch.setattr(ctypes,'windll',SimpleNamespace(user32=fake_user32),raising=False)
    monkeypatch.setattr(actions,'_list_windows_internal',lambda **k:[{'handle':123,'pid':45}])
    monkeypatch.setattr(actions,'_process_alive',lambda pid:True)
    monkeypatch.setattr(actions.time,'sleep',lambda *a:None)
    result=actions.window_close(handle=123)
    assert result['status']=='closed' and result['window_closed']
    step={'tool_name':'window.close','result':result,'target_device_id':'pc','step_id':'step_1'}
    assert confirmed_window_effect(step)=='close'
    assert build_worker_report({'task_id':'w','status':'done','device_ids':['pc'],
        'window_effects':['close'],'commands':[step]})['goal_completed']


def test_linux_required_pipeline_failures_are_not_hidden(monkeypatch):
    from agent.platforms import linux
    seen=[]
    def run(argv,**kwargs):
        seen.append(argv);return SimpleNamespace(returncode=1,stdout='',stderr='required stage failed')
    monkeypatch.setattr(linux.subprocess,'run',run)
    result=linux.execute_cmd('required_operation | successful_last_stage')
    assert seen[0][:5]==['/bin/bash','-e','-o','pipefail','-c']
    assert normalize_operation_result('execute_cmd',result)=='failed'


def test_orchestrator_instructions_distinguish_results_not_technical_stages():
    from server import orchestrator
    prompt=' '.join(v for k,v in vars(orchestrator).items() if isinstance(v,str) and 'одного Worker' in v)
    assert 'Посмотри новости про ИИ' in prompt and 'отдельный обзор новостей' in prompt


def test_common_contract_is_used_for_both_worker_environments():
    from server.controller_prompts import WORKER_EXECUTION_RULES, SYSTEM_PROMPT_TEMPLATE
    from server import controller_onboarding
    import inspect
    assert WORKER_EXECUTION_RULES in SYSTEM_PROMPT_TEMPLATE
    assert 'system_msg=DYNAMIC_CONTEXT_RULES+WORKER_EXECUTION_RULES' in inspect.getsource(controller_onboarding)


@pytest.mark.parametrize('mode', ['create','edit'])
def test_office_roundtrip_exposes_complete_small_applied_input_without_second_inspect(tmp_path,mode):
    from agent.core.office_tools import office
    path=tmp_path/'values.xlsx'
    created=office('office_xlsx','create',str(path),data={'sheets':[{'name':'Data','rows':[['key','value'],['A',1]]}]},_source_task_id='o')
    assert created['status']=='success' and created['applied_input']['data']['sheets'][0]['rows'][1][1]==1
    if mode=='edit':
        changes=[{'op':'write_range','sheet':'Data','start':'B2','rows':[[2]]}]
        # Exact supported operation spelling is checked by the existing Office contract tests.
        result=office('office_xlsx','edit',str(path),changes=changes,expected_sha256=created['sha256'],_source_task_id='o')
        assert result['status']=='success' and result['applied_input']['changes']==changes
    else: result=created
    assert result['operation_verified'] and result['sha256']


def test_confirmed_file_evidence_survives_invalid_last_model_reply(monkeypatch):
    path='C:/result.txt';digest=hashlib.sha256(b'hello').hexdigest()
    task={'task_id':'saved','device_ids':['device-1'],'assigned_artifacts':[path]}
    monkeypatch.setitem(tasks,'saved',task)
    async def send(device,action,args):
        return {'status':'ok','verified':True,'committed':True,'path':path,'file_sha256':digest,'total_size':5}
    result=_run_case([_message(tool_calls=[_tool_call('write','write_content',{'path':path,'content':'hello'})]),
                      _message(tool_calls=[_tool_call('bad','answer_text',{'result':''})])],
                     poll_task_id='saved',send_command_fn=send,max_iterations=2)
    report=build_worker_report({**task,'status':'done','commands':result['commands'],'answer':result['answer']})
    assert report['goal_completed'] and report['artifacts'][0]['sha256']==digest
    assert 'Финальный текст модели не подтверждён' in report['summary']


def test_valid_office_receipt_without_content_coverage_is_not_assignment_success():
    path='C:/Desktop/news.pptx'
    step={'step_id':'step_1','tool_name':'office_pptx','status':'success','target_device_id':'device-1','result':office_result()}
    report=build_worker_report({'task_id':'news','status':'done','device_ids':['device-1'],
        'assigned_artifacts':[path],'original_request':'Required comparison of A, B and C',
        'commands':[step,{'tool_name':'answer.text','status':'terminal','result':{'text':'Done','_result_contract':3,'unresolved':[]}}]})
    assert not report['goal_completed']


def test_exact_text_write_does_not_need_semantic_storage_recheck():
    path='C:/x.txt'
    step={'step_id':'step_1','tool_name':'write_content','target_device_id':'pc','status':'success',
          'result':{'status':'ok','mode':'replace_exact','path':path,'verified':True,'committed':True,'file_sha256':'a'*64,'total_size':5}}
    assert semantic_assessment_question({'task_id':'text','preserve_file_bytes':True,'device_ids':['pc'],'assigned_artifacts':[path]},
        [step],{'text':'Written','_result_contract':3,'unresolved':[]}) is None


def test_optional_recommendation_does_not_suppress_required_semantic_question():
    question=semantic_assessment_question({'execution_scope':'analysis'},[search_step()],
        {'text':'Findings','_result_contract':3,'unresolved':[],'remaining':['Optional further reading']})
    assert question and 'scope' in question


def test_prepared_windows_native_command_checks_each_required_exit(monkeypatch):
    from agent.platforms import windows
    seen=[]
    def run(argv,stdout,stderr,**kwargs):
        seen.append(argv)
        # This fake verifies transport of the authored fail-fast command, not PowerShell semantics.
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(windows.subprocess,'run',run)
    monkeypatch.setattr(windows.subprocess,'STARTUPINFO',lambda:SimpleNamespace(dwFlags=0,wShowWindow=0),raising=False)
    monkeypatch.setattr(windows.subprocess,'STARTF_USESHOWWINDOW',1,raising=False)
    monkeypatch.setattr(windows.subprocess,'CREATE_NO_WINDOW',0,raising=False)
    command='python required.py; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; Write-Output Done'
    result=windows.execute_cmd(command)
    assert result['returncode']==7 and normalize_operation_result('execute_cmd',result)=='failed'
    assert command in seen[0][-1] and "$ErrorActionPreference='Stop'" in seen[0][-1]


def test_independent_results_keep_existing_dependency_graph():
    from server.worker_scheduler import validate_task_graph, dependency_context
    assignments=[{'key':'review','objective':'Separate dated review','target_device_ids':[], 'depends_on':[]},
                 {'key':'word','objective':'Word from review','depends_on':['review']},
                 {'key':'slides','objective':'PPTX from review','depends_on':['review']}]
    graph=validate_task_graph(assignments)
    assert graph['word']==['review'] and graph['slides']==['review']
    material,pending=dependency_context([{'result_id':'r','sha256':'a'*64,'data':{
        'task_id':'review','answer':'Dated sourced findings','report':{'status':'success','goal_completed':True},
        'commands':[search_step()]}}])
    assert not pending and material[0]['historical_material_only']
    assert material[0]['research_material'][0]['result']['results'][0]['url']


def test_applied_edit_delta_is_not_full_document_observation():
    result=office_result()
    result.update(action='edit',applied_input={'changes':[{'op':'replace_text','text':'A','replacement':'B'}]},
                  total_items=2,next_offset=1)
    step={'step_id':'step_1','tool_name':'office_pptx','status':'success','target_device_id':'device-1','result':result}
    report=build_worker_report({'task_id':'news','status':'done','device_ids':['device-1'],
        'assigned_artifacts':[result['path']],'original_request':'Review the whole document',
        'commands':[step]})
    assert not report['artifacts'][0]['content_observed'] and not report['goal_completed']
