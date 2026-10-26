"""Mixed shell diagnostics and read-only follow-ups on persisted terminal jobs."""
import asyncio
import copy
import json
import time
import uuid
from types import SimpleNamespace

import pytest

from server import database as db, orchestrator as orch
from server.worker_reports import build_worker_report, operation_facts, receipt_from_report
from server.response_presentation import normalized_worker_report, worker_presentation, saved_step_reports
from server.worker_scheduler import init_worker_storage
from server.runtime_state import tasks
from test_orchestrator_worker import owners


def diagnostic(owner):
    # c9f3cf49: successful measurements, a compound command with useful stdout
    # but failed exit, a later successful measurement, and an audited report.
    t={'task_id':str(uuid.uuid4()),'user_id':owner['id'],'chat_id':owner['chat_id'],
       'kind':'worker','worker_id':'worker-1','status':'partial','execution_scope':'analysis','message':'Давай',
       'proposed_objective':'Проверь интернет и его характер','device_ids':[f"{owner['id']}:pc"],
       'created_at':time.time(),'answer':'GitHub: 17/20, три таймаута. Загрузка: 15 Мбит/с. Сигнал Wi-Fi не измерен.',
       'commands':[],'tasks':[],
       'task_receipt':{'answer_source':'audited_terminal','task_status':'completed',
                       'goal_completed':True,'final_verification_status':'verified'}}
    for index,(rc,out) in enumerate([(0,'HTTP=200'),(1,'github: ok=17 fail=3\nya.ru: ok=20 fail=0\n--- wifi signal ---'),(0,'download=15 Mbit/s')],1):
        t['commands'].append({'step_id':f'step_{index}','tool_name':'execute_cmd',
            'status':'success' if rc==0 else 'failed','device_id':'pc','target_device_id':'pc',
            'command':'Measure HTTP; inspect Wi-Fi' if rc else 'Measure HTTP',
            'result':{'returncode':rc,'stdout':out,'stderr':''}})
    t['commands'].append({'tool_name':'answer.text','step_id':'step_4','status':'terminal',
        'result':{'answer_type':'grounded_report','text':t['answer'],'basis':['step_1','step_2','step_3'],
          'self_check':{'depends_on_current_external_state':True,'claims_completed_action':False,
                        'has_sufficient_evidence':True,'missing_evidence_question':'Wi-Fi signal not measured'}}})
    t['worker_report']={**build_worker_report(t),'status':'unknown','goal_completed':False,
        'error_code':'action_evidence_unconfirmed','summary':'Исход не подтверждён.'}
    return t


def store(t):
    init_worker_storage()
    with db.get_db() as c:
        c.execute('''INSERT INTO worker_jobs(task_id,owner_user_id,chat_id,state,payload,report,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)''',(t['task_id'],t['user_id'],t['chat_id'],t['status'],
                json.dumps(t,ensure_ascii=False),json.dumps(t['worker_report']),t['created_at'],t['created_at']))


def cmd(message='Что там с анализом интернета?'):
    return SimpleNamespace(request_id=str(uuid.uuid4()),message=message,device_id='pc',modes={},broadcast=False)


def decide_as(monkeypatch,choice):
    async def decide(*args,**kwargs):return choice,{'llm_calls':1,'snapshot_calls':0}
    monkeypatch.setattr(orch,'decide',decide)


def test_legacy_mixed_case_is_partial_without_accepting_failed_step(owners):
    t=diagnostic(owners[0]);before=copy.deepcopy(t)
    report=normalized_worker_report(t,t['worker_report'])
    assert report['status']=='partial' and report['goal_completed'] is False
    assert report['error_code']=='mixed_diagnostic_evidence'
    assert operation_facts(t['commands'][1])=='failed'
    assert 'частично' in worker_presentation(t,report)['conversational_response']
    assert saved_step_reports(t)[0]['text']==t['answer']
    assert t['commands']==before['commands']


def test_unrecovered_failed_measurement_keeps_historical_report_readable(owners):
    t=diagnostic(owners[0]);t['commands'].pop(2)
    t['commands'][-1]['result']['basis']=['step_1','step_2']
    t['answer']=t['commands'][-1]['result']['text']='Есть HTTP-ответ; измерение сигнала завершилось ошибкой.'
    assert normalized_worker_report(t,t['worker_report'])['status']=='partial'
    assert saved_step_reports(t)[0]['text']==t['answer']
    assert operation_facts(t['commands'][1])=='failed'


def test_new_receipt_and_projection_agree_on_mixed_result(owners):
    t=diagnostic(owners[0]);payload=t['commands'][-1]['result']
    t['status']='done'
    receipt=receipt_from_report(build_worker_report(t))
    assert receipt['task_status']=='partial' and receipt['goal_completed'] is False
    assert receipt['final_verification_status']=='unverified'
    t.update(status='done',task_receipt=receipt)
    assert build_worker_report(t)['status']=='partial'
    assert receipt_from_report(build_worker_report(t))==receipt


def test_rc_zero_and_failed_steps_cannot_prove_completed_action(owners):
    t=diagnostic(owners[0]);t['task_receipt']={};t['status']='done'
    assert build_worker_report(t)['status']=='partial'
    t['commands'][-1]['result']['self_check']['claims_completed_action']=True
    t['task_receipt']={'answer_source':'audited_terminal','task_status':'completed','goal_completed':True,'final_verification_status':'verified'}
    assert build_worker_report(t)['status']!='success'
    assert not build_worker_report(t)['goal_completed']  # rc=0 is not goal proof


def test_failed_only_diagnostic_is_not_complete_and_unused_failures_do_not_poison_basis(owners):
    t=diagnostic(owners[0]);payload=t['commands'][-1]['result']
    payload['basis']=['step_2']
    t['status']='done'
    receipt=receipt_from_report(build_worker_report(t))
    assert receipt['task_status']=='partial' and not receipt['goal_completed']
    payload['basis']=['step_3']
    t['status']='done'
    receipt=receipt_from_report(build_worker_report(t))
    assert receipt['task_status']=='partial'
    assert not build_worker_report(t)['goal_completed']


@pytest.mark.parametrize('reference',['unique','active'])
@pytest.mark.parametrize('details',[False,True])
def test_followup_reads_sole_completed_job_after_restart(owners,monkeypatch,reference,details):
    a,b=owners;t=diagnostic(a);store(t);tasks.pop(t['task_id'],None)
    decide_as(monkeypatch,orch.Decision(intent='task_status',reference=reference,show_execution_details=details))
    result=asyncio.run(orch.run_turn(cmd(),a,a['chat_id'],None))
    assert not result['worker_task_id'] and 'частично' in result['answer']
    if details:assert t['answer'] in result['answer'] and 'исходный материал' in result['answer']
    with db.get_db() as c:
        saved=c.execute('SELECT state,payload FROM worker_jobs WHERE task_id=?',(t['task_id'],)).fetchone()
    assert saved['state']==t['status']=='partial' and json.loads(saved['payload'])==t


@pytest.mark.parametrize('intent',['cancel','amend'])
@pytest.mark.parametrize('reference',['unique','active'])
def test_completed_job_read_fallback_never_applies_to_mutations(owners,monkeypatch,intent,reference):
    a,b=owners;t=diagnostic(a);store(t)
    decide_as(monkeypatch,orch.Decision(intent=intent,reference=reference,objective='Измени задачу'))
    result=asyncio.run(orch.run_turn(cmd(),a,a['chat_id'],None))
    assert 'Уточните' in result['answer'] and not result['worker_task_id']
    with db.get_db() as c:
        assert json.loads(c.execute('SELECT payload FROM worker_jobs WHERE task_id=?',(t['task_id'],)).fetchone()[0])==t


def test_multiple_terminal_jobs_require_reference_but_explicit_read_is_allowed(owners,monkeypatch):
    a,b=owners;first=diagnostic(a);second=diagnostic(a)
    store(first);store(second)
    decide_as(monkeypatch,orch.Decision(intent='task_status',reference='unique'))
    result=asyncio.run(orch.run_turn(cmd(),a,a['chat_id'],None))
    assert 'Уточните' in result['answer']
    decide_as(monkeypatch,orch.Decision(intent='task_status',reference='active',task_id=first['task_id'],result_view='saved_report'))
    result=asyncio.run(orch.run_turn(cmd(),a,a['chat_id'],None))
    assert first['answer'] in result['answer']


@pytest.mark.parametrize('reference',['unique','active'])
def test_explicit_terminal_read_does_not_switch_to_another_active_job(owners,monkeypatch,reference):
    a,b=owners;finished=diagnostic(a);store(finished)
    active=diagnostic(a);active['status']='running';active['worker_report']=build_worker_report(active);store(active)
    decide_as(monkeypatch,orch.Decision(intent='task_status',reference=reference,task_id=finished['task_id'],result_view='saved_report'))
    result=asyncio.run(orch.run_turn(cmd(),a,a['chat_id'],None))
    assert finished['answer'] in result['answer']
    assert tasks[result['task_id']]['worker_report']['task_id']==finished['task_id']


@pytest.mark.parametrize('reference',['unique','active','latest','explicit'])
def test_terminal_read_keeps_owner_and_chat_boundaries(owners,monkeypatch,reference):
    a,b=owners;t=diagnostic(b);store(t)
    decide_as(monkeypatch,orch.Decision(intent='task_status',reference=reference,task_id=t['task_id']))
    result=asyncio.run(orch.run_turn(cmd(),a,a['chat_id'],None))
    assert t['answer'] not in result['answer'] and not result['worker_task_id']
    other_chat=db.create_chat(a['id'],'other')['id'];own=diagnostic(a);own['chat_id']=other_chat;store(own)
    decide_as(monkeypatch,orch.Decision(intent='task_status',reference=reference,task_id=own['task_id']))
    result=asyncio.run(orch.run_turn(cmd(),a,a['chat_id'],None))
    assert own['answer'] not in result['answer']


def test_unknown_saved_report_is_returned_without_promoting_status(owners,monkeypatch):
    a,b=owners;t=diagnostic(a);t['status']='unknown';t['task_receipt']={};t['worker_report']=build_worker_report(t);store(t)
    decide_as(monkeypatch,orch.Decision(intent='task_status',reference='latest',show_execution_details=True))
    result=asyncio.run(orch.run_turn(cmd('Покажи сохранённый отчёт'),a,a['chat_id'],None))
    assert t['answer'] in result['answer'] and 'не подтверждение завершения' in result['answer']
    assert tasks[result['task_id']]['worker_report']['status']=='unknown'


@pytest.mark.parametrize('code',['device_not_owned_or_unavailable','selected_device_not_owned_or_connected','target_device_required'])
def test_handoff_error_explains_stage_and_preserves_exact_diagnostic(owners,monkeypatch,caplog,code):
    a,b=owners;decide_as(monkeypatch,orch.Decision(intent='delegate',objective='Проверь интернет',target_device_ids=['pc']))
    async def delegate(*args,**kwargs):raise ValueError(code)
    with caplog.at_level('ERROR',logger='iru.orchestrator'):
        result=asyncio.run(orch.run_turn(cmd(),a,a['chat_id'],delegate))
    assert ('устройство' in result['answer']) and 'разобрать' not in result['answer']
    assert code in caplog.text and '"stage": "handoff"' in caplog.text
    assert not result['worker_task_id']


def test_unclassified_handoff_failure_does_not_invent_parse_error_or_leak_exception(owners,monkeypatch,caplog):
    a,b=owners;decide_as(monkeypatch,orch.Decision(intent='delegate',objective='Проверь интернет',target_device_ids=['pc']))
    async def delegate(*args,**kwargs):raise RuntimeError('private-provider-key-should-not-leak')
    result=asyncio.run(orch.run_turn(cmd(),a,a['chat_id'],delegate))
    assert 'Не удалось подтвердить передачу' in result['answer']
    assert 'private-provider-key' not in result['answer']+caplog.text


def test_controller_keeps_failed_measurement_and_returns_partial_receipt(owners):
    from test_tool_only_protocol import _run_case, _message, _tool_call
    a,b=owners;t=diagnostic(a);sent=[]
    async def transport(device,action,params):
        sent.append(action)
        return copy.deepcopy(t['commands'][len(sent)-1]['result'])
    payload=t['commands'][-1]['result']
    result=_run_case([
        _message(tool_calls=[_tool_call('read1','execute_cmd',{'command':'Get-NetAdapter','artifact_paths':[]})]),
        _message(tool_calls=[_tool_call('read2','execute_cmd',{'command':'Get-NetAdapterStatistics','artifact_paths':[]})]),
        _message(tool_calls=[_tool_call('read3','execute_cmd',{'command':'Get-NetAdapter | Select-Object LinkSpeed','artifact_paths':[]})]),
        _message(tool_calls=[_tool_call('report','answer_text',payload)]),
        _message(content=json.dumps({'valid':True,'reason':'Measurements with an explicitly missing Wi-Fi signal'}),finish_reason='stop'),
        ],user_id=a['id'],chat_id=a['chat_id'],device_id=f"{a['id']}:pc",send_command_fn=transport,
        user_message='Проверь интернет и сообщи измерения',cfg={'model':'mock-model','answer_auditor_enabled':True})
    assert len(sent)==3,[(e.get('tool_name'),e.get('result')) for e in result.get('commands',[])]
    report=build_worker_report({**result,'task_id':'mixed-controller','status':'done'})
    assert report['status']=='partial' and not report['goal_completed']
    assert result['commands'][1]['status']=='failed'


@pytest.mark.parametrize('other_count',[0,3])
@pytest.mark.parametrize('outcome',['partial','success'])
def test_ordinary_named_followup_returns_short_content_from_main_call(owners,monkeypatch,other_count,outcome):
    from test_context_token_budget import config
    a,b=owners;t=diagnostic(a);t['proposed_objective']='Диагностика интернета'
    if outcome=='success':
        t['commands'][1]['status']='success';t['commands'][1]['result']['returncode']=0
        from server.worker_reports import coverage_binding, coverage_request
        t['status']='success'
        t['commands'][-1]['result']['_coverage']={'coverage':'complete','evidence_refs':['step_1','step_2','step_3'],
            'binding':coverage_binding(coverage_request(t),t['commands'][:-1],t['answer'])}
        t['worker_report']=build_worker_report(t)
    store(t)
    for n in range(other_count):
        other=diagnostic(a);other['proposed_objective']=f'Проверка документа {n}'
        other['answer']=other['commands'][-1]['result']['text']=f'Документ {n}: 42 страницы.'
        store(other)
    short='Интернет работал: GitHub ответил в 17 из 20 запросов, загрузка — 15 Мбит/с. Сигнал Wi-Fi не измерен; причина таймаутов не установлена.'
    calls=[]
    async def complete(*args,**kwargs):
        calls.append(kwargs.get('phase'))
        messages=kwargs.get('messages') if not args else args[3]
        context=json.loads(messages[1]['content'])['untrusted_context']
        source=next(job for job in context['tasks'] if job['task_id']==t['task_id'])
        assert source['objective']=='Диагностика интернета'
        assert source['saved_reports'][0]['text']==t['answer']
        assert source['saved_reports'][0]['historical_material_only'] is True
        return {'choices':[{'finish_reason':'stop','message':{'tool_calls':[{'function':{
            'name':'orchestrator_decision','arguments':json.dumps({'intent':'task_status',
                'task_id':t['task_id'],'reference':'explicit','answer':short})}}]}}]}
    monkeypatch.setattr(orch,'load_llm_config',lambda:config())
    monkeypatch.setattr(orch,'_chat_completion_request',complete)
    result=asyncio.run(orch.run_turn(cmd('Что там с анализом интернета?'),a,a['chat_id'],None))
    assert short in result['answer'] and len(result['answer'])<400
    assert ('частично' in result['answer'])==(outcome=='partial') and '42 страницы' not in result['answer']
    assert calls==['orchestrator'] and result['worker_task_id'] is None
    assert tasks[result['task_id']]['worker_report']['task_id']==t['task_id']
    with db.get_db() as c:
        assert c.execute('SELECT COUNT(*) FROM worker_jobs WHERE owner_user_id=?',(a['id'],)).fetchone()[0]==1+other_count


def test_report_previews_are_bounded_and_owner_scoped(owners):
    from server.context_budget import estimate_tokens
    a,b=owners
    for n in range(8):
        t=diagnostic(a);t['answer']=t['commands'][-1]['result']['text']='Материал '+('длинные данные '*2000);store(t)
    hidden=diagnostic(b);hidden['answer']=hidden['commands'][-1]['result']['text']='PRIVATE-OTHER-OWNER';store(hidden)
    ctx=orch.context_for(a['id'],a['chat_id'],'Что с результатами?','pc')
    previews=[r for job in ctx['tasks'] for r in job['saved_reports']]
    assert sum(estimate_tokens(r['text']) for r in previews)<=8192
    assert all(r['truncated'] for r in previews)
    assert 'PRIVATE-OTHER-OWNER' not in json.dumps(ctx)
